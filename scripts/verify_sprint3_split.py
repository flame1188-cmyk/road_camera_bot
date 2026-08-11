"""
Smoke-тест Sprint 3: split routers/analyze.py.

Проверяет:
1. Все новые модули импортируются.
2. analyze.py остаётся импортируемым (обратная совместимость).
3. Aggregated router содержит ВСЕ 12 исходных эндпоинтов.
4. Все схемы реэкспортируются из analyze (обратная совместимость).
5. Под-роутеры имеют правильные префиксы (без двойного /dtp/dtp).
6. Циклических импортов нет.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Добавляем miniapp/ в sys.path (как conftest.py).
# parents[1] от .../gibdd-bot/scripts/verify_sprint3_split.py = .../gibdd-bot
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINIAPP_ROOT = PROJECT_ROOT / "miniapp"
if str(MINIAPP_ROOT) not in sys.path:
    sys.path.insert(0, str(MINIAPP_ROOT))
# НЕ добавляем PROJECT_ROOT в sys.path — в /home/z/my-project/ лежит
# старая копия miniapp (29 июля), которая перекроет правильную.


# Ожидаемые эндпоинты в aggregated router (метод, путь).
# Должны совпадать с исходным analyze.py до сплита.
EXPECTED_ENDPOINTS = {
    # LLM providers
    ("GET", "/dtp/tasks/{task_id}/llm/providers"),
    # Clusters (4)
    ("POST", "/dtp/tasks/{task_id}/clusters"),
    ("GET", "/dtp/tasks/{task_id}/clusters"),
    ("GET", "/dtp/tasks/{task_id}/clusters/map"),
    ("GET", "/dtp/tasks/{task_id}/clusters/excel"),
    # Point (3)
    ("POST", "/dtp/tasks/{task_id}/point"),
    ("GET", "/dtp/tasks/{task_id}/point/excel"),
    ("GET", "/dtp/tasks/{task_id}/point/map"),
    # LLM summary (2)
    ("POST", "/dtp/tasks/{task_id}/llm/summary"),
    ("GET", "/dtp/tasks/{task_id}/llm/summary"),
    # LLM Q&A (2)
    ("POST", "/dtp/tasks/{task_id}/llm/ask"),
    ("GET", "/dtp/tasks/{task_id}/llm/qa-history"),
}

# Все символы, которые должен реэкспортить analyze.py
EXPECTED_REEXPORTS = [
    # Shared (из _common)
    "AnalysisStatusResponse",
    "_require_done_task",
    "_state_to_response",
    # Clusters
    "ClustersSummary",
    "ClusterItem",
    "ClustersResult",
    "ClustersResponse",
    "_clusters_result_to_response",
    # Point
    "PointRequest",
    "PointPeriodStats",
    "PointStatsResponse",
    # LLM
    "LLMProvidersResponse",
    "LLMSummaryRequest",
    "LLMSummaryResult",
    "LLMSummaryResponse",
    "LLMAskRequest",
    "LLMAskResponse",
    "QAHistoryItem",
]


def test_new_modules_importable() -> None:
    """Новые модули routers._common / clusters / point / llm импортируются."""
    import importlib
    for mod in [
        "backend.routers._common",
        "backend.routers.clusters",
        "backend.routers.point",
        "backend.routers.llm",
    ]:
        importlib.import_module(mod)
        assert mod in sys.modules, f"{mod} не попал в sys.modules"


def test_analyze_still_importable() -> None:
    """analyze.py остаётся импортируемым (обратная совместимость)."""
    import importlib
    importlib.import_module("backend.routers.analyze")
    assert "backend.routers.analyze" in sys.modules


def test_aggregated_router_has_all_endpoints() -> None:
    """Aggregated router содержит все 12 исходных эндпоинтов."""
    from backend.routers import analyze
    actual = {(m, r.path) for r in analyze.router.routes for m in r.methods}
    missing = EXPECTED_ENDPOINTS - actual
    extra = actual - EXPECTED_ENDPOINTS
    assert not missing, f"Отсутствуют эндпоинты: {missing}"
    assert not extra, f"Лишние эндпоинты: {extra}"


def test_no_double_dtp_prefix() -> None:
    """Под-роутеры не дублируют /dtp (нет /dtp/dtp/tasks/...)."""
    from backend.routers import analyze
    for route in analyze.router.routes:
        assert not route.path.startswith("/dtp/dtp"), (
            f"Двойной /dtp prefix: {route.path}"
        )


def test_all_reexports_present() -> None:
    """Все схемы и хелперы реэкспортируются из analyze."""
    from backend.routers import analyze
    missing = [s for s in EXPECTED_REEXPORTS if not hasattr(analyze, s)]
    assert not missing, f"Не реэкспортируются: {missing}"


def test_reexports_are_same_objects() -> None:
    """Реэкспорт даёт те же объекты, что и прямой импорт (no copies)."""
    from backend.routers import analyze, clusters, llm, point, _common
    assert analyze.ClustersResponse is clusters.ClustersResponse
    assert analyze.PointRequest is point.PointRequest
    assert analyze.LLMProvidersResponse is llm.LLMProvidersResponse
    assert analyze.AnalysisStatusResponse is _common.AnalysisStatusResponse
    assert analyze._require_done_task is _common._require_done_task
    assert analyze._state_to_response is _common._state_to_response


def test_no_circular_imports() -> None:
    """Под-роутеры не зависят от analyze (иначе цикл)."""
    import importlib
    # Импортируем под-роутеры первыми — без analyze
    for name in ["backend.routers._common", "backend.routers.clusters",
                 "backend.routers.point", "backend.routers.llm"]:
        # Если есть цикл — этот импорт упадёт с
        # "cannot import name X from partially initialized module Y"
        importlib.reload(importlib.import_module(name))


def test_main_app_still_works() -> None:
    """main.py создаёт FastAPI app без ошибок после сплита."""
    # Эта проверка — главная: если main.py упадёт при include_router,
    # весь backend не запустится.
    try:
        from backend.main import app
        # Проверяем, что /dtp-маршруты есть
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert any("/dtp/tasks" in p for p in paths), \
            "Нет /dtp/tasks маршрутов в app.routes"
    except ImportError as e:
        # Опциональные зависимости могут отсутствовать в dev-окружении
        # (slowapi, psycopg) — это ОК, главное что сам split не сломан.
        if "slowapi" in str(e) or "psycopg" in str(e):
            print(f"SKIP main app test: optional dep missing ({e})")
        else:
            raise


if __name__ == "__main__":
    print("=== Sprint 3 Smoke Test: split routers/analyze.py ===\n")
    tests = [
        test_new_modules_importable,
        test_analyze_still_importable,
        test_aggregated_router_has_all_endpoints,
        test_no_double_dtp_prefix,
        test_all_reexports_present,
        test_reexports_are_same_objects,
        test_no_circular_imports,
        test_main_app_still_works,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n=== Итог: {passed}/{passed + failed} PASSED ===")
    if failed:
        sys.exit(1)
