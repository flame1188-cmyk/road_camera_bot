"""
Verify that MAX_INMEMORY_TASKS patch on facade propagates to _register_task.

Reproduces the bug from TestLruEviction::test_eviction_when_limit_exceeded.
"""
import sys
import types
import asyncio
from pathlib import Path

# Setup sys.path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "miniapp"))

# Stub repository to avoid DB dependency
fake_repository = types.ModuleType("backend.db.repository")
async def fake_save_task(task):
    return None
fake_repository.save_task = fake_save_task
sys.modules["backend.db.repository"] = fake_repository

# Stub connection (used in get_task_async, list_user_tasks)
fake_conn = types.ModuleType("backend.db.connection")
fake_conn.is_db_ready = lambda: False
sys.modules["backend.db.connection"] = fake_conn

# Stub middleware.metrics (used in _register_task)
fake_metrics = types.ModuleType("backend.middleware.metrics")
def fake_update(n):
    pass
fake_metrics.update_tasks_in_memory = fake_update
sys.modules["backend.middleware.metrics"] = fake_metrics

# Now import facade
from backend.services import gibdd_service
from backend.services import task_registry

print(f"Initial MAX_INMEMORY_TASKS: {gibdd_service.MAX_INMEMORY_TASKS}")
print(f"task_registry.MAX_INMEMORY_TASKS: {task_registry.MAX_INMEMORY_TASKS}")
print(f"facade id: {id(gibdd_service.MAX_INMEMORY_TASKS)}")
print(f"task_registry id: {id(task_registry.MAX_INMEMORY_TASKS)}")

# Clear _tasks
gibdd_service._tasks.clear()

# Patch facade
gibdd_service.MAX_INMEMORY_TASKS = 3
print(f"\nAfter patch: gibdd_service.MAX_INMEMORY_TASKS = {gibdd_service.MAX_INMEMORY_TASKS}")
print(f"task_registry.MAX_INMEMORY_TASKS (should still be 50): {task_registry.MAX_INMEMORY_TASKS}")

# Create 5 tasks
for i in range(5):
    t = gibdd_service.create_task(
        user_id=1, region_code="1101", region_name="Рег",
        period_label=f"Период{i}", dat_list=["1.2025"], raw_query=f"q-{i}",
    )

print(f"\n_tasks count after 5 creates: {len(gibdd_service._tasks)}")
print(f"Expected: 3 (LRU evicted 2 oldest)")

if len(gibdd_service._tasks) == 3:
    print("\n✓ PASS: LRU eviction works through facade patch")
    sys.exit(0)
else:
    print(f"\n✗ FAIL: expected 3, got {len(gibdd_service._tasks)}")
    sys.exit(1)
