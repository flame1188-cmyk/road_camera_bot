"""
Smoke-тесты на структуру пакета bot/ (после Phase 3-2 рефакторинга).

Проверяют:
  1. Все 13 модулей bot/* импортируются без ошибок (если PTB установлен)
  2. Thin shim bot.py продолжает работать (python bot.py → from bot.app import main)
  3. Публичный API бота (cmd_*, on_callback_query, handle_message, _build_app, main)
     доступен через bot.app или bot.handlers.*
  4. Нет циклических импортов между модулями
  5. Структура директории соответствует плану

Запуск: pytest tests/smoke/test_bot_package.py -m smoke

ВАЖНО: Большинство тестов требуют python-telegram-bot v20+ (с классами
Update/InlineKeyboardButton/etc. в корне `telegram`). Если PTB не установлен
или установлена старая версия (≤13.x) — тесты корректно пропускаются (skip),
как уже сделано для psycopg/slowapi.

Тесты структуры директории и thin shim НЕ требуют PTB и проходят всегда.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


# Все модули, которые должны быть в пакете bot/ после рефакторинга.
EXPECTED_BOT_MODULES = [
    "bot",
    "bot._state",
    "bot.infra",
    "bot.access",
    "bot.keyboards",
    "bot.analysis",
    # Phase 3-4: подмодули пакета bot.analysis
    "bot.analysis.state",
    "bot.analysis.menu",
    "bot.analysis.pipeline",
    "bot.analysis.run",
    "bot.analysis.clusters",
    "bot.output",
    "bot.point_stats",
    "bot.qa",
    "bot.handlers",
    "bot.handlers.commands",
    "bot.handlers.callbacks",
    "bot.handlers.messages",
    "bot.app",
]


# ====================== PTB availability check ======================

def _ptb_available() -> bool:
    """Проверяет, установлен ли python-telegram-bot v20+ с нужными классами.

    PTB v20+ экспортирует Update, InlineKeyboardButton, InlineKeyboardMarkup
    в корне `telegram`. Старые версии (≤13.x) или конфликтующий пакет
    `telegram` (заброшенный, не имеющий отношения к PTB) — не экспортируют.

    Возвращает True только если нужные классы доступны.
    """
    try:
        import telegram
        return (
            hasattr(telegram, "Update")
            and hasattr(telegram, "InlineKeyboardButton")
            and hasattr(telegram, "InlineKeyboardMarkup")
        )
    except ImportError:
        return False


# Параметризованный skip-маркер: тесты, требующие PTB, пропускаются если его нет.
ptb_required = pytest.mark.skipif(
    not _ptb_available(),
    reason="python-telegram-bot v20+ не установлен — bot/* модули опциональны "
           "в dev-окружении без PTB. Установите: pip install python-telegram-bot>=20.0",
)


# ====================== Tests ======================

@pytest.mark.smoke
@pytest.mark.parametrize("module_name", EXPECTED_BOT_MODULES)
def test_bot_module_imports_without_errors(module_name: str) -> None:
    """Каждый модуль пакета bot/ должен импортироваться без исключений.

    Требует python-telegram-bot v20+. Если PTB не установлен — тест skip'ается.
    Пустые модули (bot, bot.handlers) импортируются без PTB и тестируются всегда.
    """
    # bot.handlers и bot — оба зависят от PTB, потому что:
    #   • bot/__init__.py (после fixup-1) реэкспортирует PTB-функции
    #     (_build_app, _fetch_cards_for_period, ...) для обратной
    #     совместимости с main.py и gibdd_service.py
    #   • Python всегда импортирует родительский пакет `bot` перед
    #     `bot.handlers`, поэтому bot.handlers также требует PTB
    # В prod PTB всегда установлен. В dev без PTB переходим к общей
    # ветке skip'а ниже.
    if module_name in ("bot", "bot.handlers") and _ptb_available():
        try:
            importlib.import_module(module_name)
        except ImportError as e:
            pytest.fail(f"Не удалось импортировать {module_name}: {e}")
        return

    # Остальные модули требуют PTB.
    if not _ptb_available():
        pytest.skip(
            "python-telegram-bot v20+ не установлен — bot/* модули опциональны "
            "в dev-окружении без PTB"
        )

    try:
        importlib.import_module(module_name)
    except ImportError as e:
        pytest.fail(
            f"Не удалось импортировать {module_name}: {e}. "
            f"Проверьте импорты после Phase 3-2 рефакторинга."
        )
    except SyntaxError as e:
        pytest.fail(
            f"SyntaxError при импорте {module_name}: {e}. "
            f"Проверьте, что код был корректно извлечён из bot.py."
        )


@pytest.mark.smoke
def test_thin_shim_bot_py_still_works() -> None:
    """bot.py должен оставаться тонкой обёрткой, делегирующей в bot.app.

    Не требует PTB — проверяет только структуру файла.
    """
    project_root = Path(__file__).resolve().parents[2]
    bot_py = project_root / "bot.py"

    assert bot_py.exists(), "bot.py должен существовать (thin shim)"
    content = bot_py.read_text(encoding="utf-8")

    # thin shim должен импортировать main из bot.app
    assert "from bot.app import main" in content, (
        "bot.py должен содержать 'from bot.app import main' для обратной совместимости"
    )

    # thin shim НЕ должен содержать большого количества строк кода
    line_count = len([line for line in content.splitlines() if line.strip()])
    assert line_count < 30, (
        f"bot.py должен быть тонким (<30 строк кода), фактически {line_count} строк. "
        f"Весь код должен быть в пакете bot/."
    )


@pytest.mark.smoke
@ptb_required
def test_public_api_handlers_accessible() -> None:
    """Все публичные обработчики бота должны быть доступны через bot.app или bot.handlers.*.

    Требует PTB v20+. Эти имена используются в _build_app() для регистрации
    в Application. Если они недоступны — Application не зарегистрирует
    обработчики → бот не отвечает.
    """
    from bot.handlers.commands import (
        cmd_start, cmd_help, cmd_dtp, cmd_regions, cmd_miniapp, cmd_precache,
    )
    from bot.handlers.callbacks import on_callback_query
    from bot.handlers.messages import handle_message, _handle_document
    from bot.point_stats import _handle_location_message
    from bot.app import _build_app, main, error_handler

    # Все импорты выше должны были пройти без исключений.
    # Дополнительно проверим, что это действительно callable.
    assert callable(cmd_start)
    assert callable(cmd_help)
    assert callable(cmd_dtp)
    assert callable(cmd_regions)
    assert callable(cmd_miniapp)
    assert callable(cmd_precache)
    assert callable(on_callback_query)
    assert callable(handle_message)
    assert callable(_handle_document)
    assert callable(_handle_location_message)
    assert callable(_build_app)
    assert callable(main)
    assert callable(error_handler)


@pytest.mark.smoke
@ptb_required
def test_shared_state_is_single_instance() -> None:
    """Глобальное состояние (_api_down, _user_locks, logger, etc.) должно быть
    единым экземпляром во всех модулях — иначе поведение изменится.

    Это ключевая гарантия 100% pure рефакторинга: нельзя случайно
    продублировать state в разных модулях.
    """
    import bot._state as state_mod
    import bot.infra as infra_mod
    import bot.access as access_mod
    import bot.analysis as analysis_mod

    # logger должен быть одним и тем же объектом во всех модулях
    assert state_mod.logger is infra_mod.logger
    assert state_mod.logger is access_mod.logger
    assert state_mod.logger is analysis_mod.logger

    # _user_locks dict должен быть тем же объектом
    assert state_mod._user_locks is infra_mod._user_locks

    # _precache_lock — тот же объект
    assert state_mod._precache_lock is access_mod._precache_lock

    # Константы тоже
    assert state_mod.TG_MSG_LIMIT is infra_mod.TG_MSG_LIMIT
    assert state_mod.MONTH_SHORT is access_mod.MONTH_SHORT


@pytest.mark.smoke
@ptb_required
def test_no_circular_imports_in_bot_package() -> None:
    """Проверка отсутствия циклических импортов в пакете bot/.

    Если бы цикл был — importlib.import_module("bot.app") уже упал бы
    с ImportError "cannot import name X from partially initialized module Y".
    Здесь мы дополнительно проверяем, что все модули загружены в sys.modules
    и остаются там после полной загрузки пакета.
    """
    # Очищаем кэш, чтобы получить свежий импорт
    for m in list(sys.modules.keys()):
        if m == "bot" or m.startswith("bot."):
            del sys.modules[m]

    # Импортируем в порядке, который максимизирует шанс поймать цикл
    importlib.import_module("bot.app")
    importlib.import_module("bot.handlers.commands")
    importlib.import_module("bot.handlers.callbacks")
    importlib.import_module("bot.handlers.messages")
    importlib.import_module("bot.analysis")
    importlib.import_module("bot.point_stats")
    importlib.import_module("bot.qa")
    importlib.import_module("bot.output")

    # Все ключевые модули должны быть в sys.modules
    for m in EXPECTED_BOT_MODULES:
        assert m in sys.modules, (
            f"Модуль {m} не зарегистрирован в sys.modules после импорта bot.app. "
            f"Возможно, есть циклический импорт."
        )


@pytest.mark.smoke
def test_bot_package_directory_structure() -> None:
    """Структура директории bot/ должна соответствовать плану Phase 3-2..3-4.

    Не требует PTB — проверяет только наличие файлов.

    Phase 3-2: split bot.py → bot/ пакет (14 модулей)
    Phase 3-4: split bot/analysis.py → bot/analysis/ пакет (5 модулей)
    """
    project_root = Path(__file__).resolve().parents[2]
    bot_dir = project_root / "bot"

    expected_files = [
        "__init__.py",
        "_state.py",
        "infra.py",
        "access.py",
        "keyboards.py",
        # Phase 3-4: bot/analysis.py → bot/analysis/ пакет
        "analysis/__init__.py",
        "analysis/state.py",
        "analysis/menu.py",
        "analysis/pipeline.py",
        "analysis/run.py",
        "analysis/clusters.py",
        "output.py",
        "point_stats.py",
        "qa.py",
        "app.py",
        "handlers/__init__.py",
        "handlers/commands.py",
        "handlers/callbacks.py",
        "handlers/messages.py",
    ]

    for rel_path in expected_files:
        full = bot_dir / rel_path
        assert full.exists(), f"Ожидаемый файл не найден: {full}"
        assert full.stat().st_size > 0, f"Файл пустой: {full}"

    # Старый analysis.py не должен существовать (заменён на пакет)
    assert not (bot_dir / "analysis.py").exists(), (
        "bot/analysis.py всё ещё существует — должен быть удалён после Phase 3-4"
    )
