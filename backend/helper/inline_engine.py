"""
Inline Engine — the core of the Inline Mode architecture.

The helper bot answers InlineQuery events by generating panel results.
The self-bot triggers inline mode via ``client.inline_query(bot_username, query)``
and auto-sends the first result.

Flow:
  1. Self-bot command (e.g. ``.help``) fires.
  2. Self-bot calls ``inline_engine.trigger(client, chat_id, query)``.
  3. Helper bot receives InlineQuery, generates results via registered
     inline result builders.
  4. Self-bot auto-sends the first result — message appears as
     ``OwnerName via @HelperBot`` with inline buttons.
  5. Callbacks work exactly as before — no changes to callback architecture.

Inline result builders are registered per "query key". The query string
passed to the helper bot's inline mode encodes which panel to show:
  ``<panel_id>`` or ``<panel_id>:<extra>``

The builder returns a list of InputBotInlineResult objects (usually one).
"""
import asyncio
import logging
from typing import Awaitable, Callable, Any

from telethon import events, types
from telethon.tl.custom import Button

from backend.bot.handlers.guard import is_owner
from backend.helper.context import truncate_callback_data

logger = logging.getLogger(__name__)

InlineResultBuilder = Callable[[events.InlineQuery.Event, str], Awaitable[list]]

_builders: dict[str, InlineResultBuilder] = {}
_self_client = None
_helper_username: str = ""
_owner_id: int = 0


def set_self_client(client) -> None:
    global _self_client
    _self_client = client


def set_helper_username(username: str) -> None:
    global _helper_username
    username = username.lstrip("@") if username else ""
    _helper_username = username


def set_owner_id(owner_id: int) -> None:
    global _owner_id
    _owner_id = owner_id


def get_helper_username() -> str:
    return _helper_username


def register_inline_builder(query_key: str, builder: InlineResultBuilder) -> None:
    """Register a builder that returns inline results for a query key."""
    _builders[query_key] = builder
    logger.info("[INLINE] Builder registered: key='%s' (total=%d)", query_key, len(_builders))


def get_inline_builder(query_key: str) -> InlineResultBuilder | None:
    return _builders.get(query_key)


async def trigger(self_client, chat_id: int, query: str) -> bool:
    """Trigger inline mode and auto-send the first result.

    Returns True on success, False on failure.
    """
    logger.info("[INLINE] trigger() entered: chat_id=%s, query='%s', username='%s'",
                chat_id, query, _helper_username)

    if not _helper_username:
        logger.error("[INLINE] trigger() ABORT: helper username not set (_helper_username='%s')", _helper_username)
        return False

    logger.info("[INLINE] trigger() about to call self_client.inline_query('@%s', '%s')", _helper_username, query)
    try:
        results = await self_client.inline_query(_helper_username, query)
        logger.info("[INLINE] trigger() inline_query returned: results_count=%d", len(results) if results else 0)

        if results:
            logger.info("[INLINE] trigger() about to send results[0] to chat_id=%s", chat_id)
            try:
                await results[0].send(chat_id)
                logger.info("[INLINE] trigger() results[0].send() succeeded")
                return True
            except Exception as send_exc:
                logger.exception("[INLINE] trigger() results[0].send() FAILED: %s", send_exc)
                return False
        else:
            logger.warning("[INLINE] trigger() no results for query='%s' — builder returned empty list", query)
            return False
    except Exception as exc:
        logger.exception("[INLINE] trigger() inline_query() FAILED: %s", exc)
        return False


def register_inline_handler(helper_client, owner_id: int) -> None:
    """Wire the InlineQuery handler onto the helper bot client."""

    @helper_client.on(events.InlineQuery())
    async def _inline_router(event):
        logger.info("[INLINE_QUERY] handler entered: query='%s', user_id=%s",
                    event.query, event.sender_id)

        if not is_owner(event, owner_id):
            logger.info("[INLINE_QUERY] owner check FAILED: sender_id=%s, owner_id=%s",
                        event.sender_id, owner_id)
            try:
                await event.answer([])
            except Exception:
                pass
            return

        logger.info("[INLINE_QUERY] owner check passed")

        raw_query = event.query.strip()
        if not raw_query:
            logger.warning("[INLINE_QUERY] empty query — answering with empty list")
            try:
                await event.answer([])
            except Exception:
                pass
            return

        parts = raw_query.split(":", 1)
        panel_id = parts[0]
        extra = parts[1] if len(parts) > 1 else ""

        logger.info("[INLINE_QUERY] parsed: panel_id='%s', extra='%s'", panel_id, extra)

        builder = get_inline_builder(panel_id)
        if builder is None:
            logger.warning("[INLINE_QUERY] no builder for panel_id='%s' (registered: %s)",
                           panel_id, list(_builders.keys()))
            try:
                await event.answer([])
            except Exception:
                pass
            return

        logger.info("[INLINE_QUERY] builder found for '%s' — invoking", panel_id)
        try:
            results = await builder(event, extra)
            logger.info("[INLINE_QUERY] builder returned: results_count=%d",
                        len(results) if results else 0)

            if not results:
                logger.warning("[INLINE_QUERY] builder returned empty list for panel_id='%s'", panel_id)

            logger.info("[INLINE_QUERY] about to call event.answer(results)")
            await event.answer(results)
            logger.info("[INLINE_QUERY] event.answer() succeeded")
        except Exception as exc:
            logger.exception("[INLINE_QUERY] builder '%s' FAILED: %s", panel_id, exc)
            try:
                await event.answer([])
            except Exception:
                pass


def make_result(
    title: str,
    description: str = "",
    panel_id: str = "",
    extra: str = "",
    buttons: list | None = None,
    query_id: int = 0,
) -> types.InputBotInlineResult:
    """Build a single InputBotInlineResult with a text message and buttons.

    The result body is a InputBotInlineMessageTextAuto (auto-detected type).
    """
    body_text = title
    if description:
        body_text = f"{title}\n\n{description}"

    if buttons is None:
        buttons = []

    msg = types.InputBotInlineMessageTextAuto(
        message=body_text,
        reply_markup=types.ReplyInlineMarkup(rows=buttons) if buttons else None,
    )

    return types.InputBotInlineResult(
        id="0",
        type="article",
        title=title.split("\n")[0][:255] if title else "LifeOS",
        send_message=msg,
    )


def make_button_rows(buttons_data: list[list[tuple[str, str]]]) -> list:
    """Convert a list of button rows into ReplyInlineMarkup rows.

    Each row is a list of (text, callback_data) tuples.
    """
    rows = []
    for row_data in buttons_data:
        row_buttons = []
        for text, data in row_data:
            row_buttons.append(
                types.KeyboardButtonCallback(
                    text=text,
                    data=truncate_callback_data(data).encode("utf-8"),
                )
            )
        rows.append(row_buttons)
    return rows
