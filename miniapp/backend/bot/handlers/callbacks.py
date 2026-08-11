"""
callbacks.py — обработчик inline-кнопок (Фаза 2.6 facade).

on_callback_query — самый большой handler в проекте (~492 строки):
- Разбор callback_data по префиксу (region_*, period_*, action_*)
- Делегирование в соответствующие под-функции
- Защита от гонок через _get_user_lock

Функции для миграции в Фазе 3:
  on_callback_query (~492 строк, 995-1486)  — ОЧЕНЬ сложный
  _start_fetching   (~184 строк, 1487-1670) — запуск выгрузки
  _build_menu_keyboard (~88 строк, 1740-1827)
  _preload_prev_year   (~44 строк, 1828-1871)
  _offer_analysis      (~36 строк, 1872-1907)
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[5])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


_ALLOWED = {
    "on_callback_query",
    "_start_fetching",
    "_build_menu_keyboard",
    "_preload_prev_year",
    "_offer_analysis",
}


def __getattr__(name: str):
    if name in _ALLOWED:
        import bot as _b
        return getattr(_b, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
