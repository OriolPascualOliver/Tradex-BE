"""Utility to add a user with a hashed password."""
from getpass import getpass
import sys
from pathlib import Path

# Add the project root to the Python path so ``app`` can be imported when the
# script is executed directly (e.g. ``python scripts/add_user.py``).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# Work around incompatibility between ``passlib`` and ``bcrypt`` 4.x where the
# latter removed ``__about__.__version__``.  ``passlib`` expects this attribute
# and logs a traceback if it's missing.  Adding a minimal shim prevents the
# spurious error without affecting functionality.
try:  # pragma: no cover - defensive patch
    import bcrypt as _bcrypt

    if not hasattr(_bcrypt, "__about__"):
        class _About:
            __version__ = _bcrypt.__version__

        _bcrypt.__about__ = _About()
except Exception:  # pragma: no cover
    pass

from app import auth, database


def main() -> None:
    username = input("Username: ")
    password = getpass("Password: ")
    hashed = auth.get_password_hash(password)
    conn = database.get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, hashed_password) VALUES (?, ?)",
        (username, hashed),
    )
    conn.commit()
    conn.close()
    print(f"User {username} added")


if __name__ == "__main__":
    database.create_tables()
    main()
