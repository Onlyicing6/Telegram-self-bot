"""
.organize list   — Structured overview of LifeOS data (saves, logs, bio).
.organize clean  — Purge transient bot_logs older than 7 days.
"""
import asyncio
import logging
from telethon import events
from backend.bot.handlers.guard import is_owner
from backend.db import client as db_client
from backend.diagnostics import record_event

logger = logging.getLogger(__name__)


def register(client, owner_id: int):

    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.organize\s+(list|clean)$"))
    async def organize(event):
        if not is_owner(event, owner_id):
            return

        action = event.pattern_match.group(1)

        if action == "list":
            t0 = asyncio.get_event_loop().time()
            try:
                total = db_client.count_saves(owner_id)
                fwd = db_client.count_saves(owner_id, "forward")
                deep = db_client.count_saves(owner_id, "deep")
                logs = db_client.count_logs(owner_id)
                bio = db_client.get_bio_state(owner_id)
                record_event("organize", "list", (asyncio.get_event_loop().time() - t0) * 1000, "SUCCESS")

                bio_status = "OFF"
                bio_template = "—"
                if bio:
                    bio_status = "ON" if bio.get("is_active") else "OFF"
                    bio_template = bio.get("template", "—")

                lines = [
                    "**LifeOS Status**\n",
                    f"📦 **Saves**",
                    f"  Total: `{total}`",
                    f"  Forward: `{fwd}`",
                    f"  Deep: `{deep}`\n",
                    f"📋 **Logs**",
                    f"  Entries: `{logs}`\n",
                    f"🧬 **Bio Engine**",
                    f"  Status: `{bio_status}`",
                    f"  Template: `{bio_template}`",
                ]
                await event.edit("\n".join(lines))
            except Exception as exc:
                logger.error("organize list failed: %s", exc)
                record_event("organize", "list", 0, "ERROR", str(exc))
                await event.edit(f"❌ Error: {exc}")

        elif action == "clean":
            t0 = asyncio.get_event_loop().time()
            try:
                deleted = db_client.clean_logs(owner_id, days=7)
                record_event("organize", "clean", (asyncio.get_event_loop().time() - t0) * 1000, "SUCCESS")
                await event.edit(f"🧹 Cleaned `{deleted}` log entries older than 7 days.")
            except Exception as exc:
                logger.error("organize clean failed: %s", exc)
                record_event("organize", "clean", 0, "ERROR", str(exc))
                await event.edit(f"❌ Error: {exc}")
