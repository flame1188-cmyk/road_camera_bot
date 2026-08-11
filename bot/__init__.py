"""
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

Обратная совместимость с внешними потребителями:
  main.py:        `import bot as bot_module; bot_module._build_app(...)`
  gibdd_service:  `bot_module._fetch_cards_for_period(...)`
Эти имена реэкспортируются ниже из соответствующих подмодулей.
"""
from bot.app import _build_app, main, error_handler
from bot.access import _fetch_cards_for_period
from bot.handlers.commands import (
    cmd_start,
    cmd_help,
    cmd_dtp,
    cmd_regions,
    cmd_miniapp,
    cmd_precache,
)
from bot.handlers.callbacks import on_callback_query
from bot.handlers.messages import handle_message, _handle_document
from bot.point_stats import _handle_location_message

__all__ = [
    # from bot.app
    "_build_app",
    "main",
    "error_handler",
    # from bot.access
    "_fetch_cards_for_period",
    # from bot.handlers.commands
    "cmd_start",
    "cmd_help",
    "cmd_dtp",
    "cmd_regions",
    "cmd_miniapp",
    "cmd_precache",
    # from bot.handlers.callbacks
    "on_callback_query",
    # from bot.handlers.messages
    "handle_message",
    "_handle_document",
    # from bot.point_stats
    "_handle_location_message",
]
