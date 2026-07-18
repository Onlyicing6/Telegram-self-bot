"""
Bio Engine — timezone-synchronized Telegram bio cron.

Guarantees:
- Fires exactly at xx:xx:00 by sleeping to the next minute boundary.
- Deduplicates: skips the Telegram API call when the rendered string
  has not changed since the last confirmed update.
- FloodWaitError is caught and slept precisely; all other errors are
  logged as warnings so the loop never terminates on Telegram throttles.
- Only one updater task can exist at a time (start_cron is idempotent).
- Timezone resolved via zoneinfo with UTC fallback — never crashes.
"""
import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telethon.errors import FloodWaitError
from telethon.tl.functions.account import UpdateProfileRequest

from backend.db import client as db_client

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None


def _get_tz(tz_str: str):
    """Resolve a timezone — zoneinfo first, UTC fallback."""
    try:
        return ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, Exception):
        logger.warning("Timezone '%s' not found — falling back to UTC.", tz_str)
        return timezone.utc


def render_bio(template: str, mood: str, text: str, tz_str: str) -> str:
    tz = _get_tz(tz_str)
    now = datetime.now(tz)
    return (
        (template or "🕒 {time} | 💭 {mood}")
        .replace("{time}", now.strftime("%H:%M"))
        .replace("{mood}", mood or "😊")
        .replace("{text}", text or "")
    )


def _seconds_to_next_minute(tz) -> float:
    now = datetime.now(tz)
    wait = 60.0 - now.second - now.microsecond / 1_000_000
    if wait <= 0:
        wait += 60.0
    return wait


async def _cron_loop(client, owner_id: int, tz_str: str) -> None:
    tz = _get_tz(tz_str)
    logger.info("Bio cron started (tz=%s)", tz_str)

    while True:
        await asyncio.sleep(_seconds_to_next_minute(tz))

        try:
            state = db_client.get_bio_state(owner_id)
            logger.info("Bio cron state: %r", state)

            if not state or not state.get("is_active"):
                logger.info("Bio cron: is_active=False — stopping loop.")
                return

            tmpl = state.get("template", "🕒 {time} | 💭 {mood}")
            mood = state.get("mood", "😊")
            ctxtxt = state.get("custom_text", "")
            logger.info("Bio cron render args: template=%r mood=%r custom_text=%r", tmpl, mood, ctxtxt)

            new_bio = render_bio(tmpl, mood, ctxtxt, tz_str)
            logger.info("Rendered bio: %r", new_bio)

            last_bio = state.get("last_bio")
            logger.info("Comparing last_bio=%r new_bio=%r", last_bio, new_bio)

            if new_bio == (last_bio or ""):
                logger.info("Skipping — bio unchanged since last update")
                continue

            logger.info("Calling UpdateProfileRequest")
            try:
                await client(UpdateProfileRequest(about=new_bio))
            except FloodWaitError as fwe:
                logger.warning("Bio FloodWait %ds — sleeping.", fwe.seconds)
                await asyncio.sleep(fwe.seconds + 1)
                continue
            except asyncio.CancelledError:
                raise
            except Exception as api_exc:
                logger.exception(
                    "Bio API error (retrying next minute): type=%s repr=%r",
                    type(api_exc).__name__, api_exc,
                )
                continue

            logger.info("Telegram bio updated successfully")

            db_client.update_bio_state(owner_id, {
                "last_bio": new_bio,
                "updated_at": datetime.now(tz).isoformat(),
            })
            logger.info("DB bio_state updated with last_bio=%r", new_bio)

        except asyncio.CancelledError:
            logger.info("Bio cron cancelled.")
            raise
        except Exception:
            logger.exception("Bio cron tick error (will retry next minute)")


def start_cron(client, owner_id: int, tz_str: str) -> None:
    global _task
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_cron_loop(client, owner_id, tz_str))


def stop_cron() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
    _task = None


def is_running() -> bool:
    return bool(_task and not _task.done())
