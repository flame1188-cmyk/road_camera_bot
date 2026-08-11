"""
commands.py — обработчики команд /start /help /dtp /regions /miniapp (Фаза 2.6 facade).

Сейчас: переэкспорт из корневого bot.py.
После Фазы 3: полная реализация, bot.py будет импортировать отсюда.

Функции для миграции (порядок — по сложности):
  cmd_start       (~30 строк, 613-642)  — простой
  cmd_help        (~45 строк, 643-687)  — простой
  cmd_dtp         (~10 строк, 688-695)  — простой
  cmd_regions     (~30 строк, 736-762)  — простой
  cmd_miniapp     (~40 строк, 762-803)  — простой
  cmd_precache    (~150 строк, 803-955) — сложный, фоновые задачи
  _run_precache   (~40 строк, 956-994)  — helper для precache
  _show_region_keyboard (~40 строк, 696-735)
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[5])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _import_from_root():
    """Импортирует функции команд из корневого bot.py."""
    import bot as _b
    return {
        "cmd_start": _b.cmd_start,
        "cmd_help": _b.cmd_help,
        "cmd_dtp": _b.cmd_dtp,
        "cmd_regions": _b.cmd_regions,
        "cmd_miniapp": _b.cmd_miniapp,
        "cmd_precache": _b.cmd_precache,
        "_run_precache": _b._run_precache,
        "_show_region_keyboard": _b._show_region_keyboard,
    }


# Lazy import при первом обращении — иначе циклическая зависимость.
def __getattr__(name: str):
    """PEP 562: ленивый экспорт атрибутов модуля."""
    _allowed = {
        "cmd_start", "cmd_help", "cmd_dtp", "cmd_regions",
        "cmd_miniapp", "cmd_precache", "_run_precache",
        "_show_region_keyboard",
    }
    if name in _allowed:
        return _import_from_root()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
