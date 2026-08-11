#!/usr/bin/env python3
"""Sprint 1 fix: обновить тесты так, чтобы патчи _PROJECT_ROOT и
_import_module применялись к _imports модулю (источник), а не только
к facade gibdd_service.

После рефакторинга Sprint 1 service-модули используют
_imports._PROJECT_ROOT и _imports._import_module() (через атрибут
модуля). Патчи в gibdd_service._PROJECT_ROOT не распространяются на
service-модули. Этот скрипт добавляет патчи в _imports рядом с
существующими патчами в gibdd_service.

Стратегия:
1. В каждом тестовом файле добавить `from backend.services import _imports`
   (если ещё нет)
2. Заменить каждую строку:
       monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)
   на:
       monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)
       monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)
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

    # 1. Добавляем import _imports если его нет
    if "_imports" not in text or "from backend.services import _imports" not in text:
        # Ищем существующий import gibdd_service и добавляем рядом
        pattern_import = re.compile(
            r'(from backend\.services import gibdd_service\b[^\n]*)'
        )
        if pattern_import.search(text):
            text = pattern_import.sub(
                r'\1\nfrom backend.services import _imports  # noqa: E402 — для патчей _PROJECT_ROOT/_import_module',
                text,
                count=1
            )
            changes += 1
        else:
            print(f"  [WARN] {filepath.name}: не найден import gibdd_service — пропускаем import _imports")

    # 2. Заменяем каждое monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", X)
    #    на две строки: gibdd_service + _imports
    #    Поддерживаем любое значение (tmp_path, tmp_path/, etc.)
    pattern_patch = re.compile(
        r'^(\s*)monkeypatch\.setattr\(gibdd_service, "_PROJECT_ROOT", ([^)]+)\)',
        re.MULTILINE
    )

    def replace_patch(m):
        nonlocal changes
        changes += 1
        indent = m.group(1)
        value = m.group(2)
        return (
            f'{indent}monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", {value})\n'
            f'{indent}monkeypatch.setattr(_imports, "_PROJECT_ROOT", {value})'
        )

    text = pattern_patch.sub(replace_patch, text)

    if text != original:
        filepath.write_text(text, encoding="utf-8")
        print(f"  [OK] {filepath.name}: {changes} changes")
    else:
        print(f"  [SKIP] {filepath.name}: no changes needed")

    return changes


def main() -> int:
    print("=" * 60)
    print("Sprint 1 fix: update tests to patch _imports._PROJECT_ROOT")
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
