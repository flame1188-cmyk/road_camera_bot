#!/usr/bin/env python3
"""Sprint 1 fix: переместить import _imports на уровень модуля.

Предыдущий скрипт добавлял `from backend.services import _imports` внутри
одного тестового метода, но _imports используется во многих методах.
Этот скрипт:
1. Удаляет все in-method `from backend.services import _imports` строки
2. Добавляет module-level import в начало каждого файла
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parents[1] / "tests"

FILES = [
    "unit/test_gibdd_service.py",
    "integration/test_clusters_flow.py",
    "integration/test_error_paths.py",
    "integration/test_task_lifecycle.py",
    "integration/test_analyze_flow.py",
]


def patch_file(filepath: Path) -> int:
    """Патчит один тестовый файл."""
    text = filepath.read_text(encoding="utf-8")
    original = text
    changes = 0

    # 1. Удаляем все in-method imports of _imports
    pattern_remove = re.compile(
        r'^\s*from backend\.services import _imports  # noqa: E402.*\n',
        re.MULTILINE
    )
    text, n = pattern_remove.subn('', text)
    changes += n

    # 2. Добавляем module-level import в начало файла (после первого
    #    блока импортов). Ищем первую строку `import pytest` или
    #    `from backend` или `from tests` и добавляем после неё.
    if "from backend.services import _imports" not in text:
        # Ищем подходящее место: после последнего top-level import
        lines = text.split('\n')
        insert_idx = 0
        for i, line in enumerate(lines):
            # Если строка — top-level import (без отступа)
            if re.match(r'^(import |from )', line):
                insert_idx = i + 1
            # Если после блока импортов идёт пустая строка или комментарий —
            # продолжаем искать
            elif line.strip() == '' and insert_idx > 0:
                continue
            elif insert_idx > 0:
                # Нашли первый non-import после блока импортов
                break

        if insert_idx > 0:
            lines.insert(insert_idx, 'from backend.services import _imports  # для патчей _PROJECT_ROOT/_import_module')
            text = '\n'.join(lines)
            changes += 1

    if text != original:
        filepath.write_text(text, encoding="utf-8")
        print(f"  [OK] {filepath.name}: {changes} changes")
    else:
        print(f"  [SKIP] {filepath.name}: no changes needed")

    return changes


def main() -> int:
    print("=" * 60)
    print("Sprint 1 fix: move _imports import to module level")
    print("=" * 60)

    total = 0
    for relpath in FILES:
        fpath = TESTS_DIR / relpath
        if not fpath.exists():
            print(f"  [MISS] {relpath}")
            continue
        total += patch_file(fpath)

    print(f"\nTotal changes: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
