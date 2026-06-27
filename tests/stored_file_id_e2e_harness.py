from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer

from hermes_a2a_bridge.operations import ingest_local_file
from hermes_a2a_bridge.server import create_app
from hermes_a2a_bridge.store import Store

from raw_capture_harness import sanitize_headers, sanitize_path_qs, sanitize_payload, sanitize_text


@dataclass
class StoredFileIdE2EHarness:
    config: dict[str, Any]
    tmp_path: Path
    store: Store | None = None
    server: TestServer | None = None
    captures: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    async def start(self, *, open_gates: bool = True) -> str:
        self.config["files"]["storage_dir"] = str(self.tmp_path / "controlled-storage")
        if open_gates:
            self.config["parts"]["allow_file_parts"] = True
            self.config["parts"]["allow_file_id_references"] = True
        self.config["parts"]["allow_remote_url_file_references"] = False
        self.config["parts"]["allow_inline_file_bytes"] = False
        self.config["files"]["auto_fetch_remote_urls"] = False
        self.store = Store(self.tmp_path / "stored-file-id-e2e.sqlite3")
        app = create_app(self.config, self.store)
        app.middlewares.append(self._capture_middleware)
        self.server = TestServer(app)
        await self.server.start_server()
        base = str(self.server.make_url("")).rstrip("/")
        self.config["server"]["public_url"] = base
        return base

    async def close(self) -> None:
        if self.server is not None:
            await self.server.close()

    def stage_file(
        self,
        name: str = "report.txt",
        content: bytes = b"stored-file-e2e-secret",
    ) -> dict[str, Any]:
        if self.store is None:
            raise AssertionError("harness must be started before staging files")
        source = self.tmp_path / name
        source.write_bytes(content)
        return ingest_local_file(
            source,
            self.store,
            self.config,
            name=name,
            metadata={"scope": "stored-file-id-e2e"},
        )["file"]

    @web.middleware
    async def _capture_middleware(self, request: web.Request, handler):
        body_text = await request.text()
        try:
            body = sanitize_payload(json.loads(body_text)) if body_text else None
        except json.JSONDecodeError:
            body = sanitize_text(body_text)
        response = await handler(request)
        captured_response: Any = None
        if isinstance(response, web.Response) and response.body:
            try:
                captured_response = sanitize_payload(json.loads(response.body.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError):
                captured_response = sanitize_text(response.body.decode("utf-8", errors="replace"))
        label = request.path.strip("/").replace("/", "_").replace(":", "_") or "root"
        self.captures.setdefault(label, []).append({
            "request": {
                "method": request.method,
                "path": sanitize_path_qs(request.path_qs),
                "headers": sanitize_headers(dict(request.headers)),
                "body": body,
            },
            "response": captured_response,
        })
        return response


def assert_safe_serialized(value: Any, *, tmp_path: Path, secret_content: str = "stored-file-e2e-secret") -> None:
    serialized = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    lowered = serialized.lower()
    forbidden = (
        "bearer ",
        "test-secret-token",
        "storage_path",
        "storagepath",
        str(tmp_path).lower(),
        "c:\\",
        "c:/",
        "/home/",
        "/tmp/",
        "\\\\",
        secret_content.lower(),
    )
    assert not any(marker in lowered for marker in forbidden), serialized
