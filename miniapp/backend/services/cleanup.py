"""
Периодическая очистка старых задач.

Удаляет задачи старше max_age_hours (по умолчанию 24 часа) из:
- In-memory _tasks и тяжёлого state (db/repository._TASKS_HEAVY_STATE)
- БД (через repository.delete_old_tasks)
- Диска — data/tasks/{task_id}/

Вызывается из main.py через asyncio.create_task(_cleanup_loop())
каждые 2 часа.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from . import _imports
from .task_registry import _tasks, _tasks_lock

logger = logging.getLogger(__name__)


async def cleanup_old_tasks(max_age_hours: int = 24) -> int:
    """
    Удаляет задачи старше max_age_hours.

    Удаляет из:
    - In-memory _tasks и тяжёлого state (db/repository._TASKS_HEAVY_STATE)
    - БД (если доступна) — через repository.delete_old_tasks
    - Диска — data/tasks/{task_id}/

    Возвращает количество удалённых задач.
    """
    # 1. Через repository (БД + memory + диск)
    deleted = 0
    try:
        from ..db.repository import delete_old_tasks
        deleted = await delete_old_tasks(max_age_hours, _imports._PROJECT_ROOT)
    except Exception as exc:
        logger.warning(f"cleanup_old_tasks: repository call failed: {exc}")

    # 2. In-memory _tasks очистка (дублирует, но безопасно — на случай
    # если repository не подхватил что-то, или БД недоступна и fallback
    # в repository отработал не полностью)
    now = datetime.now(timezone.utc)
    cutoff = now.timestamp() - max_age_hours * 3600
    with _tasks_lock:
        to_delete = [
            tid for tid, task in _tasks.items()
            if task.created_at.timestamp() < cutoff
        ]
        for tid in to_delete:
            task = _tasks.pop(tid, None)
            if task:
                for f in task.files:
                    try:
                        Path(f["path"]).unlink(missing_ok=True)
                    except Exception:
                        pass
                try:
                    task_dir = _imports._PROJECT_ROOT / "data" / "tasks" / tid
                    if task_dir.exists():
                        task_dir.rmdir()
                except Exception:
                    pass

    if to_delete or deleted:
        logger.info(
            f"Cleaned up: in-memory={len(to_delete)}, total(inc. DB)={deleted}"
        )
        # === Фаза 1.6: обновляем gauge размера _tasks после cleanup ===
        try:
            from ..middleware.metrics import update_tasks_in_memory
            update_tasks_in_memory(len(_tasks))
        except Exception:
            pass
    return max(deleted, len(to_delete))
