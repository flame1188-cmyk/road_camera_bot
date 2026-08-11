# miniapp/backend/bot — пакет для рефакторинга bot.py

## Текущее состояние (Фаза 2)

Корневой `bot.py` (4138 строк, 60+ функций) содержит **весь** код Telegram-бота:
команды, callback'и, аналитика, сообщения, error handlers, helpers.

Этот пакет — **facade**: переэкспортирует функции из `bot.py` через
чистую модульную структуру. Это позволяет:

1. Новому коду использовать канонические пути (`from miniapp.backend.bot.handlers.commands import cmd_start`)
2. Старому коду продолжать работать через `bot.cmd_start`
3. Будущему рефакторингу (Фаза 3+) переносить функции по одной без breaking changes

**Старый `bot.py` НЕ удаляется и НЕ изменяется.** Он остаётся единственным
источником истины до полной миграции.

## Структура

```
miniapp/backend/bot/
├── __init__.py          — export build_app()
├── core.py              — Application builder + lifespan hooks
├── README.md            — этот файл
├── handlers/
│   ├── __init__.py
│   ├── commands.py      — /start /help /dtp /regions /miniapp /precache
│   ├── callbacks.py     — on_callback_query (inline buttons)
│   ├── analysis.py      — _run_analysis, _run_concentration_points, etc.
│   ├── messages.py      — handle_message, _handle_document, _handle_location
│   └── error.py         — error_handler
└── helpers/
    ├── __init__.py
    ├── keyboards.py     — build_region_keyboard, build_period_keyboard
    └── format.py        — _sanitize_error, _send_long_message, _make_progress_bar
```

## Использование

### Сейчас (после Фазы 2)

```python
# main.py — ничего не меняется, продолжает работать как раньше:
from bot import _build_app  # корневой bot.py
app = _build_app(token)
```

```python
# Новый код (Фаза 2+):
from miniapp.backend.bot.core import build_app
app = build_app(token)

from miniapp.backend.bot.handlers.commands import cmd_start
from miniapp.backend.bot.helpers.keyboards import build_region_keyboard
```

### После Фазы 3 (полная миграция)

`bot.py` станет тонкой обёрткой:
```python
# bot.py (после Фазы 3)
from miniapp.backend.bot.core import build_app as _build_app
from miniapp.backend.bot.handlers.commands import *
from miniapp.backend.bot.handlers.callbacks import *
# ... и т.д.
```

## План миграции (Фаза 3+)

**Принцип**: один шаг = один PR = одна группа функций. После каждого шага —
полное тестирование в bothost (запуск бота, /dtp, /help, /regions, кнопки,
текстовые сообщения, документы).

| Шаг | Что переносим | Куда | Строк | Риск |
|-----|---------------|------|-------|------|
| 3.1 | error_handler | handlers/error.py | 44 | низкий |
| 3.2 | helpers (format, keyboards) | helpers/ | 250 | низкий |
| 3.3 | commands (кроме precache) | handlers/commands.py | 200 | средний |
| 3.4 | cmd_precache + _run_precache | handlers/commands.py | 190 | средний |
| 3.5 | messages (handle_message, document, location) | handlers/messages.py | 370 | средний |
| 3.6 | _start_fetching + _build_menu_keyboard | handlers/callbacks.py | 270 | высокий |
| 3.7 | on_callback_query | handlers/callbacks.py | 492 | ОЧЕНЬ высокий |
| 3.8 | _run_analysis | handlers/analysis.py | 458 | ОЧЕНЬ высокий |
| 3.9 | _run_concentration_points | handlers/analysis.py | 415 | ОЧЕНЬ высокий |
| 3.10 | point_stats (5 функций) | handlers/analysis.py | 400 | высокий |
| 3.11 | _handle_analytics_question | handlers/messages.py | 140 | средний |

**Итого**: ~11 шагов, ~3-5 часов на каждый (с тестированием).
После завершения `bot.py` сократится с 4138 до ~50 строк (тонкая обёртка).

## Сложности миграции

### Глобальное состояние
`bot.py` содержит ~10 глобальных переменных, разделяемых между функциями:
- `_api_down: bool` — флаг недоступности API ГИБДД
- `_api_down_lock` — asyncio.Lock для _api_down
- `_user_locks: dict[int, asyncio.Lock]` — защита от гонок
- `_conflict_last_log: float` — rate-limit логов Conflict
- `data_cache` — кэш карточек (импорт из data_cache.py)
- `_clean_shutdown: bool` — флаг штатной остановки
- `_admin_chat_id` — ID чата для админ-уведомлений
- `logger` — главный логгер

**Решение**: вынести globals в `bot/state.py` (новый модуль). Импортировать
из него во всех handlers. Это позволит тестировать handlers изолированно.

### Контекст PTB
`context.user_data` используется как shared state между командами и
callback'ами. Содержит ~20 ключей: `current_cards`, `prev_cards`,
`analytics`, `current_region`, `current_period`, и т.д.

**Решение**: в Фазе 3+ создать `bot/session.py` с типизированным
классом `UserSession`, который оборачивает `context.user_data` и
предоставляет типизированные геттеры/сеттеры. Это уберёт опечатки в
ключах и упростит рефакторинг.

### Циклические импорты
Некоторые функции в `bot.py` импортируют друг друга на лету
(например, `_run_analysis` вызывает `_run_concentration_points`).

**Решение**: при миграции использовать late imports (`from .analysis import ...`
внутри функции, не на уровне модуля).

## Почему facade, а не полный рефакторинг в Фазе 2?

1. **Безопасность**: 4138 строк working code нельзя рефакторить вслепую.
   Полное тестирование каждой функции занимает часы.
2. **Обратная совместимость**: `main.py` и другие модули импортируют
   `bot._build_app`, `bot._post_init`, `bot._fetch_cards_for_period`.
   Полный рефакторинг сломает все эти импорты.
3. **Итеративность**: facade даёт структуру СЕЙЧАС, а перенос функций
   можно делать по одной в следующих фазах.
4. **Риск regressions**: в `bot.py` есть неочевидные workaround'ы
   (например, обработка `_IsDocument` filter, retry logic для Telegram API).
   Они легко теряются при слепом рефакторинге.
