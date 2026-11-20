import argparse
import json
import os
import re
import secrets
from hashlib import sha256
from pathlib import Path

from dotenv import dotenv_values, set_key


ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
FIELD = "GATEWAY_API_KEY_HASHES"


def update_keys(action: str, names: list[str]) -> dict[str, str]:
    if len(set(names)) != len(names) or any(
        not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) for name in names
    ):
        raise ValueError("Use distinct names of 1–64 ASCII letters, digits, dashes or underscores")

    lock = ENV_FILE.with_name(".env.keys.lock")
    try:
        lock.touch(mode=0o600, exist_ok=False)
    except FileExistsError as exc:
        raise ValueError("Another key update is in progress (.env.keys.lock)") from exc

    try:
        if ENV_FILE.is_symlink():
            raise ValueError("Use a regular .env file for key storage")
        stored = dotenv_values(ENV_FILE, interpolate=False)
        if action == "init" and FIELD in stored:
            raise ValueError("Keys already initialized. Use add or revoke")
        if action != "init" and FIELD not in stored:
            raise ValueError("Run init before adding or revoking keys")
        try:
            hashes = json.loads(stored.get(FIELD, "{}"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid {FIELD} in .env") from exc
        if not isinstance(hashes, dict):
            raise ValueError(f"Invalid {FIELD} in .env")

        issued = {}
        if action == "revoke":
            if names[0] not in hashes:
                raise ValueError(f"No API key found for {names[0]}")
            del hashes[names[0]]
        else:
            for name in names:
                if name in hashes:
                    raise ValueError(f"A key already exists for {name}. Revoke it before issuing another")
                token = "gw_" + secrets.token_urlsafe(32)
                issued[name] = token
                hashes[name] = sha256(token.encode("ascii")).hexdigest()

        if os.name == "posix" and ENV_FILE.exists():
            ENV_FILE.chmod(0o600)
        set_key(ENV_FILE, FIELD, json.dumps(hashes, sort_keys=True))
        return issued
    finally:
        lock.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage per-user vLLM gateway API keys")
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("init").add_argument("names", nargs="+")
    commands.add_parser("add").add_argument("names", nargs=1)
    commands.add_parser("revoke").add_argument("names", nargs=1)
    args = parser.parse_args()

    try:
        issued = update_keys(args.action, args.names)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"{exc}\n")
    for name, token in issued.items():
        print(f"{name}: {token}")
    if args.action == "revoke":
        print(f"Revoked key for {args.names[0]}")
    print("Restart the gateway to load the updated keys.")


if __name__ == "__main__":
    main()
