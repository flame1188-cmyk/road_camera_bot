"""
Rate limiting для Mini App API (Фаза 1.5).

Защищает API от злоупотреблений:
- Баг в клиентском коде (зацикленный retry storm)
- Случайный DDoS (пользователь зажал F5)
- Скрипты, использующие API вместо Mini App UI

Стратегия:
- 60 запросов/минуту на пользователя (по telegram user_id из initData)
- 30 запросов/минуту на IP (для неавторизованных эндпоинтов типа /health)
- Exempt: /metrics, /health*, /docs, /redoc, /openapi.json — мониторинг

Реализация через slowapi (Limiter на базе in-memory sliding window).
Для multi-instance деплоя — заменить на Redis-backed limiter.

⚠️ Sprint 4 FIX: переписано как PURE ASGI middleware.
   Предыдущая версия использовала `app.middleware("http")` (BaseHTTPMiddleware),
   который БУФЕРИЗУЕТ streaming responses (SSE/WebSocket). Из-за этого
   SSE chunks доходили до клиента только после завершения стрима целиком,
   что ломало Sprint 4 streaming LLM.
   Pure ASGI middleware НЕ трогает response body — оно просто вызывает
   downstream app, который сам стримит клиенту.
"""
from __future__ import annotations

import logging
import os
from typing import Callable

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# === Конфигурация ===
# 60 req/min — это 1 запрос в секунду. Достаточно для интерактивной работы
# (пользователь не нажмёт быстрее), но ловит зацикленные retry-storm'ы.
# Для long-poll эндпоинтов (?wait=25) — один poll = один запрос, 60/мин
# более чем достаточно.
DEFAULT_LIMIT = os.environ.get("RATE_LIMIT_DEFAULT", "60/minute")

# Эндпоинты, которые НЕ лимитируются (мониторинг и метаданные)
EXEMPT_PATHS = frozenset({
    "/metrics",
    "/health",
    "/health/db",
    "/health/db/cards",
    "/health/db/clusters",
    "/health/db/excel",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
    "/app/",
})


def _get_user_key(request: Request) -> str:
    """Ключ для rate limit: telegram user_id из initData (если есть),
    иначе IP-адрес.

    Возвращает строку вида "user:123456789" или "ip:1.2.3.4".
    """
    # Пытаемся достать user_id из request.state (ставится telegram_auth middleware)
    user_id = getattr(request.state, "telegram_user_id", None)
    if user_id:
        return f"user:{user_id}"

    # Fallback на IP
    return f"ip:{get_remote_address(request)}"


# Глобальный Limiter (используется в main.py через middleware)
limiter = Limiter(key_func=_get_user_key, default_limits=[DEFAULT_LIMIT])


def rate_limit_exempt(path: str) -> bool:
    """True если путь не должен лимитироваться."""
    for exempt in EXEMPT_PATHS:
        if path == exempt or path.startswith(exempt.rstrip("/") + "/"):
            return True
    return False


# ============================================================
# Sprint 4 FIX: Pure ASGI middleware
# ============================================================
# Раньше использовался паттерн `app.middleware("http")(rate_limit_middleware)`
# с `async def (request, call_next)`. Это BaseHTTPMiddleware, который
# буферизует streaming responses (SSE/WebSocket) — см.:
#   https://github.com/encode/starlette/issues/919
#
# Pure ASGI middleware НЕ трогает response body: оно просто проверяет
# rate limit ДО вызова app, и если лимит превышен — возвращает 429.
# Если лимит OK — просто вызывает downstream app, который сам стримит
# ответ клиенту без буферизации.
# ============================================================


class RateLimitASGIMiddleware:
    """
    Pure ASGI middleware для rate limiting.

    Не буферизует streaming responses (SSE/WebSocket).
    Применяет rate limit ко всем запросам, кроме exempt-эндпоинтов.

    Использование:
        app.add_middleware(RateLimitASGIMiddleware)
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        # Пропускаем non-HTTP запросы (websocket, lifespan)
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Пропускаем exempt-эндпоинты
        if rate_limit_exempt(path):
            await self.app(scope, receive, send)
            return

        # Создаём Request для совместимости с slowapi
        # (slowapi ждёт starlette.Request)
        request = Request(scope, receive=receive)

        # Применяем лимит
        try:
            key = _get_user_key(request)
            limiter._check_request_limit(request, DEFAULT_LIMIT, key, True)
        except RateLimitExceeded as exc:
            logger.warning(
                f"Rate limit exceeded: {key} on {path} — {exc.detail}"
            )
            # Возвращаем 429 JSON — это обычный response, не streaming
            response = JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limit_exceeded",
                    "detail": "Слишком много запросов. Подождите минуту.",
                    "retry_after_seconds": 60,
                },
                headers={
                    "Retry-After": "60",
                    "X-RateLimit-Limit": DEFAULT_LIMIT,
                    "X-RateLimit-Reset": "60",
                },
            )
            await response(scope, receive, send)
            return
        except Exception as exc:
            # Rate limiter не должен ронять запросы — логируем и пропускаем
            logger.debug(f"Rate limit check skipped: {exc}")
            await self.app(scope, receive, send)
            return

        # Лимит OK — пропускаем запрос дальше (БЕЗ буферизации!)
        await self.app(scope, receive, send)


# ============================================================
# Legacy: оставлено для обратной совместимости со старыми тестами,
# но в main.py больше НЕ используется.
# ============================================================
async def rate_limit_middleware(request: Request, call_next: Callable):
    """DEPRECATED: BaseHTTPMiddleware-style rate limiter.

    ⚠️ БУФЕРИЗУЕТ streaming responses (SSE) — не использовать!
    Используйте RateLimitASGIMiddleware через app.add_middleware().
    Оставлено только для старых тестов, которые импортируют эту функцию.
    """
    path = request.url.path

    if rate_limit_exempt(path):
        return await call_next(request)

    try:
        key = _get_user_key(request)
        limiter._check_request_limit(request, DEFAULT_LIMIT, key, True)
    except RateLimitExceeded as exc:
        logger.warning(
            f"Rate limit exceeded: {key} on {path} — {exc.detail}"
        )
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate_limit_exceeded",
                "detail": "Слишком много запросов. Подождите минуту.",
                "retry_after_seconds": 60,
            },
            headers={
                "Retry-After": "60",
                "X-RateLimit-Limit": DEFAULT_LIMIT,
                "X-RateLimit-Reset": "60",
            },
        )
    except Exception as exc:
        logger.debug(f"Rate limit check skipped: {exc}")
        return await call_next(request)

    return await call_next(request)
