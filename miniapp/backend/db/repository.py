"""
TaskRepository — CRUD задач выгрузки + аудит-лог обращений к ПДн.

Дизайн:
- Если PostgreSQL готов (is_db_ready() == True) — операции идут в БД,
  in-memory словарь используется как кэш для тяжёлых полей (cards,
  raw_clusters и т.д.), которые не сериализуются в БД на Этапе 2.
- Если PostgreSQL НЕ готов — операции идут только in-memory,
  поведение идентично тому, что было до подключения БД.

Это гарантирует, что:
1. При недоступности БД приложение не падает.
2. При рестарте с БД — задачи восстанавливаются (метаданные + files +
   analytics), но тяжёлые поля (cards, raw_clusters) нужно
   перезагрузить (через data_cache или повторный расчёт).
3. При множественных воркерах — метаданные консистентны (тяжёлые
   поля могут расходиться, но это решается на Этапе 3 кэшем карточек).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from psycopg.types.json import Json, Jsonb
from psycopg.rows import dict_row

from .connection import get_pool, is_db_ready

logger = logging.getLogger(__name__)

# ====================================================================
# In-memory кэш для тяжёлых полей Task
# ====================================================================
# Ключ: task_id, значение: dict с полями {cards, prev_cards, prev_label,
# prev_cards_loaded, comparison, clusters_state, llm_summary_state,
# llm_qa_history, last_point_stats, raw_clusters, raw_preclusters,
# last_point_cards_current, last_point_cards_prev, last_point_params}
#
# Зачем: эти поля не персистятся в БД на Этапе 2 (слишком большие),
# но нужны для работы analytics/clusters/point_stats/LLM. При рестарте
# они теряются — пользователь может либо пере-открыть вкладку (тогда
# данные перезагружаются лениво через ensure_prev_cards и т.д.),
# либо пересоздать задачу.
_TASKS_HEAVY_STATE: Dict[str, Dict[str, Any]] = {}

# In-memory хранилище метаданных задач — fallback при отсутствии БД.
# При наличии БД — используется только как кэш поверх SQL (чтобы не
# дёргать БД на каждый get_task).
_TASKS_MEMORY: Dict[str, "Task"] = {}  # type: ignore[name-defined]


def set_heavy_state(task_id: str, key: str, value: Any) -> None:
    """Сохраняет тяжёлое поле Task в in-memory кэше."""
    if task_id not in _TASKS_HEAVY_STATE:
        _TASKS_HEAVY_STATE[task_id] = {}
    _TASKS_HEAVY_STATE[task_id][key] = value


def get_heavy_state(task_id: str, key: str, default: Any = None) -> Any:
    """Достаёт тяжёлое поле Task из in-memory кэша."""
    return _TASKS_HEAVY_STATE.get(task_id, {}).get(key, default)


def drop_heavy_state(task_id: str) -> None:
    """Удаляет весь тяжёлый state задачи (при cleanup)."""
    _TASKS_HEAVY_STATE.pop(task_id, None)


# ====================================================================
# Сохранение задачи в БД
# ====================================================================
async def save_task(task: Any) -> None:
    """
    Сохраняет метаданные задачи в БД (INSERT или UPDATE по id).

    Тяжёлые поля (cards, raw_clusters и т.д.) НЕ сохраняются —
    они остаются in-memory через set_heavy_state().
    """
    # Сохраняем тяжёлые поля в memory-кэш (всегда, даже если БД есть)
    _cache_heavy_fields(task)

    if not is_db_ready():
        # БД нет — fallback: обновляем in-memory
        _TASKS_MEMORY[task.id] = task
        return

    pool = get_pool()
    if pool is None:
        _TASKS_MEMORY[task.id] = task
        return

    try:
        async with pool.connection() as conn:
            # upsert: INSERT ... ON CONFLICT (id) DO UPDATE
            await conn.execute(
                """
                INSERT INTO tasks (
                    id, user_id, region_code, region_name, period_label,
                    dat_list, raw_query, status, progress, error,
                    total_dtp, total_dead, total_injured, files,
                    analytics, clusters_result, created_at, updated_at
                ) VALUES (
                    %(id)s, %(user_id)s, %(region_code)s, %(region_name)s,
                    %(period_label)s, %(dat_list)s, %(raw_query)s,
                    %(status)s, %(progress)s, %(error)s,
                    %(total_dtp)s, %(total_dead)s, %(total_injured)s,
                    %(files)s, %(analytics)s, %(clusters_result)s,
                    %(created_at)s, %(updated_at)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    progress = EXCLUDED.progress,
                    error = EXCLUDED.error,
                    total_dtp = EXCLUDED.total_dtp,
                    total_dead = EXCLUDED.total_dead,
                    total_injured = EXCLUDED.total_injured,
                    files = EXCLUDED.files,
                    analytics = COALESCE(EXCLUDED.analytics, tasks.analytics),
                    clusters_result = COALESCE(
                        EXCLUDED.clusters_result, tasks.clusters_result
                    ),
                    updated_at = NOW()
                """,
                params={
                    "id": task.id,
                    "user_id": task.user_id,
                    "region_code": task.region_code,
                    "region_name": task.region_name,
                    "period_label": task.period_label,
                    "dat_list": Json(task.dat_list),
                    "raw_query": task.raw_query,
                    "status": task.status.value
                    if hasattr(task.status, "value")
                    else str(task.status),
                    "progress": task.progress,
                    "error": task.error,
                    "total_dtp": task.total_dtp,
                    "total_dead": task.total_dead,
                    "total_injured": task.total_injured,
                    "files": Json(task.files),
                    "analytics": Json(task.analytics)
                    if task.analytics is not None
                    else None,
                    "clusters_result": Json(task.clusters_state.result)
                    if task.clusters_state
                    and task.clusters_state.result is not None
                    else None,
                    "created_at": task.created_at,
                    "updated_at": task.updated_at,
                },
            )
            await conn.commit()

        # Также обновляем in-memory кэш
        _TASKS_MEMORY[task.id] = task

    except Exception as exc:
        logger.warning(
            f"save_task({task.id}) failed: {exc} — used in-memory fallback"
        )
        _TASKS_MEMORY[task.id] = task


# ====================================================================
# Загрузка задачи из БД
# ====================================================================
async def load_task(task_id: str, task_factory: Any) -> Optional[Any]:
    """
    Загружает задачу по id.

    Сначала проверяет in-memory кэш (быстро + содержит тяжёлые поля).
    Если нет — идёт в БД (если готова) и конструирует Task из строки.
    Если нет нигде — None.

    task_factory: callable(id, user_id, region_code, region_name,
                           period_label, dat_list, raw_query) -> Task
    Используется для создания объекта Task без циклического импорта.
    """
    # 1. Memory cache hit
    if task_id in _TASKS_MEMORY:
        return _TASKS_MEMORY[task_id]

    if not is_db_ready():
        return None

    pool = get_pool()
    if pool is None:
        return None

    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT id, user_id, region_code, region_name, period_label,
                       dat_list, raw_query, status, progress, error,
                       total_dtp, total_dead, total_injured, files,
                       analytics, clusters_result,
                       created_at, updated_at
                FROM tasks WHERE id = %(id)s
                """,
                params={"id": task_id},
                prepare=False,
            )
            row = await cur.fetchone()

        if row is None:
            return None

        # Создаём Task через factory (избегаем циклического импорта)
        task = task_factory(
            id=row["id"],
            user_id=row["user_id"],
            region_code=row["region_code"],
            region_name=row["region_name"],
            period_label=row["period_label"],
            dat_list=list(row["dat_list"]) if row["dat_list"] else [],
            raw_query=row["raw_query"] or "",
        )

        # Восстанавливаем сохранённые поля
        _restore_status(task, row["status"], row["progress"], row["error"])
        task.total_dtp = row["total_dtp"] or 0
        task.total_dead = row["total_dead"] or 0
        task.total_injured = row["total_injured"] or 0
        task.files = list(row["files"]) if row["files"] else []
        task.analytics = row["analytics"]
        if (
            row["clusters_result"]
            and task.clusters_state
        ):
            task.clusters_state.result = row["clusters_result"]
            task.clusters_state.status = _make_analysis_status("done")
            task.clusters_state.progress = 100
            task.clusters_state.stage = "Готово (восстановлено из БД)"

        # created_at/updated_at из БД
        if row["created_at"]:
            task.created_at = row["created_at"]
        if row["updated_at"]:
            task.updated_at = row["updated_at"]

        # Кэшируем
        _TASKS_MEMORY[task_id] = task
        return task

    except Exception as exc:
        logger.warning(f"load_task({task_id}) failed: {exc}")
        return None


def _restore_status(task: Any, status: str, progress: int, error: Optional[str]) -> None:
    """Восстанавливает статус задачи из строкового представления."""
    # TaskStatus — Enum, ищем по value
    try:
        from ..services.gibdd_service import TaskStatus

        for s in TaskStatus:
            if s.value == status:
                task.status = s
                break
    except Exception:
        pass
    task.progress = progress or 0
    task.error = error


def _make_analysis_status(value: str):
    """Создаёт AnalysisStatus из строкового значения."""
    try:
        from ..services.gibdd_service import AnalysisStatus

        for s in AnalysisStatus:
            if s.value == value:
                return s
    except Exception:
        pass
    return None


# ====================================================================
# Список задач пользователя
# ====================================================================
async def list_user_tasks_from_db(
    user_id: int, limit: int, task_factory: Any
) -> List[Any]:
    """
    Возвращает последние N задач пользователя (из БД).
    Если БД недоступна — fallback на in-memory.
    """
    if not is_db_ready():
        # In-memory fallback
        user_tasks = [
            t for t in _TASKS_MEMORY.values() if t.user_id == user_id
        ]
        user_tasks.sort(key=lambda t: t.created_at, reverse=True)
        return user_tasks[:limit]

    pool = get_pool()
    if pool is None:
        return []

    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT id, user_id, region_code, region_name, period_label,
                       dat_list, raw_query, status, progress, error,
                       total_dtp, total_dead, total_injured, files,
                       analytics, clusters_result,
                       created_at, updated_at
                FROM tasks
                WHERE user_id = %(uid)s
                ORDER BY created_at DESC
                LIMIT %(limit)s
                """,
                params={"uid": user_id, "limit": limit},
                prepare=False,
            )
            rows = await cur.fetchall()

        tasks: List[Any] = []
        for row in rows:
            # Проверяем in-memory кэш (чтобы вернуть тяжёлые поля, если они есть)
            if row["id"] in _TASKS_MEMORY:
                tasks.append(_TASKS_MEMORY[row["id"]])
                continue

            task = task_factory(
                id=row["id"],
                user_id=row["user_id"],
                region_code=row["region_code"],
                region_name=row["region_name"],
                period_label=row["period_label"],
                dat_list=list(row["dat_list"]) if row["dat_list"] else [],
                raw_query=row["raw_query"] or "",
            )
            _restore_status(task, row["status"], row["progress"], row["error"])
            task.total_dtp = row["total_dtp"] or 0
            task.total_dead = row["total_dead"] or 0
            task.total_injured = row["total_injured"] or 0
            task.files = list(row["files"]) if row["files"] else []
            task.analytics = row["analytics"]
            if row["clusters_result"] and task.clusters_state:
                task.clusters_state.result = row["clusters_result"]
                task.clusters_state.status = _make_analysis_status("done")
                task.clusters_state.progress = 100
                task.clusters_state.stage = "Готово (восстановлено из БД)"

            if row["created_at"]:
                task.created_at = row["created_at"]
            if row["updated_at"]:
                task.updated_at = row["updated_at"]

            _TASKS_MEMORY[task.id] = task
            tasks.append(task)

        return tasks

    except Exception as exc:
        logger.warning(f"list_user_tasks_from_db failed: {exc}")
        # In-memory fallback
        user_tasks = [t for t in _TASKS_MEMORY.values() if t.user_id == user_id]
        user_tasks.sort(key=lambda t: t.created_at, reverse=True)
        return user_tasks[:limit]


# ====================================================================
# Удаление старых задач
# ====================================================================
async def delete_old_tasks(
    max_age_hours: int, project_root: Path
) -> int:
    """
    Удаляет задачи старше max_age_hours.

    Удаляет из:
    - in-memory кэша (_TASKS_MEMORY и _TASKS_HEAVY_STATE)
    - БД (если доступна)
    - диска (data/tasks/{task_id}/)

    Возвращает количество удалённых задач.
    """
    now = datetime.now(timezone.utc)
    cutoff_ts = now.timestamp() - max_age_hours * 3600

    # 1. Собираем кандидатов на удаление из in-memory
    to_delete_memory = [
        tid
        for tid, task in _TASKS_MEMORY.items()
        if task.created_at.timestamp() < cutoff_ts
    ]

    # 2. Если БД есть — собираем кандидатов и оттуда
    db_deleted = 0
    if is_db_ready():
        pool = get_pool()
        if pool is not None:
            try:
                async with pool.connection() as conn:
                    # Сначала выбираем id задач для удаления файлов
                    cur = await conn.execute(
                        """
                        SELECT id, files FROM tasks
                        WHERE created_at < NOW() - (%(hours)s || ' hours')::INTERVAL
                        """,
                        params={"hours": str(max_age_hours)},
                        prepare=False,
                    )
                    rows = await cur.fetchall()

                    # Удаляем файлы с диска для найденных задач
                    for row in rows:
                        tid = row["id"]
                        files = row["files"] or []
                        for f in files:
                            try:
                                Path(f.get("path", "")).unlink(missing_ok=True)
                            except Exception:
                                pass
                        # Удаляем директорию задачи
                        try:
                            task_dir = project_root / "data" / "tasks" / tid
                            if task_dir.exists():
                                task_dir.rmdir()
                        except Exception:
                            pass

                    # Удаляем сами строки из БД
                    if rows:
                        ids_to_delete = [r["id"] for r in rows]
                        await conn.execute(
                            "DELETE FROM tasks WHERE id = ANY(%s)",
                            params=(ids_to_delete,),
                        )
                        await conn.commit()
                        db_deleted = len(ids_to_delete)

            except Exception as exc:
                logger.warning(f"delete_old_tasks (DB) failed: {exc}")

    # 3. In-memory cleanup
    memory_deleted = 0
    for tid in to_delete_memory:
        _TASKS_MEMORY.pop(tid, None)
        drop_heavy_state(tid)
        # Удаляем файлы с диска
        task = _TASKS_MEMORY.get(tid)
        if task:
            for f in task.files:
                try:
                    Path(f.get("path", "")).unlink(missing_ok=True)
                except Exception:
                    pass
            try:
                task_dir = project_root / "data" / "tasks" / tid
                if task_dir.exists():
                    task_dir.rmdir()
            except Exception:
                pass
        memory_deleted += 1

    total = max(db_deleted, memory_deleted)
    if total > 0:
        logger.info(
            f"Cleanup: удалено {total} старых задач "
            f"(db={db_deleted}, memory={memory_deleted})"
        )
    return total


# ====================================================================
# Аудит-лог обращений к ПДн (152-ФЗ)
# ====================================================================
async def log_access(
    user_id: int,
    action: str,
    region_code: Optional[str] = None,
    period_label: Optional[str] = None,
    task_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Логирует обращение пользователя к данным ДТП.

    См. миниapp/README.md → «Требования 152-ФЗ»:
    «Журнал аудита доступа к ПДн (логировать все запросы
    user_id → region_code, period)».

    Если БД недоступна — запись логируется только в обычный логгер
    (теряется при рестарте, но не роняет приложение).
    """
    if not is_db_ready():
        logger.info(
            f"ACCESS_LOG (in-memory): user={user_id} action={action} "
            f"region={region_code} period={period_label} task={task_id}"
        )
        return

    pool = get_pool()
    if pool is None:
        return

    try:
        async with pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO access_log (
                    user_id, region_code, period_label, action, task_id, details
                ) VALUES (
                    %(uid)s, %(reg)s, %(period)s, %(action)s, %(tid)s, %(details)s
                )
                """,
                params={
                    "uid": user_id,
                    "reg": region_code,
                    "period": period_label,
                    "action": action,
                    "tid": task_id,
                    "details": Json(details) if details else None,
                },
            )
            await conn.commit()
    except Exception as exc:
        logger.warning(f"log_access failed: {exc}")


# ====================================================================
# Вспомогательные: сохранение/восстановление тяжёлых полей
# ====================================================================
# Список полей Task, которые НЕ персистятся в БД на Этапе 2.
# Они остаются in-memory, чтобы не раздувать JSONB-колонки.
# Этап 3 (cards cache) и Этап 4 (clusters history) закроют их отдельно.
_HEAVY_FIELDS = (
    "cards",
    "prev_cards",
    "prev_label",
    "prev_cards_loaded",
    "comparison",
    "llm_summary_state",
    "llm_qa_history",
    "last_point_stats",
    "raw_clusters",
    "raw_preclusters",
    "last_point_cards_current",
    "last_point_cards_prev",
    "last_point_params",
)


def _cache_heavy_fields(task: Any) -> None:
    """Копирует тяжёлые поля Task в in-memory кэш."""
    cache = _TASKS_HEAVY_STATE.setdefault(task.id, {})
    for field_name in _HEAVY_FIELDS:
        if hasattr(task, field_name):
            cache[field_name] = getattr(task, field_name)


def attach_heavy_state(task: Any) -> None:
    """
    Присоединяет к Task тяжёлые поля из кэша (если они есть).
    Вызывается после load_task, чтобы восстановить состояние.
    """
    cache = _TASKS_HEAVY_STATE.get(task.id)
    if not cache:
        return
    for field_name in _HEAVY_FIELDS:
        if field_name in cache and hasattr(task, field_name):
            # Не затираем поле, если оно уже заполнено
            # (например, после ensure_prev_cards)
            current = getattr(task, field_name)
            if not current and cache[field_name]:
                setattr(task, field_name, cache[field_name])


# ====================================================================
# Sprint 5: Task recovery на startup
# ====================================================================
# При рестарте сервера in-flight задачи (status='fetching'/'parsing'/'analytics'
# 'generating'/'running') остаются в этом статусе вечно — рабочий процесс,
# который их обрабатывал, умер вместе с сервером.
# Эта функция находит такие задачи в БД и помечает их как failed с понятным
# сообщением, чтобы пользователь увидел ошибку и мог пересоздать задачу
# вместо бесконечного ожидания.
_INCOMPLETE_STATUSES = ("fetching", "parsing", "analytics", "generating", "running")


async def recover_incomplete_tasks() -> int:
    """
    Sprint 5: помечает незавершённые задачи как failed.

    Вызывается один раз при старте сервера (после init_pool).
    Возвращает количество восстановленных задач.

    Логика:
      - status IN (fetching, parsing, analytics, generating, running) → failed
      - error = 'Прервано рестартом сервера (Sprint 5 recovery)'
      - progress не трогаем (полезно для отладки — видно, где оборвалось)
      - clusters_state.status='running' / llm_summary_state.status='running'
        тоже помечаем как failed (тяжёлые state-объекты лежат в БД только
        частично — JSONB-колонки clusters_result и т.д., но status-строка
        в самих колонках не персистится; здесь работает только на in-memory).
    """
    if not is_db_ready():
        # Без БД — in-memory задачи и так пусты после рестарта.
        return 0

    pool = get_pool()
    if pool is None:
        return 0

    recovered_count = 0
    try:
        async with pool.connection() as conn:
            # Сначала собираем ID задач для логирования
            cur = await conn.execute(
                """
                SELECT id, status, progress FROM tasks
                WHERE status = ANY(%(statuses)s)
                """,
                params={"statuses": list(_INCOMPLETE_STATUSES)},
                prepare=False,
            )
            rows = await cur.fetchall()

            if not rows:
                return 0

            # UPDATE одним запросом — помечаем все как failed
            await conn.execute(
                """
                UPDATE tasks
                SET status = 'failed',
                    error = %(error_msg)s,
                    updated_at = NOW()
                WHERE status = ANY(%(statuses)s)
                """,
                params={
                    "error_msg": "Прервано рестартом сервера (Sprint 5 recovery)",
                    "statuses": list(_INCOMPLETE_STATUSES),
                },
            )
            await conn.commit()
            recovered_count = len(rows)

        # Логируем каждую восстановленную задачу
        for row in rows:
            logger.warning(
                f"Sprint 5 recovery: task {row['id']} "
                f"was status='{row['status']}' progress={row['progress']} "
                f"→ marked as failed (server restart)"
            )

        # Также чистим in-memory кэш от мёртвых задач
        for tid in list(_TASKS_MEMORY.keys()):
            task = _TASKS_MEMORY[tid]
            try:
                if hasattr(task, "status") and hasattr(task.status, "value"):
                    if task.status.value in _INCOMPLETE_STATUSES:
                        # Не удаляем из памяти — оставляем с пометкой failed,
                        # чтобы пользователь увидел ошибку в UI.
                        from ..services.gibdd_service import TaskStatus
                        task.status = TaskStatus.FAILED
                        task.error = (
                            "Прервано рестартом сервера (Sprint 5 recovery)"
                        )
            except Exception:
                pass

        if recovered_count:
            logger.info(
                f"Sprint 5 recovery: {recovered_count} incomplete tasks "
                f"marked as failed"
            )

    except Exception as exc:
        logger.warning(f"Sprint 5 recovery failed: {exc}")

    return recovered_count


# ====================================================================
# Sprint 6: Сохранение LLM-сессий (summary + qa_history)
# ====================================================================
# Раньше task.llm_summary_state и task.llm_qa_history были чисто
# in-memory — после рестарта приложения пользователь терял всё:
# резюме (нужно было перегенерировать) и Q&A-историю (массив пустой).
# Sprint 6: персистим в таблице llm_sessions и восстанавливаем
# при первом обращении через get_task_async().
#
# Три функции:
#   - save_llm_session: upsert — сохраняет summary (полная перезапись).
#   - append_qa_entry: atomic jsonb insert — добавляет один Q&A в конец
#     массива qa_history, тримит до 10 последних. НЕ трогает summary.
#   - load_llm_session: возвращает dict {summary_text, summary_provider,
#     summary_generated_at, qa_history} или None. Вызывается при
#     восстановлении задачи в get_task_async.
# ====================================================================


async def save_llm_session(
    task_id: str,
    user_id: int,
    summary_text: str,
    summary_provider: str,
    summary_generated_at: Optional[datetime] = None,
    qa_history: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """
    Sprint 6: upsert LLM-сессии в БД.

    Сохраняет summary-текст (перезаписывает, если уже было). qa_history
    обновляется только если передан явно (для полного восстановления
    при save_task — обычно нет, т.к. append_qa_entry добавляет по одной).

    Если БД недоступна — тихо пропускает (in-memory fallback: сессия
    всё равно потеряется при рестарте, но текущая работа пользователя
    не должна обрываться).
    """
    if not is_db_ready():
        return

    pool = get_pool()
    if pool is None:
        return

    if summary_generated_at is None:
        summary_generated_at = datetime.now(timezone.utc)

    try:
        async with pool.connection() as conn:
            # qa_history — опциональный, COALESCE сохраняет существующий.
            # Используем Jsonb (а не Json) — колонка qa_history имеет тип
            # JSONB, и только Jsonb адаптируется к JSONB без необходимости
            # явного каста. Json даёт json-тип, который при использовании в
            # бинарных операторах (jsonb || json) падает с ошибкой
            # "operator does not exist: jsonb || json".
            qa_json = Jsonb(qa_history) if qa_history is not None else None
            await conn.execute(
                """
                INSERT INTO llm_sessions (
                    task_id, user_id,
                    summary_text, summary_provider, summary_generated_at,
                    qa_history, updated_at
                ) VALUES (
                    %(tid)s, %(uid)s,
                    %(st)s, %(sp)s, %(sgt)s,
                    COALESCE(%(qh)s, '[]'::jsonb), NOW()
                )
                ON CONFLICT (task_id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    summary_text = EXCLUDED.summary_text,
                    summary_provider = EXCLUDED.summary_provider,
                    summary_generated_at = EXCLUDED.summary_generated_at,
                    qa_history = COALESCE(%(qh)s, llm_sessions.qa_history),
                    updated_at = NOW()
                """,
                params={
                    "tid": task_id,
                    "uid": user_id,
                    "st": summary_text,
                    "sp": summary_provider,
                    "sgt": summary_generated_at,
                    "qh": qa_json,
                },
            )
            await conn.commit()
        logger.info(
            f"Sprint 6: saved LLM session for task={task_id} "
            f"(summary {len(summary_text)} chars, provider={summary_provider})"
        )
    except Exception as exc:
        logger.warning(
            f"Sprint 6: save_llm_session({task_id}) failed: {exc}"
        )


async def append_qa_entry(
    task_id: str,
    user_id: int,
    question: str,
    answer: str,
    provider: str,
    timestamp: Optional[datetime] = None,
) -> None:
    """
    Sprint 6: atomic append Q&A-записи в llm_sessions.qa_history JSONB.

    Использует jsonb_insert для добавления в конец массива, затем
    тримит до 10 последних (по аналогии с task.llm_qa_history logic).

    summary НЕ трогает — он сохраняется отдельно через save_llm_session.

    Если записи для task_id ещё нет — создаёт с пустым summary и одним
    Q&A. Это нормально: summary будет сохранён позже, либо вообще не
    был сгенерирован (пользователь сразу пошёл в Q&A).
    """
    if not is_db_ready():
        return

    pool = get_pool()
    if pool is None:
        return

    if timestamp is None:
        timestamp = datetime.now(timezone.utc)

    new_entry = {
        "question": question,
        "answer": answer,
        "provider": provider,
        "timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat")
        else str(timestamp),
    }

    try:
        async with pool.connection() as conn:
            # Сначала upsert пустой записи (если ещё нет) — это гарантирует,
            # что INSERT ниже не упадёт по FOREIGN KEY / NOT NULL.
            await conn.execute(
                """
                INSERT INTO llm_sessions (
                    task_id, user_id, qa_history, updated_at
                ) VALUES (
                    %(tid)s, %(uid)s, '[]'::jsonb, NOW()
                )
                ON CONFLICT (task_id) DO NOTHING
                """,
                params={"tid": task_id, "uid": user_id},
            )

            # atomic append + trim до 10 последних:
            # 1. qa_history || new_entry → добавляет в конец
            # 2. CASE WHEN jsonb_array_length > 10 → берём последние 10
            #    через jsonb_path_query_array ('$[last 10 to last]')
            #
            # ВАЖНО: используем Jsonb (не Json) — колонка qa_history имеет
            # тип JSONB, и оператор `||` определён только для (jsonb, jsonb).
            # Json адаптируется к типу json, что вызывало ошибку:
            #   operator does not exist: jsonb || json
            # Дополнительно добавлен явный каст %(entry)s::jsonb для
            # надёжности (на случай если пул вернёт кэшированный prepared
            # statement с другим типом параметра).
            await conn.execute(
                """
                UPDATE llm_sessions
                SET qa_history = (
                    CASE
                        WHEN jsonb_array_length(qa_history || %(entry)s::jsonb) > 10
                        THEN (
                            SELECT jsonb_agg(elem)
                            FROM jsonb_array_elements(qa_history || %(entry)s::jsonb)
                            WITH ORDINALITY AS arr(elem, idx)
                            WHERE idx > jsonb_array_length(qa_history || %(entry)s::jsonb) - 10
                        )
                        ELSE qa_history || %(entry)s::jsonb
                    END
                ),
                user_id = %(uid)s,
                updated_at = NOW()
                WHERE task_id = %(tid)s
                """,
                params={
                    "tid": task_id,
                    "uid": user_id,
                    "entry": Jsonb(new_entry),
                },
            )
            await conn.commit()
        logger.info(
            f"Sprint 6: appended Q&A to session task={task_id} "
            f"(answer {len(answer)} chars)"
        )
    except Exception as exc:
        logger.warning(
            f"Sprint 6: append_qa_entry({task_id}) failed: {exc}"
        )


async def load_llm_session(task_id: str) -> Optional[Dict[str, Any]]:
    """
    Sprint 6: загружает LLM-сессию из БД.

    Возвращает dict:
        {
            "summary_text": str | None,
            "summary_provider": str | None,
            "summary_generated_at": datetime | None,
            "qa_history": list[dict],     # []
        }
    или None, если записи нет / БД недоступна.

    Вызывается из get_task_async() при cache-miss в in-memory, чтобы
    восстановить task.llm_summary_state и task.llm_qa_history.
    """
    if not is_db_ready():
        return None

    pool = get_pool()
    if pool is None:
        return None

    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT summary_text, summary_provider, summary_generated_at,
                       qa_history
                FROM llm_sessions
                WHERE task_id = %(tid)s
                """,
                params={"tid": task_id},
                prepare=False,
            )
            row = await cur.fetchone()

        if row is None:
            return None

        return {
            "summary_text": row.get("summary_text"),
            "summary_provider": row.get("summary_provider"),
            "summary_generated_at": row.get("summary_generated_at"),
            "qa_history": list(row.get("qa_history") or []),
        }
    except Exception as exc:
        logger.warning(
            f"Sprint 6: load_llm_session({task_id}) failed: {exc}"
        )
        return None

