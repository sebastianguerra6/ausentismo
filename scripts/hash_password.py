"""Generate a bcrypt PasswordHash for dbo.App_Users (app password, not Windows).

Usage:
    python scripts/hash_password.py
    python scripts/hash_password.py mySecretPassword

Paste the printed hash into:
    INSERT INTO dbo.App_Users (Username, PasswordHash, DisplayName, CanWrite, IsActive)
    VALUES ('scotiaId', '<hash>', 'Display Name', 1, 1);
"""

from __future__ import annotations

import getpass
import sys

import bcrypt


def main() -> None:
    if len(sys.argv) > 1:
        password = sys.argv[1]
    else:
        password = getpass.getpass("Password to hash: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords do not match.", file=sys.stderr)
            sys.exit(1)

    if not password:
        print("Password cannot be empty.", file=sys.stderr)
        sys.exit(1)

    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    print(hashed)


if __name__ == "__main__":
    main()
