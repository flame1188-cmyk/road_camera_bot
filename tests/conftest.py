"""
Общие фикстуры и путь импорта для всех тестов.

Добавляет /home/z/my-project/gibdd-bot в sys.path, чтобы можно было
импортировать модули проекта напрямую (без установки пакета).

Wave 2: добавлены фикстуры для моков HTTP/LLM/FastAPI.
"""
import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import pytest

# Корень проекта — где лежат analytics.py, user_request_parser.py и т.д.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Чтобы regions_builtin.py и regions_cache.py были импортируемыми
# (user_request_parser их импортирует при первом обращении).
import regions_builtin  # noqa: F401,E402  — проверка что файл на месте

# Mini App backend — чтобы импортировать backend.services.gibdd_service,
# backend.routers.analyze и т.д.
# ВАЖНО: добавляем miniapp/ (а не miniapp/backend/), потому что routers
# используют relative imports (`from ..services.gibdd_service import ...`),
# которые работают только если `backend` — полноценный пакет.
MINIAPP_ROOT = PROJECT_ROOT / "miniapp"
if str(MINIAPP_ROOT) not in sys.path:
    sys.path.insert(0, str(MINIAPP_ROOT))


# ============================================================
# Wave 2: shared fixtures
# ============================================================

@pytest.fixture
def patch_llm_keys(monkeypatch):
    """Подменяет LLM_API_KEY и LLM_PAID_API_KEY в config на тестовые значения.

    Возвращает dict с ключами для удобства проверок:
        {"free": "test-free-key", "paid": "test-paid-key",
         "paid_url": "https://test.example.com/v1", "paid_model": "test-model"}
    """
    import config

    test_keys = {
        "free": "test-free-key-1234",
        "paid": "test-paid-key-5678",
        "paid_url": "https://test.example.com/v1",
        "paid_model": "test-model-flash",
    }

    monkeypatch.setattr(config, "LLM_API_KEY", test_keys["free"])
    monkeypatch.setattr(config, "LLM_MODEL", "test-glm-flash")
    monkeypatch.setattr(config, "LLM_PAID_API_KEY", test_keys["paid"])
    monkeypatch.setattr(config, "LLM_PAID_API_URL", test_keys["paid_url"])
    monkeypatch.setattr(config, "LLM_PAID_MODEL", test_keys["paid_model"])

    # Также патчим в llm_analyzer, т.к. он импортирует значения на уровне модуля
    import llm_analyzer
    # llm_analyzer импортирует через `from config import LLM_API_KEY, ...`,
    # поэтому в нём эти имена — отдельные ссылки. Патчим их тоже.
    monkeypatch.setattr(llm_analyzer, "LLM_API_KEY", test_keys["free"], raising=False)
    monkeypatch.setattr(llm_analyzer, "LLM_MODEL", "test-glm-flash", raising=False)
    monkeypatch.setattr(llm_analyzer, "LLM_PAID_API_KEY", test_keys["paid"], raising=False)
    monkeypatch.setattr(llm_analyzer, "LLM_PAID_API_URL", test_keys["paid_url"], raising=False)
    monkeypatch.setattr(llm_analyzer, "LLM_PAID_MODEL", test_keys["paid_model"], raising=False)

    return test_keys


@pytest.fixture
def reset_llm_clients():
    """Сбрасывает глобальные HTTP-клиенты LLM до/после теста.

    Необходимо, чтобы тесты не протекали: _free_llm_client — глобальная
    переменная модуля, и без сброса один тест сможет использовать клиент
    из другого (с другими mock'ами).
    """
    import llm_analyzer

    # Сброс до теста
    llm_analyzer._free_llm_client = None
    llm_analyzer._paid_llm_client = None

    yield

    # Сброс после теста (на случай, если тест создал клиент)
    # Если клиент создан — закрываем его асинхронно.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if llm_analyzer._free_llm_client is not None and not llm_analyzer._free_llm_client.is_closed:
        if loop and loop.is_running():
            loop.create_task(llm_analyzer._free_llm_client.aclose())
        else:
            try:
                llm_analyzer._free_llm_client.sync_close()
            except Exception:
                pass
    if llm_analyzer._paid_llm_client is not None and not llm_analyzer._paid_llm_client.is_closed:
        if loop and loop.is_running():
            loop.create_task(llm_analyzer._paid_llm_client.aclose())
        else:
            try:
                llm_analyzer._paid_llm_client.sync_close()
            except Exception:
                pass

    llm_analyzer._free_llm_client = None
    llm_analyzer._paid_llm_client = None
    # Сбрасываем rate limiter, чтобы тесты не ждали 5 секунд
    llm_analyzer._last_llm_call_time = 0.0


@pytest.fixture
def disable_rate_limiter(monkeypatch):
    """Отключает глобальный rate limiter LLM (иначе тесты ждут по 5 сек)."""
    import llm_analyzer

    monkeypatch.setattr(llm_analyzer, "_MIN_LLM_INTERVAL", 0.0)
    monkeypatch.setattr(llm_analyzer, "_last_llm_call_time", 0.0)


@pytest.fixture
def sample_comparison():
    """Минимальный comparison dict, подходящий для format_metrics_for_prompt.

    Воспроизводит структуру, которую возвращает analytics.compare_metrics,
    но без полной карточки — только нужные для форматирования поля.
    """
    return {
        "total":       {"current": 100, "previous": 80,  "change": 25.0},
        "deaths":      {"current": 5,   "previous": 8,   "change": -37.5},
        "injured":     {"current": 120, "previous": 100, "change": 20.0},
        "alcohol":     {"current": 10,  "previous": 12,  "change": -16.7},
        "pedestrians": {"current": 20,  "previous": 25,  "change": -20.0},
        "deaths_per_100":      {"current": 5.0,  "previous": 10.0, "change": -50.0},
        "injured_per_100":     {"current": 120.0,"previous": 125.0,"change": -4.0},
        "by_weekday": {
            "current":  {"Пн": 10, "Вт": 12, "Ср": 8, "Чт": 15, "Пт": 20, "Сб": 25, "Вс": 10},
            "previous": {"Пн": 8,  "Вт": 10, "Ср": 9, "Чт": 12, "Пт": 18, "Сб": 18, "Вс": 5},
        },
        "by_hour": {
            "current":  {0: 2, 6: 5, 9: 12, 12: 10, 15: 15, 18: 25, 21: 8, 23: 3},
            "previous": {0: 1, 6: 3, 9: 10, 12: 8,  15: 12, 18: 20, 21: 5, 23: 2},
        },
        "by_type": {
            "current":  {"Столкновение": 50, "Наезд на пешехода": 30, "Опрокидывание": 10},
            "previous": {"Столкновение": 40, "Наезд на пешехода": 25, "Опрокидывание": 8},
        },
        "by_weather": {
            "current":  {"Ясно": 60, "Дождь": 15, "Снег": 10},
            "previous": {"Ясно": 50, "Дождь": 12, "Снег": 8},
        },
    }


@pytest.fixture
def telegram_init_data_factory():
    """Фабрика для генерации валидного Telegram initData с правильной HMAC-подписью.

    Использование:
        data = telegram_init_data_factory(user_id=123, bot_token="xxx")
        # data — строка вида "query_id=...&user=...&auth_date=...&hash=..."

    Без bot_token — используется "test:bot_token" (как в Settings).
    """
    import hashlib
    import hmac
    import json
    import time as _time
    from urllib.parse import urlencode

    def _make(
        *,
        user_id: int = 123456789,
        first_name: str = "Test",
        last_name: str = "User",
        username: str = "testuser",
        language_code: str = "ru",
        is_premium: bool = False,
        auth_date: int | None = None,
        bot_token: str = "123456789:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        extra_fields: dict[str, str] | None = None,
        corrupt_hash: bool = False,
    ) -> str:
        if auth_date is None:
            auth_date = int(_time.time())

        user_obj = {
            "id": user_id,
            "first_name": first_name,
            "last_name": last_name,
            "username": username,
            "language_code": language_code,
            "is_premium": is_premium,
        }

        params: dict[str, str] = {
            "query_id": f"query_{user_id}",
            "user": json.dumps(user_obj, separators=(",", ":")),
            "auth_date": str(auth_date),
        }
        if extra_fields:
            params.update(extra_fields)

        # Строим data_check_string: ключи отсортированы по алфавиту
        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(params.items())
        )

        # secret_key = HMAC-SHA256("WebAppData", bot_token)
        secret_key = hmac.new(
            key=b"WebAppData",
            msg=bot_token.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()

        # expected_hash = HMAC-SHA256(secret_key, data_check_string)
        computed_hash = hmac.new(
            key=secret_key,
            msg=data_check_string.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()

        if corrupt_hash:
            # Делаем подпись заведомо неверной
            computed_hash = "0" * 64

        params["hash"] = computed_hash
        return urlencode(params)

    return _make


@pytest.fixture
def test_bot_token(monkeypatch):
    """Фиксирует TELEGRAM_BOT_TOKEN в miniapp settings для тестов."""
    token = "123456789:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

    # telegram_auth.py импортирует `from .config import settings` (Pydantic Settings).
    # Патчим field у singleton.
    try:
        from backend.config import settings as miniapp_settings
        monkeypatch.setattr(miniapp_settings, "telegram_bot_token", token)
        monkeypatch.setattr(miniapp_settings, "allowed_user_ids", "")
    except ImportError:
        pass

    # Также патчим основной config.TELEGRAM_BOT_TOKEN
    import config as main_config
    monkeypatch.setattr(main_config, "TELEGRAM_BOT_TOKEN", token)

    return token


@pytest.fixture
def fastapi_test_user():
    """Возвращает TelegramUser, который будет использоваться при override auth."""
    from backend.telegram_auth import TelegramUser
    return TelegramUser(
        id=999999,
        first_name="Test",
        last_name="User",
        username="testuser",
        language_code="ru",
        is_premium=False,
        auth_date=int(time.time()),
    )


@pytest.fixture
def fastapi_client(fastapi_test_user, test_bot_token):
    """FastAPI TestClient с переопределённой Telegram-авторизацией.

    Все эндпоинты будут видеть пользователя fastapi_test_user без проверки подписи.

    Возвращает сам клиент (через with-callable); после выхода из fixture
    override снимается.
    """
    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.telegram_auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: fastapi_test_user
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def clear_in_memory_tasks():
    """Очищает глобальный _tasks в gibdd_service до и после теста.

    Без этого тесты протекают: задачи, созданные одним тестом, видны другому.
    """
    from backend.services import gibdd_service

    gibdd_service._tasks.clear()
    yield
    gibdd_service._tasks.clear()
