"""
Парсинг запросов пользователя и справочник регионов.

Тонкая обёртка над user_request_parser.parse_user_message() и
ensure_regions_loaded() из gibdd-bot.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from . import _imports

logger = logging.getLogger(__name__)


async def parse_user_query(query: str) -> Dict[str, Any]:
    """
    Парсит запрос пользователя через существующий user_request_parser.

    Returns:
        {
            "ok": True,
            "region_code": "1101",
            "region_name": "Вологодская область",
            "period": "Январь-Май 2026",
            "dat_list": ["1.2026", "2.2026", ...],
            "raw_query": "..."
        }
        или {"ok": False, "error": "...", "raw_query": "..."}
    """
    try:
        parser = _imports._import_module("user_request_parser")
        result = await parser.parse_user_message(query)

        if result is None:
            return {
                "ok": False,
                "error": "Не удалось распознать регион и период в запросе",
                "raw_query": query,
            }

        return {
            "ok": True,
            "region_code": result.region_code,
            "region_name": result.region_name,
            "period": result.period.label,
            "dat_list": result.period.get_dat_list(),
            "raw_query": query,
        }
    except Exception as exc:
        logger.exception("parse_user_query failed")
        return {
            "ok": False,
            "error": f"Ошибка парсинга: {exc}",
            "raw_query": query,
        }


async def get_regions() -> List[Dict[str, Any]]:
    """
    Возвращает список доступных регионов.

    Использует единую функцию ensure_regions_loaded() из user_request_parser,
    которая сама делает: API → файловый кэш → BUILTIN_REGIONS.
    Возвращает список [{'code': '1101', 'name': 'Алтайский край'}, ...].
    """
    try:
        parser = _imports._import_module("user_request_parser")
        regions = await parser.ensure_regions_loaded()
        if regions:
            return regions
    except Exception as exc:
        logger.warning(f"ensure_regions_loaded failed: {exc}, fallback to builtin")

    # Последний fallback — встроенный справочник
    builtin = _imports._import_module("regions_builtin")
    return list(builtin.BUILTIN_REGIONS)
