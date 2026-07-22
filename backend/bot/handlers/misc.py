"""
.ping    — Edit trigger with PONG (zero-spam policy).
.id      — Chat ID + Message ID of the current context.
.help    — Interactive inline help panel (via Inline Mode).
.health  — Full health dashboard (inline panel).
.kill    — Diagnostic snapshot + stalled-task recovery (inline panel).
.logs    — View recent diagnostic events (inline panel).

Inline Mode architecture:
  - .help triggers inline mode → self sends inline result with buttons.
  - .health/.kill/.logs trigger inline mode → self sends inline result.
  - All panel navigation happens via callbacks (no new messages).
  - Falls back to plain-text edit-in-place when the helper bot is not
    available (no BOT_TOKEN).
"""
import asyncio
import logging
import os
import resource
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telethon import events

from backend import diagnostics, health
from backend.bio import engine as bio_engine
from backend.bot.handlers.guard import is_owner
from backend.db import client as db_client
from backend.helper import (
    InlinePanelBuilder,
    register_panel,
    get_panel,
    register_inline_builder,
    send_inline_panel,
)
from backend.helper.client import get_client


def _resolve_tz() -> str:
    try:
        tz_str = os.getenv("TZ", "Asia/Tehran")
        ZoneInfo(tz_str)
        return tz_str
    except (ZoneInfoNotFoundError, Exception):
        return "UTC"


logger = logging.getLogger(__name__)

# ── Help menu data ──────────────────────────────────────────────────────

_HELP_CATEGORIES: list[tuple[str, list[str]]] = [
    (
        "General",
        [
            "**General**\n",
            "`.ping` — PONG",
            "`.id` — Chat & Msg IDs",
            "`.help` — This menu",
            "`.health` — Health dashboard",
        ],
    ),
    (
        "Save Engine",
        [
            "**Save Engine**  _(reply to a message)_\n",
            "`.save f` · `.s f` — Forward save",
            "`.save d` · `.s d` — Deep save",
        ],
    ),
    (
        "Retrieve",
        [
            "**Retrieve**\n",
            "`.preview <code>` — Show metadata",
            "`.r <code>` · `.retrieve <code>` — Alias",
            "`.send <code>` — Forward asset here",
        ],
    ),
    (
        "Organizer",
        [
            "**Organizer**\n",
            "`.del <n>` — Delete last n messages",
            "`.del id <msgid>` — Delete from msgid",
            "`.del <code>` — Delete a saved item",
            "`.organize list` — Data overview",
            "`.organize clean` — Purge old logs",
        ],
    ),
    (
        "Bio Engine",
        [
            "**Bio Engine**\n",
            "`.bio help` — Token reference",
            "`.bio on` — Start cron",
            "`.bio off` — Stop cron",
            "`.bio show` — Inspect state",
            "`.bio template <tpl>` — Set template",
            "`.bio text <text>` — Set {text}",
            "`.bio mood <mood>` — Set {mood}",
        ],
    ),
    (
        "Database",
        [
            "**Database**\n",
            "`.db clean` — Remove orphan rows",
            "`.db stats` — Database statistics",
            "`.db vacuum` — Cleanup + optimize",
        ],
    ),
    (
        "Diagnostics",
        [
            "**Diagnostics**\n",
            "`.kill` — Snapshot + recovery",
            "`.logs` — Recent events (last 20)",
            "`.logs 50` — Last 50 events",
            "`.logs errors` — Errors only",
            "`.logs module <m>` — Filter by module",
        ],
    ),
]


def _build_main_menu_text() -> str:
    lines = ["**LifeOS Command Center**\n"]
    for i, (label, _) in enumerate(_HELP_CATEGORIES, start=1):
        lines.append(f"{i} • {label}")
    lines.append("\n_Tap a category._")
    return "\n".join(lines)


def _build_category_page_text(index: int) -> str:
    _, lines = _HELP_CATEGORIES[index]
    return "\n".join(lines)


def _build_main_menu_keyboard() -> list:
    builder = InlinePanelBuilder()
    for i, (label, _) in enumerate(_HELP_CATEGORIES):
        builder.add_row(label, f"panel:help:cat:{i}")
    builder.add_row("Close", "panel:help:close")
    return builder.build()


def _build_category_keyboard() -> list:
    builder = InlinePanelBuilder()
    builder.add_buttons(("Back", "panel:help:back"), ("Close", "panel:help:close"))
    return builder.build()


async def _help_panel_handler(event, extra: str) -> None:
    logger.info("HELP STEP 13 - callback received: extra='%s'", extra)
    if extra == "close":
        try:
            await event.delete()
            logger.info("HELP STEP 13 - close: message deleted")
        except Exception:
            logger.exception("HELP STEP 13 - close: delete FAILED")
        return
    if extra == "back":
        try:
            await event.edit(_build_main_menu_text(), buttons=_build_main_menu_keyboard())
            logger.info("HELP STEP 13 - back: edited to main menu")
        except Exception:
            logger.exception("HELP STEP 13 - back: edit FAILED")
        return
    if extra.startswith("cat:"):
        idx_str = extra[4:]
        if idx_str.isdigit():
            idx = int(idx_str)
            if 0 <= idx < len(_HELP_CATEGORIES):
                try:
                    await event.edit(
                        _build_category_page_text(idx),
                        buttons=_build_category_keyboard(),
                    )
                    logger.info("HELP STEP 13 - cat:%d: edited to category page", idx)
                except Exception:
                    logger.exception("HELP STEP 13 - cat:%d: edit FAILED", idx)
                return
    try:
        await event.edit(_build_main_menu_text(), buttons=_build_main_menu_keyboard())
        logger.info("HELP STEP 13 - default: edited to main menu")
    except Exception:
        logger.exception("HELP STEP 13 - default: edit FAILED")


async def _help_inline_builder(event, extra: str) -> list:
    from backend.helper.inline_engine import make_result
    logger.info("HELP STEP 8b - inline builder entered: extra='%s'", extra)
    text = _build_main_menu_text()
    buttons = _build_main_menu_keyboard()
    from telethon.tl import types
    msg = types.InputBotInlineMessageTextAuto(
        message=text,
        reply_markup=types.ReplyInlineMarkup(rows=buttons) if buttons else None,
    )
    result = types.InputBotInlineResult(
        id="0",
        type="article",
        title="LifeOS Command Center",
        send_message=msg,
    )
    logger.info("HELP STEP 8b - inline builder returning 1 result with %d button rows", len(buttons))
    return [result]


def _register_help_panel() -> None:
    register_panel("help", _help_panel_handler)


# ── Health dashboard ────────────────────────────────────────────────────


def _format_uptime(uptime_s):
    if uptime_s is None or uptime_s < 0:
        return "unknown"
    hours = int(uptime_s // 3600)
    minutes = int((uptime_s % 3600) // 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _format_age(age_s):
    if age_s is None:
        return "—"
    if age_s < 60:
        return f"{int(age_s)}s ago"
    m = int(age_s // 60)
    if m < 60:
        return f"{m}m ago"
    h = m // 60
    return f"{h}h {m % 60}m ago"


def _indicator(ok):
    return "🟢" if ok else "🔴"


def _build_health_report(snap):
    process_ok = snap.get("process_alive", False)
    telegram_ok = snap.get("telethon_connected", False)
    supervisor_ok = snap.get("supervisor_ok", False)
    bio_cron_ok = snap.get("bio_cron_ok", False)
    watchdog_ok = snap.get("watchdog_ok", False)
    heartbeat_age = snap.get("heartbeat_age_s")
    uptime_s = snap.get("uptime_s")
    restart_count = snap.get("restart_count", 0)
    last_watchdog = snap.get("last_watchdog_check_s")
    last_tg_event = snap.get("last_telethon_event_s")
    last_bio = snap.get("last_bio_update_s")
    status = snap.get("status", "unknown")

    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        mem_mb = usage.ru_maxrss / 1024
        cpu_s = usage.ru_utime + usage.ru_stime
    except Exception:
        mem_mb = None
        cpu_s = None

    try:
        all_tasks = asyncio.all_tasks()
        running = sum(1 for t in all_tasks if not t.done())
        pending = sum(1 for t in all_tasks if not t.done())
        locked = 0
    except Exception:
        running = None
        pending = None
        locked = None

    db_ok = db_client.is_available()

    if heartbeat_age is not None and heartbeat_age <= 15.0:
        hb_status = "OK"
    elif heartbeat_age is not None:
        hb_status = "WARNING"
    else:
        hb_status = "ERROR"

    lines = ["🩺 **LifeOS Health Dashboard**", ""]

    lines.append(f"{_indicator(process_ok)} **Process**: {'Alive' if process_ok else 'Dead'}")
    if mem_mb is not None:
        lines.append(f"   • Memory: `{mem_mb:.1f} MB`")
    if cpu_s is not None:
        lines.append(f"   • CPU: `{cpu_s:.2f}s`")

    lines.append(f"{_indicator(telegram_ok)} **Telegram**: {'Connected' if telegram_ok else 'Disconnected'}")
    lines.append(f"   • Last event: {_format_age(last_tg_event)}")

    lines.append(f"{_indicator(supervisor_ok)} **Supervisor**: {'Running' if supervisor_ok else 'Stopped'}")

    lines.append(f"{_indicator(watchdog_ok)} **Watchdog**: {'Running' if watchdog_ok else 'Stopped'}")
    lines.append(f"   • Last check: {_format_age(last_watchdog)}")

    lines.append(f"{_indicator(bio_cron_ok)} **Bio Cron**: {'Running' if bio_cron_ok else 'Stopped'}")
    lines.append(f"   • Last update: {_format_age(last_bio)}")

    hb_icon = "🟢" if hb_status == "OK" else ("🟡" if hb_status == "WARNING" else "🔴")
    lines.append(f"{hb_icon} **Heartbeat**: {hb_status}")
    if heartbeat_age is not None:
        lines.append(f"   • Age: `{int(heartbeat_age)}s`")

    lines.append(f"{'🟢' if restart_count == 0 else '🟡'} **Restarts**: `{restart_count}`")

    if running is not None:
        lines.append(f"{'🟢' if running < 20 else '🟡'} **Running Tasks**: `{running}`")
    if pending is not None:
        lines.append(f"{'🟢' if pending < 20 else '🟡'} **Pending Async**: `{pending}`")
    if locked is not None:
        lines.append(f"{'🟢' if locked == 0 else '🟡'} **Locked Tasks**: `{locked}`")

    lines.append(f"{_indicator(db_ok)} **Database**: {'Available' if db_ok else 'Fallback'}")

    lines.append(f"{'🟢' if uptime_s and uptime_s > 0 else '🔴'} **Uptime**: `{_format_uptime(uptime_s)}`")

    lines.append("")
    if status == "ok":
        lines.append("_Everything looks healthy._")
    else:
        lines.append("_⚠️ Issues detected — needs attention._")

    return "\n".join(lines)


async def _health_inline_builder(event, extra: str) -> list:
    from telethon.tl import types
    logger.info("[HEALTH_BUILDER] entered: extra='%s'", extra)
    snap = health.snapshot()
    report = _build_health_report(snap)
    builder = InlinePanelBuilder()
    builder.add_row("Refresh", "action:health_refresh")
    builder.add_row("Close", "panel:help:close")
    buttons = builder.build()
    msg = types.InputBotInlineMessageTextAuto(
        message=report,
        reply_markup=types.ReplyInlineMarkup(rows=buttons) if buttons else None,
    )
    result = types.InputBotInlineResult(
        id="0",
        type="article",
        title="LifeOS Health Dashboard",
        send_message=msg,
    )
    logger.info("[HEALTH_BUILDER] returning 1 result")
    return [result]


async def _kill_inline_builder(event, extra: str) -> list:
    from telethon.tl import types
    logger.info("[KILL_BUILDER] entered: extra='%s'", extra)
    snap = health.snapshot()
    self_client = _get_self_client()
    report = diagnostics.build_diagnostic_report(
        self_client, bio_engine, db_client, snap
    )
    recovery = await diagnostics.recover_stalled(
        self_client, 0, _resolve_tz(), bio_engine, db_client
    )
    full_text = report + recovery
    builder = InlinePanelBuilder()
    builder.add_row("Close", "panel:help:close")
    buttons = builder.build()
    msg = types.InputBotInlineMessageTextAuto(
        message=full_text,
        reply_markup=types.ReplyInlineMarkup(rows=buttons) if buttons else None,
    )
    result = types.InputBotInlineResult(
        id="0",
        type="article",
        title="LifeOS Diagnostics",
        send_message=msg,
    )
    logger.info("[KILL_BUILDER] returning 1 result")
    return [result]


async def _logs_inline_builder(event, extra: str) -> list:
    from telethon.tl import types
    logger.info("[LOGS_BUILDER] entered: extra='%s'", extra)
    limit = 20
    if extra and extra.isdigit():
        limit = min(int(extra), 500)
    elif extra == "errors":
        limit = 20
    events_list = diagnostics.filter_events(
        limit=limit,
        errors_only=(extra == "errors"),
    )
    text = diagnostics.format_events(events_list)
    builder = InlinePanelBuilder()
    builder.add_row("Errors Only", "action:logs_errors")
    builder.add_row("Last 50", "action:logs_50")
    builder.add_row("Close", "panel:help:close")
    buttons = builder.build()
    msg = types.InputBotInlineMessageTextAuto(
        message=text,
        reply_markup=types.ReplyInlineMarkup(rows=buttons) if buttons else None,
    )
    result = types.InputBotInlineResult(
        id="0",
        type="article",
        title="LifeOS Event Log",
        send_message=msg,
    )
    logger.info("[LOGS_BUILDER] returning 1 result")
    return [result]


def _get_self_client():
    from backend.helper.inline_engine import _self_client
    return _self_client


async def _safe_edit(event, text: str) -> None:
    """Edit a message, splitting if it exceeds Telegram's limit."""
    parts = diagnostics.split_message(text)
    for i, part in enumerate(parts):
        if i == 0:
            await event.edit(part)
        else:
            await event.reply(part)


def register(client, owner_id: int):

    # ── .ping ──────────────────────────────────────────────────────────
    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.ping$"))
    async def ping(event):
        if not is_owner(event, owner_id):
            return
        try:
            await event.edit("PONG")
        except Exception as exc:
            logger.warning("ping edit failed: %s", exc)

    # ── .id ────────────────────────────────────────────────────────────
    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.id$"))
    async def id_cmd(event):
        if not is_owner(event, owner_id):
            return
        try:
            chat_id = event.chat_id
            msg_id = event.message.id
            reply = await event.message.get_reply_message()
            lines = [f"**Chat ID:** `{chat_id}`", f"**Msg ID:** `{msg_id}`"]
            if reply:
                lines.append(f"**Reply Msg ID:** `{reply.id}`")
                lines.append(f"**Reply Sender ID:** `{reply.sender_id}`")
            await event.edit("\n".join(lines))
        except Exception as exc:
            logger.warning("id_cmd failed: %s", exc)

    _register_help_panel()
    register_inline_builder("help", _help_inline_builder)
    register_inline_builder("health", _health_inline_builder)
    register_inline_builder("kill", _kill_inline_builder)
    register_inline_builder("logs", _logs_inline_builder)
    logger.info("[MISC] All inline builders registered: help, health, kill, logs")

    # ── .help — inline panel via Inline Mode (INSTRUMENTED) ────────────
    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.help$"))
    async def help_cmd(event):
        logger.info("HELP STEP 1 - .help handler entered (chat_id=%s, msg_id=%s)", event.chat_id, event.message.id)
        try:
            if not is_owner(event, owner_id):
                logger.warning("HELP STEP 1 - owner check FAILED (sender_id=%s, owner_id=%s)", event.sender_id, owner_id)
                return
            logger.info("HELP STEP 1 - owner check passed (sender_id=%s)", event.sender_id)

            logger.info("HELP STEP 2 - get_client()")
            helper = get_client()
            logger.info("HELP STEP 2 - get_client() returned: helper=%s", "None" if helper is None else "connected")
            if helper is None:
                logger.warning("HELP STEP 2 - helper is None — REASON: no BOT_TOKEN set or build_helper() failed. Falling back to edit-in-place.")
                try:
                    await event.edit(_build_main_menu_text())
                except Exception:
                    logger.exception("HELP STEP 2 - edit-in-place fallback FAILED")
                return

            logger.info("HELP STEP 3 - send_inline_panel(client, chat_id=%s, query='help')", event.chat_id)
            panel_ok = await send_inline_panel(client, event.chat_id, "help")
            logger.info("HELP STEP 3 - send_inline_panel returned: ok=%s", panel_ok)

            if not panel_ok:
                logger.warning("HELP STEP 3 - send_inline_panel returned False — REASON: see HELP STEP logs above for the exact failure. Falling back to edit-in-place.")
                try:
                    await event.edit(_build_main_menu_text())
                except Exception:
                    logger.exception("HELP STEP 3 - edit-in-place fallback FAILED")
                return

            logger.info("HELP STEP 11 - deleting trigger message (msg_id=%s)", event.message.id)
            try:
                await event.delete()
                logger.info("HELP STEP 11 - trigger message deleted")
            except Exception:
                logger.exception("HELP STEP 11 - event.delete() FAILED")

            logger.info("HELP STEP 1 - handler finished (success)")
        except Exception:
            logger.exception("HELP STEP 1 - unhandled exception in .help handler")
            raise

    # ── .health — inline panel via Inline Mode (INSTRUMENTED) ──────────
    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.health$"))
    async def health_cmd(event):
        logger.info("[HEALTH] handler entered (chat_id=%s)", event.chat_id)
        try:
            if not is_owner(event, owner_id):
                return
            helper = get_client()
            if helper is None:
                logger.info("[HEALTH] helper None — edit-in-place fallback")
                snap = health.snapshot()
                report = _build_health_report(snap)
                await _safe_edit(event, report)
                diagnostics.record_event("health", "snapshot", 0, "SUCCESS")
                return
            logger.info("[HEALTH] sending inline panel")
            panel_ok = await send_inline_panel(client, event.chat_id, "health")
            logger.info("[HEALTH] send_inline_panel returned ok=%s", panel_ok)
            if panel_ok:
                try:
                    await event.delete()
                except Exception as del_exc:
                    logger.warning("[HEALTH] event.delete() failed: %s", del_exc)
                diagnostics.record_event("health", "snapshot", 0, "SUCCESS")
            else:
                logger.warning("[HEALTH] inline failed — fallback to edit-in-place")
                snap = health.snapshot()
                report = _build_health_report(snap)
                await _safe_edit(event, report)
        except Exception:
            logger.exception("[HEALTH] unhandled exception")
            raise

    # ── .kill — diagnostic snapshot + recovery (INSTRUMENTED) ──────────
    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.kill$"))
    async def kill_cmd(event):
        logger.info("[KILL] handler entered (chat_id=%s)", event.chat_id)
        try:
            if not is_owner(event, owner_id):
                return
            helper = get_client()
            if helper is None:
                logger.info("[KILL] helper None — edit-in-place fallback")
                await event.edit("⏳ Collecting diagnostics...")
                snap = health.snapshot()
                report = diagnostics.build_diagnostic_report(
                    client, bio_engine, db_client, snap
                )
                recovery = await diagnostics.recover_stalled(
                    client, owner_id, _resolve_tz(), bio_engine, db_client
                )
                await _safe_edit(event, report + recovery)
                diagnostics.record_event("diagnostics", "kill", 0, "SUCCESS")
                return
            logger.info("[KILL] sending inline panel")
            panel_ok = await send_inline_panel(client, event.chat_id, "kill")
            logger.info("[KILL] send_inline_panel returned ok=%s", panel_ok)
            if panel_ok:
                try:
                    await event.delete()
                except Exception as del_exc:
                    logger.warning("[KILL] event.delete() failed: %s", del_exc)
                diagnostics.record_event("diagnostics", "kill", 0, "SUCCESS")
            else:
                logger.warning("[KILL] inline failed — fallback to edit-in-place")
                await event.edit("⏳ Collecting diagnostics...")
                snap = health.snapshot()
                report = diagnostics.build_diagnostic_report(
                    client, bio_engine, db_client, snap
                )
                recovery = await diagnostics.recover_stalled(
                    client, owner_id, _resolve_tz(), bio_engine, db_client
                )
                await _safe_edit(event, report + recovery)
        except Exception:
            logger.exception("[KILL] unhandled exception")
            raise

    # ── .logs — diagnostic event viewer (INSTRUMENTED) ─────────────────
    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.logs(?:\s+(.+))?$"))
    async def logs_cmd(event):
        logger.info("[LOGS] handler entered (chat_id=%s)", event.chat_id)
        try:
            if not is_owner(event, owner_id):
                return

            arg = (event.pattern_match.group(1) or "").strip()
            query = "logs"
            if arg:
                if arg.lower() == "errors":
                    query = "logs:errors"
                elif arg.lower().startswith("module "):
                    query = "logs"
                elif arg.isdigit():
                    query = f"logs:{arg}"

            helper = get_client()
            if helper is None:
                logger.info("[LOGS] helper None — edit-in-place fallback")
                limit = 20
                errors_only = False
                if arg:
                    if arg.lower() == "errors":
                        errors_only = True
                    elif arg.lower().startswith("module "):
                        pass
                    elif arg.isdigit():
                        limit = min(int(arg), 500)
                events_list = diagnostics.filter_events(
                    limit=limit, errors_only=errors_only
                )
                text = diagnostics.format_events(events_list)
                await _safe_edit(event, text)
                return

            logger.info("[LOGS] sending inline panel (query='%s')", query)
            panel_ok = await send_inline_panel(client, event.chat_id, query)
            logger.info("[LOGS] send_inline_panel returned ok=%s", panel_ok)
            if panel_ok:
                try:
                    await event.delete()
                except Exception as del_exc:
                    logger.warning("[LOGS] event.delete() failed: %s", del_exc)
            else:
                logger.warning("[LOGS] inline failed — fallback to edit-in-place")
                limit = 20
                errors_only = False
                if arg:
                    if arg.lower() == "errors":
                        errors_only = True
                    elif arg.lower().startswith("module "):
                        pass
                    elif arg.isdigit():
                        limit = min(int(arg), 500)
                events_list = diagnostics.filter_events(
                    limit=limit, errors_only=errors_only
                )
                text = diagnostics.format_events(events_list)
                await _safe_edit(event, text)
        except Exception:
            logger.exception("[LOGS] unhandled exception")
            raise
