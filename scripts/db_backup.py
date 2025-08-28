#!/usr/bin/env python3
"""Utility for backing up and restoring the Tradex SQLite database."""

import argparse
import os
import shutil
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(
    os.getenv("TRADEX_DB_PATH", str(Path.home() / ".tradex" / "users.db"))
)


def backup_db(destination: str) -> None:
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DEFAULT_DB_PATH) as src, sqlite3.connect(dest) as dst:
        src.backup(dst)
    os.chmod(dest, 0o600)


def restore_db(backup_file: str, destination: str | None = None) -> None:
    dest = Path(destination) if destination else DEFAULT_DB_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(backup_file), dest)
    os.chmod(dest, 0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backup or restore the database")
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("backup", help="Backup the database")
    b.add_argument("destination", help="Destination backup file")

    r = sub.add_parser("restore", help="Restore the database from a backup")
    r.add_argument("backup_file", help="Path to the backup file")
    r.add_argument("destination", nargs="?", help="Optional destination for restore")

    args = parser.parse_args()

    if args.command == "backup":
        backup_db(args.destination)
    elif args.command == "restore":
        restore_db(args.backup_file, args.destination)


if __name__ == "__main__":
    main()
