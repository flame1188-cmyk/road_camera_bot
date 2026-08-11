"""bot.analysis — пакет аналитики и очагов ДТП.

Phase 3-4: рефакторинг единого bot/analysis.py (1335 строк, 11 функций)
в пакет из 5 модулей. Каждый модуль — отдельная логическая группа.

Структура:
  • state.py     — геттеры состояния (5 функций, ~110 строк)
  • menu.py      — _build_menu_keyboard (~86 строк)
  • pipeline.py  — выгрузка + предложение анализа (3 функции, ~260 строк)
  • run.py       — _run_analysis (~450 строк)
  • clusters.py  — _run_concentration_points (~414 строк)

Все 11 функций реэкспортируются здесь для обратной совместимости:
  from bot.analysis import _start_fetching, _run_analysis, ...

Иерархия зависимостей (без циклов):
  state.py     → (без зависимостей от bot.analysis)
  menu.py      → state
  pipeline.py  → menu
  run.py       → state, menu
  clusters.py  → state, menu

100% pure refactoring: логика не изменена, только структура.
Оригинал сохранён как bot/analysis.py.bak для отката.
"""
from __future__ import annotations

# ============================================================
# Реэкспорты для обратной совместимости
# (внешние модули делают `from bot.analysis import _start_fetching, ...`)
# ============================================================

# state.py — геттеры и очистка
from bot.analysis.state import (
    _get_current_cards,
    _get_prev_cards,
    _has_analytics_data,
    _get_card_count,
    _clear_analytics_data,
)

# menu.py — клавиатура главного меню
from bot.analysis.menu import _build_menu_keyboard

# pipeline.py — выгрузка + preload + offer_analysis
from bot.analysis.pipeline import (
    _start_fetching,
    _preload_prev_year,
    _offer_analysis,
)

# run.py — основной цикл аналитики
from bot.analysis.run import _run_analysis

# clusters.py — очаги с динамикой
from bot.analysis.clusters import _run_concentration_points


# ============================================================
# __all__ — белый список экспорта (для `from bot.analysis import *`)
# ============================================================
__all__ = [
    # state
    "_get_current_cards",
    "_get_prev_cards",
    "_has_analytics_data",
    "_get_card_count",
    "_clear_analytics_data",
    # menu
    "_build_menu_keyboard",
    # pipeline
    "_start_fetching",
    "_preload_prev_year",
    "_offer_analysis",
    # run
    "_run_analysis",
    # clusters
    "_run_concentration_points",
]


# ============================================================
# Совместимость с smoke-тестами: bot.analysis.logger должен быть
# тем же объектом, что и bot._state.logger.
# (test_shared_state_is_single_instance проверяет это)
# ============================================================
from bot._state import logger  # noqa: F401 — re-export for tests
