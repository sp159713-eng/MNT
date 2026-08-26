"""Is there a newer MNT than the one running.

WHY THIS ASKS GITHUB AND NOTHING ELSE

The check reads the repository's latest RELEASE, not its tags or its default
branch. A tag is a bookmark anyone can push; a release is a deliberate act of
publishing, and the installer is attached to it. Reading tags would announce an
update the moment a commit was marked, which is not the same claim.

WHY A FAILURE IS SILENT

There is exactly one caller and it runs at startup, on the worker thread, into
a button that is hidden by default. A machine that is offline, behind a proxy,
rate-limited by GitHub's 60-requests-an-hour anonymous ceiling, or pointed at a
repository that does not exist yet must open the window normally - so every
failure path returns None and the button simply never appears. An update check
is not important enough to interrupt a trading application, and a dialog saying
"could not check for updates" is worse than saying nothing at all.
"""

from __future__ import annotations

import json
import urllib.request

TIMEOUT = 6
API = "https://api.github.com/repos/{repo}/releases/latest"


def parse_version(text: str) -> tuple:
    parts = []
    for chunk in str(text).strip().lstrip("vV").replace("-", ".").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if digits:
            parts.append(int(digits))
    return tuple(parts) if parts else (0,)


def is_newer(candidate: str, current: str) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    width = max(len(new), len(old))
    new = new + (0,) * (width - len(new))
    old = old + (0,) * (width - len(old))
    return new > old


def token() -> str:
    import os

    from_env = os.environ.get("MNT_GITHUB_TOKEN", "")
    if from_env.strip():
        return from_env.strip()
    try:
        import config

        with open(os.path.join(config.MODEL_DIR, "update.json"),
                  encoding="utf-8") as handle:
            value = json.load(handle).get("token")
    except Exception:
        return ""
    return value.strip() if isinstance(value, str) else ""


def check(current: str, repo: str, timeout: float = TIMEOUT,
          auth: str | None = None) -> dict | None:
    if not repo or not str(repo).strip():
        return None

    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "MNT-update-check"}
    auth = token() if auth is None else auth
    if auth:
        headers["Authorization"] = f"Bearer {auth}"

    request = urllib.request.Request(
        API.format(repo=str(repo).strip().strip("/")), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as handle:
            payload = json.load(handle)
    except Exception:
        return None

    if not isinstance(payload, dict) or payload.get("draft"):
        return None
    tag = payload.get("tag_name") or payload.get("name") or ""
    if not tag or not is_newer(str(tag), current):
        return None

    page = payload.get("html_url") or f"https://github.com/{repo}/releases/latest"
    return {"version": str(tag).strip().lstrip("vV"), "url": page}


def main() -> None:
    import config

    found = check(config.APP_VERSION, config.UPDATE_REPO)
    if not config.UPDATE_REPO:
        print("No update repository configured (config.UPDATE_REPO is empty).")
    elif found:
        print(f"Update available: {found['version']} -> {found['url']}")
    else:
        print(f"Up to date ({config.APP_VERSION}).")


if __name__ == "__main__":
    main()
