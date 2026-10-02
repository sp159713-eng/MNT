from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

import config
import gpujob
import theme
from nncard import parameter_count
from theme import Button, Card, Palette, fonts


class GPUCard(Card):
    def __init__(self, parent, app=None):
        super().__init__(parent, "Train on GPU (Kaggle)",
                         "free GPU session: walk-forward runs in parallel, "
                         "results pulled back here")
        self.app = app
        self.f = fonts()
        self.inbox = queue.Queue()
        self.stop_flag = threading.Event()
        self.poller = None
        self.job = None
        self.vars = {}
        self.labels = []
        self.inputs = []
        self.ready, self.why = gpujob.kaggle_ready()
        top = tk.Frame(self.body, bg=Palette.panel)
        top.pack(fill="x")
        self.toggle = Button(top, "Show", self._toggle, kind="ghost")
        self.toggle.pack(side="right", padx=(8, 0))
        self.status = tk.Label(top, text="", bg=Palette.panel, fg=Palette.muted,
                               font=self.f["small"], anchor="w", justify="left",
                               wraplength=600)
        self.status.pack(side="left", fill="x", expand=True)
        self.status.bind("<Configure>", lambda e: self.status.config(
            wraplength=max(200, e.width - 8)))
        self.content = tk.Frame(self.body, bg=Palette.panel)
        self.opened = False
        saved = theme.preferences().get("gpu_form") or {}
        form = {"presets": ["default"], "seeds": "3", "data": "India + US",
                "top": "1000", "hours": "8", "delivery": 1, "reference": 1,
                "workspace": gpujob.DEFAULT_WORKSPACE, **saved}

        self.preset_vars = {}
        self.chosen = set(form["presets"])
        row = tk.Frame(self.content, bg=Palette.panel)
        row.pack(fill="x", pady=(0, 4))
        tk.Label(row, text="NN presets", bg=Palette.panel, fg=Palette.muted,
                 font=self.f["small"]).pack(side="left", padx=(0, 10))
        self.preset_row = tk.Frame(row, bg=Palette.panel)
        self.preset_row.pack(side="left", fill="x", expand=True)
        self._build_presets()
        grid = tk.Frame(self.content, bg=Palette.panel)
        grid.pack(fill="x")
        for column in range(6):
            grid.grid_columnconfigure(column, weight=1, uniform="gpu")
        fields = (("seeds", "seeds (1-5)"), ("hours", "hours (1-11.5)"),
                  ("data", "data"), ("top", "US top-N"),
                  ("workspace", "workspace"))
        for index, (key, label) in enumerate(fields):
            row, column = divmod(index, 3)
            tk.Label(grid, text=label, bg=Palette.panel, fg=Palette.muted,
                     font=self.f["small"], anchor="w").grid(
                row=row, column=column * 2, sticky="w", pady=3)
            var = tk.StringVar(value=str(form[key]))
            if key == "data":
                widget = ttk.Combobox(grid, textvariable=var, state="readonly",
                                      values=("India only", "India + US"),
                                      width=10, font=self.f["mono_small"])
            else:
                widget = tk.Entry(grid, textvariable=var, width=12,
                                  bg=Palette.panel_high, fg=Palette.text,
                                  insertbackground=Palette.text, relief="flat",
                                  font=self.f["mono_small"])
            widget.grid(row=row, column=column * 2 + 1, sticky="ew", pady=3,
                        padx=(0, 10))
            var.trace_add("write", lambda *_: self._recount())
            self.vars[key] = var
            self.inputs.append(widget)

        ticks = tk.Frame(self.content, bg=Palette.panel)
        ticks.pack(fill="x", pady=(4, 0))
        for key, label in (("delivery", "delivery features"),
                           ("reference", "include reference blend")):
            var = tk.IntVar(value=int(form[key]))
            var.trace_add("write", lambda *_: self._recount())
            box = tk.Checkbutton(ticks, text=label, variable=var,
                                 bg=Palette.panel, fg=Palette.text,
                                 selectcolor=Palette.panel_high,
                                 activebackground=Palette.panel,
                                 activeforeground=Palette.text,
                                 font=self.f["small"], bd=0,
                                 highlightthickness=0)
            box.pack(side="left", padx=(0, 16))
            self.vars[key] = var
            self.inputs.append(box)

        self.count_note = self._label(Palette.muted)
        self.estimate_note = self._label(Palette.muted)

        buttons = tk.Frame(self.content, bg=Palette.panel)
        buttons.pack(fill="x", pady=(8, 0))
        self.start_button = Button(buttons, "Start on Kaggle", self.start)
        self.start_button.pack(side="left")
        self.forget_button = Button(buttons, "Forget job", self.forget,
                                    kind="ghost")
        self.forget_button.pack(side="left", padx=(8, 0))
        if gpujob.dry_run():
            Button(buttons, "Load results", self.load_dialog,
                   kind="ghost").pack(side="left", padx=(8, 0))
            tk.Label(buttons, text="DRY RUN: nothing is sent to Kaggle",
                     bg=Palette.panel, fg=Palette.warn,
                     font=self.f["small"]).pack(side="left", padx=(12, 0))

        columns = ("tag", "seeds", "avg", "worst", "gap")
        self.tree = ttk.Treeview(self.content, columns=columns, show="headings",
                                 height=5)
        for column, width, heading, anchor, stretch in (
                ("tag", 150, "RUN", "w", True),
                ("seeds", 220, "BP PER SEED", "w", True),
                ("avg", 80, "AVG", "e", False),
                ("worst", 90, "WORST", "e", False),
                ("gap", 110, "IC GAP", "e", False)):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, minwidth=40, anchor=anchor,
                             stretch=stretch)
        self.tree.tag_configure("stripe", background=Palette.stripe)
        self.tree.pack(fill="x", pady=(10, 0))

        if not self.ready:
            self._disable()
        self._recount()
        self.after(400, self._drain)
        self._resume()
        if self.job:
            self._toggle(True)

    def _label(self, colour):
        label = tk.Label(self.content, text="", bg=Palette.panel, fg=colour,
                         font=self.f["small"], anchor="w", justify="left",
                         wraplength=600)
        label.pack(fill="x", pady=(6, 0))
        label.bind("<Configure>", lambda e: label.config(
            wraplength=max(200, e.width - 8)))
        return label

    def _build_presets(self) -> None:
        for child in self.preset_row.winfo_children():
            child.destroy()
        names = config.nn_preset_names()
        self.chosen = {n for n in self.chosen if n in names}
        self.preset_vars = {}
        for index, name in enumerate(names):
            var = tk.IntVar(value=int(name in self.chosen))
            var.trace_add("write", lambda *_: self._presets_ticked())
            box = tk.Checkbutton(self.preset_row, text=name, variable=var,
                                 bg=Palette.panel, fg=Palette.text,
                                 selectcolor=Palette.panel_high,
                                 activebackground=Palette.panel,
                                 activeforeground=Palette.text,
                                 font=self.f["small"], bd=0,
                                 highlightthickness=0, anchor="w")
            box.grid(row=index // 4, column=index % 4, sticky="w", padx=(0, 14))
            self.preset_vars[name] = var
            if not self.ready:
                box.config(state="disabled")

    def _presets_ticked(self) -> None:
        self.chosen = {n for n, v in self.preset_vars.items() if v.get()}
        self._recount()

    def refresh_presets(self) -> None:
        self._build_presets()
        self._recount()

    def _toggle(self, force=None) -> None:
        self.opened = (not self.opened) if force is None else force
        if self.opened:
            self.content.pack(fill="x", pady=(8, 0))
        else:
            self.content.pack_forget()
        self.toggle.config(text="Hide" if self.opened else "Show")

    def _disable(self) -> None:
        for widget in self.inputs:
            try:
                widget.config(state="disabled")
            except tk.TclError:
                pass
        self.start_button.set_enabled(False)
        self.status.config(text=self.why, fg=Palette.warn)

    def _params(self):
        errors = []
        presets = [(n, config.nn_settings(n))
                   for n in config.nn_preset_names() if n in self.chosen]
        try:
            seeds = int(self.vars["seeds"].get())
            if not 1 <= seeds <= 5:
                raise ValueError
        except ValueError:
            seeds = 0
            errors.append("seeds")
        try:
            hours = float(self.vars["hours"].get())
            if not 1 <= hours <= 11.5:
                raise ValueError
        except ValueError:
            hours = 8.0
            errors.append("hours")
        try:
            top = int(self.vars["top"].get())
            if top < 1:
                raise ValueError
        except ValueError:
            top = 1000
            errors.append("US top-N")
        if not self.vars["workspace"].get().strip():
            errors.append("workspace")
        params = {"presets": presets, "seeds": seeds, "hours": hours,
                  "top": top, "us": self.vars["data"].get() == "India + US",
                  "delivery": bool(self.vars["delivery"].get()),
                  "reference": bool(self.vars["reference"].get()),
                  "workspace": self.vars["workspace"].get().strip()}
        if not params["presets"] and not params["reference"]:
            errors.append("nothing to run")
        return params, errors

    def _recount(self) -> None:
        params, errors = self._params()
        try:
            counts = ", ".join(
                f"{n}: {parameter_count(v):,}" for n, v in params["presets"])
        except Exception:
            counts = "?"
        self.count_note.config(
            text=f"parameters per checked preset - {counts or 'none'}")
        if errors:
            self.estimate_note.config(text=f"check: {', '.join(errors)}",
                                      fg=Palette.warn)
            return
        groups = gpujob.make_groups(params["presets"], params["us"],
                                    params["top"], params["delivery"],
                                    params["reference"])
        runs, wall = gpujob.estimate(groups, params["seeds"], params["us"])
        _, launch, kill = gpujob.clamp_hours(params["hours"])
        over = wall > launch
        self.estimate_note.config(
            text=f"{runs} runs, roughly {wall:.1f} GPU hours at "
                 f"{gpujob.PARALLEL} in parallel; no new run starts after "
                 f"{launch:.1f} h, anything running is killed at {kill:.1f} h"
                 + (" - too long, later runs will be dropped" if over else ""),
            fg=Palette.warn if over else Palette.muted)

    def _say(self, text: str, colour=None) -> None:
        self.inbox.put(("status", text, colour or Palette.muted))

    def _drain(self) -> None:
        try:
            while True:
                kind, *rest = self.inbox.get_nowait()
                if kind == "status":
                    self.status.config(text=rest[0], fg=rest[1])
                elif kind == "results":
                    self._show(rest[0])
                elif kind == "busy":
                    self.start_button.set_enabled(
                        self.ready and not rest[0])
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(400, self._drain)

    def _show(self, rows: list) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for index, row in enumerate(gpujob.summarise(rows)):
            seeds = ", ".join(f"{b:+d}" for b in row["bps"]) or "-"
            if row["failed"]:
                seeds += f"  ({row['failed']} failed)"
            self.tree.insert(
                "", "end", tags=("stripe",) if index % 2 else (),
                values=(row["tag"], seeds,
                        "-" if row["avg"] is None else f"{row['avg']:+.0f}",
                        "-" if row["worst"] is None else f"{row['worst']:+d}",
                        "-" if row["gap"] is None else f"{row['gap']:+.4f}"))

    def _save_job(self, **changes) -> None:
        self.job = dict(self.job or {}, **changes)
        theme.save_key("gpu_job", self.job)

    def start(self) -> None:
        params, errors = self._params()
        if errors:
            self.status.config(text=f"not started, check: {', '.join(errors)}",
                               fg=Palette.bad)
            return
        if self.job and self.job.get("state") in gpujob.STATES_LIVE:
            self.status.config(text="a job is already tracked - forget it "
                                    "first", fg=Palette.warn)
            return
        theme.save_key("gpu_form", {
            "presets": sorted(self.chosen), "seeds": self.vars["seeds"].get(),
            "hours": self.vars["hours"].get(), "data": self.vars["data"].get(),
            "top": self.vars["top"].get(),
            "delivery": self.vars["delivery"].get(),
            "reference": self.vars["reference"].get(),
            "workspace": params["workspace"]})
        self.stop_flag.clear()
        self._toggle(True)
        self.start_button.set_enabled(False)
        self._show([])
        settings = dict(config.NN_DEFAULTS)
        threading.Thread(target=self._launch, args=(params, settings),
                         daemon=True).start()

    def _launch(self, params: dict, settings: dict) -> None:
        try:
            self._launch_inner(params, settings)
        except Exception as error:
            self._say(f"failed: {error}", Palette.bad)
            self.inbox.put(("busy", False))

    def _launch_inner(self, params: dict, settings: dict) -> None:
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
                      f"Groups: {shown}", Palette.good)
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

    def _start_poller(self) -> None:
        self.stop_flag.clear()
        self.poller = threading.Thread(target=self._poll, daemon=True)
        self.poller.start()

    def _poll(self) -> None:
        slug, user = self.job["slug"], self.job["user"]
        failures = 0
        while not self.stop_flag.is_set():
            code, text = gpujob.kaggle(["kernels", "status", f"{user}/{slug}"],
                                       timeout=120)
            if code != 0:
                failures += 1
                if failures >= 12:
                    self._say(f"status check keeps failing: {text[-150:]}",
                              Palette.bad)
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

    def _finish(self, state: str) -> None:
        out = os.path.join(self.job["dir"], "out")
        os.makedirs(out, exist_ok=True)
        self._say("downloading output...")
        gpujob.kaggle(["kernels", "output",
                       f"{self.job['user']}/{self.job['slug']}", "-p", out],
                      timeout=1800)
        self._save_job(state="done" if state == "complete" else "failed")
        self._load_folder(out, label=state)
        self.inbox.put(("busy", False))

    def _load_folder(self, folder: str, label: str = "loaded") -> None:
        rows = gpujob.load_results(folder)
        self.inbox.put(("results", rows))
        if rows:
            self._say(f"{label}: {len(rows)} runs from {folder}", Palette.good)
        else:
            self._say(f"{label}: no results json found in {folder}",
                      Palette.warn)

    def _resume(self) -> None:
        job = theme.preferences().get("gpu_job")
        if not isinstance(job, dict) or not job.get("slug") or not self.ready:
            return
        self.job = job
        state = job.get("state")
        if state in ("pushed", "running"):
            self.start_button.set_enabled(False)
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

    def load_dialog(self) -> None:
        folder = os.environ.get("MNT_GPU_DRY_OUT") or filedialog.askdirectory(
            title="Folder with a results json")
        if folder:
            self._load_folder(folder)

    def forget(self) -> None:
        self.stop_flag.set()
        self.job = None
        theme.save_key("gpu_job", None)
        self._show([])
        self.status.config(text="job forgotten (a running Kaggle kernel keeps "
                                "going)", fg=Palette.muted)
        self.start_button.set_enabled(self.ready)
