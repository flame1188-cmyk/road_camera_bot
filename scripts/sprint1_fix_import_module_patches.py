#!/usr/bin/env python3
"""Sprint 1 fix: для каждого monkeypatch.setattr(gibdd_service, "_import_module", X)
добавить параллельный патч monkeypatch.setattr(_imports, "_import_module", X).

Это нужно потому что после рефакторинга service-модули используют
_imports._import_module() через атрибут модуля, и патч только в
gibdd_service не распространяется на service-модули.
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
    "integration/test_routes.py",
]


def patch_file(filepath: Path) -> int:
    """Патчит один тестовый файл."""
    text = filepath.read_text(encoding="utf-8")
    original = text
    changes = 0

    # Проверяем, есть ли _imports на уровне модуля
    has_imports_import = bool(re.search(
        r'^from backend\.services import _imports\b',
        text,
        re.MULTILINE
    ))

    if not has_imports_import:
        # Добавляем import на уровне модуля
        # Ищем последний top-level import
        lines = text.split('\n')
        insert_idx = 0
        for i, line in enumerate(lines):
            if re.match(r'^(import |from )', line):
                insert_idx = i + 1
            elif line.strip() == '' and insert_idx > 0:
                continue
            elif insert_idx > 0:
                break

        if insert_idx > 0:
            lines.insert(insert_idx, 'from backend.services import _imports  # для патчей _PROJECT_ROOT/_import_module')
            text = '\n'.join(lines)
            changes += 1
            print(f"  [+] Added module-level import _imports")

    # Для каждой строки monkeypatch.setattr(gibdd_service, "_import_module", X)
    # добавляем параллельную строку для _imports (если её ещё нет рядом)
    pattern = re.compile(
        r'^(\s*)monkeypatch\.setattr\(gibdd_service, "_import_module", ([^)]+)\)',
        re.MULTILINE
    )

    def add_imports_patch(m):
        nonlocal changes
        indent = m.group(1)
        value = m.group(2)
        replacement = (
            f'{indent}monkeypatch.setattr(gibdd_service, "_import_module", {value})\n'
            f'{indent}monkeypatch.setattr(_imports, "_import_module", {value})'
        )
        changes += 1
        return replacement

    text = pattern.sub(add_imports_patch, text)

    if text != original:
        filepath.write_text(text, encoding="utf-8")
        print(f"  [OK] {filepath.name}: {changes} changes")
    else:
        print(f"  [SKIP] {filepath.name}: no changes needed")

    return changes


def main() -> int:
    print("=" * 60)
    print("Sprint 1 fix: propagate _import_module patches to _imports")
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
