"""
core.py — фабрика Application (Фаза 2.6 facade).

Сейчас: переэкспортирует _build_app из корневого bot.py.
После миграции Фазы 3: будет содержать полную реализацию Application builder
с регистрацией handlers и lifespan hooks.
"""
from __future__ import annotations

# Импортируем корневой bot.py — он добавит корень gibdd-bot в sys.path
# автоматически (см. bot.py:19-22).
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[4])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def build_app(token: str):
    """Создаёт и настраивает Telegram Application.

    Сейчас: тонкая обёртка над bot._build_app().
    После Фазы 3: полная реализация с модульными handlers.
    """
    import bot as _bot_module
    return _bot_module._build_app(token)


# Переэкспорт lifespan hooks (используются в main.py после миграции)
def _safe_get(name: str):
    """Достаёт атрибут из bot.py, не падая если его нет."""
    try:
        import bot as _bot_module
        return getattr(_bot_module, name, None)
    except Exception:
        return None


post_init = _safe_get("_post_init")
post_shutdown = _safe_get("_post_shutdown")
