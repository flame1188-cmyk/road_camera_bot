"""
Request ID middleware (Фаза 2.8).

Добавляет уникальный request_id к каждому HTTP-запросу:
  1. Если клиент прислал X-Request-ID — используем его (для трассировки
     через фронтенд → бэкенд → внешние API).
  2. Иначе генерируем новый (format: req_<8 hex chars>).

request_id сохраняется в:
  - contextvars (доступен через logging_config.get_request_id())
  - response header X-Request-ID (клиент видит и может сообщить в support)
  - logger context (каждый лог в этом запросе содержит request_id)

В бизнес-коде используй:
  from .logging_config import get_request_id
  rid = get_request_id()  # текущий request_id или None
"""
from __future__ import annotations

import secrets
import logging
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .logging_config import set_log_context, get_request_id

logger = logging.getLogger(__name__)

HEADER_NAME = "X-Request-ID"


def _generate_request_id() -> str:
    """Генерирует новый request_id: req_<8 hex>."""
    return f"req_{secrets.token_hex(8)}"


def _extract_request_id(request: Request) -> str:
    """Достаёт request_id из заголовка или генерирует новый."""
    incoming = request.headers.get(HEADER_NAME)
    if incoming and len(incoming) <= 64:  # защита от мусора
        # Нормализуем — оставляем только безопасные символы
        cleaned = "".join(
            c for c in incoming if c.isalnum() or c in "-_"
        )
        if cleaned:
            return cleaned
    return _generate_request_id()


def get_current_request_id() -> Optional[str]:
    """Возвращает текущий request_id (для использования в business-коде)."""
    return get_request_id()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Starlette/FastAPI middleware: добавляет request_id в каждый запрос.

    Подключение в main.py:
        from .middleware.request_id import RequestIdMiddleware
        app.add_middleware(RequestIdMiddleware)

    После этого все логи внутри запроса автоматически содержат request_id,
    а в response добавляется header X-Request-ID.
    """

    async def dispatch(self, request: Request, call_next):
        request_id = _extract_request_id(request)

        # Сохраняем в contextvars (доступно во всех async-вызовах внутри запроса)
        set_log_context(request_id=request_id)

        # Также кладём в request.state для синхронного доступа
        request.state.request_id = request_id

        # Вызываем следующий middleware/endpoint
        try:
            response = await call_next(request)
        except Exception:
            # Логируем unhandled exception с request_id
            logger.exception(
                f"Unhandled exception in request {request_id}: "
                f"{request.method} {request.url.path}"
            )
            raise

        # Добавляем request_id в response header
        response.headers[HEADER_NAME] = request_id

        return response
