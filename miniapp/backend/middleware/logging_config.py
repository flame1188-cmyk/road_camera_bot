"""
Структурированное логирование (Фаза 2.7).

Поддерживает два формата:
  - "text" (по умолчанию): человекочитаемый, для dev/bothost.
  - "json": структурированные логи для Loki/ELK/Datadog.

В JSON-режиме каждое сообщение — объект с полями:
  {
    "timestamp": "2026-08-05T19:11:53.511Z",
    "level": "INFO",
    "logger": "miniapp.backend.services.gibdd_service",
    "message": "Task abc123 done: 2495 ДТП, ...",
    "request_id": "req_abc123",
    "user_id": 513940126,
    "task_id": "abc123def456"
  }

Использование:
  from .logging_config import setup_logging
  setup_logging()

  import logging
  logger = logging.getLogger(__name__)
  logger.info("Task done")

  # Контекст через contextvars:
  from .logging_config import log_context
  with log_context(request_id="req_abc", user_id=123):
      logger.info("Processing")
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import sys
from typing import Any, Dict, Optional


# contextvars — пробрасывают контекст через asyncio без явной передачи
_REQUEST_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "request_id", default=None
)
_USER_ID: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "user_id", default=None
)
_TASK_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "task_id", default=None
)


def get_request_id() -> Optional[str]:
    return _REQUEST_ID.get()


def get_user_id() -> Optional[int]:
    return _USER_ID.get()


def get_task_id() -> Optional[str]:
    return _TASK_ID.get()


def set_log_context(
    *,
    request_id: Optional[str] = None,
    user_id: Optional[int] = None,
    task_id: Optional[str] = None,
) -> None:
    """Устанавливает контекстные поля для текущей async-задачи."""
    if request_id is not None:
        _REQUEST_ID.set(request_id)
    if user_id is not None:
        _USER_ID.set(user_id)
    if task_id is not None:
        _TASK_ID.set(task_id)


@contextlib.contextmanager
def log_context(
    *,
    request_id: Optional[str] = None,
    user_id: Optional[int] = None,
    task_id: Optional[str] = None,
):
    """Context manager: устанавливает контекст на время блока."""
    tokens = []
    if request_id is not None:
        tokens.append(_REQUEST_ID.set(request_id))
    if user_id is not None:
        tokens.append(_USER_ID.set(user_id))
    if task_id is not None:
        tokens.append(_TASK_ID.set(task_id))
    try:
        yield
    finally:
        for token in reversed(tokens):
            try:
                token.var.reset(token)
            except Exception:
                pass


# ============================================================
# Форматтеры
# ============================================================

class _ContextAwareFormatter(logging.Formatter):
    """Базовый форматтер с поддержкой contextvars."""

    def _get_context(self) -> Dict[str, Any]:
        ctx: Dict[str, Any] = {}
        rid = _REQUEST_ID.get()
        if rid:
            ctx["request_id"] = rid
        uid = _USER_ID.get()
        if uid is not None:
            ctx["user_id"] = uid
        tid = _TASK_ID.get()
        if tid:
            ctx["task_id"] = tid
        return ctx


class TextFormatter(_ContextAwareFormatter):
    """Человекочитаемый формат (dev/bothost).

    Пример:
        2026-08-05 19:11:53 [INFO] gibdd_service: Task abc done [req=req_123 user=513940]
    """

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        ctx = self._get_context()
        if ctx:
            parts = []
            if "request_id" in ctx:
                parts.append(f"req={ctx['request_id']}")
            if "user_id" in ctx:
                parts.append(f"user={ctx['user_id']}")
            if "task_id" in ctx:
                parts.append(f"task={ctx['task_id']}")
            if parts:
                msg = f"{msg} [{', '.join(parts)}]"
        return msg


class JsonFormatter(_ContextAwareFormatter):
    """JSON-формат для Loki/ELK/Datadog.

    Каждая строка — валидный JSON-объект.
    """

    def format(self, record: logging.LogRecord) -> str:
        import json
        from datetime import datetime, timezone

        log_entry: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        ctx = self._get_context()
        log_entry.update(ctx)

        if record.exc_info and record.exc_info[1] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        if record.funcName and record.funcName != "<module>":
            log_entry["func"] = record.funcName
        if record.lineno:
            log_entry["line"] = record.lineno

        return json.dumps(log_entry, ensure_ascii=False, default=str)


# ============================================================
# Setup
# ============================================================

def setup_logging(level: Optional[str] = None, fmt: Optional[str] = None) -> None:
    """Настраивает logging. Вызывать ОДИН раз при старте приложения.

    Args:
        level: Уровень (DEBUG/INFO/WARNING/ERROR).
        fmt: Формат — "text" или "json".
    """
    log_level = level or os.environ.get("LOG_LEVEL", "INFO")
    log_format = (fmt or os.environ.get("LOG_FORMAT", "text")).lower()

    if log_format == "json":
        formatter: logging.Formatter = JsonFormatter()
    else:
        formatter = TextFormatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Шумные логеры — понижаем уровень
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        f"Logging configured: level={log_level}, format={log_format}"
    )
