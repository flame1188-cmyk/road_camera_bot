"""
Роутер для справочника регионов.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends

from ..services.gibdd_service import get_regions
from ..telegram_auth import TelegramUser, get_current_user

router = APIRouter(prefix="/regions", tags=["regions"])


@router.get("", response_model=List[dict])
async def list_regions(user: TelegramUser = Depends(get_current_user)):
    """Возвращает список всех доступных регионов с кодами."""
    return await get_regions()


@router.get("/search")
async def search_regions(
    q: str,
    user: TelegramUser = Depends(get_current_user),
):
    """Поиск регионов по подстроке (для autocomplete в форме)."""
    all_regions = await get_regions()
    q_lower = q.lower().strip()
    if not q_lower:
        return all_regions[:20]

    matches = [
        r for r in all_regions
        if q_lower in (str(r.get("name", "")) + str(r.get("title", ""))).lower()
    ]
    return matches[:20]
