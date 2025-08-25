"""Utility to add a user with a hashed password."""
from getpass import getpass

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
