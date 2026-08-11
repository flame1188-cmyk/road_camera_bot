"""
Общие хелперы и схемы для роутеров аналитики (clusters / point / llm).

Вынесено в отдельный модуль, чтобы избежать дублирования между
clusters.py и llm.py (которые оба используют AnalysisStatusResponse
и _state_to_response), а также чтобы все роутеры могли переиспользовать
_require_done_task без циклических импортов.

Зависимости (только services + telegram_auth — без других роутеров):
- services.gibdd_service: Task, TaskStatus, get_task_async
- telegram_auth: TelegramUser, get_current_user

Никаких зависимостей от clusters.py / point.py / llm.py — иначе будет цикл.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException, status
from pydantic import BaseModel

from ..services.gibdd_service import Task, TaskStatus, get_task_async
from ..telegram_auth import TelegramUser, get_current_user  # noqa: F401 — re-export

logger = logging.getLogger(__name__)


# ============================================================
# Shared schemas
# ============================================================
class AnalysisStatusResponse(BaseModel):
    """
    Статус длительной операции (очаги / LLM-резюме).

    Используется в ClustersResponse и LLMSummaryResponse — поэтому живёт
    в _common, а не в одном из под-роутеров.
    """
    status: str  # idle | running | done | failed
    progress: int
    stage: str
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


# ============================================================
# Shared helpers
# ============================================================
async def _require_done_task(task_id: str, user: TelegramUser) -> Task:
    """
    Проверяет, что задача принадлежит пользователю и завершена.
    Возвращает task или raises HTTPException.

    Используется всеми роутерами аналитики: clusters, point, llm.
    Вынесено в _common, чтобы не дублировать логику 3 раза.
    """
    task = await get_task_async(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    if task.status != TaskStatus.DONE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Task status is '{task.status.value}', must be 'done' "
                f"to run analysis"
            ),
        )
    return task


def _state_to_response(state) -> AnalysisStatusResponse:
    """
    Преобразует AnalysisState в AnalysisStatusResponse.

    Используется роутерами clusters и llm (оба имеют long-running операции
    с state-машиной). Point — не использует (точка считается синхронно).
    """
    return AnalysisStatusResponse(
        status=state.status.value if hasattr(state.status, "value") else str(state.status),
        progress=state.progress,
        stage=state.stage,
        error=state.error,
        started_at=state.started_at.isoformat() if state.started_at else None,
        finished_at=state.finished_at.isoformat() if state.finished_at else None,
    )
