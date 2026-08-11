"""
Facade для service-слоя MiniApp.

Реальная реализация разбита на модули внутри пакета services/:
- models.py          — Task, TaskStatus, AnalysisStatus, AnalysisState
- _imports.py        — _PROJECT_ROOT, _ensure_project_path, _import_module
- task_registry.py   — _tasks, _tasks_lock, _register_task, get_task*,
                       list_user_tasks, _task_factory, MAX_INMEMORY_TASKS
- query_ops.py       — parse_user_query, get_regions
- pipeline.py        — create_task, execute_task, ensure_prev_cards,
                       ensure_cards (Sprint 3.1: cards recovery),
                       _parse_files_sync, _task_dir, _EXECUTE_SEMAPHORE,
                       MAX_CONCURRENT_TASKS
- analytics_ops.py   — _get_cross_tables, ensure_comparison, compute_point_stats
- clusters_ops.py    — start_clusters_calculation, generate_clusters_map_html,
                       generate_clusters_excel, _serialize_cluster,
                       _build_clusters_map_html, _color_for_severity
- point_stats_ops.py — generate_point_stats_excel, generate_point_stats_map_html
- llm_ops.py         — start_llm_summary, _run_llm_summary_inner,
                       ask_llm_question, get_llm_providers_status
- cleanup.py         — cleanup_old_tasks

Этот файл — ТОЛЬКО re-export для обратной совместимости со всеми
потребителями (routers/*.py, main.py, db/repository.py, тесты).
Любую новую логику добавляйте в соответствующий модуль выше, а не сюда.

Исторически это был один 2391-строчный файл; рефакторинг выполнен
в Sprint 1 (архитектурная подготовка к масштабированию на 10-30 пиковых
пользователей).
"""
from __future__ import annotations

# === Models ===
from .models import (
    AnalysisState,
    AnalysisStatus,
    Task,
    TaskStatus,
)

# === Shared import infrastructure ===
from ._imports import (
    _PROJECT_ROOT,
    _ensure_project_path,
    _import_module,
)

# === Task registry (in-memory LRU + DB fallback) ===
from .task_registry import (
    MAX_INMEMORY_TASKS,
    _gen_task_id,
    _now_utc,
    _register_task,
    _task_factory,
    _tasks,
    _tasks_lock,
    _touch_task_lru,
    get_task,
    get_task_async,
    list_user_tasks,
)

# === Query parsing & regions ===
from .query_ops import (
    get_regions,
    parse_user_query,
)

# === Pipeline (create + execute task) ===
from .pipeline import (
    MAX_CONCURRENT_TASKS,
    _EXECUTE_SEMAPHORE,
    _parse_files_sync,
    _task_dir,
    create_task,
    ensure_cards,
    ensure_prev_cards,
    execute_task,
)

# === Analytics operations ===
from .analytics_ops import (
    _get_cross_tables,
    compute_point_stats,
    ensure_comparison,
)

# === Clusters (concentration points) ===
from .clusters_ops import (
    _build_clusters_map_html,
    _color_for_severity,
    _serialize_cluster,
    generate_clusters_excel,
    generate_clusters_map_html,
    start_clusters_calculation,
)

# === Point stats exports (Excel + map) ===
from .point_stats_ops import (
    generate_point_stats_excel,
    generate_point_stats_map_html,
)

# === LLM operations ===
from .llm_ops import (
    _LLM_SEMAPHORE,
    _run_llm_summary_inner,
    _init_llm_semaphore,
    ask_llm_question,
    get_llm_providers_status,
    start_llm_summary,
)

# === Cleanup ===
from .cleanup import cleanup_old_tasks


__all__ = [
    # Models
    "AnalysisState",
    "AnalysisStatus",
    "Task",
    "TaskStatus",
    # Shared infra
    "_PROJECT_ROOT",
    "_ensure_project_path",
    "_import_module",
    # Task registry
    "MAX_INMEMORY_TASKS",
    "_gen_task_id",
    "_now_utc",
    "_register_task",
    "_task_factory",
    "_tasks",
    "_tasks_lock",
    "_touch_task_lru",
    "get_task",
    "get_task_async",
    "list_user_tasks",
    # Query
    "get_regions",
    "parse_user_query",
    # Pipeline
    "MAX_CONCURRENT_TASKS",
    "_EXECUTE_SEMAPHORE",
    "_parse_files_sync",
    "_task_dir",
    "create_task",
    "ensure_cards",
    "ensure_prev_cards",
    "execute_task",
    # Analytics
    "_get_cross_tables",
    "compute_point_stats",
    "ensure_comparison",
    # Clusters
    "_build_clusters_map_html",
    "_color_for_severity",
    "_serialize_cluster",
    "generate_clusters_excel",
    "generate_clusters_map_html",
    "start_clusters_calculation",
    # Point stats
    "generate_point_stats_excel",
    "generate_point_stats_map_html",
    # LLM
    "_LLM_SEMAPHORE",
    "_init_llm_semaphore",
    "_run_llm_summary_inner",
    "ask_llm_question",
    "get_llm_providers_status",
    "start_llm_summary",
    # Cleanup
    "cleanup_old_tasks",
]
