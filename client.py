"""One GET to the caller's Umami origin. Bearer key, no redirect, no second try."""
from __future__ import annotations

import http.client
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

if __package__:
    from .gates import parse_origin, redact
else:
    from gates import parse_origin, redact

TIMEOUT_SECONDS = 20
TOTAL_SECONDS = 30
BODY_CAP = 1_000_000
USER_AGENT = "hermes-plugin-umami-digest/0.1.0"
REDIRECTS = frozenset({301, 302, 303, 307, 308})
STATS_SUFFIX = "/stats"
METRICS_SUFFIX = "/metrics/expanded"

Transport = Callable[[str, str], tuple[int, bytes]]


class ClientError(Exception):
    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def http_error_301(self, req, fp, code, msg, headers):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    http_error_302 = http_error_301
    http_error_303 = http_error_301
    http_error_307 = http_error_301
    http_error_308 = http_error_301

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ARG002
        return None


def _unchanged() -> str:
    return "Nothing was stored."


def stats_path(website_id: str) -> str:
    return f"/api/websites/{website_id}/stats"


def metrics_path(website_id: str) -> str:
    return f"/api/websites/{website_id}/metrics/expanded"


def build_url(origin: str, path: str, pairs: list[tuple[str, str]]) -> str:
    if "/../" in path or path.startswith("//") or "\\" in path:
        raise ClientError("bad_path", "Only website stats and expanded metrics are requested, so nothing was sent.")
    if not path.startswith("/api/websites/") or not (
        path.endswith(STATS_SUFFIX) or path.endswith(METRICS_SUFFIX)
    ):
        raise ClientError("bad_path", "Only website stats and expanded metrics are requested, so nothing was sent.")
    query = urllib.parse.urlencode(pairs, quote_via=urllib.parse.quote)
    base = origin.rstrip("/") + path
    return f"{base}?{query}" if query else base


def _arm_read_timeout(response, seconds: float) -> None:
    current = response
    seen: set[int] = set()
    sock = None
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        fp = getattr(current, "fp", None)
        raw = getattr(fp, "raw", None) if fp is not None else None
        sock = getattr(raw, "_sock", None) if raw is not None else None
        if sock is None and raw is not None:
            sock = getattr(getattr(raw, "raw", None), "_sock", None)
        if sock is not None:
            break
        current = fp
    if sock is not None:
        sock.settimeout(max(0.05, seconds))


def _pinned_opener(box: dict[str, Any]):
    lock = box["lock"]

    class HTTP(http.client.HTTPConnection):
        def connect(self):
            super().connect()
            with lock:
                box["sock"] = self.sock

    class HTTPS(http.client.HTTPSConnection):
        def connect(self):
            super().connect()
            with lock:
                box["sock"] = self.sock

    class HTTPOpen(urllib.request.HTTPHandler):
        def http_open(self, req):  # noqa: ANN001
            return self.do_open(HTTP, req)

    class HTTPSOpen(urllib.request.HTTPSHandler):
        def https_open(self, req):  # noqa: ANN001
            return self.do_open(HTTPS, req)

    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
        HTTPOpen(),
        HTTPSOpen(),
    )


def _close_socket(sock) -> None:
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def _open_bounded(opener, request, deadline: float, box: dict[str, Any]):
    lock = box["lock"]

    def run() -> None:
        try:
            response = opener.open(request, timeout=TIMEOUT_SECONDS)
        except BaseException as exc:
            with lock:
                box["err"] = exc
            return
        with lock:
            if box.get("abandoned"):
                try:
                    response.close()
                except Exception:
                    pass
            else:
                box["resp"] = response

    thread = threading.Thread(target=run, daemon=True, name="umami-digest-get")
    thread.start()
    left = deadline - time.monotonic()
    thread.join(left if left > 0 else 0)
    with lock:
        if "resp" in box:
            return box["resp"]
        if "err" in box:
            raise box["err"]
        box["abandoned"] = True
        sock = box.get("sock")
    _close_socket(sock)
    thread.join(1.0)
    raise ClientError("timeout", "The Umami request timed out. " + _unchanged())


def _read_capped(response, deadline: float) -> bytes:
    parts: list[bytes] = []
    size = 0
    while size <= BODY_CAP:
        left = deadline - time.monotonic()
        if left <= 0:
            raise ClientError("timeout", "The Umami request timed out. " + _unchanged())
        _arm_read_timeout(response, min(float(TIMEOUT_SECONDS), left))
        try:
            chunk = response.read(1)
        except (TimeoutError, socket.timeout) as exc:
            raise ClientError("timeout", "The Umami request timed out. " + _unchanged()) from exc
        if not chunk:
            break
        parts.append(chunk)
        size += len(chunk)
    return b"".join(parts)


def _urllib_transport(url: str, token: str) -> tuple[int, bytes]:
    box: dict[str, Any] = {"lock": threading.Lock()}
    opener = _pinned_opener(box)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="GET",
    )
    if request.get_method() != "GET":
        raise ClientError("method", "Only GET is sent. " + _unchanged())
    deadline = time.monotonic() + TOTAL_SECONDS
    try:
        response = _open_bounded(opener, request, deadline, box)
    except urllib.error.HTTPError as exc:
        try:
            body = _read_capped(exc.fp, deadline) if exc.fp is not None else b""
            return int(exc.code), body
        finally:
            exc.close()
    try:
        status = int(getattr(response, "status", 0) or getattr(response, "code", 0) or 0)
        return status, _read_capped(response, deadline)
    finally:
        response.close()


def _api_text(body: bytes) -> str:
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    if isinstance(err, dict):
        msg = str(err.get("message") or err.get("code") or "")
        return msg
    err_s = str(data.get("error") or "")
    msg = str(data.get("message") or "")
    text = f"{err_s}: {msg}".strip(": ")
    return text


def parse_response(path: str, status: int, body: bytes, *, token: str) -> Any:
    if status in REDIRECTS:
        raise ClientError(
            "redirect",
            redact(
                f"HTTP {status} was not followed. Nothing was sent to the other host. " + _unchanged(),
                token,
            ),
            status=status,
        )
    if len(body) > BODY_CAP:
        raise ClientError(
            "oversize",
            "The response was too large to read, so the numbers are unknown. " + _unchanged(),
            status=status,
        )
    if status < 200 or status >= 300:
        detail = redact(_api_text(body), token)
        extra = f" {detail}." if detail else ""
        kind = "auth" if status in {401, 403} else "http"
        if status == 404:
            kind = "not_found"
        elif status >= 500:
            kind = "upstream"
        raise ClientError(
            f"http_{status}",
            redact(
                f"HTTP {status} is a failure, not an empty success.{extra} " + _unchanged(),
                token,
            ),
            status=status,
        )
    if not body or not body.strip():
        raise ClientError("bad_body", "HTTP 200 with an empty body is a failure. " + _unchanged(), status=status)
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise ClientError("bad_body", "HTTP 200 was not JSON. " + _unchanged(), status=status) from exc
    if path.endswith(STATS_SUFFIX):
        if not isinstance(data, dict):
            raise ClientError("bad_body", "HTTP 200 stats were not a JSON object. " + _unchanged(), status=status)
        if "error" in data:
            detail = redact(_api_text(body) or str(data.get("error")), token)
            raise ClientError(
                "bad_body",
                f"HTTP 200 included an error ({detail}). This is a failure, not zero traffic. " + _unchanged(),
                status=status,
            )
        return data
    if path.endswith(METRICS_SUFFIX):
        if isinstance(data, dict) and "error" in data:
            detail = redact(_api_text(body) or str(data.get("error")), token)
            raise ClientError(
                "bad_body",
                f"HTTP 200 included an error ({detail}). This is a failure, not an empty page list. " + _unchanged(),
                status=status,
            )
        if not isinstance(data, list):
            raise ClientError("bad_body", "HTTP 200 metrics were not a JSON array. " + _unchanged(), status=status)
        return data
    raise ClientError("bad_path", "Only website stats and expanded metrics are requested, so nothing was sent.")


def get_json(
    origin: str,
    path: str,
    pairs: list[tuple[str, str]],
    token: str,
    transport: Transport | None = None,
) -> Any:
    pinned = parse_origin(origin)
    url = build_url(pinned, path, pairs)
    try:
        status, body = (transport or _urllib_transport)(url, token)
    except ClientError as exc:
        exc.message = redact(exc.message, token)
        raise
    except TimeoutError as exc:
        raise ClientError("timeout", "The Umami request timed out. " + _unchanged()) from exc
    except Exception as exc:
        if "timed out" in str(exc).lower():
            raise ClientError("timeout", "The Umami request timed out. " + _unchanged()) from exc
        raise ClientError("network", "The Umami instance could not be reached. " + _unchanged()) from exc
    return parse_response(path, status, body, token=token)
