#!/usr/bin/env python3
"""Authenticate privileged atlasctl operations against the local backend."""

import argparse
import getpass
import json
import os
from pathlib import Path
import pwd
import secrets
import stat
import urllib.error
import urllib.request


API = "http://127.0.0.1:8081/api/auth"


def caller_identity():
    uid = int(os.environ.get("SUDO_UID", os.getuid()))
    account = pwd.getpwuid(uid)
    return uid, account.pw_gid


def token_file():
    uid, _gid = caller_identity()
    return Path(f"/run/user/{uid}/atlas/session.json")


def request(path, *, method="GET", body=None, token=None):
    headers = {"Accept": "application/json", "X-Atlas-CLI": "1"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(API + path, data=data, headers=headers, method=method),
            timeout=10,
        ) as response:
            content = response.read()
    except urllib.error.HTTPError as exc:
        try:
            error = json.loads(exc.read()).get("error", {}).get("message")
        except (ValueError, AttributeError):
            error = None
        raise RuntimeError(error or f"Atlas authentication failed (HTTP {exc.code})") from None
    except (OSError, urllib.error.URLError):
        raise RuntimeError(
            "Atlas backend is unavailable; use status/deploy recovery commands if needed"
        ) from None
    return json.loads(content) if content else {}


def read_token():
    path = token_file()
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            return None
        if stat.S_IMODE(info.st_mode) != 0o600:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    token = value.get("access_token") if isinstance(value, dict) else None
    return token if isinstance(token, str) and len(token) >= 32 else None


def write_token(data):
    path = token_file()
    uid, gid = caller_identity()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    if os.geteuid() == 0:
        os.chown(path.parent, uid, gid)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "access_token": data["access_token"],
                    "username": data["username"],
                    "role": data["role"],
                },
                handle,
            )
            handle.write("\n")
        if os.geteuid() == 0:
            os.chown(temporary, uid, gid)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def current():
    token = read_token()
    if not token:
        return None
    try:
        return request("/me", token=token)
    except RuntimeError:
        try:
            token_file().unlink()
        except FileNotFoundError:
            pass
        return None


def login():
    username = input("Atlas administrator: ").strip()
    password = getpass.getpass("Atlas password: ")
    data = request(
        "/login",
        method="POST",
        body={"username": username, "password": password},
    )
    if not isinstance(data.get("access_token"), str):
        raise RuntimeError("Atlas did not return a CLI session token")
    write_token(data)
    print(f"[OK] Authenticated as {data['username']} ({data['role']})")
    return data


def logout():
    token = read_token()
    if token:
        try:
            request("/logout", method="POST", token=token)
        except RuntimeError:
            pass
    try:
        token_file().unlink()
    except FileNotFoundError:
        pass
    print("[OK] Atlas administrator session removed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("login", "logout", "whoami", "require"))
    args = parser.parse_args()
    if args.action == "logout":
        logout()
        return
    principal = current()
    if args.action == "login" or principal is None:
        principal = login()
    if args.action == "whoami":
        print(f"{principal['username']} ({principal['role']})")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, KeyboardInterrupt) as exc:
        message = "Interrupted" if isinstance(exc, KeyboardInterrupt) else str(exc)
        raise SystemExit(f"[ERROR] {message}")
