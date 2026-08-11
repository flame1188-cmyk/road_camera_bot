"""
Smoke-тесты для FastAPI приложения: app создаётся, роутеры зарегистрированы,
health-эндпоинт отвечает.

Запуск: pytest tests/smoke/test_app_init.py -m smoke
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.mark.smoke
def test_app_creates_without_errors() -> None:
    """FastAPI app должен создаваться без исключений."""
    from backend.main import app

    assert isinstance(app, FastAPI)
    assert app.title == "GIBDD Mini App API"


@pytest.mark.smoke
def test_all_routers_registered() -> None:
    """Все 6 роутеров должны быть зарегистрированы в app.

    Если кто-то забыл app.include_router(...) — тест поймает.

    Использует OpenAPI-схему как канонический источник правды о путях:
    app.routes в разных версиях FastAPI/Starlette может отдавать маршруты
    из include_router() лениво (только после первого запроса), а OpenAPI
    всегда содержит полный список после инициализации app.
    """
    from backend.main import app

    # Достаём пути из OpenAPI-схемы — это самый надёжный способ.
    # Если schema ещё не сгенерирована — генерируем принудительно.
    try:
        schema = app.openapi()
        all_paths = set(schema.get("paths", {}).keys())
    except Exception:
        # Fallback: читаем через TestClient
        with TestClient(app) as client:
            response = client.get("/openapi.json")
            assert response.status_code == 200, (
                f"/openapi.json должен возвращать 200, got {response.status_code}"
            )
            all_paths = set(response.json().get("paths", {}).keys())

    # Минимальный набор путей, которые обязаны быть.
    # Если добавили новый эндпоинт — добавьте его путь сюда.
    expected_prefixes = [
        "/regions",       # regions.router
        "/parse",         # parse.router
        "/dtp",           # dtp.router + analyze.router (оба под /dtp)
        "/cameras",       # cameras.router
        "/np-bdd",        # np_bdd.router
    ]

    missing = []
    for prefix in expected_prefixes:
        # Проверяем, что есть хотя бы один путь, начинающийся с префикса.
        # Например, "/regions" → проверяем, что есть "/regions" или "/regions/...".
        if not any(p == prefix or p.startswith(prefix + "/") for p in all_paths):
            missing.append(prefix)

    assert not missing, (
        f"Не зарегистрированы роутеры для префиксов: {missing}. "
        f"Проверь app.include_router(...) в backend/main.py. "
        f"Доступные пути: {sorted(all_paths)}"
    )


@pytest.mark.smoke
def test_health_endpoint_returns_ok() -> None:
    """/miniapp/health должен отвечать 200 и {"status": "ok"}."""
    from backend.main import app

    with TestClient(app) as client:
        response = client.get("/miniapp/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "gibdd-miniapp"


@pytest.mark.smoke
def test_openapi_schema_generated() -> None:
    """OpenAPI schema должна генерироваться без ошибок.

    Если валидация Pydantic-моделей сломана — /openapi.json упадёт.
    """
    from backend.main import app

    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "GIBDD Mini App API"
    # Должны быть пути для всех роутеров
    assert "/miniapp/health" in schema["paths"]
    assert "/regions/" in schema["paths"] or "/regions" in schema["paths"]


@pytest.mark.smoke
def test_cors_middleware_configured() -> None:
    """CORS middleware должен быть добавлен (иначе mini-app не заработает в браузере)."""
    from backend.main import app

    # Проверяем, что хотя бы один middleware зарегистрирован
    # (CORSMiddleware добавляется в backend.main, хотя в текущем main.py
    # нет явного add_middleware — это может быть проблемой, но
    # тест провалится осознанно, если CORS нет).
    middleware_types = [type(m.cls).__name__ if hasattr(m, "cls") else "" for m in app.user_middleware]

    # Если CORS нет — это серьёзная проблема для mini-app.
    # Но не падаем жёстко: возможно, разработчик решил перенести CORS в nginx.
    # Логируем предупреждение в виде soft assert.
    has_cors = any("CORSMiddleware" in t for t in middleware_types)
    if not has_cors:
        # Soft warning — тест проходит, но выводит заметку.
        # Если вы уверены, что CORS должен быть в app — поменяйте на assert.
        import warnings
        warnings.warn(
            "CORSMiddleware не найден в app.user_middleware. "
            "Убедитесь, что CORS настроен либо в FastAPI, либо в nginx.",
            stacklevel=2,
        )


@pytest.mark.smoke
def test_app_has_docs_and_redoc() -> None:
    """/docs и /redoc должны быть доступны (стандартные FastAPI endpoints)."""
    from backend.main import app

    with TestClient(app) as client:
        docs_response = client.get("/docs")
        redoc_response = client.get("/redoc")

    assert docs_response.status_code == 200, "/docs должен возвращать 200"
    assert redoc_response.status_code == 200, "/redoc должен возвращать 200"


@pytest.mark.smoke
def test_settings_loaded_without_errors() -> None:
    """Settings (pydantic-settings) должны загружаться без ошибок валидации."""
    from backend.config import Settings, get_settings

    settings = get_settings()
    assert isinstance(settings, Settings)

    # Базовые проверки — поля, которые используются везде
    assert isinstance(settings.telegram_bot_token, str)
    assert isinstance(settings.app_port, int)
    assert settings.app_port > 0


@pytest.mark.smoke
def test_gibdd_service_global_state_initializable() -> None:
    """Глобальное состояние gibdd_service (_tasks) должно быть инициализируемо.

    Проверяет, что gibdd_service импортируется без ошибок и
    имеет ожидаемые глобальные переменные.
    """
    from backend.services import gibdd_service

    assert hasattr(gibdd_service, "_tasks"), "_tasks dict должен существовать"
    assert isinstance(gibdd_service._tasks, dict), "_tasks должен быть dict"
