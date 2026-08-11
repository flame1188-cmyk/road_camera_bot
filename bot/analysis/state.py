"""bot.analysis.state — геттеры и очистка состояния аналитики.

Содержит:
  • _get_current_cards / _get_prev_cards / _has_analytics_data / _get_card_count
  • _clear_analytics_data

Эти функции не имеют зависимостей от других подмодулей bot.analysis —
чистые геттеры user_data + data_cache. Используются в menu.py, pipeline.py,
run.py, clusters.py и в других пакетах (output.py, point_stats.py, qa.py).

Выделено из единого bot/analysis.py (Phase 3-4). 100% pure.
"""
from __future__ import annotations

from bot._state import *


def _get_current_cards(
    context: ContextTypes.DEFAULT_TYPE,
) -> list[dict] | None:
    """
    Получает карточки ДТП текущего периода.
    Сначала проверяет user_data, потом data_cache.
    Возвращает None если данные не найдены.
    """
    cards = context.user_data.get("analytics_cards", [])
    if cards:
        return cards

    reg_code = context.user_data.get("analytics_reg_code", "")
    period = context.user_data.get("analytics_period")
    if not reg_code or not period:
        return None

    dat_list = [f"{m}.{period.year}" for m in period.months]
    cached = data_cache.get(reg_code, dat_list)
    if cached:
        return cached[0]
    return None


def _has_analytics_data(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Проверяет, есть ли данные для аналитики.
    Используется для построения меню.
    """
    period = context.user_data.get("analytics_period")
    reg_name = context.user_data.get("analytics_reg_name", "")
    return bool(period and reg_name)


def _get_card_count(context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Возвращает количество ДТП текущего периода.
    """
    cards = _get_current_cards(context)
    if cards:
        return len(cards)
    return 0


def _get_prev_cards(
    context: ContextTypes.DEFAULT_TYPE,
) -> list[dict] | None:
    """
    Получает карточки ДТП за прошлый период.
    Сначала проверяет user_data, потом data_cache.
    Возвращает None если данные не найдены.
    """
    prev_cards = context.user_data.get("analytics_prev_cards", [])
    if prev_cards:
        return prev_cards

    reg_code = context.user_data.get("analytics_reg_code", "")
    period = context.user_data.get("analytics_period")
    if not reg_code or not period:
        return None

    prev_year = period.year - 1
    dat_list_prev = [f"{m}.{prev_year}" for m in period.months]
    cached = data_cache.get(reg_code, dat_list_prev)
    if cached:
        return cached[0]
    return None


def _clear_analytics_data(user_data: dict) -> None:
    """Очищает все данные аналитики из user_data (включая тяжёлые списки ДТП)."""
    for key in [
        "analytics_ready", "analytics_reg_code", "analytics_reg_name",
        "analytics_period", "analytics_cards", "analytics_comparison",
        "analytics_current_label", "analytics_prev_label",
        "analytics_prev_cards", "analytics_clusters",
        "analytics_news_context", "qa_mode", "qa_llm_provider", "qa_history",
        "point_stats_mode", "point_stats_lat", "point_stats_lon", "point_stats_radius",
        "cameras_data", "waiting_camera_file", "waiting_camera_for_map",
        "_settlement_polygons", "_preload_task",
    ]:
        user_data.pop(key, None)
