from hashlib import sha256
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import current_key_owners


def require_api_key(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
    ],
) -> None:
    if credentials is not None:
        digest = sha256(credentials.credentials.encode("utf-8")).hexdigest()
        try:
            owner = current_key_owners().get(digest)
        except (OSError, RuntimeError, UnicodeError) as exc:
            raise HTTPException(status_code=503, detail="Gateway key configuration unavailable") from exc
        if owner is not None:
            request.state.key_owner = owner
            return
    raise HTTPException(
        status_code=401,
        detail="Invalid or missing API key",
        headers={"WWW-Authenticate": "Bearer"},
    )
