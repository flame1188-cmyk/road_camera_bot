"""
keyboards.py — inline-клавиатуры (Фаза 2.6 facade).

Функции для миграции в Фазе 3:
  build_region_keyboard (~43 строк, 510-552)
  build_period_keyboard (~60 строк, 553-612)
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[5])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


_ALLOWED = {"build_region_keyboard", "build_period_keyboard"}


def __getattr__(name: str):
    if name in _ALLOWED:
        import bot as _b
        return getattr(_b, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
