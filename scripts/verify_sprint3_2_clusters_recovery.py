"""
Smoke-тест Sprint 3.2: clusters_cache recovery fix.

Проверяет:
1. В clusters_ops.start_clusters_calculation при cache HIT с raw_clusters=None
   и raw_preclusters=None кэш игнорируется (нет раннего return),
   расчёт идёт штатным путём.
2. В routers/clusters.start_clusters при status=DONE с пустыми
   raw_clusters/raw_preclusters форсируется recompute
   (state сбрасывается, запускается start_clusters_calculation).
3. При cache HIT с raw_clusters — кэш используется, return на месте.
4. При cache MISS (cached is None) — расчёт идёт штатно (не регрессия).
5. Информативное логирование при игнорировании кэша.
6. При status=DONE с непустыми raw_clusters — кэш возвращается без recompute.
7. AST-валидация обоих изменённых файлов.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import sys
import types
from pathlib import Path

# Добавляем miniapp/ в sys.path (как conftest.py)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINIAPP_ROOT = PROJECT_ROOT / "miniapp"
if str(MINIAPP_ROOT) not in sys.path:
    sys.path.insert(0, str(MINIAPP_ROOT))

import os
os.environ.setdefault("DATABASE_URL", "")  # пустая — БД не используется
os.environ.setdefault("LLM_API_KEY", "test-key")


# ============================================================
# 1. AST-валидация изменённых файлов
# ============================================================
def test_ast_valid() -> None:
    """Оба файла синтаксически валидны."""
    files = [
        PROJECT_ROOT / "miniapp" / "backend" / "services" / "clusters_ops.py",
        PROJECT_ROOT / "miniapp" / "backend" / "routers" / "clusters.py",
    ]
    for f in files:
        with open(f) as fp:
            ast.parse(fp.read())
    assert len(files) == 2


# ============================================================
# 2. clusters_ops: cache HIT с raw=None → игнор кэша
# ============================================================
def test_clusters_ops_ignores_cache_without_raw() -> None:
    """При cache HIT с raw_clusters=None и raw_preclusters=None
    кэш игнорируется — нет раннего return, расчёт идёт штатно.
    """
    from backend.services import clusters_ops

    src = inspect.getsource(clusters_ops.start_clusters_calculation)

    # Проверяем, что есть новая логика "if not has_raw"
    assert "has_raw = bool(cached_raw_clusters or cached_raw_preclusters)" in src, (
        "missing has_raw check"
    )
    assert "if not has_raw:" in src, (
        "missing 'if not has_raw' branch (ignore cache)"
    )
    # В старой ветке должно быть логирование
    assert "игнорируем кэш" in src, (
        "missing log message about ignoring cache"
    )
    # В ветке есть else с восстановлением кэша
    assert "else:" in src, "missing else branch (use cache)"

    # Находим место "if not has_raw:"
    # После него должен быть блок логирования и НЕ быть return
    # (просто падаем к штатному расчёту).
    idx = src.index("if not has_raw:")
    # Берём 2000 символов после — должно покрыть весь if/else блок
    block = src[idx:idx + 2000]
    # Блок логирует и НЕ вызывает return внутри if not has_raw
    assert "logger.info" in block, "missing logger.info in ignore-cache branch"
    assert "пересчитываем" in block, "missing 'пересчитываем' in log message"

    # Проверяем, что return есть в else-ветке (use cache).
    # Находим "else:" — return должен быть после него, до конца функции.
    else_idx = block.index("else:")
    else_block = block[else_idx:else_idx + 2000]
    assert "return" in else_block, "missing return in else (use cache) branch"


# ============================================================
# 3. clusters_ops: cache HIT с raw → использует кэш (return)
# ============================================================
def test_clusters_ops_uses_cache_with_raw() -> None:
    """При cache HIT с raw_clusters кэш используется (return)."""
    from backend.services import clusters_ops

    src = inspect.getsource(clusters_ops.start_clusters_calculation)

    # Старая ветка else должна восстанавливать raw_clusters
    assert "task.raw_clusters = cached_raw_clusters" in src, (
        "missing 'task.raw_clusters = cached_raw_clusters' in cache-hit branch"
    )
    assert "task.raw_preclusters = cached_raw_preclusters" in src, (
        "missing 'task.raw_preclusters = cached_raw_preclusters'"
    )
    assert 'state.stage = "Готово (из кэша)"' in src, (
        "missing 'Готово (из кэша)' stage"
    )


# ============================================================
# 4. routers/clusters: DONE без raw → recompute
# ============================================================
def test_router_done_without_raw_forces_recompute() -> None:
    """При status=DONE с пустыми raw_clusters/raw_preclusters
    форсируется recompute (сброс state + запуск start_clusters_calculation).
    """
    from backend.routers import clusters as clusters_router

    src = inspect.getsource(clusters_router.start_clusters)

    # Проверяем наличие новой логики
    assert "raw_clusters" in src, "missing raw_clusters check in start_clusters"
    assert "raw_preclusters" in src, "missing raw_preclusters check"
    assert "if not task.raw_clusters and not task.raw_preclusters:" in src, (
        "missing 'if not task.raw_clusters and not task.raw_preclusters' check"
    )
    assert "форсируем recompute" in src, (
        "missing 'форсируем recompute' log message"
    )
    assert "start_clusters_calculation" in src, (
        "missing start_clusters_calculation call in recompute branch"
    )


# ============================================================
# 5. routers/clusters: DONE с raw → кэш без recompute
# ============================================================
def test_router_done_with_raw_returns_cached() -> None:
    """При status=DONE с raw_clusters — кэш возвращается без recompute."""
    from backend.routers import clusters as clusters_router

    src = inspect.getsource(clusters_router.start_clusters)

    # Должна быть ветка возврата готового результата без recompute
    assert "ClustersResponse(" in src
    assert "_clusters_result_to_response(state.result)" in src


# ============================================================
# 6. Behavior test: clusters_calculation с cache HIT raw=None
# ============================================================
def test_behavior_cache_hit_without_raw_triggers_recompute() -> None:
    """Полный сценарий: cache HIT, но raw_clusters=None — функция
    продолжает работу и пересчитывает clusters через concentration_points.
    """
    from backend.services import gibdd_service
    from backend.services import _imports
    from tests.integration._gibdd_stubs import install_stubs, make_minimal_cards

    # Minimal monkeypatch-like stub: setattr/setitem на dict sys.modules
    class _MiniMonkey:
        def setattr(self, obj, name, value):
            setattr(obj, name, value)
        def setitem(self, mapping, key, value):
            mapping[key] = value
    install_stubs(_MiniMonkey())

    # Stub: cache HIT, но БЕЗ raw_clusters / raw_preclusters (старая запись)
    fake_cc = types.ModuleType("backend.db.clusters_cache")
    cached_result_no_raw = {
        "result": {
            "total_clusters": 5,  # в кэше было 5 очагов
            "total_lost": 0,
            "total_prev_matched": 0,
            "total_preclusters": 0,
            "current_total_dtp": 50,
            "current_deaths": 1,
            "current_injured": 10,
            "dynamics": {"new": 5},
            "clusters": [],
            "preclusters": [],
            "has_prev_data": False,
            "prev_label": None,
            "current_label": "Май 2025",
            "region_name": "Рег",
        },
        "raw_clusters": None,  # старая запись без raw
        "raw_preclusters": None,
    }
    put_calls = []
    async def fake_get(**kw):
        return cached_result_no_raw
    async def fake_put(**kw):
        put_calls.append(kw)
        return None
    fake_cc.get_cached_clusters = fake_get
    fake_cc.put_cached_clusters = fake_put
    sys.modules["backend.db.clusters_cache"] = fake_cc

    # Stub: concentration_points вернёт 1 очаг
    conc = types.ModuleType("concentration_points")
    async def fake_calc(current_cards, prev_cards, progress_callback=None, reg_code=None):
        if progress_callback:
            await progress_callback("test stage")
        # Возвращаем 1 очаг — это покажет, что пересчёт произошёл
        return [
            {
                "road": "ул. Пересчёта",
                "center": (60.0, 30.0),
                "total_accidents": 5,
                "deaths": 0,
                "injured": 2,
                "dominant_type": "столкновение",
                "type_counter": {"столкновение": 5},
                "start_pos": 0.0,
                "end_pos": 100.0,
                "dates": ["2025-05-01"],
                "dynamics": {"status": "new"},
                "cards": [],
            }
        ], [], []
    conc.calculate_concentration_dynamics = fake_calc
    conc.enrich_clusters_with_cameras = lambda *a, **kw: None
    sys.modules["concentration_points"] = conc

    # Stub repository (save_task)
    fake_repo = types.ModuleType("backend.db.repository")
    async def fake_save_task(t): return None
    fake_repo.save_task = fake_save_task
    sys.modules["backend.db.repository"] = fake_repo

    # Stub connection — DB готов (чтобы cache lookup шёл в наш fake_cc)
    fake_db = types.ModuleType("backend.db.connection")
    fake_db.is_db_ready = lambda: True
    fake_db.get_pool = lambda: None  # pool не нужен — fake_cc уже подменён
    sys.modules["backend.db.connection"] = fake_db

    task = gibdd_service.create_task(
        user_id=1, region_code="1101", region_name="Рег",
        period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
    )
    task.cards = make_minimal_cards(3)

    asyncio.get_event_loop().run_until_complete(
        gibdd_service.start_clusters_calculation(task)
    )

    state = task.clusters_state
    # Должен быть DONE (не FAILED)
    assert state.status == gibdd_service.AnalysisStatus.DONE, (
        f"Expected DONE, got {state.status}, error={state.error}"
    )
    # Пересчёт дал 1 очаг (не 5 из кэша)
    assert state.result["total_clusters"] == 1, (
        f"Expected 1 (recomputed), got {state.result['total_clusters']} "
        f"(cache should have been ignored)"
    )
    # raw_clusters должны быть заполнены пересчётом
    assert len(task.raw_clusters) == 1, (
        f"Expected 1 raw_cluster, got {len(task.raw_clusters)}"
    )
    # PUT в кэш вызван с новыми raw_clusters
    assert len(put_calls) == 1, f"Expected 1 put_cached_clusters call, got {len(put_calls)}"
    put_kw = put_calls[0]
    assert put_kw.get("raw_clusters") is not None, (
        "put_cached_clusters called without raw_clusters — cache will stay broken"
    )
    assert len(put_kw["raw_clusters"]) == 1


# ============================================================
# 7. Behavior test: cache HIT с raw → используется (без пересчёта)
# ============================================================
def test_behavior_cache_hit_with_raw_uses_cache() -> None:
    """Полный сценарий: cache HIT с raw_clusters — функция использует кэш,
    concentration_points НЕ вызывается.
    """
    from backend.services import gibdd_service
    from backend.services import _imports
    from tests.integration._gibdd_stubs import install_stubs, make_minimal_cards

    class _MiniMonkey:
        def setattr(self, obj, name, value):
            setattr(obj, name, value)
        def setitem(self, mapping, key, value):
            mapping[key] = value
    install_stubs(_MiniMonkey())

    # Stub: cache HIT С raw_clusters (новая запись)
    fake_cc = types.ModuleType("backend.db.clusters_cache")
    cached_full = {
        "result": {
            "total_clusters": 3,
            "total_lost": 0,
            "total_prev_matched": 0,
            "total_preclusters": 0,
            "current_total_dtp": 30,
            "current_deaths": 0,
            "current_injured": 5,
            "dynamics": {"new": 3},
            "clusters": [],
            "preclusters": [],
            "has_prev_data": False,
            "prev_label": None,
            "current_label": "Май 2025",
            "region_name": "Рег",
        },
        "raw_clusters": [{"road": "ул. ИзКэша", "cards": []}],  # есть raw!
        "raw_preclusters": None,
    }
    conc_called = []
    async def fake_get(**kw):
        return cached_full
    async def fake_put(**kw):
        return None
    fake_cc.get_cached_clusters = fake_get
    fake_cc.put_cached_clusters = fake_put
    sys.modules["backend.db.clusters_cache"] = fake_cc

    # Stub: concentration_points — НЕ должен вызываться
    conc = types.ModuleType("concentration_points")
    async def fake_calc(*a, **kw):
        conc_called.append(True)
        return [], [], []
    conc.calculate_concentration_dynamics = fake_calc
    conc.enrich_clusters_with_cameras = lambda *a, **kw: None
    sys.modules["concentration_points"] = conc

    fake_repo = types.ModuleType("backend.db.repository")
    async def fake_save_task(t): return None
    fake_repo.save_task = fake_save_task
    sys.modules["backend.db.repository"] = fake_repo

    fake_db = types.ModuleType("backend.db.connection")
    fake_db.is_db_ready = lambda: True
    fake_db.get_pool = lambda: None
    sys.modules["backend.db.connection"] = fake_db

    task = gibdd_service.create_task(
        user_id=1, region_code="1101", region_name="Рег",
        period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
    )
    task.cards = make_minimal_cards(3)

    asyncio.get_event_loop().run_until_complete(
        gibdd_service.start_clusters_calculation(task)
    )

    state = task.clusters_state
    assert state.status == gibdd_service.AnalysisStatus.DONE, (
        f"Expected DONE, got {state.status}"
    )
    # Кэш вернул 3 очага — НЕ пересчитано в 0
    assert state.result["total_clusters"] == 3, (
        f"Expected 3 (from cache), got {state.result['total_clusters']}"
    )
    # raw_clusters восстановлены из кэша
    assert len(task.raw_clusters) == 1, (
        f"Expected 1 raw_cluster (from cache), got {len(task.raw_clusters)}"
    )
    # concentration_points НЕ вызывался
    assert conc_called == [], (
        f"concentration_points called {len(conc_called)} times — "
        f"cache should have been used"
    )


# ============================================================
# 8. Behavior test: cache MISS → обычный расчёт (не регрессия)
# ============================================================
def test_behavior_cache_miss_proceeds_normally() -> None:
    """Cache MISS → функция штатно пересчитывает через concentration_points.
    Это проверка, что мы не сломали существующее поведение.
    """
    from backend.services import gibdd_service
    from backend.services import _imports
    from tests.integration._gibdd_stubs import install_stubs, make_minimal_cards

    class _MiniMonkey:
        def setattr(self, obj, name, value):
            setattr(obj, name, value)
        def setitem(self, mapping, key, value):
            mapping[key] = value
    install_stubs(_MiniMonkey())

    # Stub: cache MISS
    fake_cc = types.ModuleType("backend.db.clusters_cache")
    async def fake_get(**kw):
        return None
    async def fake_put(**kw):
        return None
    fake_cc.get_cached_clusters = fake_get
    fake_cc.put_cached_clusters = fake_put
    sys.modules["backend.db.clusters_cache"] = fake_cc

    conc = types.ModuleType("concentration_points")
    async def fake_calc(current_cards, prev_cards, progress_callback=None, reg_code=None):
        if progress_callback:
            await progress_callback("test")
        return [
            {
                "road": "ул. Новая",
                "center": (60.0, 30.0),
                "total_accidents": 3,
                "deaths": 0,
                "injured": 1,
                "dominant_type": "наезд",
                "type_counter": {"наезд": 3},
                "start_pos": 0.0,
                "end_pos": 50.0,
                "dates": ["2025-05-01"],
                "dynamics": {"status": "new"},
                "cards": [],
            }
        ], [], []
    conc.calculate_concentration_dynamics = fake_calc
    conc.enrich_clusters_with_cameras = lambda *a, **kw: None
    sys.modules["concentration_points"] = conc

    fake_repo = types.ModuleType("backend.db.repository")
    async def fake_save_task(t): return None
    fake_repo.save_task = fake_save_task
    sys.modules["backend.db.repository"] = fake_repo

    fake_db = types.ModuleType("backend.db.connection")
    fake_db.is_db_ready = lambda: True
    fake_db.get_pool = lambda: None
    sys.modules["backend.db.connection"] = fake_db

    task = gibdd_service.create_task(
        user_id=1, region_code="1101", region_name="Рег",
        period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
    )
    task.cards = make_minimal_cards(2)

    asyncio.get_event_loop().run_until_complete(
        gibdd_service.start_clusters_calculation(task)
    )

    state = task.clusters_state
    assert state.status == gibdd_service.AnalysisStatus.DONE
    assert state.result["total_clusters"] == 1
    assert len(task.raw_clusters) == 1


if __name__ == "__main__":
    print("=== Sprint 3.2 Smoke Test: clusters_cache recovery fix ===\n")

    tests = [
        test_ast_valid,
        test_clusters_ops_ignores_cache_without_raw,
        test_clusters_ops_uses_cache_with_raw,
        test_router_done_without_raw_forces_recompute,
        test_router_done_with_raw_returns_cached,
        test_behavior_cache_hit_without_raw_triggers_recompute,
        test_behavior_cache_hit_with_raw_uses_cache,
        test_behavior_cache_miss_proceeds_normally,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            import traceback
            print(f"  FAIL  {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n=== Итог: {passed}/{passed + failed} PASSED ===")
    if failed:
        sys.exit(1)
