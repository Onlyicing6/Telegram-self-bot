"""
Panel context — shared state for inline panel rendering and callbacks.

Each inline panel can carry context data (e.g. which save code to preview,
which bio field to edit) through the callback data string. This module
provides utilities to encode/decode that context.

Context is stored in the callback data as:
  panel:<panel_id>:<extra>

The ``extra`` portion can encode:
  - Simple actions: "back", "close", "cat:2"
  - Action with payload: "exec:save:f", "exec:bio:on"
  - Input state: "input:save_code", "input:bio_text"
"""
import logging

logger = logging.getLogger(__name__)

MAX_CALLBACK_LEN = 64


def encode_extra(*parts: str) -> str:
    """Join parts with ':' into a callback extra string."""
    return ":".join(str(p) for p in parts)


def decode_extra(extra: str) -> list[str]:
    """Split extra into parts by ':'."""
    if not extra:
        return []
    return extra.split(":")


def truncate_callback_data(data: str) -> str:
    """Ensure callback data doesn't exceed Telegram's 64-byte limit."""
    if len(data) <= MAX_CALLBACK_LEN:
        return data
    logger.warning("Callback data truncated: %s (%d chars)", data, len(data))
    return data[:MAX_CALLBACK_LEN]
