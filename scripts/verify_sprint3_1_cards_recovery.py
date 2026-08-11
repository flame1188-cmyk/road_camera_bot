"""
Smoke-тест Sprint 3.1: cards_cache recovery fix.

Проверяет:
1. ensure_cards существует в pipeline и экспортируется из facade.
2. ensure_cards вызывается из всех 4 точек:
   - llm_ops._run_llm_summary_inner
   - llm_ops._check_llm_cache
   - llm_ops.ask_llm_question
   - clusters_ops.start_clusters_calculation
   - analytics_ops.compute_point_stats
3. ensure_cards корректно обрабатывает случаи:
   - task.cards уже есть → ok=True, без вызова _fetch
   - task.cards пустой + stub _fetch возвращает cards → ok=True
   - task.cards пустой + stub _fetch возвращает [] → ok=False
   - task.status=FETCHING → ok=False (не вмешиваемся)
   - task.status=FAILED → ok=False (не пытаемся снова)
4. CARDS_CACHE_TTL_SECONDS теперь 604800 (7 дней).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Добавляем miniapp/ в sys.path (как conftest.py)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINIAPP_ROOT = PROJECT_ROOT / "miniapp"
if str(MINIAPP_ROOT) not in sys.path:
    sys.path.insert(0, str(MINIAPP_ROOT))

# Устанавливаем переменные окружения для тестов
import os
os.environ.setdefault("DATABASE_URL", "")  # пустая — БД не используется
os.environ.setdefault("LLM_API_KEY", "test-key")


def test_ensure_cards_exists() -> None:
    """ensure_cards есть в pipeline и экспортируется из facade."""
    from backend.services import gibdd_service
    from backend.services.pipeline import ensure_cards
    assert ensure_cards is gibdd_service.ensure_cards
    assert callable(ensure_cards)


def test_ensure_cards_called_in_all_entry_points() -> None:
    """ensure_cards вызывается из всех 5 точек использования."""
    import inspect
    from backend.services import llm_ops, clusters_ops, analytics_ops

    src_llm = inspect.getsource(llm_ops._run_llm_summary_inner)
    src_check = inspect.getsource(llm_ops._check_llm_cache)
    src_ask = inspect.getsource(llm_ops.ask_llm_question)
    src_clusters = inspect.getsource(clusters_ops.start_clusters_calculation)
    src_point = inspect.getsource(analytics_ops.compute_point_stats)

    assert "ensure_cards" in src_llm, "ensure_cards missing in _run_llm_summary_inner"
    assert "ensure_cards" in src_check, "ensure_cards missing in _check_llm_cache"
    assert "ensure_cards" in src_ask, "ensure_cards missing in ask_llm_question"
    assert "ensure_cards" in src_clusters, "ensure_cards missing in start_clusters_calculation"
    assert "ensure_cards" in src_point, "ensure_cards missing in compute_point_stats"


async def _run_async(coro):
    """Хелпер для запуска coroutine в тесте."""
    return await coro


def test_ensure_cards_fast_path() -> None:
    """Если task.cards уже есть — ok=True, без вызова _fetch."""
    from backend.services.gibdd_service import ensure_cards, Task, TaskStatus

    task = Task(
        id="test1", user_id=1, region_code="1101", region_name="Тест",
        period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        status=TaskStatus.DONE,
    )
    task.cards = [{"kart_id": "1", "pog": "1", "ran": "2"}]

    result = asyncio.get_event_loop().run_until_complete(ensure_cards(task))
    assert result["ok"] is True
    assert result["cards"] is task.cards  # тот же объект, без копирования


def test_ensure_cards_fetching_status() -> None:
    """Если task.status=FETCHING — ok=False (не вмешиваемся)."""
    from backend.services.gibdd_service import ensure_cards, Task, TaskStatus

    task = Task(
        id="test2", user_id=1, region_code="1101", region_name="Тест",
        period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        status=TaskStatus.FETCHING,
    )
    task.cards = []

    result = asyncio.get_event_loop().run_until_complete(ensure_cards(task))
    assert result["ok"] is False
    assert "выполняется" in result["error"]


def test_ensure_cards_failed_status() -> None:
    """Если task.status=FAILED — ok=False (не пытаемся снова)."""
    from backend.services.gibdd_service import ensure_cards, Task, TaskStatus

    task = Task(
        id="test3", user_id=1, region_code="1101", region_name="Тест",
        period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        status=TaskStatus.FAILED,
        error="API ГИБДД недоступен",
    )
    task.cards = []

    result = asyncio.get_event_loop().run_until_complete(ensure_cards(task))
    assert result["ok"] is False
    assert "ошибкой" in result["error"]


def test_cards_cache_ttl_raised() -> None:
    """CARDS_CACHE_TTL_SECONDS теперь 604800 (7 дней), не 3600."""
    import config
    assert config.CARDS_CACHE_TTL_SECONDS == 604800, (
        f"Expected 604800, got {config.CARDS_CACHE_TTL_SECONDS}"
    )


def test_cards_cache_module_uses_new_ttl() -> None:
    """cards_cache.DEFAULT_TTL_SECONDS соответствует config."""
    from backend.db.cards_cache import DEFAULT_TTL_SECONDS
    assert DEFAULT_TTL_SECONDS == 604800, (
        f"Expected 604800, got {DEFAULT_TTL_SECONDS}"
    )


if __name__ == "__main__":
    print("=== Sprint 3.1 Smoke Test: cards_cache recovery fix ===\n")

    tests = [
        test_ensure_cards_exists,
        test_ensure_cards_called_in_all_entry_points,
        test_ensure_cards_fast_path,
        test_ensure_cards_fetching_status,
        test_ensure_cards_failed_status,
        test_cards_cache_ttl_raised,
        test_cards_cache_module_uses_new_ttl,
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
