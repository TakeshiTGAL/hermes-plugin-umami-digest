"""Fake HTTP: 401, 404, 5xx, timeout, huge body, redirect, success."""
from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

import client
from gates import ConfigError
from service import Deps, umami_digest

SITE = "11111111-1111-1111-1111-111111111111"
KEY = "umami_live_http_key_7c2b"


class State:
    def __init__(self) -> None:
        self.mode = "ok"
        self.seen: list[tuple[str, str]] = []
        self.sleep = 0.0
        self.body = b""


def make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:  # noqa: ARG002
            return

        def do_GET(self) -> None:  # noqa: N802
            auth = self.headers.get("Authorization", "")
            state.seen.append((self.path, auth))
            if state.sleep:
                time.sleep(state.sleep)
            if state.mode == "redirect":
                self.send_response(302)
                self.send_header("Location", "https://example.net/steal")
                self.end_headers()
                return
            if state.mode == "huge":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{" + b"x" * (client.BODY_CAP + 10) + b"}")
                return
            mapping = {
                "ok": (200, {"pageviews": 3, "visitors": 1, "comparison": {"pageviews": 1, "visitors": 1}}),
                "metrics": (200, [{"name": "/home", "pageviews": "3"}]),
                "401": (401, {"error": {"message": "Unauthorized.", "status": 401}}),
                "404": (404, {"error": {"message": "Not found.", "status": 404}}),
                "500": (500, {"error": {"message": "Server error.", "status": 500}}),
                "empty": (200, None),
                "html": (200, "nope"),
            }
            if "/metrics/expanded" in self.path and state.mode == "ok":
                status, payload = 200, [{"name": "/home", "pageviews": "3"}]
            else:
                status, payload = mapping.get(state.mode, mapping["ok"])
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if payload is None:
                self.end_headers()
                return
            if payload == "nope":
                raw = b"<html>no</html>"
            else:
                raw = json.dumps(payload).encode("utf-8")
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:  # noqa: N802
            self.send_response(405)
            self.end_headers()

    return Handler


@pytest.fixture()
def httpd():
    from tests.conftest import network_blocked

    if network_blocked():
        pytest.skip("socket connect is blocked in this environment")
    state = State()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    origin = f"http://{host}:{port}"
    yield origin, state
    server.shutdown()
    server.server_close()
    thread.join(2)


def _deps(origin: str, state: State | None = None) -> Deps:
    deps = Deps()
    bag = {
        "UMAMI_URL": origin,
        "UMAMI_API_KEY": KEY,
        "UMAMI_WEBSITE_ID": SITE,
        "UMAMI_TIMEZONE": "UTC",
    }
    deps.secrets = lambda name: bag.get(name)
    return deps


def test_ok_letter(httpd):
    origin, state = httpd
    state.mode = "ok"
    out = umami_digest(_deps(origin), {})
    assert out["ok"] is True
    assert "Pageviews: 3 vs 1" in out["message"]
    assert "`/home` — 3" in out["message"]
    assert any("/metrics/expanded" in path and "type=path" in path for path, _auth in state.seen)
    assert all(auth == f"Bearer {KEY}" for _path, auth in state.seen)
    assert KEY not in out["message"]


def test_401(httpd):
    origin, state = httpd
    state.mode = "401"
    out = umami_digest(_deps(origin), {})
    assert out["ok"] is False
    assert "401" in out["message"]
    assert KEY not in out["message"]
    assert "zero" not in out["message"].lower() or "not a zero" in out["message"].lower()


def test_404(httpd):
    origin, state = httpd
    state.mode = "404"
    out = umami_digest(_deps(origin), {})
    assert out["ok"] is False
    assert "404" in out["message"]
    assert KEY not in out["message"]


def test_500(httpd):
    origin, state = httpd
    state.mode = "500"
    out = umami_digest(_deps(origin), {})
    assert out["ok"] is False
    assert "500" in out["message"]


def test_timeout(httpd, monkeypatch):
    origin, state = httpd
    state.mode = "ok"
    state.sleep = 2.0
    monkeypatch.setattr(client, "TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(client, "TOTAL_SECONDS", 0.3)
    out = umami_digest(_deps(origin), {})
    assert out["ok"] is False
    assert "timed out" in out["message"]
    assert KEY not in out["message"]


def test_huge_body(httpd):
    origin, state = httpd
    state.mode = "huge"
    out = umami_digest(_deps(origin), {})
    assert out["ok"] is False
    assert "too large" in out["message"]
    assert "0 page" not in out["message"].lower()


def test_redirect_not_followed(httpd):
    origin, state = httpd
    state.mode = "redirect"
    out = umami_digest(_deps(origin), {})
    assert out["ok"] is False
    assert "not followed" in out["message"]
    assert "example.net" not in "".join(path for path, _auth in state.seen)


def test_empty_200_is_failure(httpd):
    origin, state = httpd
    state.mode = "empty"
    out = umami_digest(_deps(origin), {})
    assert out["ok"] is False
    assert "empty body" in out["message"]


def test_non_json_200_is_failure(httpd):
    origin, state = httpd
    state.mode = "html"
    out = umami_digest(_deps(origin), {})
    assert out["ok"] is False
    assert "JSON" in out["message"]


def test_https_origin_is_accepted_without_connecting():
    from gates import parse_origin

    assert parse_origin("https://umami.example.net") == "https://umami.example.net"
