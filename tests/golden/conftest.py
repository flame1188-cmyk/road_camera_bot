"""
Conftest для golden-тестов: фикстура для сравнения с эталоном + флаг --update-golden.

Запуск:
    pytest tests/golden/                — сравнить с эталонами
    pytest tests/golden/ --update-golden — перезаписать эталоны
"""
import json
import sys
from pathlib import Path
from typing import Any

import pytest


# ============================================================
# pytest option: --update-golden
# ============================================================
def pytest_addoption(parser):
    """Добавляет флаг --update-golden для перезаписи эталонов."""
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Перезаписать эталонные файлы вместо сравнения (используйте при "
             "осознанном изменении формата вывода)",
    )


# ============================================================
# Фикстура golden_compare
# ============================================================
GOLDEN_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def golden_compare():
    """Возвращает функцию compare(actual, relative_path).

    Если запущено с --update-golden — перезаписывает эталон.
    Иначе — сравнивает actual с эталоном, при расхождении падает с читаемым diff.

    Args:
        actual: произвольный JSON-сериализуемый объект (dict, list, str, число).
        relative_path: путь относительно tests/golden/fixtures/
                       (например, "parser/card_row.json").

    Example:
        def test_something(golden_compare):
            result = parse_card_to_row(BASE_CARD)
            golden_compare(result, "parser/card_row.json")
    """
    def _compare(actual: Any, relative_path: str) -> None:
        golden_path = GOLDEN_DIR / relative_path
        golden_path.parent.mkdir(parents=True, exist_ok=True)

        # Сериализуем actual в JSON с сортировкой ключей и отступами
        # (для читаемого diff в git).
        actual_serialized = json.dumps(
            actual, ensure_ascii=False, indent=2, sort_keys=True, default=str
        )

        # Режим обновления: просто перезаписываем
        if pytest.config.getoption("--update-golden") if hasattr(pytest, "config") else False:
            golden_path.write_text(actual_serialized + "\n", encoding="utf-8")
            return

        # В pytest >= 7 используется другой способ получения опции
        update_mode = _get_update_option()
        if update_mode:
            golden_path.write_text(actual_serialized + "\n", encoding="utf-8")
            return

        # Режим сравнения
        if not golden_path.exists():
            pytest.fail(
                f"Golden-эталон не найден: {golden_path}\n"
                f"Запустите pytest --update-golden для создания эталона."
            )

        expected_serialized = golden_path.read_text(encoding="utf-8").rstrip("\n")
        actual_serialized = actual_serialized.rstrip("\n")

        if actual_serialized != expected_serialized:
            # Читаемый diff
            import difflib
            expected_lines = expected_serialized.splitlines(keepends=False)
            actual_lines = actual_serialized.splitlines(keepends=False)
            diff = list(difflib.unified_diff(
                expected_lines,
                actual_lines,
                fromfile=f"expected ({golden_path.name})",
                tofile="actual",
                lineterm="",
            ))

            diff_text = "\n".join(diff) if diff else "(no line diff — возможно, отличия в whitespace)"

            pytest.fail(
                f"Golden-тест провален: {relative_path}\n"
                f"Эталон: {golden_path}\n\n"
                f"Diff:\n{diff_text}\n\n"
                f"Если изменение осознанное — запустите:\n"
                f"  pytest {relative_path} --update-golden\n"
                f"и закоммитьте обновлённый эталон."
            )

    return _compare


# Хранение опции на уровне модуля (фиксируется в pytest_configure)
_UPDATE_GOLDEN_FLAG: bool = False


def pytest_configure(config):
    """Сохраняет значение --update-golden в модульную переменную."""
    global _UPDATE_GOLDEN_FLAG
    _UPDATE_GOLDEN_FLAG = config.getoption("--update-golden", False)


def _get_update_option() -> bool:
    """Возвращает значение флага --update-golden."""
    return _UPDATE_GOLDEN_FLAG


@pytest.fixture
def golden_text_path():
    """Возвращает функцию path(relative_path) → Path к эталонному текстовому файлу.

    Используется для golden-тестов с .txt/.md выходами (LLM prompts),
    где важен формат (отступы, переводы строк).

    Example:
        def test_prompt(golden_text_path):
            prompt = build_summary_prompt(...)
            path = golden_text_path("llm/summary_prompt.txt")
            if path.exists() and not update_mode:
                assert prompt == path.read_text(...)
            else:
                path.write_text(prompt)
    """
    def _path(relative_path: str) -> Path:
        return GOLDEN_DIR / relative_path
    return _path


@pytest.fixture
def golden_text_compare(golden_text_path):
    """Сравнивает текст с эталоном (для .txt/.md файлов).

    Args:
        actual: строка для сравнения.
        relative_path: путь относительно tests/golden/fixtures/.
    """
    def _compare(actual: str, relative_path: str) -> None:
        path = golden_text_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        if _get_update_option():
            path.write_text(actual, encoding="utf-8")
            return

        if not path.exists():
            pytest.fail(
                f"Golden-эталон не найден: {path}\n"
                f"Запустите pytest --update-golden для создания."
            )

        expected = path.read_text(encoding="utf-8")
        if actual != expected:
            import difflib
            diff = list(difflib.unified_diff(
                expected.splitlines(keepends=False),
                actual.splitlines(keepends=False),
                fromfile=f"expected ({path.name})",
                tofile="actual",
                lineterm="",
            ))
            diff_text = "\n".join(diff) if diff else "(no line diff)"
            pytest.fail(
                f"Golden-тест провален: {relative_path}\n"
                f"Эталон: {path}\n\n"
                f"Diff:\n{diff_text}\n\n"
                f"Если изменение осознанное — запустите:\n"
                f"  pytest --update-golden\n"
            )

    return _compare
