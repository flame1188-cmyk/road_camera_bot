"""
Smoke-тесты: проверяют, что все ключевые модули импортируются без ошибок.

Запуск: pytest tests/smoke/test_imports.py -m smoke

Эти тесты не проверяют поведение — только то, что:
  - нет циклических импортов
  - нет опечаток в импортах
  - нет SyntaxError
  - опциональные зависимости (pydantic, fastapi, httpx) реально установлены

Если что-то здесь падает — CI должен остановиться сразу, до запуска тяжёлых тестов.
"""
import importlib
import sys

import pytest


# Список модулей, которые обязаны импортироваться без ошибок.
# Если добавили новый модуль — добавьте его сюда.
EXPECTED_MODULES = [
    # Корневые модули gibdd-bot
    "analytics",
    "gibdd_parser",
    "llm_analyzer",
    "user_request_parser",
    "config",
    "regions_builtin",
    "regions_cache",
    "camera_matcher",
    "camera_loader",
    "camera_cache",
    "data_cache",
    "point_statistics",
    "concentration_points",
    "news_fetcher",
    "excel_generator",
    "report_generator",
    # Mini app backend (без DB — DB требует psycopg, опционально)
    "backend.main",
    "backend.config",
    "backend.telegram_auth",
    "backend.services.gibdd_service",
    "backend.services.np_bdd_service",
    "backend.routers.analyze",
    "backend.routers.cameras",
    "backend.routers.dtp",
    "backend.routers.np_bdd",
    "backend.routers.parse",
    "backend.routers.regions",
    "backend.middleware.metrics",
]

# rate_limit требует slowapi — опциональную зависимость. Пропускаем если нет.
OPTIONAL_MIDDLEWARE_MODULES = [
    "backend.middleware.rate_limit",
]

# DB-модули требуют psycopg (async Postgres). В dev-окружении их может не быть.
# Если psycopg установлен — модули обязаны импортироваться; если нет — пропускаем.
OPTIONAL_DB_MODULES = [
    "backend.db.connection",
    "backend.db.repository",
    "backend.db.cards_cache",
    "backend.db.clusters_cache",
    "backend.db.excel_cache",
    "backend.db.init_schema",
]


def _module_available(name: str) -> bool:
    """Проверяет, установлен ли модуль (без импорта модулей проекта)."""
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False


@pytest.mark.smoke
@pytest.mark.parametrize("module_name", EXPECTED_MODULES)
def test_module_imports_without_errors(module_name: str) -> None:
    """Каждый ключевой модуль должен импортироваться без исключений."""
    try:
        importlib.import_module(module_name)
    except ImportError as e:
        pytest.fail(
            f"Не удалось импортировать модуль {module_name}: {e}. "
            f"Возможно, не установлена зависимость или есть опечатка в импорте."
        )
    except SyntaxError as e:
        pytest.fail(
            f"SyntaxError при импорте {module_name}: {e}. "
            f"Проверьте последние изменения в файле."
        )


@pytest.mark.smoke
@pytest.mark.parametrize("module_name", OPTIONAL_MIDDLEWARE_MODULES)
def test_optional_middleware_imports_when_deps_present(module_name: str) -> None:
    """Опциональные middleware (rate_limit → slowapi) импортируются, только если dep есть."""
    if not _module_available("slowapi"):
        pytest.skip("slowapi не установлен — rate_limit опционален в dev-окружении")

    try:
        importlib.import_module(module_name)
    except ImportError as e:
        pytest.fail(
            f"slowapi установлен, но модуль {module_name} не импортируется: {e}."
        )


@pytest.mark.smoke
@pytest.mark.parametrize("module_name", OPTIONAL_DB_MODULES)
def test_db_module_imports_when_psycopg_present(module_name: str) -> None:
    """DB-модули импортируются, только если psycopg установлен.

    psycopg — опциональная зависимость (нужна только в продакшене с PostgreSQL).
    В dev-окружении без DB — модули пропускаются.
    """
    if not _module_available("psycopg"):
        pytest.skip("psycopg не установлен — DB-модули опциональны в dev-окружении")

    try:
        importlib.import_module(module_name)
    except ImportError as e:
        pytest.fail(
            f"psycopg установлен, но модуль {module_name} не импортируется: {e}. "
            f"Видимо, реальный баг в импортах."
        )


@pytest.mark.smoke
def test_no_circular_imports_in_miniapp() -> None:
    """Проверка, что нет циклических импортов в miniapp.backend.

    Если бы цикл был — importlib.import_module("backend.main") выше уже упал бы
    с ImportError "cannot import name X from partially initialized module Y".
    Здесь мы дополнительно убеждаемся, что все роутеры импортируются в любом
    порядке без ре-импорта.
    """
    importlib.import_module("backend.routers.analyze")
    importlib.import_module("backend.routers.cameras")
    importlib.import_module("backend.routers.dtp")
    importlib.import_module("backend.routers.np_bdd")
    importlib.import_module("backend.routers.parse")
    importlib.import_module("backend.routers.regions")

    # Если мы сюда попали — значит, циклических импортов нет.
    assert "backend.routers.analyze" in sys.modules
    assert "backend.routers.dtp" in sys.modules


@pytest.mark.smoke
def test_test_fixtures_importable() -> None:
    """Тестовые фикстуры (synthetic_cards) должны импортироваться."""
    from tests.fixtures.synthetic_cards import (
        BASE_CARD,
        card_with_alcohol,
        card_with_death,
        card_with_pedestrian,
        cards_basic_set,
        make_card,
    )

    assert isinstance(BASE_CARD, dict)
    assert len(cards_basic_set()) == 5
    assert card_with_death()["pog"] == "1"
    assert card_with_pedestrian()["uch_info"]


@pytest.mark.smoke
def test_worklog_file_exists() -> None:
    """Worklog должен существовать — это обязательный artifact проекта."""
    from pathlib import Path

    worklog = Path(__file__).resolve().parents[2] / "worklog.md"
    assert worklog.exists(), f"worklog.md не найден по пути {worklog}"
    # Файл не пустой
    assert worklog.stat().st_size > 0, "worklog.md пуст"
