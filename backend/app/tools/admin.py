"""Manage local admin logins (not Plex accounts) from the command line.

    docker compose exec -it telemetry python -m app.tools.admin create glenn
    docker compose exec -it telemetry python -m app.tools.admin password glenn
    docker compose exec telemetry python -m app.tools.admin list
"""
from __future__ import annotations

import getpass
import sqlite3
import sys

from ..auth import hash_password, password_problem
from ..config import DB_PATH
from ..secrets import SecretBox
from ..store import Directory, Store


def ask_password() -> str:
    while True:
        pw = getpass.getpass("New password: ")
        problem = password_problem(pw)
        if problem:
            print(problem)
            continue
        if pw != getpass.getpass("Repeat password: "):
            print("Passwords do not match.")
            continue
        return pw


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("create", "password", "list", "delete"):
        print(__doc__)
        sys.exit(1)
    d = Directory(Store(DB_PATH, SecretBox()))
    cmd = sys.argv[1]
    if cmd == "list":
        for a in d.list_admins():
            print(a["username"])
        return
    if len(sys.argv) < 3:
        print("Give a username.")
        sys.exit(1)
    user = sys.argv[2]
    if cmd == "create":
        try:
            d.add_admin(user, hash_password(ask_password()))
        except sqlite3.IntegrityError:
            print(f"{user} already exists.")
            sys.exit(1)
        print(f"Created admin login {user}.")
    elif cmd == "password":
        a = d.get_admin(user)
        if not a:
            print(f"No admin login named {user}.")
            sys.exit(1)
        d.set_admin_password(a["id"], hash_password(ask_password()))
        print(f"Password changed for {user}. Their sessions were signed out.")
    elif cmd == "delete":
        a = d.get_admin(user)
        if not a:
            print(f"No admin login named {user}.")
            sys.exit(1)
        d.delete_admin(a["id"])
        print(f"Deleted {user}.")


if __name__ == "__main__":
    main()
