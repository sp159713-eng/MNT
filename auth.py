"""The password that gates the window.

WHAT THIS PROTECTS, AND WHAT IT DOES NOT

It stops someone sitting down at an unattended machine from opening MNT and
placing orders. That is the whole of it.

It is not encryption. Broker keys still live in plain JSON next to this file,
readable by anyone with access to the account's files, and this password does
not change that. Storing them encrypted under a key derived from this password
would - at the cost that forgetting the password destroys them for good. That
trade was declined deliberately; forgetting this password costs nothing but the
file, and the note below says how to clear it.

FORGOT IT

Delete auth.json from the store directory. The next launch offers to set a new
one. Nothing else is lost, because nothing else depends on it.

The hash is scrypt with a random 16-byte salt, verified with a constant-time
compare. Parameters are written alongside the digest so they can be raised
later without stranding anyone's existing password.
"""

from __future__ import annotations

import binascii
import hmac
import json
import os
from hashlib import scrypt

import credentials as credentials_module

FILE = "auth.json"
MIN_LENGTH = 8
MAX_NAME = 32
N, R, P, DKLEN = 2 ** 14, 8, 1, 32
SALT_BYTES = 16
MAXMEM = 64 * 1024 * 1024


def path() -> str:
    return os.path.join(credentials_module.store_dir(), FILE)


def _read() -> dict:
    try:
        with open(path(), encoding="utf-8") as handle:
            stored = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return stored if isinstance(stored, dict) else {}


def exists() -> bool:
    """Whether an account has been set up on this machine."""
    record = _read()
    return bool(record.get("hash")) and bool(record.get("salt"))


def account_name() -> str:
    """The name the account was created under, or an empty string."""
    name = _read().get("name", "")
    return name if isinstance(name, str) else ""


def name_problem(name: str) -> str:
    """Why this account name will not do, or an empty string when it is fine."""
    if not isinstance(name, str) or not name.strip():
        return "Enter an account name."
    if len(name.strip()) > MAX_NAME:
        return f"Keep the name under {MAX_NAME} characters."
    return ""


def _derive(password: str, salt: bytes, n: int, r: int, p: int,
            dklen: int) -> bytes:
    return scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                  dklen=dklen, maxmem=MAXMEM)


def problem(password: str, again: str | None = None) -> str:
    """Why this password will not do, or an empty string when it is fine."""
    if not isinstance(password, str) or not password.strip():
        return "Enter a password."
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if again is not None and password != again:
        return "The two entries do not match."
    return ""


def create(password: str, again: str | None = None, name: str = "") -> str:
    """Set up the account. Returns an empty string, or why it was refused."""
    refusal = name_problem(name) if name is not None and name != "" else ""
    if refusal:
        return refusal
    refusal = problem(password, again)
    if refusal:
        return refusal
    salt = os.urandom(SALT_BYTES)
    digest = _derive(password, salt, N, R, P, DKLEN)
    record = {
        "name": (name or "").strip(),
        "algorithm": "scrypt",
        "n": N, "r": R, "p": P, "dklen": DKLEN,
        "salt": binascii.hexlify(salt).decode("ascii"),
        "hash": binascii.hexlify(digest).decode("ascii"),
    }
    os.makedirs(credentials_module.store_dir(), exist_ok=True)
    target = path()
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2)
    credentials_module._restrict(target)
    return ""


def verify(password: str) -> bool:
    """Constant-time check against the stored digest."""
    record = _read()
    if not record.get("hash") or not record.get("salt"):
        return False
    if record.get("algorithm", "scrypt") != "scrypt":
        return False
    try:
        salt = binascii.unhexlify(record["salt"])
        expected = binascii.unhexlify(record["hash"])
        n = int(record.get("n", N))
        r = int(record.get("r", R))
        p = int(record.get("p", P))
        dklen = int(record.get("dklen", DKLEN))
    except (binascii.Error, TypeError, ValueError):
        return False
    if not isinstance(password, str) or not password:
        return False
    try:
        candidate = _derive(password, salt, n, r, p, dklen)
    except ValueError:
        return False
    return hmac.compare_digest(candidate, expected)


def change(old: str, new: str, again: str | None = None) -> str:
    """Replace the password, keeping the account name."""
    if exists() and not verify(old):
        return "The current password is not right."
    return create(new, again, account_name())


def reset() -> bool:
    """Forget the password entirely. The next launch will ask for a new one."""
    try:
        os.remove(path())
        return True
    except OSError:
        return False
