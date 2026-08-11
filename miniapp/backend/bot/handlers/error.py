"""
error.py — глобальный error handler (Фаза 2.6 facade).

Самый простой модуль для начала миграции в Фазе 3:
  error_handler (~44 строк, 3954-3997) — глобальный PTB error handler
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[5])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


_ALLOWED = {"error_handler"}


def __getattr__(name: str):
    if name in _ALLOWED:
        import bot as _b
        return getattr(_b, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
