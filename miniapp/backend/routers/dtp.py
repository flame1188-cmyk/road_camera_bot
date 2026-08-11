"""
Основной роутер: создание задач выгрузки, опрос статуса, скачивание файлов.

Архитектура endpoint'ов:
- POST /api/dtp/tasks           — создать задачу (async выполнение в фоне)
- GET  /api/dtp/tasks           — список задач пользователя
- GET  /api/dtp/tasks/{id}      — статус задачи (для polling)
- GET  /api/dtp/tasks/{id}/files— список готовых файлов
- GET  /api/dtp/tasks/{id}/map  — HTML-карта (для iframe)
- GET  /api/dtp/tasks/{id}/download/{file_type} — скачать Excel/HTML
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from ..services.gibdd_service import (
    Task,
    TaskStatus,
    create_task,
    execute_task,
    get_task_async,
    list_user_tasks,
    parse_user_query,
)
from ..telegram_auth import TelegramUser, get_current_user

router = APIRouter(prefix="/dtp", tags=["dtp"])


# ============================================================
# Schemas
# ============================================================
class TaskCreateRequest(BaseModel):
    """Запрос на создание задачи выгрузки.

    Поддерживает два режима:
    1. Structured (рекомендуется): регион + период выбраны из списка.
       Поля region_code, region_name, dat_list, period_label заполнены.
       Парсинг текста не выполняется — ошибок распознавания нет.
    2. Text (legacy): произвольный текст в `query`.
       Парсится через user_request_parser.

    Если region_code + dat_list заполнены → structured mode,
    иначе — text mode (тогда `query` обязателен).
    """

    query: Optional[str] = Field(
        default=None, max_length=500,
        description="Текстовый запрос (legacy-режим). "
                    "Игнорируется, если заданы region_code и dat_list.",
    )
    region_code: Optional[str] = Field(
        default=None,
        description="Код региона (например '1101'). Structured-режим.",
    )
    region_name: Optional[str] = Field(
        default=None,
        description="Название региона для отображения. Structured-режим.",
    )
    dat_list: Optional[List[str]] = Field(
        default=None,
        description="Список месяцев в формате 'M.YYYY' (например ['1.2025', '2.2025']).",
    )
    period_label: Optional[str] = Field(
        default=None,
        description="Человекочитаемая метка периода (например '2025 год').",
    )


class TaskCreateResponse(BaseModel):
    task_id: str
    status: TaskStatus
    region_code: str
    region_name: str
    period: str


class TaskFileSchema(BaseModel):
    # task.files содержит также "path", которого нет в схеме — игнорируем лишнее
    model_config = ConfigDict(extra="ignore")

    type: str
    filename: str
    size_bytes: int
    mime: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: TaskStatus
    progress: int
    region_code: str
    region_name: str
    period: str
    total_dtp: int = 0
    total_dead: int = 0
    total_injured: int = 0
    error: Optional[str] = None
    files: List[TaskFileSchema] = []
    analytics: Optional[Dict[str, Any]] = None


# ============================================================
# Endpoints
# ============================================================
@router.post("/tasks", response_model=TaskCreateResponse)
async def create_dtp_task(
    request: TaskCreateRequest,
    user: TelegramUser = Depends(get_current_user),
):
    """
    Создаёт задачу выгрузки ДТП и запускает её асинхронно (через asyncio).

    Два режима:
    - Structured: заполнены region_code + dat_list (+ region_name, period_label).
      Парсинг текста не выполняется.
    - Text (legacy): только query. Парсится через user_request_parser.
    """
    # === Определяем режим ===
    is_structured = bool(
        request.region_code
        and request.dat_list
        and len(request.dat_list) > 0
    )

    if is_structured:
        # Structured mode — без парсинга
        region_code = request.region_code or ""
        region_name = request.region_name or f"Регион {region_code}"
        dat_list = request.dat_list or []
        period_label = request.period_label or (
            f"{len(dat_list)} мес." if dat_list else "—"
        )
        raw_query = f"[structured] {region_name} | {period_label} | {dat_list}"
    else:
        # Text mode — парсим через user_request_parser
        if not request.query or len(request.query.strip()) < 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Укажите либо region_code + dat_list (structured mode), "
                    "либо query длиной минимум 2 символа (text mode)."
                ),
            )
        parsed = await parse_user_query(request.query)
        if not parsed.get("ok"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=parsed.get("error", "Не удалось распознать запрос"),
            )
        region_code = parsed["region_code"]
        region_name = parsed["region_name"]
        dat_list = parsed["dat_list"]
        period_label = parsed["period"]
        raw_query = request.query

    task = create_task(
        user_id=user.id,
        region_code=region_code,
        region_name=region_name,
        period_label=period_label,
        dat_list=dat_list,
        raw_query=raw_query,
    )

    # Аудит обращения к ПДн (152-ФЗ): пользователь создал задачу выгрузки.
    # Логируем регион/период — этого достаточно для журнала доступа.
    try:
        from ..db.repository import log_access
        await log_access(
            user_id=user.id,
            action="create_task",
            region_code=region_code,
            period_label=period_label,
            task_id=task.id,
        )
    except Exception:
        pass  # аудит не должен ронять создание задачи

    # Асинхронный запуск в фоне — execute_task сам обновляет статус
    asyncio_create_task(task.id)

    return TaskCreateResponse(
        task_id=task.id,
        status=task.status,
        region_code=task.region_code,
        region_name=task.region_name,
        period=task.period_label,
    )


def asyncio_create_task(task_id: str) -> None:
    """
    Запускает async execute_task в текущем event loop.
    Не блокирует HTTP-ответ.
    """
    import asyncio
    loop = asyncio.get_running_loop()
    loop.create_task(execute_task(task_id))


@router.get("/tasks", response_model=List[TaskStatusResponse])
async def list_tasks(
    user: TelegramUser = Depends(get_current_user),
    limit: int = 20,
):
    """Возвращает последние N задач пользователя."""
    tasks = await list_user_tasks(user.id, limit=limit)
    return [_task_to_response(t) for t in tasks]


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(
    task_id: str,
    user: TelegramUser = Depends(get_current_user),
):
    """Возвращает статус задачи (для polling из frontend)."""
    task = await get_task_async(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    if task.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )
    return _task_to_response(task)


@router.get("/tasks/{task_id}/files", response_model=List[TaskFileSchema])
async def list_task_files(
    task_id: str,
    user: TelegramUser = Depends(get_current_user),
):
    """Возвращает список файлов, сгенерированных задачей."""
    task = await get_task_async(task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="Task not found")
    return [TaskFileSchema(**f) for f in task.files]


@router.get("/tasks/{task_id}/map", response_class=HTMLResponse)
async def get_task_map(
    task_id: str,
    user: TelegramUser = Depends(get_current_user),
):
    """
    Отдаёт HTML-карту (inline Leaflet с кластеризацией).
    Используется в <iframe> на frontend.
    """
    task = await get_task_async(task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="Task not found")

    map_file = next(
        (f for f in task.files if f["type"] == "map_html"),
        None,
    )
    if not map_file:
        raise HTTPException(status_code=404, detail="Map file not generated yet")

    path = Path(map_file["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Map file missing on disk")

    return HTMLResponse(content=path.read_text(encoding="utf-8"))


@router.get("/tasks/{task_id}/download/{file_type}")
async def download_file(
    task_id: str,
    file_type: str,
    user: TelegramUser = Depends(get_current_user),
):
    """
    Скачивание Excel/HTML-файла.

    file_type: 'dtp_cards' | 'dtp_participants' | 'map_html'
    """
    task = await get_task_async(task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="Task not found")

    file_meta = next(
        (f for f in task.files if f["type"] == file_type),
        None,
    )
    if not file_meta:
        raise HTTPException(
            status_code=404,
            detail=f"File of type '{file_type}' not found",
        )

    path = Path(file_meta["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk")

    return FileResponse(
        path=str(path),
        media_type=file_meta["mime"],
        filename=file_meta["filename"],
    )


# ============================================================
# Helpers
# ============================================================
def _task_to_response(task: Task) -> TaskStatusResponse:
    return TaskStatusResponse(
        task_id=task.id,
        status=task.status,
        progress=task.progress,
        region_code=task.region_code,
        region_name=task.region_name,
        period=task.period_label,
        total_dtp=task.total_dtp,
        total_dead=task.total_dead,
        total_injured=task.total_injured,
        error=task.error,
        files=[TaskFileSchema(**f) for f in task.files],
        analytics=task.analytics,
    )
