"""
Sprint 2 sanity checks: импорты, semaphore, cache_key.
Запуск: python scripts/smoke_sprint2_llm.py
"""
import sys
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "miniapp"))

# Minimal config stub
import types
fake_config = types.ModuleType("config")
fake_config.LLM_API_KEY = "test-key"
fake_config.LLM_MAX_CONCURRENT = 2
fake_config.LLM_CACHE_TTL_SECONDS = 86400
fake_config.LLM_CACHE_VERSION = "1"
fake_config.LLM_PAID_API_KEY = ""
fake_config.LLM_PAID_API_URL = ""
fake_config.LLM_MODEL = "glm-4-flash"
fake_config.LLM_PAID_MODEL = "deepseek-chat"
sys.modules["config"] = fake_config

# Stub other deps
for mod_name in [
    "miniapp.backend.db.connection",
    "miniapp.backend.db.repository",
    "miniapp.backend.middleware.metrics",
]:
    if mod_name not in sys.modules:
        m = types.ModuleType(mod_name)
        if "connection" in mod_name:
            m.is_db_ready = lambda: False
            m.get_pool = lambda: None
            m.init_pool = lambda: asyncio.sleep(0)
            m.close_pool = lambda: asyncio.sleep(0)
            m.health_check = lambda: asyncio.sleep(0)
        if "repository" in mod_name:
            async def _save(t): return None
            m.save_task = _save
            m.load_task = lambda *a: None
            m.attach_heavy_state = lambda t: None
            m.list_user_tasks_from_db = lambda *a: []
            m.delete_old_tasks = lambda *a: 0
        if "metrics" in mod_name:
            m.update_tasks_in_memory = lambda n: None
            m.record_cache_hit = lambda name: None
            m.record_cache_miss = lambda name: None
        sys.modules[mod_name] = m

PASS = 0
FAIL = 0

def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK  ] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}")


print("=== 1. Import llm_ops + check _LLM_SEMAPHORE ===")
async def test_imports():
    try:
        from backend.services import gibdd_service
        from backend.services import llm_ops
        check("import llm_ops", True)
        check("has _LLM_SEMAPHORE", hasattr(llm_ops, "_LLM_SEMAPHORE"))
        check("has _init_llm_semaphore", hasattr(llm_ops, "_init_llm_semaphore"))
        check("_LLM_SEMAPHORE is asyncio.Semaphore", isinstance(llm_ops._LLM_SEMAPHORE, asyncio.Semaphore))
        check("facade re-exports _LLM_SEMAPHORE", hasattr(gibdd_service, "_LLM_SEMAPHORE"))
        # _bound_value is the original limit (Python 3.10+: asyncio.Semaphore stores
        # original value in _bound_value attribute, but it's not public API)
        # Safer: acquire+release to test limit
        sem = llm_ops._LLM_SEMAPHORE
        check(f"semaphore _value attribute present ({sem._value})", hasattr(sem, "_value"))
        check("initial _value matches limit=2", sem._value == 2)
        await sem.acquire()
        check("after acquire: _value=1", sem._value == 1)
        await sem.acquire()
        check("after 2nd acquire: _value=0", sem._value == 0)
        sem.release()
        sem.release()
        check("after 2 releases: _value=2", sem._value == 2)
    except Exception as e:
        check(f"import llm_ops (got {e})", False)
        import traceback; traceback.print_exc()

asyncio.run(test_imports())


print("\n=== 2. Import llm_cache ===")
try:
    from backend.db import llm_cache
    check("import llm_cache", True)
    check("has get_cached_summary", hasattr(llm_cache, "get_cached_summary"))
    check("has put_cached_summary", hasattr(llm_cache, "put_cached_summary"))
    check("has cleanup_expired_llm_cache", hasattr(llm_cache, "cleanup_expired_llm_cache"))
    check("has make_cache_key", hasattr(llm_cache, "make_cache_key"))
except Exception as e:
    check(f"import llm_cache (got {e})", False)
    import traceback; traceback.print_exc()


print("\n=== 3. Cache key determinism ===")
try:
    k1, dh, ph = llm_cache.make_cache_key(
        reg_code="1146", dat_list=["1.2026", "2.2026"], provider="free",
        clusters_ctx="ctx1", cross_tables_ctx="ctx2", system_prompt="sys",
    )
    k2, _, _ = llm_cache.make_cache_key(
        reg_code="1146", dat_list=["2.2026", "1.2026"], provider="free",
        clusters_ctx="ctx1", cross_tables_ctx="ctx2", system_prompt="sys",
    )
    check("same key for same input (dat_list order doesn't matter)", k1 == k2)
    check("cache_key is 64-char SHA-256 hex", len(k1) == 64)

    # Different provider → different key
    k3, _, _ = llm_cache.make_cache_key(
        reg_code="1146", dat_list=["1.2026", "2.2026"], provider="paid",
        clusters_ctx="ctx1", cross_tables_ctx="ctx2", system_prompt="sys",
    )
    check("different provider → different key", k1 != k3)

    # Different clusters_ctx → different key
    k4, _, _ = llm_cache.make_cache_key(
        reg_code="1146", dat_list=["1.2026", "2.2026"], provider="free",
        clusters_ctx="DIFFERENT", cross_tables_ctx="ctx2", system_prompt="sys",
    )
    check("different clusters_ctx → different key", k1 != k4)
except Exception as e:
    check(f"cache key (got {e})", False)
    import traceback; traceback.print_exc()


print("\n=== 4. Semaphore concurrency test ===")
async def test_semaphore():
    # Create a fresh semaphore with limit=2
    sem = asyncio.Semaphore(2)

    # Track concurrent executions
    concurrent = 0
    max_concurrent = 0

    async def worker(idx):
        nonlocal concurrent, max_concurrent
        async with sem:
            concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
            await asyncio.sleep(0.05)  # simulate LLM call
            concurrent -= 1
            return idx

    # Launch 5 concurrent workers with limit=2
    results = await asyncio.gather(*[worker(i) for i in range(5)])

    check(f"all 5 workers completed (results={results})", results == [0, 1, 2, 3, 4])
    check(f"max concurrent ≤ 2 (got {max_concurrent})", max_concurrent == 2)

asyncio.run(test_semaphore())


print("\n=== 5. Cache miss when DB not ready ===")
async def test_cache_miss():
    # is_db_ready() returns False → cache returns None
    result = await llm_cache.get_cached_summary(
        reg_code="1146", dat_list=["1.2026"], provider="free",
        clusters_ctx="ctx", cross_tables_ctx="ctx2", system_prompt="sys",
    )
    check("DB not ready → cache returns None", result is None)

    # put should also be no-op (not raise)
    await llm_cache.put_cached_summary(
        reg_code="1146", dat_list=["1.2026"], provider="free",
        summary_text="test summary",
        clusters_ctx="ctx", cross_tables_ctx="ctx2", system_prompt="sys",
    )
    check("put_cached_summary no-op when DB not ready (no exception)", True)

asyncio.run(test_cache_miss())


print("\n=== 6. FastAPI app init ===")
try:
    sys.path.insert(0, str(ROOT / "miniapp" / "backend"))
    # Stub telegram_auth
    if "backend.telegram_auth" not in sys.modules:
        ta = types.ModuleType("backend.telegram_auth")
        ta.verify_telegram_init_data = lambda *a, **kw: {"ok": True, "user": {"id": 1}}
        ta.TelegramAuthError = Exception
        # New: TelegramUser class for routers/analyze.py import
        from typing import Optional
        class TelegramUser:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)
        ta.TelegramUser = TelegramUser
        ta.get_current_user = lambda: TelegramUser(id=1)
        sys.modules["backend.telegram_auth"] = ta

    from backend.main import app
    routes = [r.path for r in app.routes]
    check(f"FastAPI app has routes ({len(routes)})", len(routes) > 10)
    check("has /metrics", "/metrics" in routes)
except Exception as e:
    check(f"FastAPI app init (got {e})", False)
    import traceback; traceback.print_exc()


print(f"\n{'='*70}")
print(f"RESULT: {PASS} PASSED, {FAIL} FAILED")
print(f"{'='*70}")
sys.exit(0 if FAIL == 0 else 1)
