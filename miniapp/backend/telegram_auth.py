"""
Проверка подлинности Telegram WebApp initData.

Документация:
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

Алгоритм:
1. Получаем строку initData из query-параметра или заголовка.
2. Парсим её как URL-encoded form data.
3. Извлекаем hash, остальные параметры сортируем по ключу.
4. Строим data_check_string = "key1=value1\nkey2=value2\n...".
5. secret_key = HMAC-SHA256("WebAppData", bot_token).
6. expected_hash = HMAC-SHA256(secret_key, data_check_string) в hex.
7. Сравниваем с переданным hash.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qsl

from fastapi import HTTPException, Header, Query, status

from .config import settings


@dataclass(slots=True)
class TelegramUser:
    """Распарсенный пользователь из initData."""

    id: int
    first_name: str
    last_name: str = ""
    username: str = ""
    language_code: str = "ru"
    is_premium: bool = False
    auth_date: int = 0


def _verify_init_data(init_data: str, bot_token: str) -> Optional[dict]:
    """
    Проверяет подпись initData.

    Возвращает словарь с распарсенными параметрами (без hash) или None,
    если подпись невалидна.
    """
    if not init_data:
        return None

    # Парсим как form data
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    hash_value = parsed.pop("hash", None)

    if not hash_value:
        return None

    # Проверяем срок действия (не старше 24 часов)
    auth_date_str = parsed.get("auth_date")
    if auth_date_str and auth_date_str.isdigit():
        auth_date = int(auth_date_str)
        # 24 часа = 86400 сек
        if time.time() - auth_date > 86400:
            return None

    # Строим data_check_string
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(parsed.items())
    )

    # secret_key = HMAC-SHA256("WebAppData", bot_token)
    secret_key = hmac.new(
        key=b"WebAppData",
        msg=bot_token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()

    # expected_hash = HMAC-SHA256(secret_key, data_check_string)
    expected_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    # Сравнение в constant-time (защита от timing-атак)
    if not hmac.compare_digest(expected_hash, hash_value):
        return None

    return parsed


def _extract_user(parsed: dict) -> TelegramUser:
    """Извлекает TelegramUser из распарсенного initData."""
    user_raw = parsed.get("user", "")
    if not user_raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User field missing in initData",
        )

    import json

    try:
        user_data = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid user JSON: {exc}",
        ) from exc

    return TelegramUser(
        id=int(user_data.get("id", 0)),
        first_name=user_data.get("first_name", ""),
        last_name=user_data.get("last_name", ""),
        username=user_data.get("username", ""),
        language_code=user_data.get("language_code", "ru"),
        is_premium=bool(user_data.get("is_premium", False)),
        auth_date=int(parsed.get("auth_date", 0)),
    )


def _check_whitelist(user: TelegramUser) -> None:
    """Проверяет, есть ли пользователь в whitelist (если задан)."""
    allowed = settings.allowed_user_ids_list
    if allowed and user.id not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User {user.id} is not in whitelist",
        )


async def get_current_user(
    tg_init_data: Optional[str] = Query(
        default=None, alias="tg_init_data", description="Telegram initData"
    ),
    x_tg_init_data: Optional[str] = Header(default=None),
) -> TelegramUser:
    """
    FastAPI dependency: извлекает и проверяет пользователя Telegram.

    initData может прийти:
    - query-параметром ?tg_init_data=... (удобно для ссылок)
    - заголовком X-Tg-Init-Data: ... (удобно для fetch из JS)
    """
    init_data = tg_init_data or x_tg_init_data
    if not init_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="tg_init_data query parameter or X-Tg-Init-Data header required",
        )

    parsed = _verify_init_data(init_data, settings.telegram_bot_token)
    if parsed is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Telegram initData signature",
        )

    user = _extract_user(parsed)
    _check_whitelist(user)
    return user
