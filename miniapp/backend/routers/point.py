"""
Роутер статистики ДТП по точке (point).

Endpoints:
- POST /api/dtp/tasks/{task_id}/point       — статистика по точке (sync)
- GET  /api/dtp/tasks/{task_id}/point/excel — Excel статистики по точке
- GET  /api/dtp/tasks/{task_id}/point/map   — HTML-карта точки (iframe)

Все endpoints требуют готовую задачу (task.status == 'done').

Вынесено из routers/analyze.py (Sprint 3).
"""
from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from ..services.gibdd_service import (
    compute_point_stats,
    generate_point_stats_excel,
    generate_point_stats_map_html,
)
from ..telegram_auth import TelegramUser, get_current_user
from ._common import _require_done_task

logger = logging.getLogger(__name__)

# Без prefix — analyze.py (facade) задаёт /dtp на агрегированном router.
router = APIRouter(tags=["analyze"])


# ============================================================
# Schemas
# ============================================================
class PointRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90, description="Широта")
    lon: float = Field(..., ge=-180, le=180, description="Долгота")
    radius_m: int = Field(
        default=500, gt=0, le=10000,
        description="Радиус в метрах (рекомендуется: 250, 500, 1000, 3000)",
    )


class PointPeriodStats(BaseModel):
    total: int
    deaths: int
    injured: int
    alcohol: int
    pedestrians: int
    by_type: Dict[str, int]
    by_road: Dict[str, int]
    by_weather: Dict[str, int]
    cards_count: int
    cards_preview: List[Dict[str, Any]] = []


class PointStatsResponse(BaseModel):
    ok: bool
    center: Dict[str, float]
    radius_m: int
    current_label: str
    prev_label: str | None = None
    current: PointPeriodStats | None = None
    prev: PointPeriodStats | None = None
    error: str | None = None


# ============================================================
# Endpoints
# ============================================================
@router.post(
    "/tasks/{task_id}/point",
    response_model=PointStatsResponse,
)
async def compute_point_statistics(
    task_id: str,
    request: PointRequest,
    user: TelegramUser = Depends(get_current_user),
):
    """
    Считает статистику ДТП в радиусе от точки.

    Быстрая операция (<1 сек): фильтрация карточек по радиусу
    через формулу Гаверсинуса.

    Автоматически загружает данные за прошлый год (если ещё нет)
    для сравнения динамики.
    """
    task = await _require_done_task(task_id, user)
    result = await compute_point_stats(
        task=task,
        lat=request.lat,
        lon=request.lon,
        radius_m=request.radius_m,
    )
    if not result.get("ok"):
        return PointStatsResponse(
            ok=False,
            center={"lat": request.lat, "lon": request.lon},
            radius_m=request.radius_m,
            current_label=task.period_label,
            error=result.get("error", "Неизвестная ошибка"),
        )

    return PointStatsResponse(
        ok=True,
        center=result["center"],
        radius_m=result["radius_m"],
        current_label=result["current_label"],
        prev_label=result.get("prev_label"),
        current=result.get("current"),
        prev=result.get("prev"),
    )


@router.get(
    "/tasks/{task_id}/point/excel",
)
async def get_point_stats_excel(
    task_id: str,
    user: TelegramUser = Depends(get_current_user),
):
    """
    Скачивает Excel-файл со статистикой по точке (2 листа):
      Лист 1 — текущий период (все ДТП в радиусе с детальной информацией)
      Лист 2 — прошлый период (если есть)

    Требует предварительно выполненный POST /point — берёт карточки из кэша задачи.

    Content-Disposition: attachment; filename="point_stats_<регион>_<период>.xlsx"
    """
    task = await _require_done_task(task_id, user)

    if not task.last_point_cards_current and not task.last_point_cards_prev:
        raise HTTPException(
            status_code=404,
            detail=(
                "Point statistics not calculated yet. "
                "Call POST /point first with lat/lon/radius_m."
            ),
        )

    xlsx_bytes = await generate_point_stats_excel(task)
    if not xlsx_bytes:
        raise HTTPException(
            status_code=500,
            detail="Excel generation failed",
        )

    # Безопасное имя файла (RFC 5987: ASCII-fallback + UTF-8 form)
    safe_reg_ascii = re.sub(
        r"[^A-Za-z0-9_-]", "_", task.region_name[:30]
    ).strip("_") or "region"
    params = task.last_point_params or {}
    lat_str = f"{params.get('lat', 0):.4f}".replace(".", "-")
    lon_str = f"{params.get('lon', 0):.4f}".replace(".", "-")
    radius = int(params.get("radius_m", 0))
    filename_ascii = (
        f"point_stats_{safe_reg_ascii}_{lat_str}_{lon_str}_{radius}m.xlsx"
    )
    # Полное имя с кириллицей для современных клиентов
    filename_full = (
        f"point_stats_{task.region_name}_{lat_str}_{lon_str}_{radius}m.xlsx"
    )
    filename_utf8 = urllib.parse.quote(filename_full, safe="")

    return Response(
        content=xlsx_bytes,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename_ascii}"; '
                f"filename*=UTF-8''{filename_utf8}"
            ),
        },
    )


@router.get(
    "/tasks/{task_id}/point/map",
    response_class=HTMLResponse,
)
async def get_point_stats_map(
    task_id: str,
    lat: float = Query(..., ge=-90, le=90, description="Широта"),
    lon: float = Query(..., ge=-180, le=180, description="Долгота"),
    radius_m: int = Query(
        default=500, gt=0, le=10000,
        description="Радиус в метрах",
    ),
    user: TelegramUser = Depends(get_current_user),
):
    """
    Отдаёт HTML-карту статистики по точке (Leaflet в iframe).

    Карта: точка запроса + круг радиуса + ДТП (текущий/прошлый) +
    камеры в радиусе. С попапами на каждой точке.
    """
    task = await _require_done_task(task_id, user)

    html = await generate_point_stats_map_html(task, lat, lon, radius_m)
    if not html:
        raise HTTPException(
            status_code=500,
            detail="Map generation failed",
        )
    return HTMLResponse(content=html)
