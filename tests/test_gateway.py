import json
import os
import unittest
from contextlib import AsyncExitStack, ExitStack
from functools import partial
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from gateway.main import app
from scripts import manage_keys


USAGE = {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}


class EventStream(httpx.AsyncByteStream):
    closed = False

    async def __aiter__(self):
        yield b'data: {"choices":[]}\n\n'
        yield b'data: {"us'
        yield b'age": ' + json.dumps(USAGE).encode() + b'}\r\n\r\n'
        yield b'data: [DONE]\n\n'

    async def aclose(self):
        self.closed = True


class GatewayTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stream = EventStream()
        self.upstream_requests = []
        self.failure = None
        self.auth = {"Authorization": "Bearer test-gateway"}
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        contexts.enter_context(patch.dict(os.environ, {
            "VLLM_API_KEY": "test-upstream",
            "VLLM_BASE_URL": "http://127.0.0.1:8000",
        }))
        directory = contexts.enter_context(TemporaryDirectory())
        env = Path(directory) / ".env"
        hashes = {"alice": sha256(b"test-gateway").hexdigest(), "bob": sha256(b"test-bob").hexdigest()}
        env.write_text(f"GATEWAY_API_KEY_HASHES='{json.dumps(hashes)}'\n", encoding="utf-8")
        contexts.enter_context(patch("gateway.config.ENV_FILE", env))

    async def asyncSetUp(self):
        contexts = AsyncExitStack()
        self.addAsyncCleanup(contexts.aclose)
        mock_client = partial(httpx.AsyncClient, transport=httpx.MockTransport(self.upstream))
        with patch("gateway.main.httpx.AsyncClient", mock_client):
            await contexts.enter_async_context(app.router.lifespan_context(app))
        self.client = await contexts.enter_async_context(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway.local"
        ))

    def upstream(self, request):
        self.upstream_requests.append(request)
        if self.failure:
            raise self.failure("mock failure")
        if request.url.path == "/health":
            return httpx.Response(200)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "Qwen3-8B"}]})

        self.assertEqual((request.method, request.url.path), ("POST", "/v1/chat/completions"))
        body = json.loads(request.content)
        if body["model"] != "Qwen3-8B":
            return httpx.Response(404, json={"error": {"message": "Unknown model"}})
        if body.get("stream"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream; charset=utf-8"},
                stream=self.stream,
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "你好"}}], "usage": USAGE})

    async def test_authentication(self):
        for auth_header in (None, "Bearer wrong", "Basic test-gateway"):
            with self.subTest(auth_header=auth_header):
                headers = {"Authorization": auth_header} if auth_header else {}
                response = await self.client.get("/v1/models", headers=headers)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["www-authenticate"], "Bearer")
        response = await self.client.post("/v1/chat/completions", json={})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.upstream_requests, [])

    async def test_models_and_chat(self):
        models = await self.client.get("/v1/models", headers=self.auth)
        self.assertEqual(models.json()["data"][0]["id"], "Qwen3-8B")
        self.assertEqual(self.upstream_requests[-1].headers["authorization"], "Bearer test-upstream")
        with self.assertLogs("uvicorn.error.gateway", "INFO") as logs:
            second = await self.client.get("/v1/models", headers={"Authorization": "Bearer test-bob"})
        self.assertEqual(second.status_code, 200)
        self.assertIn("user=bob", logs.output[0])
        self.assertNotIn("test-bob", logs.output[0])

        payload = {
            "model": "Qwen3-8B",
            "messages": [{"role": "user", "content": "\u4f60\u597d"}],
            "chat_template_kwargs": {"enable_thinking": False},
        }
        with self.assertLogs("uvicorn.error.gateway", "INFO") as logs:
            response = await self.client.post(
                "/v1/chat/completions?tag=1&tag=2", headers=self.auth, json=payload
            )
        self.assertIn("prompt_tokens=7 completion_tokens=3 total_tokens=10", logs.output[0])
        self.assertEqual(response.json()["choices"][0]["message"]["content"], "你好")
        forwarded = self.upstream_requests[-1]
        self.assertEqual(json.loads(forwarded.content), payload)
        self.assertEqual(forwarded.url.params.get_list("tag"), ["1", "2"])

        response = await self.client.post(
            "/v1/chat/completions", headers=self.auth, json={"model": "other"}
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": {"message": "Unknown model"}})

    async def test_stream_and_close(self):
        with self.assertLogs("uvicorn.error.gateway", "INFO") as logs:
            async with self.client.stream(
                "POST", "/v1/chat/completions", headers=self.auth,
                json={"model": "Qwen3-8B", "stream": True},
            ) as response:
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
                lines = [line async for line in response.aiter_lines() if line]
                self.assertEqual(lines[0], 'data: {"choices":[]}')
                self.assertEqual(json.loads(lines[1][5:])["usage"], USAGE)
                self.assertEqual(lines[2], "data: [DONE]")
        self.assertIn("prompt_tokens=7 completion_tokens=3 total_tokens=10", logs.output[0])
        payload = json.loads(self.upstream_requests[-1].content)
        self.assertEqual(payload["stream_options"], {"include_usage": True})
        self.assertTrue(self.stream.closed)

    async def test_health_and_upstream_failures(self):
        self.assertEqual((await self.client.get("/ready")).status_code, 200)
        self.assertEqual((await self.client.get("/healthz")).status_code, 200)
        self.failure = httpx.ConnectError
        self.assertEqual((await self.client.get("/health")).status_code, 200)
        self.assertEqual((await self.client.get("/ready")).status_code, 503)
        self.assertEqual((await self.client.get("/v1/models", headers=self.auth)).status_code, 502)
        self.failure = httpx.ReadTimeout
        self.assertEqual((await self.client.get("/v1/models", headers=self.auth)).status_code, 504)


class KeyLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_keys_change_during_same_lifespan(self):
        with TemporaryDirectory() as directory:
            env = Path(directory) / ".env"
            with patch("gateway.config.ENV_FILE", env), patch.object(manage_keys, "ENV_FILE", env):
                async with AsyncExitStack() as contexts:
                    mock_upstream = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
                    mock_client = partial(httpx.AsyncClient, transport=mock_upstream)
                    with patch("gateway.main.httpx.AsyncClient", mock_client):
                        await contexts.enter_async_context(app.router.lifespan_context(app))
                    client = await contexts.enter_async_context(httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app), base_url="http://gateway.local"
                    ))

                    async def status(key):
                        response = await client.get(
                            "/v1/models", headers={"Authorization": f"Bearer {key}"}
                        )
                        return response.status_code

                    self.assertEqual(await status("unknown"), 401)
                    env.write_text("# preserve settings\nVLLM_BASE_URL=http://127.0.0.1:8000\n", encoding="utf-8")
                    keys = manage_keys.update_keys("init", ["alice", "bob"])
                    self.assertNotEqual(keys["alice"], keys["bob"])
                    self.assertEqual(await status(keys["alice"]), 200)
                    self.assertEqual(await status(keys["bob"]), 200)
                    with self.assertRaisesRegex(ValueError, "already initialized"):
                        manage_keys.update_keys("init", ["charlie"])

                    keys.update(manage_keys.update_keys("add", ["charlie", "dana"]))
                    self.assertEqual(await status(keys["charlie"]), 200)
                    self.assertEqual(await status(keys["dana"]), 200)
                    manage_keys.update_keys("revoke", ["bob"])
                    self.assertEqual(await status(keys["bob"]), 401)
                    self.assertEqual(await status(keys["alice"]), 200)

                    content = env.read_text(encoding="utf-8")
                    self.assertIn("# preserve settings", content)
                    self.assertNotIn(sha256(keys["bob"].encode()).hexdigest(), content)
                    self.assertTrue(all(key not in content for key in keys.values()))
                    env.write_text("GATEWAY_API_KEY_HASHES=broken\n", encoding="utf-8")
                    self.assertEqual(await status(keys["alice"]), 503)
                    env.unlink()
                    self.assertEqual(await status(keys["alice"]), 401)
