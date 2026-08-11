"""
Excel-выгрузка и HTML-карта для статистики по точке.

- generate_point_stats_excel() — 2-листный Excel (текущий/прошлый период
  со всеми ДТП в радиусе и детализацией)
- generate_point_stats_map_html() — Leaflet-карта через ReportGenerator
  (точка + радиус + ДТП + камеры в радиусе)

Требуют предварительно выполненный compute_point_stats — берут сохранённые
в task карточки (с дистанцией _dist_m).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from . import _imports
from .models import Task
from .pipeline import ensure_prev_cards

logger = logging.getLogger(__name__)


# ============================================================
# Excel-выгрузка: статистика по точке (2 листа)
# ============================================================
async def generate_point_stats_excel(task: Task) -> Optional[bytes]:
    """
    Генерирует Excel-файл со статистикой по точке (2 листа):
      Лист 1 — текущий период (все ДТП в радиусе с детализацией)
      Лист 2 — прошлый период (если есть данные)

    Требует предварительно выполненный compute_point_stats —
    берёт сохранённые в task карточки (с дистанцией _dist_m).
    """
    if not task.last_point_cards_current and not task.last_point_cards_prev:
        return None

    try:
        point_stats_module = _imports._import_module("point_statistics")
        excel_module = _imports._import_module("excel_generator")

        current_rows, prev_rows = point_stats_module.build_point_stats_excel_data(
            task.last_point_cards_current,
            task.last_point_cards_prev if task.last_point_cards_prev else None,
            task.period_label,
            task.prev_label or "",
        )
        columns = point_stats_module.get_point_stats_column_names()

        xlsx_bytes = await asyncio.to_thread(
            excel_module.generate_point_stats_file,
            current_rows,
            prev_rows if prev_rows else None,
            columns,
            task.period_label,
            task.prev_label if prev_rows else None,
        )

        logger.info(
            f"Task {task.id}: point stats Excel generated — "
            f"{len(current_rows)} текущих, {len(prev_rows)} прошлых"
        )
        return xlsx_bytes

    except Exception as exc:
        logger.exception(f"Task {task.id}: point stats Excel generation failed")
        return None


# ============================================================
# Карта статистики по точке
# ============================================================
async def generate_point_stats_map_html(
    task: Task,
    lat: float,
    lon: float,
    radius_m: int,
) -> Optional[str]:
    """
    Генерирует HTML-карту статистики по точке через
    ReportGenerator.generate_point_stats_map() из бота.

    Карта: точка + радиус + ДТП (текущий/прошлый) + камеры в радиусе.
    """
    if not task.cards:
        return None

    try:
        report_gen_module = _imports._import_module("report_generator")
        point_stats_module = _imports._import_module("point_statistics")
        camera_cache_module = _imports._import_module("camera_cache")
        camera_matcher_module = _imports._import_module("camera_matcher")

        # Загружаем прошлый год (если ещё нет)
        if not task.prev_cards_loaded:
            await ensure_prev_cards(task)
        prev_cards = task.prev_cards or []

        # Камеры в радиусе
        cameras_in_radius = []
        try:
            if camera_cache_module.has_cached_cameras(task.region_code):
                all_cameras = camera_cache_module.load_cameras_from_cache(
                    task.region_code
                ) or []
                for cam in all_cameras:
                    d = camera_matcher_module.haversine(
                        lat, lon, cam["lat"], cam["lon"]
                    )
                    if d <= radius_m:
                        cameras_in_radius.append({**cam, "distance_m": round(d, 0)})
        except Exception as exc:
            logger.warning(
                f"Task {task.id}: cameras for point map failed: {exc}"
            )

        gen = report_gen_module.ReportGenerator(
            region_name=task.region_name,
            period_label=task.period_label,
        )
        html = await asyncio.to_thread(
            gen.generate_point_stats_map,
            lat, lon, radius_m,
            task.cards,
            prev_cards if prev_cards else None,
            cameras_in_radius if cameras_in_radius else None,
            task.period_label,
            task.prev_label or "",
        )

        logger.info(
            f"Task {task.id}: point stats map generated — "
            f"lat={lat}, lon={lon}, radius={radius_m}м, "
            f"{len(cameras_in_radius)} камер в радиусе"
        )
        return html

    except Exception as exc:
        logger.exception(f"Task {task.id}: point stats map generation failed")
        return None
