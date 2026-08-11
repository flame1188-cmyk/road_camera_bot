"""
Аналитические операции над задачей: кросс-таблицы, сравнение АППГ, точечная статистика.

- _get_cross_tables() — кэшированный расчёт cross_tables (по id(cards))
- ensure_comparison() — comparison текущих vs прошлых метрик (с параллельным
  расчётом metrics(current) и загрузкой prev_cards)
- compute_point_stats() — статистика ДТП в радиусе от точки
"""
from __future__ import annotations

import asyncio
import logging
import time as _time
from typing import Any, Dict, Optional

from . import _imports
from .models import Task
from .pipeline import ensure_cards, ensure_prev_cards

logger = logging.getLogger(__name__)


# === Phase 3.1: In-memory кэшированные помощники ===
# Возвращают посчитанные cross_tables / metrics из task, если cards_id
# совпадает. Иначе — пересчитывают и сохраняют в кэше.
# Синхронные (CPU-bound), но быстрые: 2629 ДТП ≈ 38 ms на cross_tables.
def _get_cross_tables(task: Task, prev: bool = False) -> Optional[Dict[str, Any]]:
    """Возвращает кэшированные cross_tables для task.cards (или prev_cards)."""
    analytics_module = _imports._import_module("analytics")

    if prev:
        if not task.prev_cards:
            return None
        cards_id = id(task.prev_cards)
        if (task.prev_cross_tables is not None
                and task.prev_cross_tables_cards_id == cards_id):
            logger.debug(f"Task {task.id}: prev_cross_tables cache hit")
            return task.prev_cross_tables
        t0 = _time.perf_counter()
        result = analytics_module.calculate_cross_tables(task.prev_cards)
        elapsed_ms = (_time.perf_counter() - t0) * 1000
        logger.info(
            f"Task {task.id}: calculate_cross_tables(prev) — "
            f"{len(task.prev_cards)} ДТП, {elapsed_ms:.1f} ms"
        )
        task.prev_cross_tables = result
        task.prev_cross_tables_cards_id = cards_id
        return result

    if not task.cards:
        return None
    cards_id = id(task.cards)
    if (task.cross_tables is not None
            and task.cross_tables_cards_id == cards_id):
        logger.debug(f"Task {task.id}: cross_tables cache hit")
        return task.cross_tables
    t0 = _time.perf_counter()
    result = analytics_module.calculate_cross_tables(task.cards)
    elapsed_ms = (_time.perf_counter() - t0) * 1000
    logger.info(
        f"Task {task.id}: calculate_cross_tables(current) — "
        f"{len(task.cards)} ДТП, {elapsed_ms:.1f} ms"
    )
    task.cross_tables = result
    task.cross_tables_cards_id = cards_id
    return result


async def ensure_comparison(task: Task) -> Dict[str, Any]:
    """
    Гарантирует, что task.comparison посчитан.

    Сравнение = текущие метрики vs метрики прошлого года.
    Если данных за прошлый год нет — comparison содержит только current.

    Phase 3.1: используется in-memory кэш task.current_metrics /
    task.prev_metrics (инвалидируется по id(task.cards)). Запуск
    calculate_metrics(current) идёт ПАРАЛЛЕЛЬНО с ensure_prev_cards() —
    пока идёт сетевой запрос к ГИБДД за АППГ, CPU не простаивает.
    """
    if task.comparison is not None:
        return {"ok": True, "comparison": task.comparison}

    if not task.cards:
        return {"ok": False, "error": "Карточки текущего периода не загружены"}

    analytics_module = _imports._import_module("analytics")
    t_start = _time.perf_counter()

    async def _calc_current_metrics():
        # In-memory кэш по id(cards) — если cards не менялся, не пересчитываем
        cards_id = id(task.cards)
        if (task.current_metrics is not None
                and task.current_metrics_cards_id == cards_id):
            logger.debug(f"Task {task.id}: current_metrics cache hit")
            return task.current_metrics
        t0 = _time.perf_counter()
        metrics = analytics_module.calculate_metrics(task.cards)
        elapsed_ms = (_time.perf_counter() - t0) * 1000
        logger.info(
            f"Task {task.id}: calculate_metrics(current) — "
            f"{len(task.cards)} ДТП, {elapsed_ms:.1f} ms"
        )
        task.current_metrics = metrics
        task.current_metrics_cards_id = cards_id
        return metrics

    async def _load_and_calc_prev():
        prev_result = await ensure_prev_cards(task)
        if not prev_result.get("ok") or not prev_result.get("prev_cards"):
            return None, prev_result
        prev_cards_id = id(task.prev_cards)
        if (task.prev_metrics is not None
                and task.prev_metrics_cards_id == prev_cards_id):
            logger.debug(f"Task {task.id}: prev_metrics cache hit")
            return task.prev_metrics, prev_result
        t0 = _time.perf_counter()
        metrics = analytics_module.calculate_metrics(task.prev_cards)
        elapsed_ms = (_time.perf_counter() - t0) * 1000
        logger.info(
            f"Task {task.id}: calculate_metrics(prev) — "
            f"{len(task.prev_cards)} ДТП, {elapsed_ms:.1f} ms"
        )
        task.prev_metrics = metrics
        task.prev_metrics_cards_id = prev_cards_id
        return metrics, prev_result

    # Параллельно: metrics(current) (CPU) + ensure_prev_cards (сеть)
    current_metrics, (prev_metrics, _prev_result) = await asyncio.gather(
        _calc_current_metrics(),
        _load_and_calc_prev(),
    )

    # compare_metrics — быстро (<1 ms)
    t0 = _time.perf_counter()
    if prev_metrics:
        comparison = analytics_module.compare_metrics(
            current_metrics, prev_metrics
        )
    else:
        # Нет прошлого года — формируем урезанный comparison
        comparison = {
            "total": {"current": current_metrics.get("total", 0),
                      "previous": 0, "change": 0},
            "deaths": {"current": current_metrics.get("deaths", 0),
                       "previous": 0, "change": 0},
            "injured": {"current": current_metrics.get("injured", 0),
                        "previous": 0, "change": 0},
            "alcohol": {"current": current_metrics.get("alcohol", 0),
                        "previous": 0, "change": 0},
            "pedestrians": {"current": current_metrics.get("pedestrians", 0),
                            "previous": 0, "change": 0},
            "deaths_per_100": {
                "current": current_metrics.get("deaths_per_100", 0),
                "previous": 0, "change": 0,
            },
            "injured_per_100": {
                "current": current_metrics.get("injured_per_100", 0),
                "previous": 0, "change": 0,
            },
            "by_weekday": {"current": current_metrics.get("by_weekday", {}),
                           "previous": {}},
            "by_hour": {"current": current_metrics.get("by_hour", {}),
                        "previous": {}},
            "by_type": {"current": current_metrics.get("by_type", {}),
                        "previous": {}},
            "by_weather": {"current": current_metrics.get("by_weather", {}),
                           "previous": {}},
        }
    compare_ms = (_time.perf_counter() - t0) * 1000
    total_ms = (_time.perf_counter() - t_start) * 1000
    logger.info(
        f"Task {task.id}: ensure_comparison done — "
        f"compare_metrics={compare_ms:.1f} ms, total={total_ms:.0f} ms"
    )

    task.comparison = comparison
    return {"ok": True, "comparison": comparison}


# ============================================================
# Статистика по точке
# ============================================================
async def compute_point_stats(
    task: Task,
    lat: float,
    lon: float,
    radius_m: int,
) -> Dict[str, Any]:
    """
    Считает статистику ДТП в радиусе от точки.

    Использует point_statistics.calculate_point_statistics.
    Требует загруженные карточки (task.cards).

    Returns:
        {
            "ok": True,
            "center": {"lat": ..., "lon": ...},
            "radius_m": ...,
            "current": {total, deaths, injured, alcohol, pedestrians,
                        by_type, by_road, by_weather, cards},
            "prev": {...} | null,
            "prev_label": "...",
            "current_label": "...",
        }
    """
    # Sprint 3.1: восстанавливаем task.cards из cards_cache, если задача
    # была выгружена из in-memory LRU или после рестарта.
    cards_result = await ensure_cards(task)
    if not cards_result.get("ok"):
        return {"ok": False, "error": cards_result.get("error")}

    point_stats_module = _imports._import_module("point_statistics")

    # Загружаем прошлый год (если ещё нет)
    prev_cards = []
    prev_label = ""
    if not task.prev_cards_loaded:
        await ensure_prev_cards(task)
    prev_cards = task.prev_cards or []
    prev_label = task.prev_label or ""

    stats = await asyncio.to_thread(
        point_stats_module.calculate_point_statistics,
        lat, lon, radius_m,
        task.cards,
        prev_cards if prev_cards else None,
    )

    # Сериализуем: убираем непередаваемые объекты (Counter уже dict)
    def _serialize_period(p: dict) -> dict:
        if not p:
            return None
        return {
            "total": p.get("total", 0),
            "deaths": p.get("deaths", 0),
            "injured": p.get("injured", 0),
            "alcohol": p.get("alcohol", 0),
            "pedestrians": p.get("pedestrians", 0),
            "by_type": dict(p.get("by_type", {})),
            "by_road": dict(p.get("by_road", {})),
            "by_weather": dict(p.get("by_weather", {})),
            # Не возвращаем cards целиком — только количество и первые 5
            # для отображения. Полный список доступен через Excel-выгрузку.
            "cards_count": len(p.get("cards", [])),
            "cards_preview": [
                {
                    "date": str(c.get("date_dtp", "")),
                    "time": str(c.get("time", "")),
                    "type": str(c.get("dtpv", "")),
                    "road": str(c.get("dor", "") or c.get("street", "")),
                    "deaths": int(c.get("pog", 0) or 0),
                    "injured": int(c.get("ran", 0) or 0),
                    "dist_m": round(float(c.get("_dist_m", 0)), 1),
                    "lat": float(str(c.get("coord_w", "0")).strip() or 0),
                    "lon": float(str(c.get("coord_l", "0")).strip() or 0),
                }
                for c in (p.get("cards") or [])[:20]
            ],
        }

    result = {
        "ok": True,
        "center": {"lat": lat, "lon": lon},
        "radius_m": radius_m,
        "current_label": task.period_label,
        "prev_label": prev_label if prev_cards else None,
        "current": _serialize_period(stats.get("current")),
        "prev": _serialize_period(stats.get("prev")) if prev_cards else None,
    }

    # Кэшируем на задаче для повторного отображения
    task.last_point_stats = result
    # Сохраняем карточки (с _dist_m) для Excel-выгрузки
    cur = (stats.get("current") or {})
    prev = (stats.get("prev") or {})
    task.last_point_cards_current = list(cur.get("cards", []) or [])
    task.last_point_cards_prev = list(prev.get("cards", []) or []) if prev_cards else []
    task.last_point_params = {"lat": lat, "lon": lon, "radius_m": radius_m}
    return result
