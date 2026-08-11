"""
extract_bot.py — Разбивает единый bot.py (4138 строк) на модульный пакет bot/.

Принцип: 100% pure refactoring — только перемещение кода, без правок логики.

Стратегия:
1. Читаем bot.py как список строк.
2. Для каждого целевого модуля указываем (start, end) — полуоткрытый интервал строк.
3. К каждому модулю добавляем заголовок: docstring + imports + shared state.
4. Записываем в bot/<module>.py.

Все модули импортируют shared state из bot._state, чтобы избежать дублирования
глобальных переменных (logger, _api_down, _user_locks, etc.).
"""
from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

SRC = Path("/home/z/my-project/gibdd-bot/bot.py")
DST = Path("/home/z/my-project/gibdd-bot/bot")

# ----------------------------- shared state -----------------------------

# Этот модуль содержит: imports, logging setup, module-level state.
# Все остальные модули делают: from bot._state import *
STATE_HEADER = '''"""
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
'''

# ----------------------------- modules spec -----------------------------

# Each entry: (filename, docstring, list of (start, end) line ranges, extra_imports)
# Line numbers are 1-indexed, ranges are [start, end] inclusive.
# We'll extract the source lines as-is (no modification).

MODULES = [
    # -------------------------- infra.py --------------------------
    (
        "infra.py",
        '"""bot.infra — инфраструктурные утилиты Telegram API.\n\nСодержит:\n  • _tg_retry — ретрай при TimedOut/NetworkError\n  • _IsDocument — кастомный фильтр\n  • _mark_api_down / _is_api_down / _log_memory\n  • _get_user_lock — Lock на пользователя\n  • _sanitize_error, _safe_edit, _send_long_message\n  • _make_progress_bar\n\nВыделено из единого bot.py (Phase 3-2). 100% pure.\n"""',
        [
            # _tg_retry + constants
            (45, 76),
            # _IsDocument filter
            (130, 137),
            # _mark_api_down, _is_api_down, _log_memory
            (160, 182),
            # _get_user_lock, TG_MSG_LIMIT, _sanitize_error, _safe_edit, _send_long_message
            (209, 296),
            # _make_progress_bar (из конца файла, перед document handler)
            (3590, 3598),
        ],
        "from bot._state import *",
    ),
    # -------------------------- access.py --------------------------
    (
        "access.py",
        '"""bot.access — контроль доступа и загрузка регионов.\n\nСодержит:\n  • is_user_allowed\n  • _get_regions / _load_regions_if_needed\n  • _fetch_cards_for_period — основная функция загрузки карточек ДТП\n    (API ГИБДД + web_fallback + кэш)\n\nВыделено из единого bot.py (Phase 3-2). 100% pure.\n"""',
        [
            # is_user_allowed, _get_regions, _load_regions_if_needed, _fetch_cards_for_period
            (331, 503),
        ],
        "from bot._state import *",
    ),
    # -------------------------- keyboards.py --------------------------
    (
        "keyboards.py",
        '"""bot.keyboards — построение inline-клавиатур.\n\nСодержит:\n  • build_region_keyboard — выбор региона с пагинацией\n  • build_period_keyboard — выбор периода (месяц/квартал/полугодие/год)\n\nВыделено из единого bot.py (Phase 3-2). 100% pure.\n"""',
        [
            # build_region_keyboard, build_period_keyboard
            (510, 606),
        ],
        "from bot._state import *",
    ),
    # -------------------------- handlers/commands.py --------------------------
    (
        "handlers/commands.py",
        '"""bot.handlers.commands — обработчики команд Telegram.\n\nСодержит:\n  • cmd_start, cmd_help, cmd_dtp, cmd_regions, cmd_miniapp, cmd_precache\n  • _show_region_keyboard, _run_precache\n\nВыделено из единого bot.py (Phase 3-2). 100% pure.\n"""',
        [
            # All command handlers
            (613, 989),
        ],
        "from bot._state import *\nfrom bot.access import is_user_allowed, _load_regions_if_needed\nfrom bot.keyboards import build_region_keyboard",
    ),
    # -------------------------- handlers/callbacks.py --------------------------
    (
        "handlers/callbacks.py",
        '"""bot.handlers.callbacks — диспетчер callback-запросов (нажатия inline-кнопок).\n\nСодержит:\n  • on_callback_query — главный диспетчер (большой if-elif по callback_data).\n    В будущем может быть декомпозирован в dispatch-таблицу,\n    но в рамках Phase 3-2 (100% pure) сохранён как есть.\n\nВыделено из единого bot.py (Phase 3-2). 100% pure.\n"""',
        [
            # on_callback_query (488 строк, переносим целиком)
            (995, 1482),
        ],
        # callbacks.py использует много чего из access/analysis/keyboards — добавим все
        "from bot._state import *\n"
        "from bot.access import is_user_allowed, _get_regions, _load_regions_if_needed, _fetch_cards_for_period\n"
        "from bot.keyboards import build_region_keyboard, build_period_keyboard\n"
        "from bot.analysis import (\n"
        "    _start_fetching, _get_current_cards, _has_analytics_data, _get_card_count,\n"
        "    _get_prev_cards, _build_menu_keyboard, _preload_prev_year, _offer_analysis,\n"
        "    _run_analysis, _clear_analytics_data, _run_concentration_points,\n"
        ")\n"
        "from bot.output import _html_map_menu, _generate_and_send_dtp_map, _send_analytics_html, _send_clusters_html\n"
        "from bot.point_stats import _start_point_stats, _handle_point_stats_radius, _send_point_stats_excel, _send_point_stats_html, _process_point_stats, _handle_location_message\n"
        "from bot.qa import _handle_analytics_question\n"
        "from bot.infra import _tg_retry, _IsDocument, _safe_edit, _send_long_message, _get_user_lock, _sanitize_error, _make_progress_bar",
    ),
    # -------------------------- analysis.py --------------------------
    (
        "analysis.py",
        '"""bot.analysis — конвейер аналитики и очагов ДТП.\n\nСодержит:\n  • _start_fetching — мультизапрос с прогрессом\n  • _get_current_cards / _get_prev_cards / _has_analytics_data / _get_card_count\n  • _build_menu_keyboard — меню действий после выгрузки\n  • _preload_prev_year — предзагрузка предыдущего периода\n  • _offer_analysis — предложение анализа\n  • _run_analysis — основной цикл анализа\n  • _clear_analytics_data — очистка состояния\n  • _run_concentration_points — расчёт очагов\n\nСамый большой модуль (~1300 строк). В будущем можно разбить на\nanalysis/pipeline.py + analysis/clusters.py + analysis/menu.py.\n\nВыделено из единого bot.py (Phase 3-2). 100% pure.\n"""',
        [
            # Весь раздел "Мультизапрос с прогрессом" (1483-2796)
            (1487, 2796),
        ],
        "from bot._state import *\n"
        "from bot.infra import _tg_retry, _safe_edit, _send_long_message, _get_user_lock, _sanitize_error, _make_progress_bar, _log_memory\n"
        "from bot.access import _fetch_cards_for_period\n"
        "from bot.keyboards import build_region_keyboard, build_period_keyboard\n"
        "from bot.output import _send_analytics_html, _send_clusters_html, _generate_and_send_dtp_map, _html_map_menu",
    ),
    # -------------------------- output.py --------------------------
    (
        "output.py",
        '"""bot.output — HTML-вывод и карты.\n\nСодержит:\n  • _html_map_menu — меню карты\n  • _generate_and_send_dtp_map — генерация и отправка HTML-карты\n  • _send_analytics_html — отправка аналитики HTML\n  • _send_clusters_html — отправка очагов HTML\n\nВыделено из единого bot.py (Phase 3-2). 100% pure.\n"""',
        [
            # HTML-карта ДТП (2797-3043)
            (2801, 3043),
        ],
        "from bot._state import *\n"
        "from bot.infra import _tg_retry, _safe_edit, _send_long_message, _make_progress_bar",
    ),
    # -------------------------- point_stats.py --------------------------
    (
        "point_stats.py",
        '"""bot.point_stats — статистика ДТП по точке (геолокация).\n\nСодержит:\n  • _start_point_stats — начало сессии статистики по точке\n  • _handle_point_stats_radius — выбор радиуса\n  • _send_point_stats_excel / _send_point_stats_html — отправка результатов\n  • _process_point_stats — основной расчёт\n  • _handle_location_message — обработка Location от Telegram\n\nВыделено из единого bot.py (Phase 3-2). 100% pure.\n"""',
        [
            # Статистика по точке (3044-3452) — до _handle_analytics_question
            (3048, 3452),
        ],
        "from bot._state import *\n"
        "from bot.infra import _tg_retry, _safe_edit, _send_long_message, _get_user_lock, _sanitize_error\n"
        "from bot.access import _fetch_cards_for_period",
    ),
    # -------------------------- qa.py --------------------------
    (
        "qa.py",
        '"""bot.qa — Q&A-режим с LLM (вопросы по данным).\n\nСодержит:\n  • _handle_analytics_question — обработка текстового вопроса\n    пользователя по текущим данным ДТП\n\nВыделено из единого bot.py (Phase 3-2). 100% pure.\n"""',
        [
            # _handle_analytics_question (3453-3589)
            (3453, 3589),
        ],
        "from bot._state import *\n"
        "from bot.infra import _tg_retry, _send_long_message, _sanitize_error",
    ),
    # -------------------------- handlers/messages.py --------------------------
    (
        "handlers/messages.py",
        '"""bot.handlers.messages — обработчики текстовых сообщений и документов.\n\nСодержит:\n  • _handle_document — приём Excel-файла с камерами фотовидеофиксации\n  • handle_message — основной обработчик текста (NLP-парсинг запроса)\n\nВыделено из единого bot.py (Phase 3-2). 100% pure.\n"""',
        [
            # Обработчик текстовых сообщений (NLP) (3600-3949)
            (3604, 3949),
        ],
        "from bot._state import *\n"
        "from bot.access import is_user_allowed, _get_regions, _load_regions_if_needed, _fetch_cards_for_period\n"
        "from bot.keyboards import build_region_keyboard, build_period_keyboard\n"
        "from bot.infra import _tg_retry, _safe_edit, _send_long_message, _get_user_lock, _sanitize_error, _IsDocument\n"
        "from bot.analysis import (\n"
        "    _start_fetching, _build_menu_keyboard, _offer_analysis,\n"
        "    _clear_analytics_data, _run_analysis,\n"
        ")",
    ),
    # -------------------------- app.py --------------------------
    (
        "app.py",
        '"""bot.app — точка входа и сборка Application.\n\nСодержит:\n  • error_handler — глобальный обработчик ошибок PTB\n  • _post_init / _post_shutdown — lifecycle hooks\n  • _build_app — сборка Application с хендлерами\n  • main — точка входа (стартап-ретрай)\n\nВыделено из единого bot.py (Phase 3-2). 100% pure.\n"""',
        [
            # Функция ошибки (3950-3993)
            (3954, 3993),
            # Точка входа (3994-end)
            (3998, 4138),
        ],
        "from bot._state import *\n"
        "from bot.infra import _IsDocument\n"
        "from bot.handlers.commands import (\n"
        "    cmd_start, cmd_help, cmd_dtp, cmd_regions, cmd_miniapp, cmd_precache,\n"
        ")\n"
        "from bot.handlers.callbacks import on_callback_query\n"
        "from bot.handlers.messages import handle_message, _handle_document\n"
        "from bot.point_stats import _handle_location_message",
    ),
]


# ----------------------------- extraction -----------------------------

def read_lines(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8") as f:
        return f.readlines()


def extract_range(lines: list[str], start: int, end: int) -> str:
    """1-indexed inclusive [start, end] → string."""
    return "".join(lines[start - 1:end])


def write_module(path: Path, docstring: str, extra_imports: str, ranges: list[tuple[int, int]]) -> None:
    parts = [docstring, "\n", extra_imports, "\n\n"]
    for start, end in ranges:
        parts.append(extract_range(SRC_LINES, start, end))
        parts.append("\n\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(parts), encoding="utf-8")
    nlines = path.read_text(encoding="utf-8").count("\n")
    print(f"  ✓ {path.relative_to(DST.parent)}: {nlines} строк")


# ----------------------------- __init__.py -----------------------------

INIT_PY = '''"""
bot — модульный пакет Telegram-бота для выгрузки данных ДТП с stat.gibdd.ru.

Структура (Phase 3-2, 100% pure refactoring из единого bot.py):
  bot._state           — shared state (imports, logger, globals, constants)
  bot.infra            — утилиты Telegram API (retry, safe_edit, send_long_message)
  bot.access           — контроль доступа + загрузка регионов
  bot.keyboards        — inline-клавиатуры
  bot.analysis         — конвейер аналитики и очагов (~1300 строк)
  bot.output           — HTML-вывод и карты
  bot.point_stats      — статистика по точке (геолокация)
  bot.qa               — Q&A-режим с LLM
  bot.handlers.commands     — /start /help /dtp /regions /miniapp /precache
  bot.handlers.callbacks    — on_callback_query
  bot.handlers.messages     — handle_message + _handle_document
  bot.app              — точка входа (main, _build_app, error_handler)

Совместимость: thin `bot.py` рядом с пакетом делает
    from bot.app import main; main()
— это позволяет запускать `python bot.py` как раньше, а также
`python -m bot.app`.

Все тесты (445) продолжают проходить без изменений — импорты из
модуля `bot` разрешаются через этот __init__.py.
"""
'''


# ----------------------------- main -----------------------------

SRC_LINES = read_lines(SRC)

def main() -> None:
    # Backup original
    backup = SRC.with_suffix(".py.bak")
    if not backup.exists():
        backup.write_text(SRC.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"✓ Backup saved: {backup}")

    # Create package dir
    DST.mkdir(parents=True, exist_ok=True)
    (DST / "handlers").mkdir(parents=True, exist_ok=True)

    # Write __init__.py files
    (DST / "__init__.py").write_text(INIT_PY, encoding="utf-8")
    print(f"  ✓ bot/__init__.py")

    (DST / "handlers" / "__init__.py").write_text(
        '"""bot.handlers — подпакет обработчиков команд, callback\'ов и сообщений."""\n',
        encoding="utf-8",
    )
    print(f"  ✓ bot/handlers/__init__.py")

    # Write _state.py
    (DST / "_state.py").write_text(STATE_HEADER, encoding="utf-8")
    nlines = (DST / "_state.py").read_text(encoding="utf-8").count("\n")
    print(f"  ✓ bot/_state.py: {nlines} строк")

    # Write each module
    for filename, docstring, ranges, extra_imports in MODULES:
        write_module(DST / filename, docstring, extra_imports, ranges)

    print("\n✅ Все модули созданы. Проверяю синтаксис...")
    import subprocess
    for py in sorted(DST.rglob("*.py")):
        result = subprocess.run(
            ["python3", "-c", f"import ast; ast.parse(open('{py}').read())"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"  ✗ {py.relative_to(DST.parent)}: {result.stderr.strip()}")
        else:
            print(f"  ✓ {py.relative_to(DST.parent)}: OK")


if __name__ == "__main__":
    main()
