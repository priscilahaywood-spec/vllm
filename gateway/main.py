import json
import logging
from contextlib import asynccontextmanager
from time import perf_counter

import httpx
from anyio import CancelScope
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from starlette.types import Receive, Scope, Send

from .auth import require_api_key
from .config import Settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    headers = {}
    if settings.upstream_api_key:
        headers["Authorization"] = f"Bearer {settings.upstream_api_key}"

    async with httpx.AsyncClient(
        base_url=settings.upstream_url,
        headers=headers,
        timeout=httpx.Timeout(300.0, connect=5.0, pool=5.0, write=30.0),
        trust_env=False,
    ) as client:
        app.state.key_owners = settings.key_owners
        app.state.upstream = client
        yield


app = FastAPI(title="vLLM Gateway", lifespan=lifespan)
api = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])
logger = logging.getLogger("uvicorn.error.gateway")


@app.get("/health")
@app.get("/healthz")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready(request: Request):
    try:
        upstream = await request.app.state.upstream.get("/health", timeout=3.0)
        upstream.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="vLLM is not ready") from exc
    return {"status": "ready"}


def json_object(content: bytes) -> dict:
    try:
        value = json.loads(content)
    except (ValueError, RecursionError):
        return {}
    return value if isinstance(value, dict) else {}


def log_request(owner, path, status, started, usage=None, interrupted=False):
    usage = usage if isinstance(usage, dict) else {}
    counts = [usage.get(name) for name in ("prompt_tokens", "completion_tokens", "total_tokens")]
    logger.info(
        "user=%s path=%s status=%d duration_ms=%.1f prompt_tokens=%s completion_tokens=%s "
        "total_tokens=%s interrupted=%s",
        owner, path, status, (perf_counter() - started) * 1000,
        *(count if type(count) is int else "-" for count in counts), interrupted,
    )


class UpstreamStream(StreamingResponse):
    def __init__(
        self, upstream: httpx.Response, headers: dict[str, str], owner: str, path: str, started: float
    ):
        self.upstream = upstream
        self.owner = owner
        self.path = path
        self.started = started
        self.usage = None
        self.finished = False
        super().__init__(self.events(), status_code=upstream.status_code, headers=headers)

    async def events(self):
        pending = b""
        async for chunk in self.upstream.aiter_bytes():
            *lines, pending = (pending + chunk).split(b"\n")
            for line in lines:
                if line.startswith(b"data:") and b'"usage"' in line:
                    usage = json_object(line[5:]).get("usage")
                    if isinstance(usage, dict):
                        self.usage = usage
            yield chunk
        self.finished = True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Release the upstream connection even when the downstream client disconnects.
            with CancelScope(shield=True):
                await self.upstream.aclose()
            log_request(
                self.owner, self.path, self.status_code, self.started,
                self.usage, interrupted=not self.finished,
            )


async def forward(request: Request, path: str) -> Response:
    started = perf_counter()
    owner = request.state.key_owner
    client: httpx.AsyncClient = request.app.state.upstream
    headers = {
        name: request.headers[name]
        for name in ("content-type", "x-request-id")
        if name in request.headers
    }
    content = await request.body()
    if request.method == "POST":
        payload = json_object(content)
        if payload.get("stream") is True:
            options = payload.get("stream_options")
            if options is None or (isinstance(options, dict) and "include_usage" not in options):
                payload["stream_options"] = {**(options or {}), "include_usage": True}
                content = json.dumps(payload).encode("utf-8")

    upstream_request = client.build_request(
        request.method,
        path,
        params=request.query_params.multi_items(),
        content=content,
        headers=headers,
    )

    try:
        upstream = await client.send(upstream_request, stream=True)
        response_headers = {
            name: upstream.headers[name]
            for name in ("content-type", "x-request-id", "retry-after")
            if name in upstream.headers
        }
        content_type = upstream.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type == "text/event-stream":
            response_headers.update({"cache-control": "no-cache", "x-accel-buffering": "no"})
            return UpstreamStream(upstream, response_headers, owner, path, started)

        try:
            content = await upstream.aread()
        finally:
            with CancelScope(shield=True):
                await upstream.aclose()
        usage = json_object(content).get("usage") if request.method == "POST" else None
        log_request(owner, path, upstream.status_code, started, usage)
        return Response(content, status_code=upstream.status_code, headers=response_headers)
    except httpx.TimeoutException as exc:
        log_request(owner, path, 504, started)
        raise HTTPException(status_code=504, detail="vLLM request timed out") from exc
    except httpx.RequestError as exc:
        log_request(owner, path, 502, started)
        raise HTTPException(status_code=502, detail="Could not reach vLLM") from exc


@api.get("/models")
async def models(request: Request):
    return await forward(request, "/v1/models")


@api.post("/chat/completions")
async def chat_completions(request: Request):
    return await forward(request, "/v1/chat/completions")


app.include_router(api)
