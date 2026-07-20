"""
Helper Bot — Inline Mode + Callback Engine infrastructure.

The helper bot is a *secondary* Telegram client (bot token, not user session)
that handles ONLY:
  - Inline Mode (answering InlineQuery events with panel results)
  - Callback queries (button presses on inline messages)

The self-bot (Telethon StringSession) remains the brain — it processes
commands and business logic. The helper bot is purely a presentation layer.

Architecture:
  - ``build_helper(bot_token)`` — creates and connects the Telethon
    ``TelegramClient`` for the bot. Returns ``None`` if no token is set.
  - ``register_callback_handlers(client, owner_id)`` — wires the
    callback-query router onto the helper client.
  - ``register_inline_handler(client, owner_id)`` — wires the InlineQuery
    handler onto the helper client.
  - ``panels`` module — provides ``InlinePanelBuilder`` for constructing
    inline keyboards, plus ``register_panel``, ``register_action``, and
    ``register_input`` for registering handlers.
  - ``inline_engine`` module — the core Inline Mode engine: trigger,
    register_inline_builder, make_result, make_button_rows.
  - ``inline_sender`` module — self-bot side: send_inline_panel,
    register_input_listener.
  - ``input_state`` module — pending input state management for Type B
    commands requiring user text input.

Lifecycle:
  - Started in ``main.py`` Phase 3.5 (after self-bot handlers, before web).
  - Stopped in shutdown (disconnected cleanly, zero orphans).
  - If ``BOT_TOKEN`` is not set, the helper bot is simply skipped — the
    self-bot continues to work without inline UI.
"""
from backend.helper.client import build_helper, is_available, get_bot_username
from backend.helper.panels import (
    InlinePanelBuilder,
    register_panel,
    get_panel,
    register_action,
    get_action,
    register_input,
    get_input,
)
from backend.helper.inline_engine import (
    register_inline_builder,
    get_inline_builder,
    trigger,
    make_result,
    make_button_rows,
)
from backend.helper.inline_sender import (
    send_inline_panel,
    register_input_listener,
)
from backend.helper.tmp_context import set_context, get_context, clear_context

__all__ = [
    "build_helper",
    "is_available",
    "get_bot_username",
    "InlinePanelBuilder",
    "register_panel",
    "get_panel",
    "register_action",
    "get_action",
    "register_input",
    "get_input",
    "register_inline_builder",
    "get_inline_builder",
    "trigger",
    "make_result",
    "make_button_rows",
    "send_inline_panel",
    "register_input_listener",
    "set_context",
    "get_context",
    "clear_context",
]
