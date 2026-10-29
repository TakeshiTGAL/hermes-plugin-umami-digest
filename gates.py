"""Delivery allowlist, Umami URL pin, website ids, and profile prefix."""
from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if __package__:
    from .local_host import host_is_local
else:
    from local_host import host_is_local

PLUGIN_NAME = "umami-digest"
PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
NEVER_DELIVER = frozenset({"cli", "cron", "api_server"})
DELIVER_EXACT = frozenset({"origin", "local", "all"})
BUILTIN_DELIVERY_PLATFORMS = frozenset({
    "telegram", "discord", "slack", "whatsapp", "signal", "matrix", "mattermost",
    "dingtalk", "feishu", "wecom", "wecom_callback", "weixin", "sms", "email",
    "webhook", "bluebubbles", "qqbot", "yuanbao",
})
SCHEDULE_WORDS = frozenset({
    "every", "in", "at", "on", "daily", "hourly", "weekly", "monthly", "yearly", "annually",
    "once", "now", "today", "tonight", "tomorrow", "midnight", "noon", "minute", "minutes",
    "hour", "hours", "day", "days", "week", "weeks", "weekdays", "weekends", "weekday", "weekend",
})
WEEKDAYS = frozenset({
    "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
})
_DURATION = re.compile(
    r"(\d*)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)\Z",
    re.IGNORECASE,
)
WEBSITE_ID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
CLOUD_HOSTS = frozenset({
    "cloud.umami.is",
    "api.umami.is",
    "umami.is",
    "www.umami.is",
})
_DOCS_V4 = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
)
_LOOPBACK_V4 = ipaddress.ip_network("127.0.0.0/8")
_LOOPBACK_V6 = ipaddress.ip_address("::1")
MAX_SITES = 20


class ConfigError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def plugin_data_dir() -> Path:
    try:
        import importlib
        module = importlib.import_module("plugins.plugin_storage")
    except Exception as exc:
        raise RuntimeError("plugin_data_dir is not available") from exc
    fn = getattr(module, "plugin_data_dir", None)
    if not callable(fn):
        raise RuntimeError("plugin_data_dir is not available")
    path = fn(PLUGIN_NAME)
    if not isinstance(path, Path):
        path = Path(path)
    return path


def write_guard_error(path: str) -> str | None:
    try:
        import importlib
        module = importlib.import_module("agent.file_safety")
    except Exception:
        return "The write check could not be loaded, so the file was not saved."
    fn = getattr(module, "get_write_denied_error", None)
    if not callable(fn):
        return "The write check could not be loaded, so the file was not saved."
    try:
        denied = fn(path)
    except Exception:
        return "The write check raised, so the file was not saved."
    if denied:
        return str(denied)
    return None


_REMEMBERED_PROFILE: str | None = None


def remember_profile_name(reported: Any) -> None:
    global _REMEMBERED_PROFILE
    if isinstance(reported, str) and PROFILE_NAME.fullmatch(reported.strip()):
        _REMEMBERED_PROFILE = reported.strip()
        return
    _REMEMBERED_PROFILE = None


def active_profile_name() -> str:
    if _REMEMBERED_PROFILE is None:
        return "default"
    return _REMEMBERED_PROFILE


def hermes_cron_lines(profile: str | None = None, job_id: str | None = None) -> list[str]:
    name = active_profile_name() if profile is None else profile
    prefix = "hermes" if name == "default" else f"hermes -p {name}"
    lines = []
    ident = str(job_id).strip() if job_id else ""
    for verb in ("create", "run", "list", "status", "remove"):
        if verb in {"run", "remove"}:
            if ident:
                lines.append(f"{prefix} cron {verb} {ident}")
            continue
        lines.append(f"{prefix} cron {verb}")
    return lines


def load_platforms() -> set[str] | None:
    try:
        import importlib
        module = importlib.import_module("gateway.platform_registry")
    except Exception:
        return None
    registry = getattr(module, "platform_registry", None)
    if registry is None:
        return None
    try:
        names: set[str] = set()
        for entry_name in registry.registered_names():
            text = str(entry_name).strip().lower()
            if not text:
                continue
            entry = registry.get(text)
            if entry is not None and getattr(entry, "cron_deliver_env_var", None):
                names.add(text)
    except Exception:
        return None
    return names - NEVER_DELIVER


def _duration(text: str) -> bool:
    return _DURATION.fullmatch(text.strip()) is not None


def _looks_like_schedule(target: str) -> bool:
    word = target.strip().lower()
    if not word:
        return False
    if word in SCHEDULE_WORDS or word in WEEKDAYS or word.rstrip("s") in WEEKDAYS:
        return True
    if word[0].isdigit() or word[0] in "*@":
        return True
    if "/" in word or _duration(word):
        return True
    try:
        datetime.fromisoformat(word.replace("z", "+00:00"))
    except ValueError:
        return False
    return True


_MISSING = object()


def canonical_deliver(deliver: str, platforms: Any = _MISSING) -> str | None:
    if platforms is _MISSING:
        platforms = load_platforms()
    allowed = set(BUILTIN_DELIVERY_PLATFORMS)
    if platforms:
        allowed |= set(platforms)
    allowed -= NEVER_DELIVER
    canon: list[str] = []
    for part in str(deliver).split(","):
        piece = part.strip()
        if not piece or any(ch.isspace() for ch in piece) or _looks_like_schedule(piece):
            return None
        low = piece.lower()
        if low in NEVER_DELIVER:
            return None
        if low in DELIVER_EXACT:
            canon.append(low)
            continue
        if low == "bot-chat":
            canon.append(low)
            continue
        if low.startswith("bot-chat:"):
            chat = piece.split(":", 1)[1].strip()
            if not chat or any(ch.isspace() for ch in chat):
                return None
            canon.append(f"bot-chat:{chat}")
            continue
        platform, sep, chat = piece.partition(":")
        key = platform.lower()
        if key not in allowed:
            return None
        if not sep:
            canon.append(key)
            continue
        if not chat or any(ch.isspace() for ch in chat):
            return None
        canon.append(f"{key}:{chat}")
    return ",".join(canon) if canon else None


def host_is_loopback(host: str) -> bool:
    """True for localhost, 127.0.0.0/8, and ::1. LAN and link-local are false."""
    if not isinstance(host, str) or not host:
        return False
    if any(char.isspace() or ord(char) < 33 or ord(char) == 127 for char in host):
        return False
    name = host.rstrip(".").lower()
    if name == "localhost":
        return True
    if name.startswith("[") and name.endswith("]") and name.count("[") == 1:
        inner = name[1:-1]
        if not inner or "[" in inner or "]" in inner:
            return False
        name = inner
    if any(char in name for char in "@/\\%"):
        return False
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        return address == _LOOPBACK_V6
    return address in _LOOPBACK_V4


def _literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    name = host.rstrip(".").lower()
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    try:
        return ipaddress.ip_address(name)
    except ValueError:
        return None


def host_is_refused_literal(host: str) -> bool:
    """Refuse unspecified, link-local, documentation, multicast, and reserved literals."""
    address = _literal_ip(host)
    if address is None:
        return False
    if address in _LOOPBACK_V4 or address == _LOOPBACK_V6:
        return False
    if address.is_unspecified or address.is_link_local or address.is_multicast or address.is_reserved:
        return True
    if isinstance(address, ipaddress.IPv4Address):
        return any(address in net for net in _DOCS_V4)
    return False


def parse_origin(raw: str) -> str:
    """Return scheme://host[:port] with no path. Raise ConfigError otherwise."""
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError("bad_url", "UMAMI_URL is not set. Put it in this profile's .env.")
    text = raw.strip()
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ConfigError("bad_url", "UMAMI_URL contains a control character, so nothing was sent.")
    if "\\" in text or "\x00" in text:
        raise ConfigError("bad_url", "UMAMI_URL is not a usable origin, so nothing was sent.")
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"}:
        raise ConfigError("bad_url", "UMAMI_URL must be http or https, so nothing was sent.")
    if parsed.username is not None or parsed.password is not None or parsed.netloc.find("@") >= 0:
        raise ConfigError("bad_url", "UMAMI_URL must not include a username or password, so nothing was sent.")
    host = parsed.hostname
    if not host:
        raise ConfigError("bad_url", "UMAMI_URL has no host, so nothing was sent.")
    if parsed.query or parsed.fragment:
        raise ConfigError("bad_url", "UMAMI_URL must be an origin without a query or fragment, so nothing was sent.")
    low = host.rstrip(".").lower()
    if low in CLOUD_HOSTS or low.endswith(".umami.is"):
        raise ConfigError(
            "cloud",
            "Umami Cloud is not used. Set UMAMI_URL to an instance you run.",
        )
    if host_is_refused_literal(host):
        raise ConfigError("bad_url", "UMAMI_URL points at an unusable address, so nothing was sent.")
    if parsed.scheme == "http":
        if not host_is_loopback(host):
            raise ConfigError(
                "plain_http",
                "Plain http is allowed only to loopback. Use https for any other host.",
            )
    else:
        # https to a LAN or public name is allowed. https to a refused literal is already out.
        if low != "localhost" and _literal_ip(host) is None:
            # A DNS name is allowed on https. host_is_local is not required.
            if any(ch in low for ch in "@/\\%"):
                raise ConfigError("bad_url", "UMAMI_URL host is not usable, so nothing was sent.")
        elif not host_is_loopback(host) and host_is_local(host) is False and _literal_ip(host) is not None:
            # Public or private literal over https is allowed unless refused above.
            pass
    if parsed.path not in {"", "/"}:
        raise ConfigError(
            "bad_url",
            "UMAMI_URL must be the instance origin with no extra path, so nothing was sent.",
        )
    hostname = host
    if ":" in hostname and not hostname.startswith("["):
        try:
            ipaddress.IPv6Address(hostname)
            hostname = f"[{hostname}]"
        except ValueError:
            pass
    if parsed.port:
        origin = f"{parsed.scheme}://{hostname}:{parsed.port}"
    else:
        origin = f"{parsed.scheme}://{hostname}"
    return origin


def parse_website_ids(raw: str) -> list[str]:
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(
            "bad_ids",
            "UMAMI_WEBSITE_ID is not set. Put one UUID, or several separated by commas, in this profile's .env.",
        )
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise ConfigError("bad_ids", "UMAMI_WEBSITE_ID contains a control character, so nothing was sent.")
    parts = [item.strip() for item in raw.split(",")]
    ids: list[str] = []
    for item in parts:
        if not item:
            continue
        if any(ch in item for ch in "/?#\\"):
            raise ConfigError("bad_ids", "UMAMI_WEBSITE_ID must be a UUID, so nothing was sent.")
        if not WEBSITE_ID.fullmatch(item):
            raise ConfigError("bad_ids", "UMAMI_WEBSITE_ID must be a UUID, so nothing was sent.")
        if item not in ids:
            ids.append(item)
    if not ids:
        raise ConfigError("bad_ids", "UMAMI_WEBSITE_ID is empty, so nothing was sent.")
    if len(ids) > MAX_SITES:
        raise ConfigError("bad_ids", f"UMAMI_WEBSITE_ID lists more than {MAX_SITES} sites, so nothing was sent.")
    return ids


def parse_timezone(raw: str | None) -> ZoneInfo:
    name = (raw or "").strip() or "UTC"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise ConfigError("bad_tz", "UMAMI_TIMEZONE is not a usable IANA name, so nothing was sent.")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        raise ConfigError(
            "bad_tz",
            "UMAMI_TIMEZONE is not a usable IANA name, so nothing was sent.",
        ) from exc


def redact(text: str, secret: str | None) -> str:
    if not secret or not text:
        return text
    out = text
    if secret in out:
        out = out.replace(secret, "[redacted]")
    trimmed = secret.strip()
    if trimmed and trimmed in out:
        out = out.replace(trimmed, "[redacted]")
    return out
