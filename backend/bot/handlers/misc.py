"""
.ping  — Editing the trigger message with PONG (zero-spam policy).
.id    — Chat ID + Message ID of the current context.
.help  — Full command reference.
"""
import logging
from telethon import events
from backend.bot.handlers.guard import is_owner

logger = logging.getLogger(__name__)

_HELP = (
    "━━━━━━━━━━━━\n"
    "🧠 **LifeOS**\n"
    "━━━━━━━━━━━━\n"
    "\n"
    "📦 **Save Engine**  _(reply to a message)_\n"
    "  `.save f` · `.s f` — Forward save\n"
    "  `.save d` · `.s d` — Deep save\n"
    "  `.send <code>`       — Forward asset here\n"
    "\n"
    "🔍 **Discovery**\n"
    "  `.list [n]`      — Recent saves\n"
    "  `.find <text>`   — Search saves\n"
    "  `.preview` · `.r` · `.retrieve <code>` — Metadata\n"
    "\n"
    "🗑 **Organizer**\n"
    "  `.del <n>`          — Delete last n messages\n"
    "  `.del id <msgid>`   — Delete from msgid\n"
    "  `.del <code>`       — Delete a saved item\n"
    "  `.organize list`    — Data overview\n"
    "  `.organize clean`   — Purge old logs\n"
    "  `.db clean`         — Remove orphan DB rows\n"
    "  `.db stats`         — Database statistics\n"
    "  `.db vacuum`        — Cleanup + optimize\n"
    "\n"
    "🧬 **Bio Engine**\n"
    "  `.bio on` · `.bio off`     — Toggle cron\n"
    "  `.bio template <tpl>`      — Set template\n"
    "  `.bio text <text>`         — Set {text}\n"
    "  `.bio mood <mood>`         — Set {mood}\n"
    "  `.bio show` · `.bio help`  — Inspect / tokens\n"
    "\n"
    "⚙️ **Utility**\n"
    "  `.ping`  — PONG\n"
    "  `.id`    — Chat & Msg IDs\n"
    "  `.help`  — This message\n"
    "━━━━━━━━━━━━"
)


def register(client, owner_id: int):

    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.ping$"))
    async def ping(event):
        if not is_owner(event, owner_id):
            return
        try:
            await event.edit("PONG")
        except Exception as exc:
            logger.warning("ping edit failed: %s", exc)

    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.id$"))
    async def id_cmd(event):
        if not is_owner(event, owner_id):
            return
        try:
            chat_id = event.chat_id
            msg_id = event.message.id
            reply = await event.message.get_reply_message()
            lines = [f"**Chat ID:** `{chat_id}`", f"**Msg ID:** `{msg_id}`"]
            if reply:
                lines.append(f"**Reply Msg ID:** `{reply.id}`")
                lines.append(f"**Reply Sender ID:** `{reply.sender_id}`")
            await event.edit("\n".join(lines))
        except Exception as exc:
            logger.warning("id_cmd failed: %s", exc)

    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.help$"))
    async def help_cmd(event):
        if not is_owner(event, owner_id):
            return
        try:
            await event.edit(_HELP)
        except Exception as exc:
            logger.warning("help edit failed: %s", exc)
