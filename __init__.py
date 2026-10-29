"""Yesterday's Umami pageviews in one morning letter, with no model."""
import json


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def register(ctx) -> None:
    if __package__:
        from . import gates
        from .cli import handle, setup_parser
        from .service import Deps, TOOLSET, umami_digest
    else:
        import gates
        from cli import handle, setup_parser
        from service import Deps, TOOLSET, umami_digest

    try:
        reported = ctx.profile_name
    except Exception:
        reported = None
    gates.remember_profile_name(reported)

    def _digest(args, **_kwargs):
        return _json(umami_digest(Deps(), args or {}))

    ctx.register_tool(
        name="umami_digest",
        toolset=TOOLSET,
        schema={
            "name": "umami_digest",
            "description": (
                "Yesterday's Umami pageviews, visitors, and top pages. "
                "Morning website analytics digest of traffic stats: day-over-day change, "
                "the top five paths by pageviews, and a surge flag at 2.0x from your self-hosted Umami. "
                "Expanded paths come 500 at a time in visitor order. "
                "The letter walks offset until the list ends, or 20 pages, then ranks by pageviews. "
                "A capped day says it is the top 5 among the first N paths. "
                "A previous day with 0 pageviews is new traffic, not a ratio. "
                "Call it with an empty object. It does not write, and it does not send events."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
        },
        handler=_digest,
    )
    ctx.register_cli_command(
        name="umami-digest",
        help="Morning Umami digest: schedule, unschedule, or status",
        setup_fn=setup_parser,
        handler_fn=handle,
        description=(
            "Save one no-agent cron job that prints yesterday's Umami numbers. "
            "Does not register a slash command."
        ),
    )
    try:
        from pathlib import Path
        ctx.register_skill(
            "umami-digest",
            Path(__file__).resolve().parent / "skills" / "umami-digest" / "SKILL.md",
            description=(
                "Yesterday's Umami pageviews, visitors, top pages, and a surge flag. "
                "Use tool_search for umami_digest, then tool_call. It reads your instance."
            ),
        )
    except Exception:
        import logging
        logging.getLogger(__name__).warning("umami-digest skill was not registered", exc_info=True)
