"""Structured JSON and human-readable logging with correlation context and durable run logs."""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_log_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "forgeai_log_context",
    default=None,
)


@contextlib.contextmanager
def bind_log_context(**kwargs: Any) -> Iterator[None]:
    """Bind structured correlation fields (e.g., run_id, stage, loop) for the current scope."""
    current = dict(_log_context.get() or {})
    merged = {**current, **{k: v for k, v in kwargs.items() if v is not None}}
    token = _log_context.set(merged)
    try:
        yield
    finally:
        _log_context.reset(token)


class JsonLogFormatter(logging.Formatter):
    """Render log records as single-line JSON objects with run_id and stage."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        ctx = _log_context.get()
        if ctx:
            payload.update(ctx)
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextLogFormatter(logging.Formatter):
    """Human-readable log formatter including bound correlation context."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        ctx = _log_context.get() or {}
        ctx_str = " ".join(f"{k}={v}" for k, v in ctx.items())
        prefix = f"[{ts}] {record.levelname:<7} {record.name}"
        if ctx_str:
            prefix = f"{prefix} ({ctx_str})"
        msg = f"{prefix}: {record.getMessage()}"
        if record.exc_info:
            msg = f"{msg}\n{self.formatException(record.exc_info)}"
        return msg


class _RunIdFilter(logging.Filter):
    """Ensure a per-run file handler only writes logs belonging to its run_id."""

    def __init__(self, run_id: str) -> None:
        super().__init__()
        self.run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        return (_log_context.get() or {}).get("run_id") == self.run_id


@contextlib.contextmanager
def attach_run_log_file(run_id: str, log_path: Path) -> Iterator[Path]:
    """Attach a durable JSONL FileHandler for `run_id` so logs are never lost on crash."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("forgeai")
    if not root.handlers:
        configure_logging()

    handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    handler.setFormatter(JsonLogFormatter())
    handler.addFilter(_RunIdFilter(run_id))
    root.addHandler(handler)
    try:
        yield log_path
    finally:
        handler.flush()
        root.removeHandler(handler)
        handler.close()


def configure_logging(level: str = "INFO", log_format: str = "json") -> None:
    """Configure root ForgeAI logger."""
    root = logging.getLogger("forgeai")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Preserve any active per-run file handlers
    file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonLogFormatter() if log_format == "json" else TextLogFormatter())
    root.addHandler(handler)
    for fh in file_handlers:
        root.addHandler(fh)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced ForgeAI logger."""
    if not name.startswith("forgeai"):
        name = f"forgeai.{name}"
    return logging.getLogger(name)
