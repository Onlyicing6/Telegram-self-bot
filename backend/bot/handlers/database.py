"""
.db clean   — Remove orphan DB rows (Saved Messages message no longer exists).
.db stats   — Display database statistics (counts, types, sizes, dates, orphans).
.db vacuum  — Run orphan cleanup + index optimization. Summary only.
"""
import logging
from datetime import datetime
from telethon import events
from backend.bot.handlers.guard import is_owner
from backend.db import client as db_client
from backend.bio.engine import _get_tz

logger = logging.getLogger(__name__)

_MEDIA_ICON = {
    "Photo": "📷",
    "Video": "🎬",
    "Animation": "🎞",
    "Audio": "🎵",
    "Voice": "🎤",
    "Sticker": "🏷",
    "Document": "📄",
    "Unknown": "📦",
}


def _icon(media_type: str | None) -> str:
    return _MEDIA_ICON.get(media_type or "Unknown", "📦")


def _format_date(iso_str: str | None, tz_str: str) -> str:
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y")
    except Exception:
        return str(iso_str)[:10]


async def _find_orphans(client, owner_id: int) -> tuple[list[int], int]:
    """Check every saved item's Telegram message. Returns (orphan_ids, total_checked)."""
    items = db_client.list_all_saves(owner_id)
    orphan_ids: list[int] = []
    for item in items:
        saved_chat_id = item.get("saved_chat_id")
        saved_msg_id = item.get("saved_msg_id")
        if not saved_chat_id or not saved_msg_id:
            orphan_ids.append(item.get("id"))
            continue
        try:
            msg = await client.get_messages(saved_chat_id, ids=saved_msg_id)
            if msg is None or (isinstance(msg, list) and not any(m is not None for m in msg)):
                orphan_ids.append(item.get("id"))
        except Exception:
            pass
    return orphan_ids, len(items)


def register(client, owner_id: int, tz_str: str):

    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.db\s+(clean|stats|vacuum)$"))
    async def db_cmd(event):
        if not is_owner(event, owner_id):
            return

        action = event.pattern_match.group(1)

        if action == "clean":
            await event.edit("🧹 Checking Saved Messages…")
            try:
                orphan_ids, total = await _find_orphans(client, owner_id)
                removed = db_client.cleanup_orphans(owner_id, orphan_ids)
                remaining = total - removed
                await event.edit(
                    f"🧹 **Database cleanup complete**\n\n"
                    f"Removed: `{removed}` orphan rows\n"
                    f"Remaining: `{remaining}` items"
                )
                await db_client.log(owner_id, "INFO", f"DB clean: removed {removed} orphans", {
                    "removed": removed, "remaining": remaining,
                })
            except Exception as exc:
                logger.error("db clean failed: %s", exc)
                await event.edit(f"❌ Cleanup error: {exc}")
                await db_client.log(owner_id, "ERROR", f"DB clean failed: {exc}", {})

        elif action == "stats":
            try:
                stats = db_client.get_stats(owner_id)
                total = stats["total"]
                by_type = stats["by_type"]
                size_bytes = stats["size_estimate"]

                if size_bytes >= 1024 * 1024:
                    size_str = f"{size_bytes / (1024 * 1024):.1f} MB"
                elif size_bytes >= 1024:
                    size_str = f"{size_bytes / 1024:.1f} KB"
                else:
                    size_str = f"{size_bytes} B"

                lines = [
                    f"📊 **Database Statistics**\n",
                    f"Total saved items: `{total}`\n",
                    f"**Breakdown by type:**",
                ]
                for mt in ["Photo", "Video", "Animation", "Audio", "Voice", "Document", "Unknown"]:
                    count = by_type.get(mt, 0)
                    if count:
                        lines.append(f"  {_icon(mt)} {mt}: `{count}`")

                lines.append(f"\n**Database size estimate:** `{size_str}`")
                lines.append(f"**Oldest save:** {_format_date(stats['oldest'], tz_str)}")
                lines.append(f"**Newest save:** {_format_date(stats['newest'], tz_str)}")

                await event.edit("\n".join(lines))
                await db_client.log(owner_id, "INFO", f"DB stats: {total} items", stats)
            except Exception as exc:
                logger.error("db stats failed: %s", exc)
                await event.edit(f"❌ Stats error: {exc}")

        elif action == "vacuum":
            await event.edit("⚙️ Vacuuming…")
            try:
                orphan_ids, total = await _find_orphans(client, owner_id)
                removed = db_client.cleanup_orphans(owner_id, orphan_ids)
                remaining = total - removed

                await event.edit(
                    f"⚙️ **Vacuum complete**\n\n"
                    f"Orphans removed: `{removed}`\n"
                    f"Items remaining: `{remaining}`\n"
                    f"Index optimization: skipped (PostgREST)"
                )
                await db_client.log(owner_id, "INFO", f"DB vacuum: removed {removed} orphans", {
                    "removed": removed, "remaining": remaining,
                })
            except Exception as exc:
                logger.error("db vacuum failed: %s", exc)
                await event.edit(f"❌ Vacuum error: {exc}")
                await db_client.log(owner_id, "ERROR", f"DB vacuum failed: {exc}", {})
