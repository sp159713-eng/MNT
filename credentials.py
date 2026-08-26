"""Where API keys live, and the rules about what counts as configured.

WHERE THEY ARE STORED

    %LOCALAPPDATA%\\MNT\\credentials.json      (Windows)
    ~/.mnt/credentials.json                    (anywhere else)

Outside the project directory, deliberately. A credentials file inside the repo
is one `git add .` away from being published, and this project sits in a folder
with several others.

PLAINTEXT, AND SAYING SO

The file is plain JSON. It is not encrypted, and calling it encrypted because it
is base64 or XOR'd would be worse than plaintext, because it would invite the
trust that the encoding does not earn. On Windows the file is written with an
ACL granting the current user only, which is the real protection available
without asking you to manage a passphrase. Anyone with your login can read it.

ENVIRONMENT WINS

`effective()` reads the environment first, then the file. This is what lets a
scheduled run or a CI job supply credentials without a file existing at all, and
it means an exported variable always overrides a stale saved value rather than
silently losing to it.

WHAT "USABLE" MEANS AND WHY IT IS DECIDED HERE

Each provider declares its own rule, because the shapes genuinely differ: Groww
accepts a standalone token OR a key plus one second factor, while Angel One has
no long-lived token at all and needs all four fields for every login. Two copies
of that rule - one here, one in the UI - is how a dashboard ends up enabling
ordering for an account it cannot actually open. Everything asks this file.

Run with:  py -3.13 credentials.py                  what is configured
           py -3.13 credentials.py --set groww access_token
           py -3.13 credentials.py --forget groww
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

# (field, label, environment variable, help text)
GROWW_FIELDS = (
    ("access_token", "Access token", "MNT_GROWW_ACCESS_TOKEN",
     "Issued directly by Groww. On its own this is enough."),
    ("api_key", "API key", "MNT_GROWW_API_KEY",
     "Needs one of the two secrets below."),
    ("api_secret", "API secret", "MNT_GROWW_API_SECRET",
     "For an approval-type key."),
    ("totp_secret", "TOTP secret", "MNT_GROWW_TOTP_SECRET",
     "For a TOTP-type key. The secret behind the QR, not the 6-digit code."),
)

ANGEL_FIELDS = (
    ("api_key", "API key", "MNT_ANGEL_API_KEY",
     "The trading API key from smartapi.angelone.in."),
    ("client_code", "Client code", "MNT_ANGEL_CLIENT_CODE",
     "Your Angel One login ID, e.g. A123456."),
    ("mpin", "MPIN", "MNT_ANGEL_MPIN",
     "The login PIN, not the old web password."),
    ("totp_secret", "TOTP secret", "MNT_ANGEL_TOTP_SECRET",
     "The secret behind the authenticator QR, not the 6-digit code."),
)

TABPFN_FIELDS = (
    ("token", "API key", "TABPFN_TOKEN",
     "From ux.priorlabs.ai/account after accepting the licence. "
     "Also cached at ~/.cache/tabpfn/auth_token by an interactive login."),
)


def _groww_usable(values: dict) -> bool:
    """A token alone, or a key with one second factor.

    A key by itself cannot authenticate and must not count as configured, or
    the UI would offer ordering for an account it cannot open.
    """
    if values.get("access_token"):
        return True
    return bool(values.get("api_key")
                and (values.get("api_secret") or values.get("totp_secret")))


def _groww_explain(values: dict) -> str:
    if values.get("access_token"):
        return "Access token set - ready."
    if _groww_usable(values):
        return "API key with a second factor - ready."
    if values.get("api_key"):
        return ("An API key alone cannot authenticate. Add the API secret "
                "(approval-type key) or the TOTP secret (TOTP-type key).")
    return "No credentials. Ordering stays disabled."


def _angel_usable(values: dict) -> bool:
    """All four, because Angel One's login has no other shape.

    There is no long-lived token to paste: every session is a fresh login with a
    freshly generated TOTP, so a missing field is not a weaker login, it is no
    login at all.
    """
    return all(values.get(field) for field, _, _, _ in ANGEL_FIELDS)


def _angel_explain(values: dict) -> str:
    if _angel_usable(values):
        return "API key, client code, MPIN and TOTP secret set - ready."
    missing = [label for field, label, _, _ in ANGEL_FIELDS
               if not values.get(field)]
    return ("Angel One needs all four; missing " + ", ".join(missing).lower()
            + ". The login is TOTP-only, so the secret behind the QR is "
              "required, not the 6-digit code it shows.")


def _tabpfn_usable(values: dict) -> bool:
    if values.get("token"):
        return True
    # An interactive login caches a token; that counts as configured.
    for path in (os.path.join(os.path.expanduser("~"), ".cache", "tabpfn",
                              "auth_token"),
                 os.path.join(os.path.expanduser("~"), ".tabpfn", "token")):
        try:
            if os.path.exists(path) and open(path).read().strip():
                return True
        except OSError:
            continue
    return False


def _tabpfn_explain(values: dict) -> str:
    if _tabpfn_usable(values):
        return "Licence token present - TabPFN can load weights."
    return ("No token. Register at ux.priorlabs.ai/account, accept the "
            "licence, then paste the key here.")


PROVIDERS = {
    "groww": {"title": "Groww", "fields": GROWW_FIELDS,
              "usable": _groww_usable, "explain": _groww_explain,
              "kind": "broker"},
    "angelone": {"title": "Angel One", "fields": ANGEL_FIELDS,
                 "usable": _angel_usable, "explain": _angel_explain,
                 "kind": "broker"},
    "tabpfn": {"title": "TabPFN", "fields": TABPFN_FIELDS,
               "usable": _tabpfn_usable, "explain": _tabpfn_explain,
               "kind": "model"},
}


def provider(name: str) -> dict:
    if name not in PROVIDERS:
        raise ValueError(f"unknown provider {name!r}; have {sorted(PROVIDERS)}")
    return PROVIDERS[name]


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------

def store_dir() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "MNT")
    return os.path.join(os.path.expanduser("~"), ".mnt")


def store_path() -> str:
    return os.path.join(store_dir(), "credentials.json")


def load_all() -> dict:
    path = store_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            stored = json.load(handle)
    except (json.JSONDecodeError, OSError):
        # A corrupt store must not take the application down; it means "no
        # saved credentials", which is a state the rest of the code handles.
        return {}
    return stored if isinstance(stored, dict) else {}


def load(name: str) -> dict:
    values = load_all().get(name, {})
    return values if isinstance(values, dict) else {}


def _restrict(path: str) -> None:
    """Grant the current user only. Best effort - never fatal."""
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return
    try:
        user = os.environ.get("USERNAME", "")
        if user:
            subprocess.run(["icacls", path, "/inheritance:r", "/grant:r",
                            f"{user}:F"], check=False, capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:                                           # noqa: BLE001
        pass


def save(name: str, values: dict) -> str:
    """Merge these fields into the store. Blank values remove the field."""
    provider(name)
    everything = load_all()
    current = everything.get(name, {})
    if not isinstance(current, dict):
        current = {}
    for field, value in values.items():
        value = (value or "").strip()
        if value:
            current[field] = value
        else:
            current.pop(field, None)
    if current:
        everything[name] = current
    else:
        everything.pop(name, None)

    os.makedirs(store_dir(), exist_ok=True)
    path = store_path()
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(everything, handle, indent=2)
    _restrict(path)
    return path


def forget(name: str) -> bool:
    everything = load_all()
    if name not in everything:
        return False
    everything.pop(name)
    with open(store_path(), "w", encoding="utf-8") as handle:
        json.dump(everything, handle, indent=2)
    return True


def effective(name: str) -> dict:
    """Environment first, then the saved file. Environment always wins."""
    saved = load(name)
    values = {}
    for field, _, variable, _ in provider(name)["fields"]:
        values[field] = os.environ.get(variable) or saved.get(field, "")
    return values


def usable(name: str) -> bool:
    return bool(provider(name)["usable"](effective(name)))


def explain(name: str) -> str:
    return provider(name)["explain"](effective(name))


def source(name: str, field: str) -> str:
    """Where a value came from, for the UI to show without showing the value."""
    for key, _, variable, _ in provider(name)["fields"]:
        if key == field:
            if os.environ.get(variable):
                return "environment"
            break
    return "saved" if load(name).get(field) else "unset"


def masked(value: str) -> str:
    """Never print a key. Show enough to recognise it, not enough to use it."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * 8}{value[-4:]}"


def apply_to_environment(name: str) -> None:
    """Export saved values so libraries reading os.environ can see them.

    TabPFN reads TABPFN_TOKEN from the environment; this is how a value typed
    into the window reaches it without the user restarting anything.
    """
    saved = load(name)
    for field, _, variable, _ in provider(name)["fields"]:
        if saved.get(field) and not os.environ.get(variable):
            os.environ[variable] = saved[field]


def apply_all() -> None:
    for name in PROVIDERS:
        apply_to_environment(name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", nargs=2, metavar=("PROVIDER", "FIELD"))
    parser.add_argument("--forget", metavar="PROVIDER")
    args = parser.parse_args()

    if args.forget:
        print("removed" if forget(args.forget) else "nothing stored for that")
        return

    if args.set:
        name, field = args.set
        provider(name)
        # Read from stdin rather than an argument, so the key never lands in
        # shell history or a process list.
        print(f"paste the value for {name}.{field} (it will not be echoed "
              f"back):", file=sys.stderr)
        value = sys.stdin.readline().strip()
        if not value:
            raise SystemExit("nothing entered")
        path = save(name, {field: value})
        print(f"saved to {path}")
        return

    print(f"\nstore: {store_path()}"
          f"{'' if os.path.exists(store_path()) else '  (does not exist yet)'}\n")
    for name, spec in PROVIDERS.items():
        values = effective(name)
        state = "ready" if spec["usable"](values) else "not configured"
        print(f"{spec['title']} ({name})  -  {state}")
        for field, label, variable, help_text in spec["fields"]:
            where = source(name, field)
            shown = masked(values.get(field, ""))
            print(f"   {label:<16}{shown or '-':<22}{where:<12}{variable}")
        print(f"   {spec['explain'](values)}\n")


if __name__ == "__main__":
    main()
