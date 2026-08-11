#!/usr/bin/env python3
"""Smoke-тест Sprint 1: проверка, что facade gibdd_service корректно
re-export'ит все символы после рефакторинга.

Проверки:
1. Импорт всех публичных символов
2. Импорт всех приватных символов, которые используют тесты
3. Проверка типов/значений ключевых символов
4. Проверка, что _tasks остаётся тем же dict-объектом (для тестов)
"""
from __future__ import annotations

import sys
from pathlib import Path

# Добавляем корень gibdd-bot в sys.path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "miniapp"))

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "OK  " if cond else "FAIL"
    msg = f"  [{status}] {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    if not cond:
        failures.append(label)


print("=" * 70)
print("Sprint 1 Smoke Test: gibdd_service facade imports")
print("=" * 70)

# === 1. Все публичные символы, которые импортируют routers ===
print("\n--- 1. Public API (используется routers) ---")

try:
    from miniapp.backend.services.gibdd_service import (
        AnalysisStatus,
        Task,
        TaskStatus,
        ask_llm_question,
        compute_point_stats,
        create_task,
        execute_task,
        generate_clusters_excel,
        generate_clusters_map_html,
        generate_point_stats_excel,
        generate_point_stats_map_html,
        get_llm_providers_status,
        get_regions,
        get_task,
        get_task_async,
        list_user_tasks,
        parse_user_query,
        start_clusters_calculation,
        start_llm_summary,
    )
    check("All public symbols importable", True)
except Exception as exc:
    check("All public symbols importable", False, str(exc))
    raise

# === 2. Приватные символы, которые используют тесты ===
print("\n--- 2. Private symbols (используются tests/) ---")

try:
    from miniapp.backend.services.gibdd_service import (
        _EXECUTE_SEMAPHORE,
        _PROJECT_ROOT,
        _build_clusters_map_html,
        _color_for_severity,
        _ensure_project_path,
        _get_cross_tables,
        _import_module,
        _parse_files_sync,
        _register_task,
        _run_llm_summary_inner,
        _serialize_cluster,
        _task_dir,
        _task_factory,
        _tasks,
        _tasks_lock,
        cleanup_old_tasks,
        ensure_comparison,
        ensure_prev_cards,
    )
    check("All private symbols importable", True)
except Exception as exc:
    check("All private symbols importable", False, str(exc))
    raise

# === 3. Типы ключевых объектов ===
print("\n--- 3. Types of key objects ---")

from collections import OrderedDict
import asyncio

check("_tasks is OrderedDict", isinstance(_tasks, OrderedDict))
check("_tasks_lock has acquire", hasattr(_tasks_lock, "acquire"))
check("_EXECUTE_SEMAPHORE is asyncio.Semaphore",
      isinstance(_EXECUTE_SEMAPHORE, asyncio.Semaphore))

# === 4. Проверка id объекта _tasks (что facade ссылается на тот же dict) ===
print("\n--- 4. Object identity (critical for tests) ---")

from miniapp.backend.services import gibdd_service
from miniapp.backend.services.task_registry import _tasks as registry_tasks

check("gibdd_service._tasks IS task_registry._tasks (same object)",
      gibdd_service._tasks is registry_tasks,
      f"id(gibdd)={id(gibdd_service._tasks)}, id(registry)={id(registry_tasks)}")

check("gibdd_service._register_task IS task_registry._register_task",
      gibdd_service._register_task is __import__(
          "miniapp.backend.services.task_registry", fromlist=["_register_task"]
      )._register_task)

# === 5. Создание задачи через facade ===
print("\n--- 5. create_task via facade ---")

_tasks.clear()  # чистим перед тестом
task = create_task(
    user_id=999,
    region_code="1101",
    region_name="Тестовый регион",
    period_label="Январь 2026",
    dat_list=["1.2026"],
    raw_query="тест",
)
check("create_task returns Task", isinstance(task, Task))
check("Task has correct id", len(task.id) == 12, f"id={task.id}")
check("Task registered in _tasks", task.id in _tasks)
check("Task status is PENDING", task.status == TaskStatus.PENDING)

# === 6. get_task / get_task_async ===
print("\n--- 6. get_task / get_task_async ---")

found = get_task(task.id)
check("get_task returns task", found is task)

# sync version не находит несуществующую задачу
not_found = get_task("nonexistent-id-xxx")
check("get_task returns None for unknown", not_found is None)

# === 7. _task_factory ===
print("\n--- 7. _task_factory ---")

factory_task = _task_factory(
    id="factorytest123",
    user_id=1,
    region_code="1101",
    region_name="X",
    period_label="Y",
    dat_list=["1.2026"],
    raw_query="z",
)
check("_task_factory creates Task", isinstance(factory_task, Task))
check("_task_factory sets id", factory_task.id == "factorytest123")

# === 8. _task_dir ===
print("\n--- 8. _task_dir ---")

d = _task_dir("smoke-test-dir")
check("_task_dir returns Path", isinstance(d, Path))
check("_task_dir exists after call", d.exists())
# cleanup
import shutil
shutil.rmtree(d, ignore_errors=True)

# === 9. get_llm_providers_status ===
print("\n--- 9. get_llm_providers_status ---")

status = get_llm_providers_status()
check("get_llm_providers_status returns dict", isinstance(status, dict))
check("status has 'free' key", "free" in status)
check("status has 'paid' key", "paid" in status)
check("status has 'free_model' key", "free_model" in status)

# === 10. _serialize_cluster ===
print("\n--- 10. _serialize_cluster ---")

serialized = _serialize_cluster({
    "road": "Test Rd",
    "zone_type": "city",
    "total_accidents": 5,
    "deaths": 1,
    "injured": 3,
    "dominant_type": None,
    "type_counter": {"Съезд на обочину": 3, "Наезд на пешехода": 2},
    "center": (55.75, 37.62),
    "start_pos": None,
    "end_pos": None,
    "dates": ["2026-01-15"],
    "dynamics": {"status": "new"},
    "camera_match": None,
    "_is_lost": False,
    "_is_prev_matched": False,
})
check("_serialize_cluster returns dict", isinstance(serialized, dict))
check("center converted to {lat, lon}",
      serialized["center"] == {"lat": 55.75, "lon": 37.62})
check("dominant_type None → ''",
      serialized["dominant_type"] == "")
check("type_counter dictified",
      isinstance(serialized["type_counter"], dict))

# === 11. _color_for_severity ===
print("\n--- 11. _color_for_severity ---")

check("0 deaths → blue", _color_for_severity({"deaths": 0}) == "#2481cc")
check("1 death → orange", _color_for_severity({"deaths": 1}) == "#ff9500")
check("3 deaths → red", _color_for_severity({"deaths": 3}) == "#ff3b30")

# === 12. Очистка ===
_tasks.clear()

# === Итог ===
print("\n" + "=" * 70)
if failures:
    print(f"RESULT: {len(failures)} FAILURES")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("RESULT: ALL CHECKS PASSED ✓")
    print("=" * 70)
    sys.exit(0)
