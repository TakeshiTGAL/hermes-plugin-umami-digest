#!/usr/bin/env python3
"""Mint a local API key and send a few pageviews. Not part of the plugin."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ORIGIN = "http://localhost:3000"
ADMIN_USER = "admin"
ADMIN_PASSWORD = "umami"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ARG002
        return None


_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


def _json(method: str, path: str, body: dict | None = None, token: str | None = None) -> tuple[int, object]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(ORIGIN + path, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(req, timeout=20) as resp:
            raw = resp.read()
            return int(resp.status), json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            parsed = {"error": raw[:200].decode("utf-8", "replace")}
        return int(exc.code), parsed


def wait_up() -> None:
    for _ in range(60):
        try:
            status, _payload = _json("GET", "/api/heartbeat")
            if status < 500:
                return
        except Exception:
            try:
                _OPENER.open(ORIGIN, timeout=2).close()
                return
            except Exception:
                pass
        time.sleep(1)
    raise SystemExit("Umami on localhost:3000 did not become ready.")


def main() -> int:
    wait_up()
    status, payload = _json("POST", "/api/auth/login", {"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    if status != 200 or not isinstance(payload, dict) or not payload.get("token"):
        print(f"login failed: HTTP {status} {payload}", file=sys.stderr)
        return 1
    token = str(payload["token"])
    status, key_body = _json("POST", "/api/me/api-keys", {"name": "umami-digest-proof"}, token=token)
    if status not in {200, 201} or not isinstance(key_body, dict):
        print(f"api key failed: HTTP {status} {key_body}", file=sys.stderr)
        return 1
    api_key = str(key_body.get("apiKey") or key_body.get("key") or "")
    if not api_key:
        print(f"api key missing in {list(key_body)}", file=sys.stderr)
        return 1
    status, site = _json(
        "POST",
        "/api/websites",
        {"name": "digest-proof", "domain": "localhost"},
        token=token,
    )
    if status not in {200, 201} or not isinstance(site, dict) or not site.get("id"):
        print(f"website failed: HTTP {status} {site}", file=sys.stderr)
        return 1
    website_id = str(site["id"])
    tz = ZoneInfo("UTC")
    now = datetime.now(timezone.utc).astimezone(tz)
    yesterday = int((now.replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=1)).timestamp())
    hits = [
        ("/", yesterday),
        ("/", yesterday + 60),
        ("/blog", yesterday + 120),
        ("/pricing`test", yesterday + 180),
    ]
    stamped = True
    for path, ts in hits:
        body = {
            "type": "event",
            "payload": {
                "website": website_id,
                "hostname": "localhost",
                "url": path,
                "title": path,
                "timestamp": ts,
            },
        }
        status, sent = _json("POST", "/api/send", body)
        if status >= 400:
            stamped = False
            body["payload"].pop("timestamp", None)
            status, sent = _json("POST", "/api/send", body)
        if status >= 400:
            print(f"send failed: HTTP {status} {sent}", file=sys.stderr)
            return 1
    print(f"UMAMI_URL={ORIGIN}")
    print(f"UMAMI_API_KEY={api_key}")
    print(f"UMAMI_WEBSITE_ID={website_id}")
    print("UMAMI_TIMEZONE=UTC")
    print("STAMPED_YESTERDAY=" + ("yes" if stamped else "no"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
