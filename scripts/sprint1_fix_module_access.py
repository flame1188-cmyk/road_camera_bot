#!/usr/bin/env python3
"""Sprint 1 fix: изменить service-модули так, чтобы _import_module и
_PROJECT_ROOT разрешались через атрибут модуля _imports (а не через
локальную binding). Это позволяет тестам патчить _imports._import_module
и _imports._PROJECT_ROOT — и все service-модули увидят патч.

Изменения в каждом service-модуле:
  БЫЛО:  from ._imports import _PROJECT_ROOT, _import_module
  СТАЛО: from . import _imports

  БЫЛО:  _import_module("bot")
  СТАЛО: _imports._import_module("bot")

  БЫЛО:  _PROJECT_ROOT / "data" / ...
  СТАЛО: _imports._PROJECT_ROOT / "data" / ...
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SERVICES_DIR = Path(__file__).resolve().parents[1] / "miniapp/backend/services"

# Файлы, которые нужно обновить (все, кто импортирует из _imports)
FILES = [
    "pipeline.py",
    "analytics_ops.py",
    "clusters_ops.py",
    "point_stats_ops.py",
    "llm_ops.py",
    "query_ops.py",
    "task_registry.py",
    "cleanup.py",
]


def patch_file(filepath: Path) -> int:
    """Патчит один файл. Возвращает количество замен."""
    text = filepath.read_text(encoding="utf-8")
    original = text
    changes = 0

    # 1. Заменяем "from ._imports import ..." на "from . import _imports"
    #    Поддерживаем разные варианты:
    #    - from ._imports import _PROJECT_ROOT, _import_module
    #    - from ._imports import _import_module
    #    - from ._imports import _PROJECT_ROOT  # noqa: F401 ...
    pattern_import = re.compile(
        r'^from \._imports import ([^\n]+)$',
        re.MULTILINE
    )

    def replace_import(m):
        return "from . import _imports"

    text, n = pattern_import.subn(replace_import, text)
    changes += n

    # 2. Заменяем вызовы _import_module( на _imports._import_module(
    #    Но НЕ заменяем _imports._import_module (уже патченный)
    #    и НЕ заменяем в комментариях/строках.
    #    Простая эвристика: заменяем только если перед _import_module
    #    нет точки и есть пробел/начало строки/( перед ним.
    pattern_call = re.compile(r'(?<![\w.])_import_module\(')
    text, n = pattern_call.subn("_imports._import_module(", text)
    changes += n

    # 3. Заменяем _PROJECT_ROOT (не в строке from ._imports, уже обработано)
    #    на _imports._PROJECT_ROOT
    #    Но НЕ заменяем если уже есть _imports. перед ним
    pattern_root = re.compile(r'(?<![\w.])_PROJECT_ROOT')
    text, n = pattern_root.subn("_imports._PROJECT_ROOT", text)
    changes += n

    # 4. Убираем дубли: _imports._imports._import_module → _imports._import_module
    #    (на случай если уже было _imports. перед заменой)
    text = re.sub(r'_imports\._imports\.', "_imports.", text)
    #    и _imports._imports._PROJECT_ROOT
    text = re.sub(r'_imports\._imports\._PROJECT_ROOT', "_imports._PROJECT_ROOT", text)

    if text != original:
        filepath.write_text(text, encoding="utf-8")
        print(f"  [OK] {filepath.name}: {changes} replacements")
    else:
        print(f"  [SKIP] {filepath.name}: no changes needed")

    return changes


def main() -> int:
    print("=" * 60)
    print("Sprint 1 fix: switch to _imports module attribute access")
    print("=" * 60)

    total = 0
    for fname in FILES:
        fpath = SERVICES_DIR / fname
        if not fpath.exists():
            print(f"  [MISS] {fname}")
            continue
        total += patch_file(fpath)

    print(f"\nTotal replacements: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
