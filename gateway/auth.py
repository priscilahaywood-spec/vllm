from hashlib import sha256
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


async def require_api_key(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
    ],
) -> None:
    if credentials is not None:
        digest = sha256(credentials.credentials.encode("utf-8")).hexdigest()
        owner = request.app.state.key_owners.get(digest)
        if owner is not None:
            request.state.key_owner = owner
            return
    raise HTTPException(
        status_code=401,
        detail="Invalid or missing API key",
        headers={"WWW-Authenticate": "Bearer"},
    )
