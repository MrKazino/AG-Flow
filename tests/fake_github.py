"""A fake GitHub on 127.0.0.1 for the updater's tests: releases, assets, ETags, redirects, rate limits, slow answers.

Used by ``test_release_feed.py`` and ``test_update.py``. No test touches the network."""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

REPO = "o/r"


class FakeGitHub:
    def __init__(self) -> None:
        self.releases: list[dict[str, Any]] = []
        self.files: dict[str, bytes] = {}
        self.etag = 'W/"v1"'
        self.mode = "ok"            # ok | rate_limited | not_found | server_error | bad_json | not_a_list | huge | hang
        self.redirect: dict[str, str] = {}   # path -> absolute URL to redirect to
        self.seen: list[dict[str, Any]] = []  # {"path", "headers"} per request
        self.release = threading.Event()      # set at teardown so "hang" handlers end
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # silence
                pass

            def _send(self, code: int, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                fake.seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}})
                path = self.path.split("?", 1)[0]
                if fake.mode == "hang":
                    fake.release.wait(30)
                    return
                if path in fake.redirect:
                    self._send(302, b"", {"Location": fake.redirect[path]})
                    return
                if path == f"/repos/{REPO}/releases":
                    if fake.mode == "rate_limited":
                        self._send(403, b"{}", {"X-RateLimit-Remaining": "0"})
                    elif fake.mode == "not_found":
                        self._send(404, b"{}")
                    elif fake.mode == "server_error":
                        self._send(500, b"{}")
                    elif fake.mode == "bad_json":
                        self._send(200, b"<html>not json</html>")
                    elif fake.mode == "not_a_list":
                        self._send(200, b'{"message": "hi"}')
                    elif fake.mode == "huge":
                        self._send(200, b"[" + b" " * (3 * 1024 * 1024) + b"]")
                    elif self.headers.get("If-None-Match") == fake.etag:
                        self._send(304, b"", {"ETag": fake.etag})
                    else:
                        body = json.dumps(fake.releases).encode()
                        self._send(200, body, {"ETag": fake.etag, "Content-Type": "application/json"})
                elif path.startswith("/dl/") and path in fake.files:
                    self._send(200, fake.files[path], {"Content-Type": "application/octet-stream"})
                else:
                    self._send(404, b"{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self) -> FakeGitHub:
        self.thread.start()
        return self

    def stop(self) -> None:
        self.release.set()
        self.server.shutdown()
        self.server.server_close()

    def add_release(self, version: str, *, wheel: bytes | None = b"wheel-bytes", prerelease: bool = False,
                    draft: bool = False, sums: bool = True, tamper_sums: bool = False,
                    meta: dict[str, Any] | None = None, notes: str = "release notes",
                    digest: bool = False) -> dict[str, Any]:
        tag = f"v{version}"
        assets: list[dict[str, Any]] = []

        def put(name: str, data: bytes) -> dict[str, Any]:
            path = f"/dl/{tag}/{name}"
            self.files[path] = data
            asset = {"name": name, "size": len(data), "browser_download_url": f"{self.base}{path}"}
            if digest:
                asset["digest"] = "sha256:" + hashlib.sha256(data).hexdigest()
            assets.append(asset)
            return asset

        wheel_name = f"agflow-{version}-py3-none-any.whl"
        if wheel is not None:
            put(wheel_name, wheel)
        if sums and wheel is not None:
            real = hashlib.sha256(wheel).hexdigest()
            shown = hashlib.sha256(b"something else").hexdigest() if tamper_sums else real
            put("SHA256SUMS", f"{shown}  {wheel_name}\n".encode())
        if meta is not None:
            put("release.json", json.dumps(meta).encode())
        release = {"tag_name": tag, "name": f"AG-Flow {version}", "body": notes, "draft": draft,
                   "prerelease": prerelease, "published_at": "2026-10-16T12:00:00Z", "assets": assets}
        self.releases.insert(0, release)
        self.etag = f'W/"v{len(self.releases)}"'
        return release
