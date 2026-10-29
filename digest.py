"""Yesterday's Umami letter. No model. Unknown is never written as zero."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

if __package__:
    from . import client, gates
    from .gates import ConfigError
    from .client import ClientError
else:
    import client
    import gates
    from gates import ConfigError
    from client import ClientError

SURGE_RATIO = 2.0
NEW_TRAFFIC = "new traffic"
TOP_PAGES = 5
# Umami 3.4.1 expanded metrics order by visitors. The default page is 500 rows.
EXPANDED_PAGE_LIMIT = 500
EXPANDED_MAX_PAGES = 20
SECRET_NAMES = ("UMAMI_URL", "UMAMI_API_KEY", "UMAMI_WEBSITE_ID", "UMAMI_TIMEZONE")


class DigestError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _secret(reader: Callable[[str], str | None], name: str) -> str | None:
    try:
        value = reader(name)
    except Exception:
        raise
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value)
    return value


def live_secret(name: str) -> str | None:
    from agent.secret_scope import UnscopedSecretError, get_secret

    try:
        return get_secret(name)
    except UnscopedSecretError:
        raise


def yesterday_window(now: datetime, tz: ZoneInfo) -> tuple[int, int, str]:
    """Inclusive start and exclusive end of yesterday in tz, as Unix ms."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(tz)
    day = local.date() - timedelta(days=1)
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    return start_ms, end_ms, day.isoformat()


def count_value(raw: Any) -> int | None:
    """Read a stats field. Unreadable is None, never coerced to 0."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if raw != raw:  # NaN
            return None
        if raw < 0:
            return None
        if abs(raw - round(raw)) < 1e-9:
            return int(round(raw))
        return None
    if isinstance(raw, dict):
        if "value" in raw:
            return count_value(raw.get("value"))
        return None
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def change_line(today: int | None, previous: int | None) -> str:
    if today is None:
        return "unknown (the stats reply did not include a readable number)"
    if previous is None:
        return f"{today} (previous day unknown)"
    if previous == 0:
        if today == 0:
            return "0 vs 0"
        return f"{today} vs 0 (no previous-day traffic)"
    delta = today - previous
    pct = (delta / previous) * 100.0
    sign = "+" if pct >= 0 else ""
    return f"{today} vs {previous} ({sign}{pct:.0f}%)"


def surge_line(today: int | None, previous: int | None) -> str:
    if today is None or previous is None:
        return "Surge: unknown (comparison pageviews were not readable)."
    if previous <= 0:
        if today > 0:
            return f"Surge: {NEW_TRAFFIC} (pageviews {today} vs 0)."
        return "Surge: no."
    ratio = today / previous
    if ratio >= SURGE_RATIO:
        return (
            f"Surge: yes (pageviews {ratio:.1f}x versus the previous day, "
            f"threshold {SURGE_RATIO:.1f}x)."
        )
    return "Surge: no."


def display_path(raw: Any) -> str:
    if not isinstance(raw, str):
        raw = str(raw)
    cleaned: list[str] = []
    for ch in raw:
        code = ord(ch)
        if code < 32 or code == 127:
            cleaned.append("?")
        elif ch == "`":
            cleaned.append("'")
        else:
            cleaned.append(ch)
    text = "".join(cleaned)
    if len(text) > 200:
        text = text[:199] + "…"
    return text


def top_pages(rows: Any) -> tuple[list[tuple[str, int]], str | None]:
    if not isinstance(rows, list):
        return [], "unknown (metrics were not a list)"
    best: dict[str, int] = {}
    order: list[str] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        path = item.get("name")
        count = count_value(item.get("pageviews"))
        if path is None or count is None:
            continue
        shown = display_path(path)
        # A repeated name keeps the larger count so an overlapping page cannot inflate it.
        if shown not in best:
            order.append(shown)
            best[shown] = count
        elif count > best[shown]:
            best[shown] = count
    ranked = [(path, best[path]) for path in order]
    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return ranked[:TOP_PAGES], None


def pages_block(rows: Any, *, failed: str | None = None, path_window: int | None = None) -> str:
    if failed:
        return f"Top pages: unknown ({failed})."
    ranked, err = top_pages(rows)
    if err:
        return f"Top pages: {err}."
    if path_window is not None:
        note = f"top 5 among the first {path_window} paths"
        if not ranked:
            return f"Top pages: none recorded ({note})."
        lines = [f"Top pages (pageviews, {note}):"]
    else:
        if not ranked:
            return "Top pages: none recorded."
        lines = ["Top pages (pageviews):"]
    for index, (path, count) in enumerate(ranked, start=1):
        lines.append(f"{index}. `{path}` — {count}")
    return "\n".join(lines)


def site_letter(
    website_id: str,
    stats: dict[str, Any],
    metrics: Any,
    *,
    metrics_error: str | None = None,
    path_window: int | None = None,
) -> str:
    pageviews = count_value(stats.get("pageviews"))
    visitors = count_value(stats.get("visitors"))
    comparison = stats.get("comparison")
    if not isinstance(comparison, dict):
        prev_pv = None
        prev_visitors = None
        comparison_note = "previous-day comparison was missing"
    else:
        prev_pv = count_value(comparison.get("pageviews"))
        prev_visitors = count_value(comparison.get("visitors"))
        comparison_note = ""
    lines = [
        f"Website `{website_id}`",
        f"Pageviews: {change_line(pageviews, prev_pv)}",
        f"Visitors: {change_line(visitors, prev_visitors)}",
    ]
    if comparison_note:
        lines.append(f"Note: {comparison_note}.")
    lines.append(surge_line(pageviews, prev_pv))
    lines.append(pages_block(metrics, failed=metrics_error, path_window=path_window))
    return "\n".join(lines)


def load_config(reader: Callable[[str], str | None]) -> tuple[str, str, list[str], ZoneInfo]:
    url = (_secret(reader, "UMAMI_URL") or "").strip()
    key = _secret(reader, "UMAMI_API_KEY")
    ids_raw = (_secret(reader, "UMAMI_WEBSITE_ID") or "").strip()
    tz_raw = _secret(reader, "UMAMI_TIMEZONE")
    if not tz_raw:
        tz_raw = _secret(reader, "TZ")
    origin = gates.parse_origin(url)
    if not isinstance(key, str) or not key.strip():
        raise ConfigError("bad_key", "UMAMI_API_KEY is not set. Put it in this profile's .env.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in key):
        raise ConfigError("bad_key", "UMAMI_API_KEY contains a control character, so nothing was sent.")
    ids = gates.parse_website_ids(ids_raw)
    tz = gates.parse_timezone(tz_raw)
    return origin, key.strip(), ids, tz


def _expanded_pairs(start_ms: int, end_ms: int, tz_name: str, offset: int) -> list[tuple[str, str]]:
    return [
        ("startAt", str(start_ms)),
        ("endAt", str(end_ms)),
        ("timezone", tz_name),
        ("type", "path"),
        ("limit", str(EXPANDED_PAGE_LIMIT)),
        ("offset", str(offset)),
    ]


def fetch_expanded_paths(
    origin: str,
    token: str,
    website_id: str,
    start_ms: int,
    end_ms: int,
    tz_name: str,
    transport: client.Transport | None,
) -> tuple[list[Any] | None, int | None, str | None]:
    """Walk visitor-ordered pages. A full page at the cap sets the window size."""
    collected: list[Any] = []
    offset = 0
    for _ in range(EXPANDED_MAX_PAGES):
        try:
            page = client.get_json(
                origin,
                client.metrics_path(website_id),
                _expanded_pairs(start_ms, end_ms, tz_name, offset),
                token,
                transport,
            )
        except ClientError as exc:
            return None, None, exc.message
        if not isinstance(page, list):
            return None, None, "metrics were not a list"
        collected.extend(page)
        if len(page) < EXPANDED_PAGE_LIMIT:
            return collected, None, None
        offset += len(page)
    return collected, len(collected), None


def fetch_site(
    origin: str,
    token: str,
    website_id: str,
    start_ms: int,
    end_ms: int,
    tz_name: str,
    transport: client.Transport | None,
) -> tuple[dict[str, Any] | None, Any | None, str | None, int | None]:
    pairs = [
        ("startAt", str(start_ms)),
        ("endAt", str(end_ms)),
        ("timezone", tz_name),
        ("compare", "prev"),
    ]
    stats_err = None
    stats = None
    try:
        stats = client.get_json(origin, client.stats_path(website_id), pairs, token, transport)
    except ClientError as exc:
        stats_err = exc.message
    if stats is None:
        return None, None, stats_err or "stats could not be read", None
    metrics, path_window, metrics_err = fetch_expanded_paths(
        origin, token, website_id, start_ms, end_ms, tz_name, transport
    )
    if metrics is None:
        # Stats succeeded. Top pages stay unknown. Do not call that "no pages".
        return stats, None, metrics_err, None
    return stats, metrics, None, path_window


def render_letter(
    *,
    day: str,
    tz_name: str,
    sections: list[str],
) -> str:
    head = f"Umami digest for {day} ({tz_name})"
    return head + "\n\n" + "\n\n".join(sections)


def build_letter(
    reader: Callable[[str], str | None],
    now: datetime,
    transport: client.Transport | None = None,
) -> str:
    origin, token, ids, tz = load_config(reader)
    start_ms, end_ms, day = yesterday_window(now, tz)
    tz_name = getattr(tz, "key", None) or str(tz)
    sections: list[str] = []
    failures = 0
    first_err = ""
    for website_id in ids:
        stats, metrics, err, path_window = fetch_site(
            origin, token, website_id, start_ms, end_ms, tz_name, transport
        )
        if stats is None:
            failures += 1
            detail = gates.redact(err or "unknown failure", token)
            if not first_err:
                first_err = detail
            sections.append(
                f"Website `{website_id}`\n"
                f"Could not read stats: {detail}\n"
                "Numbers for this site are unknown."
            )
            continue
        if metrics is None:
            sections.append(
                site_letter(
                    website_id,
                    stats,
                    None,
                    metrics_error=gates.redact(err or "metrics could not be read", token),
                )
            )
            continue
        sections.append(site_letter(website_id, stats, metrics, path_window=path_window))
    if failures == len(ids):
        extra = f" {first_err}" if first_err else ""
        raise DigestError(
            "all_failed",
            "No website could be read, so this is not a zero-traffic day." + extra,
        )
    return render_letter(day=day, tz_name=tz_name, sections=sections)
