import os
import threading
import time

import gpujob
import theme


class GPURunner:
    def _say(self, text, tone="muted"):
        self.inbox.put(("status", text, tone))

    def _save_job(self, **changes):
        self.job = dict(self.job or {}, **changes)
        theme.save_key("gpu_job", self.job)

    def _launch(self, params, settings):
        try:
            self._launch_inner(params, settings)
        except Exception as error:
            self._say(f"failed: {error}", "bad")
            self.inbox.put(("busy", False))

    def _launch_inner(self, params, settings):
        dry = gpujob.dry_run()
        user = gpujob.username()
        self._say("building job folder...")
        built = gpujob.build_job(params["workspace"], params, settings, user)
        self.job = None
        self._save_job(slug=built["slug"], dir=built["dir"], state="preparing",
                       user=user, dry=dry)
        folder, digest = gpujob.sync_src(
            built["dir"] if dry else params["workspace"])
        shown = "; ".join(f"{g[0]} {g[1]} {g[2] or ''}" for g in built["groups"])
        if dry:
            self._say(f"DRY RUN: job {built['slug']} built in {built['dir']}. "
                      f"Would version {user}/mnt-src from {folder} and push. "
                      f"Groups: {shown}", "good")
            self._save_job(state="dry")
            out = os.environ.get("MNT_GPU_DRY_OUT")
            if out:
                self._load_folder(out)
            self.inbox.put(("busy", False))
            return
        previous = theme.preferences().get("gpu_src_hash")
        if previous != digest:
            self._say("uploading source files to mnt-src...")
            code, text = gpujob.kaggle(
                ["datasets", "version", "-p", folder, "-m",
                 f"gui {time.strftime('%Y-%m-%d %H:%M')}"], timeout=1800)
            if code != 0:
                raise RuntimeError(f"dataset version: {text[-200:]}")
            theme.save_key("gpu_src_hash", digest)
            for _ in range(90):
                code, text = gpujob.kaggle(
                    ["datasets", "status", f"{user}/mnt-src"], timeout=120)
                if code == 0 and "ready" in text.lower():
                    break
                time.sleep(10)
        wanted = ["mnt-nse"] + (["mnt-us"] if params["us"] else [])
        for name in wanted:
            code, text = gpujob.kaggle(["datasets", "status", f"{user}/{name}"],
                                       timeout=120)
            if code != 0:
                if any(err in text for err in (
                        "getaddrinfo failed", "Failed to resolve",
                        "ConnectionError", "Max retries exceeded",
                        "timed out")):
                    raise RuntimeError(
                        "network error connecting to Kaggle (check internet "
                        f"connection): {text[-150:]}")
                raise RuntimeError(
                    f"dataset {user}/{name} not found on Kaggle - upload it "
                    f"first ({text[-120:]})")
        self._say("pushing kernel...")
        code, text = gpujob.kaggle(["kernels", "push", "-p", built["dir"]],
                                   timeout=600)
        if code != 0:
            raise RuntimeError(f"kernel push: {text[-200:]}")
        self._save_job(state="pushed", pushed=time.strftime("%Y-%m-%d %H:%M"))
        self._say(f"pushed {built['slug']}; checking every 5 minutes")
        self._start_poller()

    def _start_poller(self):
        self.stop_flag.clear()
        self.poller = threading.Thread(target=self._poll, daemon=True)
        self.poller.start()

    def _poll(self):
        slug, user = self.job["slug"], self.job["user"]
        failures = 0
        while not self.stop_flag.is_set():
            code, text = gpujob.kaggle(["kernels", "status", f"{user}/{slug}"],
                                       timeout=120)
            if code != 0:
                failures += 1
                if failures >= 12:
                    self._say(f"status check keeps failing: {text[-150:]}",
                              "bad")
                    self.inbox.put(("busy", False))
                    return
            else:
                failures = 0
                state = gpujob.status_of(text)
                self._save_job(state="running" if state == "running"
                               else state)
                self._say(f"{slug}: {text.splitlines()[-1][-120:]} "
                          f"(checked {time.strftime('%H:%M')})")
                if state in ("complete", "error"):
                    self._finish(state)
                    return
            for _ in range(300):
                if self.stop_flag.is_set():
                    return
                time.sleep(1)

    def _finish(self, state):
        out = os.path.join(self.job["dir"], "out")
        os.makedirs(out, exist_ok=True)
        self._say("downloading output...")
        gpujob.kaggle(["kernels", "output",
                       f"{self.job['user']}/{self.job['slug']}", "-p", out,
                       "--file-pattern", r".*\.(log|json|txt)$"],
                      timeout=1800)
        self._save_job(state="done" if state == "complete" else "failed")
        self._load_folder(out, label=state)
        self.inbox.put(("busy", False))

    def _load_folder(self, folder, label="loaded"):
        rows = gpujob.load_results(folder)
        self.inbox.put(("results", rows))
        if rows:
            self._say(f"{label}: {len(rows)} runs from {folder}", "good")
        else:
            err = gpujob.load_error_log(folder)
            if err:
                self._say(f"{label}: {err}", "bad")
            else:
                self._say(f"{label}: kernel ended with status {label} and no "
                          f"results came down ({folder}); open the kernel on "
                          "kaggle.com for its log - a crash is usually out of "
                          "memory, so lower 'US top'", "warn")

    def _resume(self):
        job = theme.preferences().get("gpu_job")
        if not isinstance(job, dict) or not job.get("slug") or not self.ready:
            return
        self.job = job
        state = job.get("state")
        if state in ("pushed", "running"):
            self.start_button.setEnabled(False)
            self._say(f"resumed {job['slug']}, checking status")
            self._start_poller()
        elif state in ("done", "failed", "dry"):
            out = os.path.join(job["dir"], "out")
            if state == "dry":
                out = os.environ.get("MNT_GPU_DRY_OUT") or out
            if os.path.isdir(out):
                self._load_folder(out, label=state)
            else:
                self._say(f"{job['slug']}: {state}")
