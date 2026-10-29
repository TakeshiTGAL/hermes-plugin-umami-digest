"""UMAMI_URL allowlist: loopback http, otherwise https."""
from __future__ import annotations

import pytest

from gates import ConfigError, parse_origin, parse_website_ids, parse_timezone


def test_http_loopback_ok():
    assert parse_origin("http://127.0.0.1:3000") == "http://127.0.0.1:3000"
    assert parse_origin("http://localhost") == "http://localhost"
    assert parse_origin("http://[::1]:3000") == "http://[::1]:3000"


def test_https_public_ok():
    assert parse_origin("https://umami.example.net") == "https://umami.example.net"
    assert parse_origin("https://192.168.1.8") == "https://192.168.1.8"


def test_http_lan_refused():
    with pytest.raises(ConfigError) as exc:
        parse_origin("http://192.168.1.8")
    assert exc.value.code == "plain_http"


def test_docs_and_link_local_refused():
    for raw in (
        "http://169.254.169.254",
        "http://0.0.0.0",
        "http://192.0.2.1",
        "https://169.254.169.254",
        "https://0.0.0.0",
        "https://192.0.2.1",
        "https://203.0.113.9",
    ):
        with pytest.raises(ConfigError) as exc:
            parse_origin(raw)
        assert exc.value.code in {"bad_url", "plain_http"}


def test_cloud_refused():
    for raw in (
        "https://cloud.umami.is",
        "https://api.umami.is",
        "https://eu.umami.is",
    ):
        with pytest.raises(ConfigError) as exc:
            parse_origin(raw)
        assert exc.value.code == "cloud"


def test_userinfo_query_path_refused():
    for raw in (
        "https://user:pass@umami.example.net",
        "https://umami.example.net/path",
        "https://umami.example.net?x=1",
        "https://umami.example.net#frag",
        "ftp://127.0.0.1",
        "",
        "https://umami.example.net/app",
        "https://uma\nmi.example.net",
    ):
        with pytest.raises(ConfigError):
            parse_origin(raw)


def test_website_ids():
    one = "11111111-1111-1111-1111-111111111111"
    two = "22222222-2222-2222-2222-222222222222"
    assert parse_website_ids(one) == [one]
    assert parse_website_ids(f"{one},{two}") == [one, two]
    with pytest.raises(ConfigError):
        parse_website_ids("not-a-uuid")
    with pytest.raises(ConfigError):
        parse_website_ids(f"{one}/../stats")
    with pytest.raises(ConfigError):
        parse_website_ids(f"{one}?x=1")
    with pytest.raises(ConfigError):
        parse_website_ids(f"{one}\n{two}")
    with pytest.raises(ConfigError):
        parse_website_ids("")


def test_timezone():
    assert parse_timezone("Asia/Tokyo").key == "Asia/Tokyo"
    assert parse_timezone(None).key == "UTC"
    with pytest.raises(ConfigError) as exc:
        parse_timezone("Mars/Phobos")
    assert exc.value.code == "bad_tz"
