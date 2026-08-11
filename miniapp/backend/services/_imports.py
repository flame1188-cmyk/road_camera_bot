"""
Общая инфраструктура импорта модулей gibdd-bot для service-слоя MiniApp.

Все service-модули (pipeline, analytics_ops, clusters_ops, llm_ops, ...)
используют _import_module() для ленивого импорта существующих модулей
gibdd-bot (bot, analytics, gibdd_parser, llm_analyzer, ...).

Ленивость нужна, чтобы:
1. MiniApp запускался даже если какие-то модули ещё не интегрированы.
2. ImportError не валил весь backend, а возвращал понятную ошибку.
3. Можно было тестировать API без реальной выгрузки данных.
"""
from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


# Корень проекта gibdd-bot (находится на 2 уровня выше этого файла):
# miniapp/backend/services/_imports.py → gibdd-bot/
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _ensure_project_path() -> None:
    """Добавляет корень gibdd-bot в sys.path (если ещё не добавлен)."""
    root = str(_PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _import_module(name: str):
    """Безопасный импорт модуля из gibdd-bot с понятной ошибкой."""
    _ensure_project_path()
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise RuntimeError(
            f"Модуль {name} не найден. Убедитесь, что miniapp/ находится "
            f"внутри проекта gibdd-bot (текущий root: {_PROJECT_ROOT}). "
            f"Ошибка: {exc}"
        ) from exc
