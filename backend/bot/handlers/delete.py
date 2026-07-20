"""
.del <n>         — Delete the last n outgoing messages in this chat.
.del id <msgid>  — Delete all messages from <msgid> forward in this chat.
.del <code>      — Delete a saved item: Telegram message + DB row.

Edit-first policy: error feedback edits the trigger message.
Successful deletion silently removes all targeted messages (including the command).
"""
import asyncio
import logging
from telethon import events
from backend.bot.handlers.guard import is_owner
from backend.db import client as db_client
from backend.diagnostics import record_event

logger = logging.getLogger(__name__)

_BATCH = 100


def register(client, owner_id: int):

    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.del(?:\s+(.+))?$"))
    async def del_cmd(event):
        if not is_owner(event, owner_id):
            return

        arg = (event.pattern_match.group(1) or "").strip()

        if not arg:
            await event.edit("⚠️ Usage: `.del <n>` or `.del id <msgid>` or `.del <code>`")
            return

        if arg.lower().startswith("id "):
            rest = arg[3:].strip()
            if not rest.isdigit():
                await event.edit("⚠️ Usage: `.del id <msgid>`")
                return
            start_id = int(rest)
            await event.delete()
            t0 = asyncio.get_event_loop().time()
            try:
                msg_ids = []
                async for msg in client.iter_messages(event.chat_id, min_id=start_id - 1):
                    msg_ids.append(msg.id)
                    if len(msg_ids) >= _BATCH:
                        await client.delete_messages(event.chat_id, msg_ids)
                        msg_ids = []
                if msg_ids:
                    await client.delete_messages(event.chat_id, msg_ids)
                record_event("delete", "del id", (asyncio.get_event_loop().time() - t0) * 1000, "SUCCESS")
            except Exception as exc:
                logger.error("del id failed: %s", exc)
                record_event("delete", "del id", 0, "ERROR", str(exc))

        elif arg.isdigit():
            n = int(arg)
            if n < 1 or n > 500:
                await event.edit("⚠️ n must be between 1 and 500.")
                return
            await event.delete()
            t0 = asyncio.get_event_loop().time()
            try:
                msg_ids = []
                async for msg in client.iter_messages(event.chat_id, limit=n + 5, from_user="me"):
                    msg_ids.append(msg.id)
                    if len(msg_ids) >= n:
                        break
                if msg_ids:
                    await client.delete_messages(event.chat_id, msg_ids[:n])
                record_event("delete", "del n", (asyncio.get_event_loop().time() - t0) * 1000, "SUCCESS")
            except Exception as exc:
                logger.error("del n failed: %s", exc)
                record_event("delete", "del n", 0, "ERROR", str(exc))

        else:
            code = arg.upper()
            t0 = asyncio.get_event_loop().time()
            try:
                row = db_client.query_save(code)
                record_event("database", "query_save", (asyncio.get_event_loop().time() - t0) * 1000, "SUCCESS")
            except Exception as exc:
                logger.error("del save_code DB query failed: %s", exc)
                record_event("database", "query_save", 0, "ERROR", str(exc))
                await event.edit(f"❌ DB error: {exc}")
                return
            if not row:
                await event.edit(f"❌ No saved item found for `{code}`")
                return

            saved_chat_id = row.get("saved_chat_id")
            saved_msg_id = row.get("saved_msg_id")
            display = row.get("short_code") or row.get("save_code") or code

            tg_deleted = False
            tg_error = None
            if saved_chat_id and saved_msg_id:
                try:
                    await client.delete_messages(saved_chat_id, [saved_msg_id])
                    tg_deleted = True
                except Exception as exc:
                    tg_error = exc
                    logger.warning("del %s: Telegram deletion failed: %s", code, exc)
            else:
                tg_deleted = True

            db_deleted = False
            db_error = None
            try:
                removed = db_client.delete_save_row(owner_id, code)
                db_deleted = removed is not None
            except Exception as exc:
                db_error = exc
                logger.error("del %s: DB deletion failed: %s", code, exc)

            if tg_deleted and db_deleted:
                await event.edit(f"🗑 Deleted `{display}`")
            elif tg_deleted and not db_deleted:
                await event.edit(
                    f"⚠️ `{display}`: Telegram message deleted, but DB row removal failed: {db_error}"
                )
            elif not tg_deleted and db_deleted:
                if tg_error:
                    await event.edit(
                        f"⚠️ `{display}`: DB row deleted, but Telegram message deletion failed: {tg_error}"
                    )
                else:
                    await event.edit(
                        f"🗑 Deleted `{display}` (Telegram message was already missing)"
                    )
            else:
                await event.edit(
                    f"❌ `{display}`: Both Telegram and DB deletion failed. "
                    f"TG: {tg_error}, DB: {db_error}"
                )

            await db_client.log(
                owner_id,
                "INFO" if (tg_deleted and db_deleted) else "ERROR",
                f"Delete {code}: tg={'ok' if tg_deleted else 'fail'}, db={'ok' if db_deleted else 'fail'}",
                {"save_code": code, "tg_error": str(tg_error) if tg_error else None},
            )
