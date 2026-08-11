"""
Роутер очагов концентрации ДТП (clusters).

Endpoints:
- POST /api/dtp/tasks/{task_id}/clusters        — запуск расчёта очагов (async)
- GET  /api/dtp/tasks/{task_id}/clusters        — статус/результат очагов
- GET  /api/dtp/tasks/{task_id}/clusters/map    — HTML-карта очагов (iframe)
- GET  /api/dtp/tasks/{task_id}/clusters/excel  — Excel-файл очагов (4 листа)

Все endpoints требуют готовую задачу (task.status == 'done').

Вынесено из routers/analyze.py (Sprint 3) — оригинальный файл 758 строк
разбит на 4 модуля: _common (shared), clusters, point, llm.
analyze.py теперь — тонкий facade, реэкспортящий router и все schemas
для обратной совместимости (main.py использует analyze.router).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from ..services.gibdd_service import (
    AnalysisStatus,
    generate_clusters_excel,
    generate_clusters_map_html,
    start_clusters_calculation,
)
from ..telegram_auth import TelegramUser, get_current_user
from ._common import AnalysisStatusResponse, _require_done_task, _state_to_response

logger = logging.getLogger(__name__)

# Без prefix — analyze.py (facade) задаёт /dtp на агрегированном router.
# Tags=["analyze"] — чтобы все эндпоинты группировались в Swagger.
router = APIRouter(tags=["analyze"])


# ============================================================
# Schemas
# ============================================================
class ClustersSummary(BaseModel):
    total_clusters: int
    total_lost: int
    # total_prev_matched — очаги прошлого года, повторённые в текущем
    # (отдельная строка в Excel/карте со ссылкой на текущий №).
    # Опционально для обратной совместимости со старыми сохранёнными задачами.
    total_prev_matched: Optional[int] = 0
    total_preclusters: int
    current_total_dtp: int
    current_deaths: int
    current_injured: int
    dynamics: Dict[str, int]
    has_prev_data: bool
    prev_label: Optional[str] = None
    current_label: str
    region_name: str


class ClusterItem(BaseModel):
    road: str
    zone_type: str
    total_accidents: int
    deaths: int
    injured: int
    # None означает "смешанный тип" — 5+ ДТП разных видов без явного доминанта
    dominant_type: Optional[str] = None
    type_counter: Dict[str, int]
    center: Optional[Dict[str, float]] = None
    start_pos: Optional[float] = None
    end_pos: Optional[float] = None
    dates: List[str] = []
    dynamics: Dict[str, Any] = {}
    camera_match: Optional[Dict[str, Any]] = None
    # Флаги для фильтрации на фронтенде:
    #   is_lost=True — очаг прошлого периода, в текущем исчез (ДТП ниже порога).
    #     В Top-10 текущих очагов не должен попадаться.
    #   is_prev_matched=True — АППГ-очаг, повторённый в текущем (дубликат
    #     повторного). Тоже исключается из Top-10 текущих.
    # Без этих полей Pydantic молча отбрасывает их при ClusterItem(**c),
    # и фронтенд видит is_lost=undefined → фильтр !is_lost всегда true.
    is_lost: bool = False
    is_prev_matched: bool = False


class ClustersResult(BaseModel):
    summary: ClustersSummary
    clusters: List[ClusterItem]
    preclusters: List[ClusterItem]


class ClustersResponse(BaseModel):
    """Ответ POST /clusters и GET /clusters."""
    state: AnalysisStatusResponse
    result: Optional[ClustersResult] = None


# ============================================================
# Helpers
# ============================================================
def _clusters_result_to_response(result: Optional[dict]) -> Optional[ClustersResult]:
    """Преобразует результат в ClustersResult."""
    if not result:
        return None

    summary = ClustersSummary(
        total_clusters=result.get("total_clusters", 0),
        total_lost=result.get("total_lost", 0),
        total_prev_matched=result.get("total_prev_matched", 0),
        total_preclusters=result.get("total_preclusters", 0),
        current_total_dtp=result.get("current_total_dtp", 0),
        current_deaths=result.get("current_deaths", 0),
        current_injured=result.get("current_injured", 0),
        dynamics=result.get("dynamics", {}),
        has_prev_data=result.get("has_prev_data", False),
        prev_label=result.get("prev_label"),
        current_label=result.get("current_label", ""),
        region_name=result.get("region_name", ""),
    )

    clusters = [ClusterItem(**c) for c in result.get("clusters", [])]
    preclusters = [ClusterItem(**p) for p in result.get("preclusters", [])]

    return ClustersResult(summary=summary, clusters=clusters, preclusters=preclusters)


# ============================================================
# Endpoints
# ============================================================
@router.post(
    "/tasks/{task_id}/clusters",
    response_model=ClustersResponse,
)
async def start_clusters(
    task_id: str,
    user: TelegramUser = Depends(get_current_user),
):
    """
    Запускает асинхронный расчёт очагов концентрации ДТП.

    Длительная операция (15-30 сек):
      1. Загрузка границ НП из OpenStreetMap
      2. Классификация ДТП (в НП / вне НП)
      3. Кластеризация по радиусу + пикетажу
      4. Сопоставление с прошлым годом (динамика)
      5. Обогащение камерами (если есть)

    Повторный вызов возвращает текущий статус.
    Если расчёт уже выполнен — возвращает готовый результат без пересчёта.
    """
    task = await _require_done_task(task_id, user)
    state = task.clusters_state

    # Если уже выполнено — возвращаем готовое.
    # НО: Sprint 3.2 — если task.raw_clusters и task.raw_preclusters пусты,
    # значит кэш восстановил state.result, но без raw-данных (старая запись
    # без raw_clusters). В этом случае форсируем recompute, иначе карта
    # упадёт в simple map (без слоёв/попапов), а Excel вернёт None.
    if state.status == AnalysisStatus.DONE:
        if not task.raw_clusters and not task.raw_preclusters:
            logger.info(
                f"Task {task_id}: clusters state=DONE, но raw_clusters/"
                f"raw_preclusters пусты — форсируем recompute (Sprint 3.2)"
            )
            # Сбрасываем state и запускаем расчёт заново.
            # После пересчёта state.result и raw_clusters заполнятся,
            # а put_cached_clusters обновит запись в clusters_cache
            # (теперь уже с raw_clusters). Следующий запрос вернёт DONE
            # без recompute.
            state.status = AnalysisStatus.IDLE
            state.progress = 0
            state.stage = "Запуск..."
            state.started_at = None
            state.finished_at = None
            state.result = None
            # raw_clusters/raw_preclusters и так пустые
            loop = asyncio.get_running_loop()
            loop.create_task(start_clusters_calculation(task))
            return ClustersResponse(state=_state_to_response(state))
        return ClustersResponse(
            state=_state_to_response(state),
            result=_clusters_result_to_response(state.result),
        )

    # Если уже выполняется — возвращаем статус
    if state.status == AnalysisStatus.RUNNING:
        return ClustersResponse(state=_state_to_response(state))

    # Если предыдущая попытка упала — перезапускаем
    # Запускаем async
    loop = asyncio.get_running_loop()
    loop.create_task(start_clusters_calculation(task))

    return ClustersResponse(state=_state_to_response(state))


@router.get(
    "/tasks/{task_id}/clusters",
    response_model=ClustersResponse,
)
async def get_clusters_status(
    task_id: str,
    wait: int = 0,
    user: TelegramUser = Depends(get_current_user),
):
    """
    Возвращает статус расчёта очагов.

    Поддержка long polling: если ?wait=N (секунды) и статус running,
    endpoint держит соединение открытым до N секунд, ожидая завершения.
    Возвращает сразу при смене статуса на done/failed или по таймауту.
    """
    task = await _require_done_task(task_id, user)
    state = task.clusters_state

    # Long polling: ждём, пока статус running, до `wait` секунд.
    # time.monotonic() предпочтительнее asyncio.get_event_loop().time()
    # (тот устарел в Python 3.10+ и выдаёт DeprecationWarning).
    if wait > 0 and state.status == AnalysisStatus.RUNNING:
        deadline = time.monotonic() + min(wait, 60)
        while (
            state.status == AnalysisStatus.RUNNING
            and time.monotonic() < deadline
        ):
            await asyncio.sleep(1)

    return ClustersResponse(
        state=_state_to_response(state),
        result=_clusters_result_to_response(state.result)
        if state.status == AnalysisStatus.DONE
        else None,
    )


@router.get(
    "/tasks/{task_id}/clusters/map",
    response_class=HTMLResponse,
)
async def get_clusters_map(
    task_id: str,
    user: TelegramUser = Depends(get_current_user),
):
    """
    Отдаёт HTML-карту очагов (Leaflet с маркерами).
    Используется в <iframe> на frontend.

    Полноценная карта из Telegram-бота:
    - Слои (Очаги / ДТП в очагах / Предочаги / Камеры)
    - Popups на ДТП и очагах с детальной информацией
    - Линейка для измерения расстояний
    - Convex hull (зона очага)
    - Динамика (новые/рост/снижение/стабильный/исчезнувший)
    - Фильтр камер по моделям
    """
    task = await _require_done_task(task_id, user)
    if task.clusters_state.status != AnalysisStatus.DONE:
        raise HTTPException(
            status_code=404,
            detail="Clusters not calculated yet. Call POST /clusters first.",
        )
    html = await generate_clusters_map_html(task)
    if not html:
        raise HTTPException(status_code=500, detail="Map generation failed")
    return HTMLResponse(content=html)


@router.get(
    "/tasks/{task_id}/clusters/excel",
)
async def get_clusters_excel(
    task_id: str,
    user: TelegramUser = Depends(get_current_user),
):
    """
    Скачивает Excel-файл с очагами ДТП (4 листа):
      Лист 1 «Очаги ДТП» — текущие очаги (с цветовым кодированием зоны)
      Лист 2 «Динамика очагов» — текущие + исчезнувшие со статусом
      Лист 3 «Детализация ДТП» — все ДТП по периодам
      Лист 4 «Предочаги» — места, не дотянувшие до очага

    Content-Disposition: attachment; filename="dtp_ochagi_<регион>_<период>.xlsx"
    """
    task = await _require_done_task(task_id, user)
    if task.clusters_state.status != AnalysisStatus.DONE:
        raise HTTPException(
            status_code=404,
            detail="Clusters not calculated yet. Call POST /clusters first.",
        )

    xlsx_bytes = await generate_clusters_excel(task)
    if not xlsx_bytes:
        raise HTTPException(
            status_code=500,
            detail="Excel generation failed",
        )

    # Безопасное имя файла (RFC 5987: ASCII-fallback + UTF-8 form)
    # Cyrillic в filename= ломает starlette (latin-1 encode),
    # поэтому ASCII fallback + filename*=UTF-8''<urlencoded>
    safe_reg_ascii = re.sub(
        r"[^A-Za-z0-9_-]", "_", task.region_name[:30]
    ).strip("_") or "region"
    safe_period_ascii = re.sub(
        r"[^A-Za-z0-9_-]", "_", task.period_label[:30]
    ).strip("_") or "period"
    filename_ascii = f"dtp_ochagi_{safe_reg_ascii}_{safe_period_ascii}.xlsx"
    # Полное имя с кириллицей для современных клиентов
    filename_full = f"dtp_ochagi_{task.region_name}_{task.period_label}.xlsx"
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
