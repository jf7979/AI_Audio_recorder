#!/usr/bin/env python3
"""Prompts for a web UI password and writes its hash + a fresh session secret
key into config.yaml. Run this once after copying config.example.yaml."""
import getpass
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from app.config import default_config_path, web_auth_path
from werkzeug.security import generate_password_hash


def main() -> None:
    config_path = default_config_path()
    if not config_path.exists():
        example = config_path.parent / "config.example.yaml"
        print(f"No config file at {config_path}. Copy {example.name} to {config_path.name} first.")
        sys.exit(1)

    password = getpass.getpass("New web UI password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords did not match.")
        sys.exit(1)
    if len(password) < 8:
        print("Use at least 8 characters.")
        sys.exit(1)

    auth_path = web_auth_path(config_path)
    with open(auth_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            {
                "password_hash": generate_password_hash(password),
                "secret_key": secrets.token_hex(32),
            },
            f,
        )

    print(f"Password set. {auth_path} written.")


if __name__ == "__main__":
    main()
