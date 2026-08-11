"""
bot._state — shared state for the bot package.

Содержит:
  • Все внешние imports (api_client, llm_analyzer, gibdd_parser, etc.)
  • Logging configuration + logger
  • Module-level mutable state (_api_down, _user_locks, _precache_lock,
    _conflict_last_log, _clean_shutdown, _global_app_started_at)
  • Константы (TG_MSG_LIMIT, REGIONS_PER_PAGE, MONTH_*, QUARTER_LABELS,
    _MAX_TG_RETRIES, _TG_RETRY_DELAYS, _QA_HISTORY_MAX_MESSAGES)

Этот модуль — единственное место, где объявлены эти глобальные переменные.
Все остальные bot/* модули импортируют их отсюда: `from bot._state import *`.

Выделено из единого bot.py при рефакторинге (Phase 3-2). 100% pure:
никакая логика не изменена, только перемещена.
"""
import asyncio
import gc
import html as html_mod
import logging
import os
import sys
import time
from datetime import datetime
from typing import Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import Conflict, NetworkError, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import validate_config, ALLOWED_USER_IDS, LLM_API_KEY, ENABLE_NEWS_SEARCH
from config import LLM_PAID_API_KEY as _PAID_KEY
from llm_analyzer import is_paid_llm_available, is_any_llm_available

from api_client import fetch_dtp_data, fetch_regions, extract_accident_cards, error_brief, close_client
from llm_analyzer import close_llm_client
from gibdd_parser import build_file1_data, build_file2_data
from excel_generator import generate_both_files, generate_analytics_file, generate_concentration_file, generate_concentration_dynamics_file, generate_point_stats_file
from analytics import (
    calculate_metrics,
    compare_metrics,
    build_analytics_message,
    build_analytics_excel_data,
    get_analytics_column_names,
    extract_raw_supplement,
)
from llm_analyzer import get_ai_summary, get_ai_answer, format_clusters_for_prompt
from news_fetcher import fetch_news_context
from concentration_points import (
    calculate_concentration_points,
    calculate_concentration_dynamics,
    build_concentration_excel_data,
    build_concentration_detail_data,
    build_precluster_excel_data,
    build_dynamics_excel_data,
    build_dynamics_detail_data,
    build_dynamics_summary,
    get_concentration_column_names,
    get_detail_column_names,
    get_precluster_column_names,
    get_dynamics_column_names,
    get_dynamics_detail_column_names,
    enrich_clusters_with_cameras,
    close_overpass_client as _close_overpass_client,
)
from point_statistics import (
    parse_coordinates,
    calculate_point_statistics,
    format_point_stats_message,
    build_point_stats_excel_data,
    get_point_stats_column_names,
    RADIUS_OPTIONS,
)
from user_request_parser import (
    parse_user_message,
    parse_period,
    find_region,
    ensure_regions_loaded,
    ParsedPeriod,
)
from data_cache import data_cache  # noqa: E402
from data_cache import (  # noqa: E402
    get_async as data_cache_get_async,
    put_async as data_cache_put_async,
    invalidate_by_region_async as data_cache_invalidate_region_async,
    has_async as data_cache_has_async,
)

# ========================
# Настройка логирования
# ========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bot")

# Rate-limit для Conflict-предупреждений (логируем не чаще 1 раза в 60с)
_conflict_last_log: float = 0.0
_CONFLICT_LOG_INTERVAL = 60.0

# Флаг: API ГИБДД вернул 5xx → все последующие запросы сразу на web_fallback
# Сбрасывается при каждом новом пользовательском запросе (в _fetch_cards_for_period)
_api_down: bool = False
_api_down_lock: Any = None  # инициализируется в async-контексте

# Флаг: был ли бот остановлен штатно (Ctrl+C / SIGTERM) — для ретрая старта.
# Mutated global — оставлено как в исходном bot.py (100% pure).
_clean_shutdown: bool = False

# Защита от гонок при concurrent_updates=True (БАГ 5)
# Один asyncio.Lock на пользователя — предотвращает параллельную
# коррупцию user_data при быстрых двойных нажатиях кнопок.
_user_locks: dict[int, asyncio.Lock] = {}

# Lock для защиты от параллельных запусков /precache
# (предотвращает множественные subprocess-ы, которые перегружают Overpass)
_precache_lock = asyncio.Lock()

# Лимит символов в одном сообщении Telegram
TG_MSG_LIMIT = 4096

# Регионов на одной странице кнопок
REGIONS_PER_PAGE = 8

MONTH_SHORT = {
    1: "Янв", 2: "Фев", 3: "Мар", 4: "Апр",
    5: "Май", 6: "Июн", 7: "Июл", 8: "Авг",
    9: "Сен", 10: "Окт", 11: "Ноя", 12: "Дек",
}

MONTH_FULL = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}

QUARTER_LABELS = {
    1: "I кв (Янв-Мар)", 2: "II кв (Апр-Июн)",
    3: "III кв (Июл-Сен)", 4: "IV кв (Окт-Дек)",
}

# Утилита ретрая Telegram API
_MAX_TG_RETRIES = 3
_TG_RETRY_DELAYS = [2, 5, 10]  # секунды между попытками

# Лимит истории Q&A-режима (в сообщениях, не в парах).
# 12 = 6 пар вопрос/ответ. Больше раздувает промпт и упирается в контекст;
# меньше — модель теряет контекст диалога слишком быстро.
_QA_HISTORY_MAX_MESSAGES = 12

# __all__: всё, что должно быть видно при `from bot._state import *`.
# Включаем внешние имена (для обратной совместимости с кодом, который
# ожидал их в bot.py), а также наше shared state.
__all__ = [
    # stdlib
    "asyncio", "gc", "html_mod", "logging", "os", "sys", "time",
    "datetime", "Any",
    # telegram
    "Update", "InlineKeyboardButton", "InlineKeyboardMarkup",
    "Conflict", "NetworkError", "TimedOut",
    "Application", "CallbackQueryHandler", "CommandHandler",
    "ContextTypes", "MessageHandler", "filters",
    # config
    "validate_config", "ALLOWED_USER_IDS", "LLM_API_KEY", "ENABLE_NEWS_SEARCH",
    "_PAID_KEY",
    "is_paid_llm_available", "is_any_llm_available",
    # api_client
    "fetch_dtp_data", "fetch_regions", "extract_accident_cards", "error_brief", "close_client",
    # llm_analyzer
    "close_llm_client", "get_ai_summary", "get_ai_answer", "format_clusters_for_prompt",
    # gibdd_parser / excel_generator
    "build_file1_data", "build_file2_data",
    "generate_both_files", "generate_analytics_file", "generate_concentration_file",
    "generate_concentration_dynamics_file", "generate_point_stats_file",
    # analytics
    "calculate_metrics", "compare_metrics", "build_analytics_message",
    "build_analytics_excel_data", "get_analytics_column_names", "extract_raw_supplement",
    # news
    "fetch_news_context",
    # concentration_points
    "calculate_concentration_points", "calculate_concentration_dynamics",
    "build_concentration_excel_data", "build_concentration_detail_data",
    "build_precluster_excel_data", "build_dynamics_excel_data",
    "build_dynamics_detail_data", "build_dynamics_summary",
    "get_concentration_column_names", "get_detail_column_names",
    "get_precluster_column_names", "get_dynamics_column_names",
    "get_dynamics_detail_column_names", "enrich_clusters_with_cameras",
    "_close_overpass_client",
    # point_statistics
    "parse_coordinates", "calculate_point_statistics", "format_point_stats_message",
    "build_point_stats_excel_data", "get_point_stats_column_names", "RADIUS_OPTIONS",
    # user_request_parser
    "parse_user_message", "parse_period", "find_region", "ensure_regions_loaded", "ParsedPeriod",
    # data_cache
    "data_cache", "data_cache_get_async", "data_cache_put_async",
    "data_cache_invalidate_region_async", "data_cache_has_async",
    # shared state
    "logger", "_conflict_last_log", "_CONFLICT_LOG_INTERVAL",
    "_api_down", "_api_down_lock", "_clean_shutdown",
    "_user_locks", "_precache_lock",
    "TG_MSG_LIMIT", "REGIONS_PER_PAGE",
    "MONTH_SHORT", "MONTH_FULL", "QUARTER_LABELS",
    "_MAX_TG_RETRIES", "_TG_RETRY_DELAYS", "_QA_HISTORY_MAX_MESSAGES",
]
