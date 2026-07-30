"""
Per-call log file manager.

Adds a loguru sink filtered to a specific call_id when a call starts,
and removes it when the call ends. Each call gets its own file at
  logs/calls/<call_id>.log

Usage in voice_agent.py:
    call_log = call_log_manager.start(call_id)
    call_log.info("something happened")
    ...
    call_log_manager.end(call_id)
"""

from pathlib import Path
from loguru import logger

LOG_DIR = Path("logs") / "calls"

_sinks: dict[str, int] = {}  # call_id → loguru sink id


def start(call_id: str):
    """Register a filtered sink and return a bound logger for this call."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    sink_id = logger.add(
        LOG_DIR / f"{call_id}.log",
        filter=lambda record: record["extra"].get("call_id") == call_id,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {message}",
        encoding="utf-8",
        enqueue=True,   # thread-safe async writes
    )
    _sinks[call_id] = sink_id
    return logger.bind(call_id=call_id)


def end(call_id: str) -> None:
    """Flush and remove the sink for this call."""
    sink_id = _sinks.pop(call_id, None)
    if sink_id is not None:
        logger.remove(sink_id)
