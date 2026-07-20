"""
Inline panel system for the helper bot.

Provides:
  - ``InlinePanelBuilder`` — builds inline keyboards with rows of buttons.
  - ``register_panel(panel_id, handler)`` — registers a callback handler
    for a panel ID.
  - ``get_panel(panel_id)`` — retrieves a registered panel handler.
  - ``register_action(action_id, handler)`` — registers an action handler
    for immediate execution (Type A commands).
  - ``register_input(panel_id, input_id, handler, prompt)`` — registers
    an input handler for Type B commands requiring user text input.
  - ``register_callback_handlers(client, owner_id)`` — wires the callback
    query router onto the helper bot client.

Callback data format:
  - Panel navigation:  ``panel:<panel_id>:<extra>``
  - Action execution:  ``action:<action_id>:<extra>``
  - Input request:    ``input:<panel_id>:<input_id>``

The callback router dispatches based on the prefix. Panel handlers
edit the inline message in-place. Action handlers execute logic and
then edit the message with the result. Input handlers set a pending
input state and edit the message to show a prompt.
"""
import logging
from typing import Awaitable, Callable, Any

from telethon import events
from telethon.tl.custom import Button

from backend.bot.handlers.guard import is_owner
from backend.helper.context import truncate_callback_data
from backend.helper.input_state import set_pending

logger = logging.getLogger(__name__)

PanelHandler = Callable[[events.CallbackQuery.Event, str], Awaitable[None]]
ActionHandler = Callable[[events.CallbackQuery.Event, str], Awaitable[str]]
InputConfig = dict[str, Any]

_panels: dict[str, PanelHandler] = {}
_actions: dict[str, ActionHandler] = {}
_inputs: dict[str, dict[str, InputConfig]] = {}


class InlinePanelBuilder:
    """Builds inline keyboard layouts for the helper bot."""

    def __init__(self):
        self._rows: list[list[Any]] = []

    def add_row(self, text: str, callback_data: str) -> "InlinePanelBuilder":
        self._rows.append([Button.inline(text, truncate_callback_data(callback_data))])
        return self

    def add_buttons(self, *buttons: tuple[str, str]) -> "InlinePanelBuilder":
        row = [Button.inline(text, truncate_callback_data(data)) for text, data in buttons]
        self._rows.append(row)
        return self

    def add_url(self, text: str, url: str) -> "InlinePanelBuilder":
        self._rows.append([Button.url(text, url)])
        return self

    def build(self) -> list[list[Any]]:
        return self._rows


def register_panel(panel_id: str, handler: PanelHandler) -> None:
    _panels[panel_id] = handler
    logger.debug("Panel registered: %s", panel_id)


def get_panel(panel_id: str) -> PanelHandler | None:
    return _panels.get(panel_id)


def register_action(action_id: str, handler: ActionHandler) -> None:
    """Register an action handler for Type A (immediate execution) commands.

    The handler receives the callback event and extra data, and returns
    a result string to display in the panel.
    """
    _actions[action_id] = handler
    logger.debug("Action registered: %s", action_id)


def get_action(action_id: str) -> ActionHandler | None:
    return _actions.get(action_id)


def register_input(panel_id: str, input_id: str, handler: InputConfig) -> None:
    """Register an input handler for Type B (requires user input) commands.

    The ``handler`` dict contains:
      - ``handler``: async callable(text, chat_id, msg_id) -> None
      - ``prompt``: str to display when waiting for input
    """
    if panel_id not in _inputs:
        _inputs[panel_id] = {}
    _inputs[panel_id][input_id] = handler
    logger.debug("Input registered: %s/%s", panel_id, input_id)


def get_input(panel_id: str, input_id: str) -> InputConfig | None:
    return _inputs.get(panel_id, {}).get(input_id)


def register_callback_handlers(client, owner_id: int) -> None:
    """Wire the callback query router onto the helper bot client.

    Dispatches based on callback data prefix:
      - ``panel:`` → panel navigation handler
      - ``action:`` → action execution handler (Type A)
      - ``input:`` → input state setup (Type B)
    """

    @client.on(events.CallbackQuery())
    async def _callback_router(event):
        if not is_owner(event, owner_id):
            return

        data = event.data.decode("utf-8") if event.data else ""
        if not data:
            return

        try:
            if data.startswith("panel:"):
                await _handle_panel(event, data[6:])
            elif data.startswith("action:"):
                await _handle_action(event, data[7:])
            elif data.startswith("input:"):
                await _handle_input(event, data[6:], owner_id)
        except Exception as exc:
            logger.error("Callback router error (data=%s): %s", data, exc)


async def _handle_panel(event, remainder: str) -> None:
    parts = remainder.split(":", 1)
    panel_id = parts[0]
    extra = parts[1] if len(parts) > 1 else ""

    handler = get_panel(panel_id)
    if handler is None:
        logger.warning("No panel registered for id: %s", panel_id)
        return

    await handler(event, extra)


async def _handle_action(event, remainder: str) -> None:
    parts = remainder.split(":", 1)
    action_id = parts[0]
    extra = parts[1] if len(parts) > 1 else ""

    handler = get_action(action_id)
    if handler is None:
        logger.warning("No action registered for id: %s", action_id)
        return

    result = await handler(event, extra)
    if result is None:
        return
    if isinstance(result, tuple):
        text, buttons = result
    else:
        text, buttons = result, []
    if text:
        try:
            await event.edit(text, buttons=buttons)
        except Exception as exc:
            logger.warning("Action result edit failed: %s", exc)


async def _handle_input(event, remainder: str, owner_id: int) -> None:
    parts = remainder.split(":", 1)
    panel_id = parts[0]
    input_id = parts[1] if len(parts) > 1 else ""

    input_cfg = get_input(panel_id, input_id)
    if input_cfg is None:
        logger.warning("No input registered: %s/%s", panel_id, input_id)
        return

    prompt = input_cfg.get("prompt", "Enter input:")
    handler = input_cfg.get("handler")

    if handler is None:
        return

    chat_id = event.chat_id
    inline_msg_id = event.msg_id or 0
    set_pending(
        owner_id, panel_id, handler, chat_id, prompt,
        inline_chat_id=chat_id, inline_msg_id=inline_msg_id,
    )

    builder = InlinePanelBuilder()
    builder.add_row("Cancel", f"panel:{panel_id}")

    try:
        await event.edit(prompt, buttons=builder.build())
    except Exception as exc:
        logger.warning("Input prompt edit failed: %s", exc)
