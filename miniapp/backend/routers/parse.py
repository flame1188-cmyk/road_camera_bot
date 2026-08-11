"""
Роутер для парсинга запросов пользователя (натуральный язык → регион+период).

Использует существующий user_request_parser.parse_user_message().
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..services.gibdd_service import parse_user_query
from ..telegram_auth import TelegramUser, get_current_user

router = APIRouter(prefix="/parse", tags=["parse"])


class ParseRequest(BaseModel):
    """Запрос на парсинг естественного языка."""

    query: str = Field(..., min_length=2, max_length=500,
                       description="Например: 'Вологодская область за 2025 год'")


class ParseResponse(BaseModel):
    """Результат парсинга."""

    ok: bool
    region_code: Optional[str] = None
    region_name: Optional[str] = None
    period: Optional[str] = None
    dat_list: List[str] = []
    raw_query: str
    error: Optional[str] = None


@router.post("", response_model=ParseResponse)
async def parse_query(
    request: ParseRequest,
    user: TelegramUser = Depends(get_current_user),
):
    """
    Парсит естественный язык в структурированный запрос.

    Поддерживаемые форматы (из README существующего бота):
    - 'Вологодская область за 2025 год'
    - 'Алтайский край за I квартал 2025'
    - '2.2024 1119' (строгий формат: месяц.год код_региона)
    """
    result = await parse_user_query(request.query)
    return ParseResponse(**result)
