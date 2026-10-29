"""Status handling without opening a socket."""
from __future__ import annotations

import json

import pytest

import client

SITE = "11111111-1111-1111-1111-111111111111"
PATH = f"/api/websites/{SITE}/stats"
KEY = "k"


def test_401_404_500_messages_differ():
    a = pytest.raises(client.ClientError, client.parse_response, PATH, 401, b'{"error":{"message":"Unauthorized."}}', token=KEY)
    b = pytest.raises(client.ClientError, client.parse_response, PATH, 404, b'{"error":{"message":"Not found."}}', token=KEY)
    c = pytest.raises(client.ClientError, client.parse_response, PATH, 503, b'{"error":{"message":"busy"}}', token=KEY)
    assert "401" in a.value.message and KEY not in a.value.message
    assert "404" in b.value.message
    assert "503" in c.value.message
    assert a.value.message != b.value.message != c.value.message


def test_redirect_not_followed():
    with pytest.raises(client.ClientError) as exc:
        client.parse_response(PATH, 302, b"", token=KEY)
    assert exc.value.code == "redirect"
    assert "not followed" in exc.value.message


def test_oversize():
    body = b"{" + b"x" * (client.BODY_CAP + 2) + b"}"
    with pytest.raises(client.ClientError) as exc:
        client.parse_response(PATH, 200, body, token=KEY)
    assert exc.value.code == "oversize"
    assert "too large" in exc.value.message


def test_empty_and_html():
    with pytest.raises(client.ClientError) as exc:
        client.parse_response(PATH, 200, b"  ", token=KEY)
    assert "empty body" in exc.value.message
    with pytest.raises(client.ClientError) as exc:
        client.parse_response(PATH, 200, b"<html>no</html>", token=KEY)
    assert "JSON" in exc.value.message


def test_metrics_must_be_list():
    metrics = f"/api/websites/{SITE}/metrics/expanded"
    with pytest.raises(client.ClientError):
        client.parse_response(metrics, 200, b'{"name":"/","pageviews":1}', token=KEY)
    rows = client.parse_response(metrics, 200, b'[{"name":"/","pageviews":"1"}]', token=KEY)
    assert rows == [{"name": "/", "pageviews": "1"}]


def test_stats_error_field():
    with pytest.raises(client.ClientError) as exc:
        client.parse_response(PATH, 200, json.dumps({"error": "nope", "pageviews": 0}).encode(), token=KEY)
    assert "zero" not in exc.value.message.lower() or "not" in exc.value.message.lower()
    assert "failure" in exc.value.message.lower() or "error" in exc.value.message.lower()
