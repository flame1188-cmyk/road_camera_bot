"""
Роутер для вкладки «НП БДД» (Национальный проект «Безопасные дорожные движения»).

Эндпоинты:
  GET  /api/np-bdd/regions                              — список регионов.
  GET  /api/np-bdd/data?region_code=...&plan_line_mode=...  — главный payload.
  GET  /api/np-bdd/settings?region_code=...             — настройки (plan_line_mode).
  PATCH /api/np-bdd/settings                             — обновить настройки.
  GET  /api/np-bdd/frozen?region_code=...               — список замороженных лет.
  POST /api/np-bdd/freeze                                — заморозить год.
  POST /api/np-bdd/unfreeze                              — разморозить год.
"""
from __future__ import annotations

from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..services.np_bdd_service import (
    freeze_year as svc_freeze_year,
    get_data as svc_get_data,
    get_debug_info as svc_get_debug_info,
    get_settings as svc_get_settings,
    list_frozen_years as svc_list_frozen_years,
    list_regions as svc_list_regions,
    unfreeze_year as svc_unfreeze_year,
    update_settings as svc_update_settings,
)
from ..telegram_auth import TelegramUser, get_current_user

router = APIRouter(prefix="/np-bdd", tags=["np-bdd"])


# --- Pydantic-модели ------------------------------------------------------


class Region(BaseModel):
    code: str
    name: str


class FreezeRequest(BaseModel):
    region_code: str = Field(..., examples=["1106"])
    year: int = Field(..., ge=2020, le=2050)
    note: Optional[str] = Field(None, max_length=500)


class UnfreezeRequest(BaseModel):
    region_code: str
    year: int = Field(..., ge=2020, le=2050)


class SettingsUpdate(BaseModel):
    region_code: str
    plan_line_mode: Optional[Literal["linear", "horizontal"]] = None
    forecast_method: Optional[Literal["central_only", "corridor"]] = None


# --- Эндпоинты -------------------------------------------------------------


@router.get("/regions", response_model=List[Region])
async def regions_list(user: TelegramUser = Depends(get_current_user)):
    """Список регионов, для которых есть данные (Ктс + план)."""
    return await svc_list_regions()


@router.get("/data")
async def get_npbdd_data(
    region_code: str = Query(..., examples=["1106"]),
    plan_line_mode: Literal["linear", "horizontal"] = Query("linear"),
    forecast_method: Literal["central_only", "corridor"] = Query("corridor"),
    user: TelegramUser = Depends(get_current_user),
):
    """
    Главный payload для UI НП БДД.

    Возвращает:
    - region: {code, name}
    - history: {"2023": {deaths, vehicles, tr, frozen?, source}, ...}
    - current_year: {year, months_actual, months_forecast, deaths_by_month_actual,
                     deaths_ytd, deaths_forecast_full_year, deaths_forecast_optimistic?,
                     deaths_forecast_pessimistic?, tr_actual_ytd,
                     tr_forecast_full_year, tr_forecast_optimistic?,
                     tr_forecast_pessimistic?, tr_plan, monthly_chart}
    - plan_series: {"2023": 2.03, ..., "2030": 0.91}
    - kpi: {tr_actual_ytd, tr_forecast_full_year, tr_forecast_optimistic?,
            tr_forecast_pessimistic?, tr_plan, deviation_pct, status}
    - forecast_method: "central_only" | "corridor"
    - corridor_available: bool
    - calculated_at

    Кэшируется на 10 минут на бэкенде.
    """
    try:
        return await svc_get_data(
            region_code,
            plan_line_mode=plan_line_mode,
            forecast_method=forecast_method,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка: {exc}")


@router.get("/settings")
async def get_npbdd_settings(
    region_code: str = Query(...),
    user: TelegramUser = Depends(get_current_user),
):
    """Настройки региона (plan_line_mode и forecast_method)."""
    return await svc_get_settings(region_code)


@router.patch("/settings")
async def update_npbdd_settings(
    payload: SettingsUpdate,
    user: TelegramUser = Depends(get_current_user),
):
    """Обновить настройки региона."""
    return await svc_update_settings(
        region_code=payload.region_code,
        plan_line_mode=payload.plan_line_mode,
        forecast_method=payload.forecast_method,
    )


@router.get("/frozen")
async def get_frozen_years(
    region_code: str = Query(...),
    user: TelegramUser = Depends(get_current_user),
):
    """Список замороженных лет для региона."""
    return await svc_list_frozen_years(region_code)


@router.post("/freeze")
async def freeze_year_endpoint(
    payload: FreezeRequest,
    user: TelegramUser = Depends(get_current_user),
):
    """Заморозить год для региона (создаёт снапшот в data/freeze/)."""
    try:
        record = await svc_freeze_year(
            region_code=payload.region_code,
            year=payload.year,
            note=payload.note,
            frozen_by=f"tg:{user.id}",
        )
        return {
            "ok": True,
            "region_code": payload.region_code,
            "year": payload.year,
            "record": record,
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка: {exc}")


@router.post("/unfreeze")
async def unfreeze_year_endpoint(
    payload: UnfreezeRequest,
    user: TelegramUser = Depends(get_current_user),
):
    """Разморозить год для региона."""
    return await svc_unfreeze_year(payload.region_code, payload.year)


# --- Диагностический эндпоинт (без авторизации) ---------------------------
# Временный эндпоинт для отладки проблемы «Нет данных по регионам».
# Можно удалить после настройки. Возвращает пути и наличие файлов.

@router.get("/_debug")
async def debug_info():
    """
    Диагностика: показывает NPBDD_ROOT, кандидатов путей, наличие файлов
    data/vehicles/, data/plans/ и т.д.
    Без авторизации — для удобства отладки на сервере.
    """
    return svc_get_debug_info()
