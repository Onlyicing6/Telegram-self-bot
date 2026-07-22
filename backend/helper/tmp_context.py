"""
Temporary context store for inline panels that need to carry data
from the self-bot command to the callback handler.

For example, .save needs to pass the replied message reference to
the inline panel callback. This store holds that data briefly keyed
by owner_id.
"""
import logging
import time

logger = logging.getLogger(__name__)

_EXPIRY_S = 300
_store: dict[int, dict] = {}


def set_context(owner_id: int, data: dict) -> None:
    data["_ts"] = time.time()
    _store[owner_id] = data


def get_context(owner_id: int) -> dict | None:
    data = _store.get(owner_id)
    if data is None:
        return None
    if time.time() - data.get("_ts", 0) > _EXPIRY_S:
        _store.pop(owner_id, None)
        return None
    return data


def clear_context(owner_id: int) -> dict | None:
    return _store.pop(owner_id, None)
