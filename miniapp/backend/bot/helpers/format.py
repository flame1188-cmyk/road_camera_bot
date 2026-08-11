"""
format.py — форматирование, sanitization, прогресс-бары (Фаза 2.6 facade).

Чистые функции — лучшие кандидаты для миграции в Фазе 3:
  _sanitize_error       (~12 строк, 220-232)
  _safe_edit            (~20 строк, 233-252)
  _send_long_message    (~78 строк, 253-330)
  _make_progress_bar    (~14 строк, 3590-3603)
  is_user_allowed       (~6 строк, 331-336)
  _log_memory           (~13 строк, 170-183)
  _tg_retry             (~24 строк, 54-76)
  _get_user_lock        (~6 строк, 209-213)
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[5])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


_ALLOWED = {
    "_sanitize_error",
    "_safe_edit",
    "_send_long_message",
    "_make_progress_bar",
    "is_user_allowed",
    "_log_memory",
    "_tg_retry",
    "_get_user_lock",
}


def __getattr__(name: str):
    if name in _ALLOWED:
        import bot as _b
        return getattr(_b, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
