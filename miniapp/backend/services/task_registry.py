"""
In-memory хранилище задач MiniApp (LRU + lock).

Хранит последние MAX_INMEMORY_TASKS задач в OrderedDict. При превышении
лимита вытесняет самую старую (с persistence в БД через repository.save_task).

Это центральный модуль: task_registry импортируется pipeline, cleanup,
facade'ом gibdd_service и тестами. Внешний код получает доступ к _tasks
через facade `gibdd_service._tasks` (для обратной совместимости).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from threading import Lock
from typing import List, Optional

from . import _imports
from .models import Task

logger = logging.getLogger(__name__)


# In-memory хранилище задач (для production заменить на Redis/PostgreSQL)
#
# === Фаза 1.4: LRU-политика на _tasks ===
# Раньше это был обычный Dict[str, Task], который рос без ограничений.
# Каждая задача держит в памяти 3-12 MB (cards + prev_cards + raw_clusters +
# analytics). При 30 пользователях × 5 задач = 150 × 8 MB = ~1.2 GB —
# риск OOM на bothost с 2 GB RAM.
#
# OrderedDict + ручной LRU eviction: при добавлении новой задачи, если
# размер превышает MAX_INMEMORY_TASKS, вытесняем самую старую (FIFO по
# created_at). Тяжёлые поля вытесненной задачи сохраняются в БД (через
# repository.save_task), лёгкие метаданные остаются доступны через
# get_task_async() (lazy load из БД).
#
# MAX_INMEMORY_TASKS=50 выбрано как баланс: ~400 MB максимум в RAM,
# достаточно для одновременной работы 10-15 пользователей.
MAX_INMEMORY_TASKS = 50
_tasks: "OrderedDict[str, Task]" = OrderedDict()
_tasks_lock = Lock()


def _register_task(task: Task) -> None:
    """Добавляет задачу в _tasks с LRU-eviction.

    Если превышен лимит MAX_INMEMORY_TASKS — вытесняет самую старую задачу
    (по created_at). Вытесняемая задача предварительно сохраняется в БД
    (fire-and-forget через asyncio.create_task), чтобы метаданные не
    потерялись и были доступны через get_task_async().
    """
    with _tasks_lock:
        # Если задача уже есть — обновляем позицию (move_to_end)
        if task.id in _tasks:
            _tasks.move_to_end(task.id)
            _tasks[task.id] = task
            return

        # Вытесняем самые старые, если превышен лимит.
        # ВНИМАНИЕ: читаем лимит через lazy-import фасада gibdd_service, а не
        # через локальный binding MAX_INMEMORY_TASKS — иначе monkeypatch на
        # gibdd_service.MAX_INMEMORY_TASKS в тестах не сработает (патч на
        # фасаде не доходит до локальной копии в task_registry). Аналогично
        # паттерну с _imports._import_module.
        try:
            from . import gibdd_service as _facade
            _limit = getattr(_facade, "MAX_INMEMORY_TASKS", MAX_INMEMORY_TASKS)
        except Exception:
            _limit = MAX_INMEMORY_TASKS
        while len(_tasks) >= _limit:
            evicted_id, evicted_task = _tasks.popitem(last=False)
            logger.info(
                f"_tasks LRU: вытеснена задача {evicted_id} "
                f"(регион={evicted_task.region_code}, "
                f"возраст={evicted_task.created_at.isoformat()}) — "
                f"данные сохранены в БД, доступны через get_task_async()"
            )
            # Fire-and-forget persist в БД (если БД недоступна — теряем,
            # но это acceptable: задача старая, пользователь вряд ли
            # вернётся к ней в течение 24 часов)
            try:
                from ..db.repository import save_task
                asyncio.create_task(save_task(evicted_task))
            except Exception as exc:
                logger.debug(
                    f"_register_task: persist evicted {evicted_id} failed: {exc}"
                )

        _tasks[task.id] = task

    # === Фаза 1.6: обновляем Prometheus gauge размера _tasks ===
    try:
        from ..middleware.metrics import update_tasks_in_memory
        update_tasks_in_memory(len(_tasks))
    except Exception:
        pass


async def get_task_async(task_id: str) -> Optional[Task]:
    """Асинхронная версия get_task — проверяет и БД, и in-memory."""
    # Сначала in-memory (быстро + есть тяжёлые поля)
    if task_id in _tasks:
        # LRU: обновляем позицию как "недавно использованную"
        with _tasks_lock:
            if task_id in _tasks:
                _tasks.move_to_end(task_id)
        task = _tasks[task_id]
        # Sprint 6: даже если задача in-memory, LLM-сессия могла быть
        # утеряна (например, task создан заново после рестарта, а
        # llm_sessions в БД осталась). Пробуем восстановить, если
        # оба поля пустые — это no-op если уже заполнено.
        await _try_restore_llm_session(task)
        return task

    # Потом БД (если есть)
    try:
        from ..db.connection import is_db_ready
        from ..db.repository import load_task, attach_heavy_state
        if not is_db_ready():
            return None
        task = await load_task(task_id, _task_factory)
        if task is not None:
            attach_heavy_state(task)
            _register_task(task)  # добавляем в LRU-кэш
            # Sprint 6: восстанавливаем llm_summary_state + llm_qa_history
            # из llm_sessions (если они не были восстановлены через
            # attach_heavy_state из _TASKS_HEAVY_STATE).
            await _try_restore_llm_session(task)
        return task
    except Exception as exc:
        logger.debug(f"get_task_async: DB load failed: {exc}")
        return _tasks.get(task_id)


async def _try_restore_llm_session(task: Task) -> None:
    """
    Sprint 6: восстанавливает llm_summary_state и llm_qa_history из БД.

    Логика:
      - Если task.llm_qa_history пустой И task.llm_summary_state.status != DONE
        → загружаем из llm_sessions.
      - Если хотя бы одно заполнено (in-memory или из _TASKS_HEAVY_STATE)
        → ничего не делаем (не затираем актуальное состояние).

    Это гарантирует, что после рестарта приложения пользователь
    увидит резюме и Q&A-историю, не перегенерируя их.
    """
    # Быстрая проверка — нужно ли вообще что-то делать.
    has_summary = (
        task.llm_summary_state
        and task.llm_summary_state.status is not None
        and task.llm_summary_state.status.value == "done"
        and bool(task.llm_summary_state.result)
    )
    has_qa = bool(task.llm_qa_history)
    if has_summary and has_qa:
        return  # оба поля уже заполнены — ничего не делаем

    try:
        from ..db.repository import load_llm_session
        session = await load_llm_session(task.id)
        if session is None:
            return  # записи в БД нет — пользователь ещё не пользовался LLM

        # Восстанавливаем summary, если он пустой
        if not has_summary and session.get("summary_text"):
            try:
                from .gibdd_service import AnalysisStatus
                state = task.llm_summary_state
                state.status = AnalysisStatus.DONE
                state.progress = 100
                state.stage = "Готово (восстановлено из БД)"
                state.result = {
                    "text": session["summary_text"],
                    "provider": session.get("summary_provider") or "free",
                    "generated_at": (
                        session.get("summary_generated_at").isoformat()
                        if hasattr(session.get("summary_generated_at"), "isoformat")
                        else (session.get("summary_generated_at") or "")
                    ),
                    "from_session_db": True,  # маркер для диагностики
                }
                state.finished_at = session.get("summary_generated_at")
                logger.info(
                    f"Sprint 6: restored LLM summary for task={task.id} "
                    f"({len(session['summary_text'])} chars)"
                )
            except Exception as exc:
                logger.warning(
                    f"Sprint 6: restore summary for task={task.id} failed: {exc}"
                )

        # Восстанавливаем Q&A-историю, если она пустая
        if not has_qa and session.get("qa_history"):
            try:
                # Глубокая копия — чтобы избежать мутаций общих объектов
                task.llm_qa_history = list(session["qa_history"])
                logger.info(
                    f"Sprint 6: restored Q&A history for task={task.id} "
                    f"({len(task.llm_qa_history)} entries)"
                )
            except Exception as exc:
                logger.warning(
                    f"Sprint 6: restore qa_history for task={task.id} failed: {exc}"
                )
    except Exception as exc:
        logger.debug(f"Sprint 6: _try_restore_llm_session({task.id}) failed: {exc}")


def get_task(task_id: str) -> Optional[Task]:
    """Возвращает задачу по ID или None (синхронная версия).

    ВНИМАНИЕ: проверяет только in-memory кэш. Если задача существует
    только в БД (например, после рестарта процесса) — вернёт None.
    Используйте get_task_async() для полной проверки (БД + memory).
    """
    return _tasks.get(task_id)


def _task_factory(
    id: str,
    user_id: int,
    region_code: str,
    region_name: str,
    period_label: str,
    dat_list: List[str],
    raw_query: str,
) -> Task:
    """Фабрика Task для repository.load_task (без циклического импорта)."""
    return Task(
        id=id,
        user_id=user_id,
        region_code=region_code,
        region_name=region_name,
        period_label=period_label,
        dat_list=dat_list,
        raw_query=raw_query,
    )


async def list_user_tasks(user_id: int, limit: int = 20) -> List[Task]:
    """Возвращает последние N задач пользователя.

    При наличии БД — из БД (consistent между воркерами).
    Иначе — из in-memory _tasks.
    """
    # Сначала in-memory (быстро + содержит тяжёлые поля)
    user_tasks_in_memory = [
        t for t in _tasks.values() if t.user_id == user_id
    ]
    user_tasks_in_memory.sort(key=lambda t: t.created_at, reverse=True)

    # Проверяем готовность БД (lazy import чтобы избежать циклов)
    try:
        from ..db.connection import is_db_ready
    except Exception:
        is_db_ready = lambda: False  # noqa: E731

    if not is_db_ready():
        return user_tasks_in_memory[:limit]

    try:
        from ..db.repository import list_user_tasks_from_db, attach_heavy_state
        db_tasks = await list_user_tasks_from_db(user_id, limit, _task_factory)
        # Присоединяем тяжёлые поля из кэша (если есть)
        for t in db_tasks:
            attach_heavy_state(t)

        # Если в БД задач больше, чем в памяти (например, после рестарта) —
        # дополняем список из БД. Если в памяти есть задача, которой нет в БД
        # (например, только что создана, save_task ещё не завершился) —
        # включаем её в результат, убирая дубли.
        seen_ids = {t.id for t in db_tasks}
        for t in user_tasks_in_memory:
            if t.id not in seen_ids:
                db_tasks.insert(0, t)  # свежие — первыми
        return db_tasks[:limit]
    except Exception as exc:
        logger.debug(f"list_user_tasks: DB query failed: {exc}")
        return user_tasks_in_memory[:limit]


def _touch_task_lru(task_id: str) -> None:
    """Помечает задачу как недавно использованную (LRU update).

    Используется внешними модулями (pipeline, analytics_ops) после
    обновления полей задачи — чтобы LRU не вытеснил активную задачу.
    """
    with _tasks_lock:
        if task_id in _tasks:
            _tasks.move_to_end(task_id)


def _now_utc() -> datetime:
    """Хелпер для общей временной метки (используется в нескольких модулях)."""
    return datetime.now(timezone.utc)


def _gen_task_id() -> str:
    """Генерирует короткий ID задачи (12 hex-символов)."""
    return uuid.uuid4().hex[:12]
