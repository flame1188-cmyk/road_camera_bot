"""
miniapp.backend.bot — пакет для рефакторинга bot.py (Фаза 2.6).

Текущее состояние (Фаза 2):
  Корневой bot.py (4138 строк) содержит ВЕСЬ код Telegram-бота:
  commands, callbacks, analysis, messages, error handlers, helpers.
  Это working code, который не трогаем.

  Этот пакет — FACADE: предоставляет чистую структуру подмодулей с
  переэкспортом из bot.py. Когда будет время (Фаза 3+), функции
  будут переноситься по одной в соответствующие подмодули.

Структура:
  miniapp/backend/bot/
  ├── __init__.py          — re-export _build_app для main.py
  ├── core.py              — Application builder, lifespan hooks
  ├── handlers/
  │   ├── __init__.py
  │   ├── commands.py      — /start, /help, /dtp, /regions, /miniapp
  │   ├── callbacks.py     — on_callback_query (кнопки)
  │   ├── analysis.py      — _run_analysis, _run_concentration_points
  │   ├── messages.py      — handle_message, _handle_document, _handle_location_message
  │   └── error.py         — error_handler
  └── helpers/
      ├── __init__.py
      ├── keyboards.py     — build_region_keyboard, build_period_keyboard
      └── format.py        — _make_progress_bar, _sanitize_error, _send_long_message

План миграции (Фаза 3+):
  1. Перенести handlers/error.py (~50 строк) — самый простой, нет зависимостей.
  2. Перенести helpers/ (~200 строк) — чистые функции.
  3. Перенести handlers/commands.py (~300 строк) — есть общие globals.
  4. Перенести handlers/callbacks.py (~500 строк) — сложный, много callback'ов.
  5. Перенести handlers/analysis.py (~500 строк) — самый сложный, много зависимостей.

ВАЖНО: Каждый шаг миграции требует отдельного тестирования.
Не объединять несколько шагов в один PR.

Использование (после полной миграции):
  from miniapp.backend.bot.core import build_app
  app = build_app(token)
"""
from .core import build_app  # noqa: F401

__all__ = ["build_app"]
