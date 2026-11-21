import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import httpx
from dotenv import dotenv_values


ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def current_key_owners() -> dict[str, str]:
    try:
        stat = ENV_FILE.stat()
    except FileNotFoundError:
        return {}
    # The key manager atomically replaces .env, so include the file identity in the cache key.
    version = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    return _read_key_owners(ENV_FILE, version)


@lru_cache(maxsize=1)
def _read_key_owners(path: Path, _version: tuple[int, ...]) -> dict[str, str]:
    config = dotenv_values(path, interpolate=False)
    try:
        hashes = json.loads(config.get("GATEWAY_API_KEY_HASHES", "{}"))
    except (TypeError, ValueError) as exc:
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
    return {digest: owner for owner, digest in hashes.items()}


@dataclass(frozen=True)
class Settings:
    upstream_url: httpx.URL
    upstream_api_key: str

    @classmethod
    def from_env(cls) -> "Settings":
        config = dotenv_values(ENV_FILE, interpolate=False)
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

        return cls(upstream_url, (config.get("VLLM_API_KEY") or "").strip())
