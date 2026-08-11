"""
Модели данных service-слоя MiniApp.

Task — основная асинхронная задача выгрузки ДТП.
AnalysisState — состояние длительной аналитической операции (очаги/LLM).
TaskStatus / AnalysisStatus — статусы для state-машин.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskStatus(str, Enum):
    """Статус асинхронной задачи выгрузки."""

    PENDING = "pending"
    FETCHING = "fetching"
    PARSING = "parsing"
    ANALYTICS = "analytics"
    GENERATING = "generating"
    DONE = "done"
    FAILED = "failed"


class AnalysisStatus(str, Enum):
    """Статус длительной аналитической операции (очаги/LLM)."""

    IDLE = "idle"              # ещё не запускали
    RUNNING = "running"        # выполняется
    DONE = "done"              # готово
    FAILED = "failed"          # ошибка


@dataclass
class AnalysisState:
    """Состояние длительной аналитической операции.

    Хранится прямо в Task, чтобы переиспользовать результат
    при повторном открытии вкладки (без пересчёта).
    """

    status: AnalysisStatus = AnalysisStatus.IDLE
    progress: int = 0
    stage: str = ""            # человекочитаемая стадия
    result: Optional[Any] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    def reset(self) -> None:
        self.status = AnalysisStatus.IDLE
        self.progress = 0
        self.stage = ""
        self.result = None
        self.error = None
        self.started_at = None
        self.finished_at = None


@dataclass
class Task:
    """Описание асинхронной задачи выгрузки."""

    id: str
    user_id: int
    region_code: str
    region_name: str
    period_label: str
    dat_list: List[str]
    raw_query: str
    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0
    error: Optional[str] = None
    files: List[Dict[str, Any]] = field(default_factory=list)
    analytics: Optional[Dict[str, Any]] = None
    total_dtp: int = 0
    total_dead: int = 0
    total_injured: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # === Persisted data for downstream analysis ===
    # Сырые карточки ДТП текущего периода — нужны для очагов, точки, LLM
    cards: List[Dict[str, Any]] = field(default_factory=list)

    # Карточки за прошлый год (lazy-loaded через _ensure_prev_cards)
    prev_cards: List[Dict[str, Any]] = field(default_factory=list)
    prev_label: Optional[str] = None
    prev_cards_loaded: bool = False

    # Сравнение метрик (current vs prev) — нужно для LLM
    comparison: Optional[Dict[str, Any]] = None

    # === Phase 3.1: In-memory кэш analytics-расчётов ===
    # Раньше calculate_cross_tables / calculate_metrics пересчитывались
    # при каждом LLM-запросе и при каждом Q&A. На регионе 2629 ДТП это
    # ~80 ms CPU на каждый запрос — мелочь, но при частых Q&A накапливается.
    # Теперь считаем один раз и переиспользуем до вытеснения задачи из LRU.
    # Кэш инвалидируется автоматически при смене cards (по id(cards)).
    cross_tables: Optional[Dict[str, Any]] = None
    cross_tables_cards_id: Optional[int] = None  # id(task.cards) для инвалидации
    prev_cross_tables: Optional[Dict[str, Any]] = None
    prev_cross_tables_cards_id: Optional[int] = None  # id(task.prev_cards)
    current_metrics: Optional[Dict[str, Any]] = None
    current_metrics_cards_id: Optional[int] = None
    prev_metrics: Optional[Dict[str, Any]] = None
    prev_metrics_cards_id: Optional[int] = None

    # Состояния длительных операций
    clusters_state: AnalysisState = field(default_factory=AnalysisState)
    llm_summary_state: AnalysisState = field(default_factory=AnalysisState)

    # История вопросов LLM (последние 10)
    llm_qa_history: List[Dict[str, str]] = field(default_factory=list)

    # Кэш: последняя точечная статистика (для отображения без пересчёта)
    last_point_stats: Optional[Dict[str, Any]] = None

    # === Raw данные для Excel-выгрузки и продвинутой карты ===
    # Полные объекты очагов (с cards внутри) — не проходят через JSON-API,
    # но нужны для generate_cluster_map() и generate_concentration_dynamics_file()
    raw_clusters: List[Dict[str, Any]] = field(default_factory=list)
    # Полные объекты предочагов — сохраняются ОТДЕЛЬНО, потому что
    # предочаги могут существовать даже когда очагов нет (малые регионы).
    # Ранее предочаги прикреплялись к clusters[0]["_preclusters"] и терялись
    # при пустом списке очагов — это приводило к пустой карте и 500 в Excel.
    raw_preclusters: List[Dict[str, Any]] = field(default_factory=list)
    # Полные карточки последнего point stats запроса (с _dist_m) — для Excel
    last_point_cards_current: List[Dict[str, Any]] = field(default_factory=list)
    last_point_cards_prev: List[Dict[str, Any]] = field(default_factory=list)
    last_point_params: Optional[Dict[str, Any]] = None  # {lat, lon, radius_m}
