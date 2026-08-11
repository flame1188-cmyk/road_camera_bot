"""
Facade-модуль для роутеров аналитики.

Sprint 3: оригинальный routers/analyze.py (758 строк, 12 эндпоинтов)
разбит на 4 модуля:
- _common.py — shared schemas + helpers (AnalysisStatusResponse,
  _require_done_task, _state_to_response)
- clusters.py — 4 эндпоинта очагов + clusters-схемы
- point.py — 3 эндпоинта точки + point-схемы
- llm.py — 5 эндпоинтов LLM + llm-схемы

Этот файл — тонкий facade для обратной совместимости:
- main.py продолжает делать `from .routers import analyze` +
  `app.include_router(analyze.router)` — ничего менять не нужно.
- tests/smoke/test_imports.py проверяет `backend.routers.analyze`
  импортируется — это продолжает работать.

Facade делает 2 вещи:
1. Создаёт агрегированный `router = APIRouter(prefix="/dtp", tags=["analyze"])`
   и include-ит в него 3 под-роутера (clusters, point, llm).
2. Реэкспортит все схемы и хелперы, чтобы любой код, который делал
   `from .routers.analyze import ClustersResponse` и т.п., продолжил работать.

ВНИМАНИЕ: не добавляйте новые эндпоинты в этот файл. Добавляйте их
в соответствующий под-модуль (clusters / point / llm).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from . import clusters, llm, point
from ._common import (
    AnalysisStatusResponse,
    _require_done_task,
    _state_to_response,
)

logger = logging.getLogger(__name__)

# ============================================================
# Aggregated router (главный API facade)
# ============================================================
# prefix="/dtp" — задаётся ЗДЕСЬ, на агрегированном router.
# Под-роутеры (clusters, point, llm) созданы БЕЗ prefix, чтобы не было
# /dtp/dtp/tasks/... Поэтому они включаются как есть.
router = APIRouter(prefix="/dtp", tags=["analyze"])
router.include_router(clusters.router)
router.include_router(point.router)
router.include_router(llm.router)


# ============================================================
# Re-export schemas и helpers (обратная совместимость)
# ============================================================
# Все символы, которые раньше были в analyze.py, должны быть импортируемыми
# отсюда же. Это гарантирует, что любой код с `from .routers.analyze import X`
# не сломается после сплита.

# Shared (из _common)
__all__ = [
    "router",
    "AnalysisStatusResponse",
    "_require_done_task",
    "_state_to_response",
]

# Clusters
from .clusters import (  # noqa: E402
    ClusterItem,
    ClustersResponse,
    ClustersResult,
    ClustersSummary,
    _clusters_result_to_response,
)
__all__ += [
    "ClusterItem",
    "ClustersResponse",
    "ClustersResult",
    "ClustersSummary",
    "_clusters_result_to_response",
]

# Point
from .point import (  # noqa: E402
    PointPeriodStats,
    PointRequest,
    PointStatsResponse,
)
__all__ += [
    "PointPeriodStats",
    "PointRequest",
    "PointStatsResponse",
]

# LLM
from .llm import (  # noqa: E402
    LLMAskRequest,
    LLMAskResponse,
    LLMProvidersResponse,
    LLMSummaryRequest,
    LLMSummaryResponse,
    LLMSummaryResult,
    QAHistoryItem,
)
__all__ += [
    "LLMAskRequest",
    "LLMAskResponse",
    "LLMProvidersResponse",
    "LLMSummaryRequest",
    "LLMSummaryResponse",
    "LLMSummaryResult",
    "QAHistoryItem",
]
