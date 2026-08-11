"""
Prometheus-метрики для Mini App (Фаза 1.6).

Автоматически собирает:
- http_requests_total (по method, path, status)
- http_request_duration_seconds (гистограмма)
- http_requests_in_progress (gauge)

Кастомные метрики:
- gibdd_tasks_total (counter, по status)
- gibdd_tasks_in_progress (gauge)
- gibdd_cache_hits_total / gibdd_cache_misses_total (по cache_name)
- gibdd_active_long_polls (gauge)
- gibdd_tasks_in_memory (gauge — размер _tasks OrderedDict)

Использование:
1. В main.py: from .middleware.metrics import setup_metrics
   setup_metrics(app)
2. /metrics endpoint автоматически появляется

Для Prometheus scrape:
   scrape_configs:
     - job_name: 'gibdd-bot'
       static_configs:
         - targets: ['bot1234.bothost.tech:443']
       metrics_path: /metrics
       scheme: https
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Prom-client метрики НЕ зависят от prometheus_client — использум
# prometheus_fastapi_instrumentator, который внутри использует prometheus_client.
# Кастомные метрики — через prometheus_client.Counter/Gauge/Histogram.

try:
    from prometheus_client import Counter, Gauge, Histogram
    from prometheus_fastapi_instrumentator import Instrumentator
    PROMETHEUS_AVAILABLE = True
except ImportError:
    logger.warning(
        "prometheus-fastapi-instrumentator не установлен — "
        "метрики отключены. Установите: pip install prometheus-fastapi-instrumentator"
    )
    PROMETHEUS_AVAILABLE = False


# === Кастомные метрики ===
if PROMETHEUS_AVAILABLE:
    # Счётчик созданных задач по статусам
    TASKS_TOTAL = Counter(
        "gibdd_tasks_total",
        "Всего созданных задач выгрузки",
        ["status"],  # pending/fetching/parsing/analytics/generating/done/failed
    )

    # Текущее количество активных задач (в execute_task)
    TASKS_IN_PROGRESS = Gauge(
        "gibdd_tasks_in_progress",
        "Количество задач в стадии выполнения",
    )

    # Размер in-memory _tasks словаря (LRU-кэш)
    TASKS_IN_MEMORY = Gauge(
        "gibdd_tasks_in_memory",
        "Размер in-memory словаря _tasks (LRU-кэш задач)",
    )

    # Семафор: сколько слотов занято / свободно
    SEMAPHORE_OCCUPIED = Gauge(
        "gibdd_execute_semaphore_occupied",
        "Количество занятых слотов семафора execute_task",
    )

    # Cache hits / misses
    CACHE_HITS = Counter(
        "gibdd_cache_hits_total",
        "HIT кэша L2 (PostgreSQL)",
        ["cache_name"],  # cards / clusters / excel
    )
    CACHE_MISSES = Counter(
        "gibdd_cache_misses_total",
        "MISS кэша L2 (PostgreSQL)",
        ["cache_name"],
    )

    # Активные long-poll соединения
    ACTIVE_LONG_POLLS = Gauge(
        "gibdd_active_long_polls",
        "Количество активных long-poll HTTP-соединений",
    )

    # Время выполнения execute_task по фазам
    TASK_PHASE_DURATION = Histogram(
        "gibdd_task_phase_duration_seconds",
        "Время выполнения фазы задачи",
        ["phase"],  # fetching / parsing / analytics / generating
    )

    # Время ответа внешних API (ГИБДД)
    EXTERNAL_API_DURATION = Histogram(
        "gibdd_external_api_duration_seconds",
        "Время ответа внешних API",
        ["api", "status"],  # api=gibdd_api/gibdd_web/llm, status=success/error
        buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
    )

    # === Phase 2: дополнительные метрики ===

    # Размер пула PostgreSQL (active/idle)
    DB_POOL_SIZE = Gauge(
        "gibdd_db_pool_size",
        "Размер пула соединений PostgreSQL",
        ["state"],  # state=active/idle/max
    )

    # RSS памяти процесса (в байтах)
    PROCESS_RSS_BYTES = Gauge(
        "gibdd_process_rss_bytes",
        "Resident memory процесса (RSS) в байтах",
    )

    # Время выполнения execute_task целиком (от старта до done/failed)
    TASK_TOTAL_DURATION = Histogram(
        "gibdd_task_total_duration_seconds",
        "Полное время выполнения задачи (execute_task)",
        ["status"],  # done/failed
        buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0),
    )

    # Количество rate-limited запросов (HTTP 429)
    RATE_LIMITED_TOTAL = Counter(
        "gibdd_rate_limited_total",
        "Запросов отклонено rate limiter'ом (HTTP 429)",
    )
else:
    # Заглушки, чтобы не падать без prometheus_client
    class _Stub:
        def labels(self, *a, **kw): return self
        def inc(self, *a, **kw): pass
        def dec(self, *a, **kw): pass
        def set(self, *a, **kw): pass
        def observe(self, *a, **kw): pass
    TASKS_TOTAL = TASKS_IN_PROGRESS = TASKS_IN_MEMORY = _Stub()
    SEMAPHORE_OCCUPIED = CACHE_HITS = CACHE_MISSES = _Stub()
    ACTIVE_LONG_POLLS = TASK_PHASE_DURATION = EXTERNAL_API_DURATION = _Stub()
    DB_POOL_SIZE = PROCESS_RSS_BYTES = TASK_TOTAL_DURATION = _Stub()
    RATE_LIMITED_TOTAL = _Stub()


def setup_metrics(app) -> None:
    """Регистрирует Prometheus-инструментацию на FastAPI-приложении.

    Добавляет endpoint /metrics, отдающий метрики в формате Prometheus.
    Вызывать в main.py после создания app, но до подключения роутеров.
    """
    if not PROMETHEUS_AVAILABLE:
        logger.warning("setup_metrics: prometheus не установлен — пропуск")
        return

    # Включено ли скрапирование метрик (по умолчанию да)
    if os.environ.get("METRICS_ENABLED", "1") != "1":
        logger.info("Метрики отключены через METRICS_ENABLED=0")
        return

    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        should_respect_env_var=False,
        excluded_handlers=["/metrics", "/health*"],
    ).instrument(app).expose(
        app,
        endpoint="/metrics",
        include_in_schema=False,
        tags=["monitoring"],
    )
    logger.info("Prometheus метрики: /metrics endpoint активирован")


# === Удобные хелперы для использования в бизнес-коде ===

def record_task_status(status: str) -> None:
    """Вызвать при смене статуса задачи."""
    TASKS_TOTAL.labels(status=status).inc()


def task_started() -> None:
    """Вызвать в начале execute_task."""
    TASKS_IN_PROGRESS.inc()
    SEMAPHORE_OCCUPIED.inc()


def task_finished() -> None:
    """Вызвать в конце execute_task."""
    TASKS_IN_PROGRESS.dec()
    SEMAPHORE_OCCUPIED.dec()


def update_tasks_in_memory(count: int) -> None:
    """Обновить gauge размера _tasks."""
    TASKS_IN_MEMORY.set(count)


def record_cache_hit(cache_name: str) -> None:
    CACHE_HITS.labels(cache_name=cache_name).inc()


def record_cache_miss(cache_name: str) -> None:
    CACHE_MISSES.labels(cache_name=cache_name).inc()


def long_poll_start() -> None:
    ACTIVE_LONG_POLLS.inc()


def long_poll_end() -> None:
    ACTIVE_LONG_POLLS.dec()


def observe_phase_duration(phase: str, seconds: float) -> None:
    TASK_PHASE_DURATION.labels(phase=phase).observe(seconds)


def observe_external_api(api: str, status: str, seconds: float) -> None:
    EXTERNAL_API_DURATION.labels(api=api, status=status).observe(seconds)


# === Phase 2: новые хелперы ===

def update_db_pool_metrics(active: int, idle: int, max_size: int) -> None:
    """Обновляет gauges размера пула PostgreSQL.

    Вызывать из /health/detailed endpoint или из periodic background task.
    """
    DB_POOL_SIZE.labels(state="active").set(active)
    DB_POOL_SIZE.labels(state="idle").set(idle)
    DB_POOL_SIZE.labels(state="max").set(max_size)


def update_process_rss(bytes_: int) -> None:
    """Обновляет gauge RSS памяти процесса.

    Используется в /health/detailed для алертов по памяти.
    """
    PROCESS_RSS_BYTES.set(bytes_)


def observe_task_total_duration(status: str, seconds: float) -> None:
    """Вызвать при завершении execute_task (status=done/failed)."""
    TASK_TOTAL_DURATION.labels(status=status).observe(seconds)


def record_rate_limited() -> None:
    """Вызвать когда slowapi отклонил запрос (429)."""
    RATE_LIMITED_TOTAL.inc()
