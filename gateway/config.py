import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import dotenv_values


ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


@dataclass(frozen=True)
class Settings:
    key_owners: dict[str, str]
    upstream_url: httpx.URL
    upstream_api_key: str

    @classmethod
    def from_env(cls) -> "Settings":
        config = dotenv_values(ENV_FILE, interpolate=False)
        stored_keys = config.get("GATEWAY_API_KEY_HASHES")
        if stored_keys is None:
            raise RuntimeError("Initialize .env with python scripts/manage_keys.py init NAME [NAME ...]")
        try:
            hashes = json.loads(stored_keys)
        except ValueError as exc:
            raise RuntimeError("Invalid GATEWAY_API_KEY_HASHES in .env") from exc
        if not isinstance(hashes, dict) or any(
            not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", owner)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
            for owner, digest in hashes.items()
        ):
            raise RuntimeError("Invalid GATEWAY_API_KEY_HASHES in .env")
        if len(set(hashes.values())) != len(hashes):
            raise RuntimeError("Invalid GATEWAY_API_KEY_HASHES in .env")

        config.update(os.environ)
        upstream_url = httpx.URL(config.get("VLLM_BASE_URL") or "http://127.0.0.1:8000")
        if (
            upstream_url.scheme not in {"http", "https"}
            or not upstream_url.host
            or upstream_url.userinfo
            or upstream_url.path != "/"
            or upstream_url.query
            or upstream_url.fragment
        ):
            raise RuntimeError("Set VLLM_BASE_URL to the vLLM origin, without /v1 or credentials")

        key_owners = {digest: owner for owner, digest in hashes.items()}
        return cls(key_owners, upstream_url, (config.get("VLLM_API_KEY") or "").strip())
