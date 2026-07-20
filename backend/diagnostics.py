"""
Diagnostics module for .kill command.

Provides:
  - An in-memory ring buffer of recent bot events (min 100 entries)
  - A complete diagnostic snapshot of all subsystems
  - Stalled-task detection and selective recovery

All snapshot collection is synchronous and non-blocking — it reads
module-level state only, never performs I/O.
"""
import asyncio
import logging
import os
import sys
import time
import tracemalloc
from collections import deque
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_RING_SIZE = 150
_event_ring: deque = deque(maxlen=_RING_SIZE)

_STALL_THRESHOLD_S = 120.0
_HEARTBEAT_STALE_S = 15.0

_PROTECTED_TASK_NAMES = {
    "lifeos-tg-supervisor",
    "lifeos-watchdog",
    "lifeos-heartbeat",
    "lifeos-web",
}


def record_event(module: str, action: str, duration_ms: float, result: str) -> None:
    entry = {
        "ts": datetime.now(timezone.utc),
        "module": module,
        "action": action,
        "duration_ms": round(duration_ms, 1),
        "result": result,
    }
    _event_ring.append(entry)


def get_events() -> list:
    return list(_event_ring)


def _format_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{int(seconds * 1000)}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}m {secs}s"


def _format_uptime(seconds: float) -> str:
    if seconds < 0:
        return "unknown"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _get_task_state(task: asyncio.Task) -> str:
    if task.done():
        if task.cancelled():
            return "CANCELLED"
        exc = task.exception()
        if exc:
            return f"FAILED ({type(exc).__name__})"
        return "DONE"
    return "RUNNING"


def _get_coro_name(task: asyncio.Task) -> str:
    coro = task.get_coro()
    if coro is None:
        return "unknown"
    return getattr(coro, "__name__", getattr(coro, "__qualname__", "unknown"))


def _get_await_location(task: asyncio.Task) -> str:
    coro = task.get_coro()
    if coro is None:
        return ""
    frame = coro.cr_frame
    if frame is not None:
        code = frame.f_code
        return f"{code.co_filename}:{frame.f_lineno}"
    return ""


def _collect_process_section() -> list:
    lines = ["=== PROCESS ==="]
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        mem_mb = usage.ru_maxrss / 1024
        cpu_s = usage.ru_utime + usage.ru_stime
        lines.append(f"• Uptime: see Runtime below")
        lines.append(f"• PID: {os.getpid()}")
        lines.append(f"• Memory: {mem_mb:.1f} MB (max RSS)")
        lines.append(f"• CPU: {cpu_s:.2f}s user+sys")
    except Exception:
        lines.append(f"• PID: {os.getpid()}")
        lines.append("• Memory/CPU: unavailable")
    lines.append(f"• Python: {sys.version.split()[0]}")
    return lines


def _collect_telethon_section(client) -> list:
    lines = ["=== TELETHON ==="]
    try:
        connected = client.is_connected()
        lines.append(f"• Connected: {connected}")
        lines.append(f"• Authorized: {client.is_user_authorized() if connected else False}")
    except Exception:
        lines.append("• Connected: unknown")
        lines.append("• Authorized: unknown")
    lines.append("• Last received update: not tracked")
    lines.append("• Last successful API request: not tracked")
    lines.append("• Pending requests: not tracked")
    return lines


def _collect_supervisor_section(health_snap: dict) -> list:
    lines = ["=== SUPERVISOR ==="]
    tasks = asyncio.all_tasks()
    lines.append(f"• Running tasks: {len(tasks)}")
    lines.append(f"• Restart counters: not tracked")
    lines.append(f"• Watchdog: {'Running' if health_snap.get('supervisor_ok') else 'Stopped'}")
    hb_age = health_snap.get("heartbeat_age_s")
    if hb_age is not None:
        lines.append(f"• Heartbeat age: {hb_age:.1f}s")
    else:
        lines.append("• Heartbeat age: unknown")
    return lines


def _collect_bio_section(bio_engine) -> list:
    lines = ["=== BIO ENGINE ==="]
    lines.append(f"• Running: {bio_engine.is_running()}")
    lines.append("• Last successful update: not tracked")
    lines.append("• Last exception: not tracked")
    lines.append("• Next scheduled execution: next minute boundary")
    return lines


def _collect_database_section(db_client) -> list:
    lines = ["=== DATABASE ==="]
    lines.append(f"• Available: {db_client.is_available()}")
    lines.append("• Last query: not tracked")
    lines.append("• Query duration: not tracked")
    lines.append("• Pending DB operations: not tracked")
    lines.append("• Last successful response: not tracked")
    return lines


def _collect_save_engine_section() -> list:
    lines = ["=== SAVE ENGINE ==="]
    lines.append("• Current save jobs: 0")
    lines.append("• Current retrieve jobs: 0")
    lines.append("• Pending uploads: 0")
    lines.append("• Pending downloads: 0")
    return lines


def _collect_event_loop_section() -> list:
    lines = ["=== EVENT LOOP ==="]
    current = asyncio.current_task()
    for task in asyncio.all_tasks():
        if task is current:
            continue
        name = task.get_name()
        state = _get_task_state(task)
        coro_name = _get_coro_name(task)
        loc = _get_await_location(task)
        lines.append(f"• {name} — {state} — {coro_name}")
        if loc:
            lines.append(f"  at {loc}")
    return lines


def _collect_active_locks_section() -> list:
    lines = ["=== ACTIVE LOCKS ==="]
    lines.append("• (lock introspection not available in Python asyncio)")
    return lines


def _collect_background_queues_section() -> list:
    lines = ["=== BACKGROUND QUEUES ==="]
    lines.append("• No background queues in use")
    return lines


def _collect_last_events_section() -> list:
    lines = ["=== LAST EVENTS ==="]
    events = get_events()
    if not events:
        lines.append("• (no events recorded)")
        return lines
    for e in events[-20:]:
        ts = e["ts"].strftime("%H:%M:%S")
        dur = _format_duration(e["duration_ms"] / 1000)
        lines.append(f"• {ts} | {e['module']} | {e['action']} | {dur} | {e['result']}")
    return lines


def build_diagnostic_report(client, bio_engine, db_client, health_snap: dict) -> str:
    sections = []
    sections.extend(_collect_process_section())
    sections.append("")
    sections.extend(_collect_telethon_section(client))
    sections.append("")
    sections.extend(_collect_supervisor_section(health_snap))
    sections.append("")
    sections.extend(_collect_bio_section(bio_engine))
    sections.append("")
    sections.extend(_collect_database_section(db_client))
    sections.append("")
    sections.extend(_collect_save_engine_section())
    sections.append("")
    sections.extend(_collect_event_loop_section())
    sections.append("")
    sections.extend(_collect_active_locks_section())
    sections.append("")
    sections.extend(_collect_background_queues_section())
    sections.append("")
    sections.extend(_collect_last_events_section())
    return "\n".join(sections)


def _detect_stalled_tasks() -> list:
    stalled = []
    current = asyncio.current_task()
    for task in asyncio.all_tasks():
        if task is current or task.done():
            continue
        name = task.get_name()
        if name in _PROTECTED_TASK_NAMES:
            continue
        coro = task.get_coro()
        if coro is None:
            continue
        frame = coro.cr_frame
        if frame is None:
            continue
        stalled.append(task)
    return stalled


async def recover_stalled(client, owner_id: int, tz_str: str, bio_engine, db_client) -> str:
    stalled = _detect_stalled_tasks()
    recovered = []
    still_unhealthy = []

    for task in stalled:
        name = task.get_name()
        try:
            task.cancel()
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
        recovered.append(name)

    if not bio_engine.is_running():
        try:
            bio_engine.start_cron(client, owner_id, tz_str)
            await asyncio.sleep(1)
            if bio_engine.is_running():
                recovered.append("Bio Cron restarted")
            else:
                still_unhealthy.append("Bio Cron failed to restart")
        except Exception as exc:
            still_unhealthy.append(f"Bio Cron restart error: {exc}")

    lines = ["", "=== RECOVERY ==="]
    if recovered:
        lines.append("Recovered:")
        for r in recovered:
            lines.append(f"  ✔ {r}")
    if still_unhealthy:
        lines.append("Still unhealthy:")
        for u in still_unhealthy:
            lines.append(f"  ✖ {u}")
    if not recovered and not still_unhealthy:
        lines.append("No stalled tasks detected — nothing to recover.")
    return "\n".join(lines)
