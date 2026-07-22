"""
Discover handler — .list and .find commands.

  .list              — Inline panel: recent saves (first 10).
  .find <query>      — Inline panel: search saved items by tag/content.
  .find              — Inline panel: search input prompt.

Inline Mode:
  - .list (no args) → inline panel listing recent saves.
  - .find (no args) → input prompt for search query.
  - .find <query> → search and display matching items inline.
"""
import logging
from telethon import events

from backend.bot.handlers.guard import is_owner
from backend.db import client as db_client
from backend.diagnostics import record_event
from backend.helper import (
    InlinePanelBuilder,
    register_panel,
    register_inline_builder,
    register_input,
    send_inline_panel,
)
from backend.helper.client import get_client

logger = logging.getLogger(__name__)


def _format_save_row(row: dict) -> str:
    code = row.get("save_code") or row.get("short_code") or "?"
    media_type = row.get("media_type") or row.get("save_type") or "unknown"
    tags = row.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    tag_str = " ".join(f"#{t}" for t in tags[:3]) if tags else ""
    created = row.get("created_at") or ""
    if isinstance(created, str) and len(created) >= 10:
        created = created[:10]
    return f"`{code}` · {media_type} · {created} {tag_str}".strip()


async def _list_inline_builder(event, extra: str) -> list:
    from telethon.tl import types
    rows_data = []
    try:
        items, total = db_client.list_saves(0, limit=10, offset=0)
        rows_data = items or []
    except Exception as exc:
        logger.warning("list_saves failed for inline: %s", exc)
    lines = ["**Recent Saves**\n"]
    if not rows_data:
        lines.append("_No saved items yet._")
    else:
        for row in rows_data:
            lines.append(_format_save_row(row))
    lines.append(f"\n_Total: {total if 'total' in dir() else len(rows_data)}_" if rows_data else "")
    text = "\n".join(lines)
    builder = InlinePanelBuilder()
    builder.add_row("Close", "panel:help:close")
    buttons = builder.build()
    msg = types.InputBotInlineMessageTextAuto(
        message=text,
        reply_markup=types.ReplyInlineMarkup(rows=buttons) if buttons else None,
    )
    result = types.InputBotInlineResult(
        id="0",
        type="article",
        title="Recent Saves",
        send_message=msg,
    )
    return [result]


async def _find_inline_builder(event, extra: str) -> list:
    from telethon.tl import types
    text = "**Search Saved Items**\n\nEnter a search query (tag, media type, or keyword):\n\n_Reply below._"
    builder = InlinePanelBuilder()
    builder.add_row("Close", "panel:help:close")
    buttons = builder.build()
    msg = types.InputBotInlineMessageTextAuto(
        message=text,
        reply_markup=types.ReplyInlineMarkup(rows=buttons) if buttons else None,
    )
    result = types.InputBotInlineResult(
        id="0",
        type="article",
        title="Search Saved Items",
        send_message=msg,
    )
    return [result]


async def _find_input_handler(text, chat_id, msg_id, inline_chat_id, inline_msg_id):
    from backend.helper.inline_engine import _owner_id
    query = text.strip()
    if not query:
        result = "⚠️ Search query cannot be empty."
    else:
        try:
            items, total = db_client.search_saves(_owner_id, query, limit=20)
        except Exception as exc:
            logger.error("search_saves failed: %s", exc)
            items, total = [], 0
            result = f"❌ Search failed: {exc}"
        else:
            lines = [f"**Search: `{query}`**\n"]
            if not items:
                lines.append("_No results found._")
            else:
                for row in items:
                    lines.append(_format_save_row(row))
            lines.append(f"\n_Found {total} results_" if total else "")
            result = "\n".join(lines)
    builder = InlinePanelBuilder()
    builder.add_row("Close", "panel:help:close")
    helper = get_client()
    if helper and inline_chat_id and inline_msg_id:
        try:
            await helper.edit_message(inline_chat_id, inline_msg_id, result, buttons=builder.build())
            await helper.delete_messages(chat_id, [msg_id])
        except Exception as exc:
            logger.warning("find inline edit failed: %s", exc)


def register(client, owner_id: int):

    register_panel("list", _list_inline_builder)
    register_inline_builder("list", _list_inline_builder)
    register_panel("find", _find_inline_builder)
    register_inline_builder("find", _find_inline_builder)
    register_input("find", "query", {
        "handler": _find_input_handler,
        "prompt": "**Search Saved Items**\n\nEnter a search query (tag, media type, or keyword):\n\n_Reply below._",
    })

    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.list(?:\s+(.+))?$"))
    async def list_cmd(event):
        if not is_owner(event, owner_id):
            return
        arg = (event.pattern_match.group(1) or "").strip()
        if arg:
            await event.edit("⚠️ `.list` takes no arguments. Use `.find <query>` to search.")
            return
        helper = get_client()
        if helper is None:
            try:
                items, total = db_client.list_saves(owner_id, limit=10, offset=0)
            except Exception as exc:
                await event.edit(f"❌ DB error: {exc}")
                return
            lines = ["**Recent Saves**\n"]
            if not items:
                lines.append("_No saved items yet._")
            else:
                for row in items:
                    lines.append(_format_save_row(row))
            lines.append(f"\n_Total: {total}_" if items else "")
            await event.edit("\n".join(lines))
            return
        try:
            await event.delete()
            await send_inline_panel(client, event.chat_id, "list")
        except Exception as exc:
            logger.warning("list inline send failed: %s", exc)

    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.find(?:\s+(.+))?$"))
    async def find_cmd(event):
        if not is_owner(event, owner_id):
            return
        arg = (event.pattern_match.group(1) or "").strip()
        if arg:
            try:
                items, total = db_client.search_saves(owner_id, arg, limit=20)
            except Exception as exc:
                await event.edit(f"❌ Search failed: {exc}
                return
            lines = [f"**Search: `{arg}`**\n"]
            if not items:
                lines.append("_No results found._")
            else:
                for row in items:
                    lines.append(_format_save_row(row))
            lines.append(f"\n_Found {total} results_" if total else "")
            await event.edit("\n".join(lines))
            return
        helper = get_client()
        if helper is None:
            await event.edit("⚠️ Usage: `.find <query>`")
            return
        try:
            await event.delete()
            await send_inline_panel(client, event.chat_id, "find")
        except Exception as exc:
            logger.warning("find inline send failed: %s", exc)
