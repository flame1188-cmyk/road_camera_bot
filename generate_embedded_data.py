#!/usr/bin/env python3
"""
Генерирует np_bdd/scripts/embedded_data.py — все JSON-файлы из np_bdd/datasets/
запакованные в один Python-модуль. Это гарантирует, что данные попадут в
Docker-образ даже если Bothost «съедает» папку datasets/ (маловероятно после
переименования с data → datasets, но оставим как страховку).

Запускать ЛОКАЛЬНО перед коммитом:
    python3 /home/z/my-project/scripts/generate_embedded_data.py
"""
import json
from pathlib import Path

REPO = Path("/home/z/my-project/gibdd-bot")
DATA_DIR = REPO / "np_bdd" / "datasets"
OUT = REPO / "np_bdd" / "scripts" / "embedded_data.py"


def main():
    if not DATA_DIR.exists():
        raise SystemExit(f"data dir not found: {DATA_DIR}")

    files: dict[str, str] = {}
    for p in sorted(DATA_DIR.rglob("*.json")):
        rel = p.relative_to(DATA_DIR).as_posix()  # e.g. "vehicles/1106.json"
        files[rel] = p.read_text(encoding="utf-8")

    print(f"Packing {len(files)} JSON files into {OUT}")

    lines = [
        '"""',
        "Автоматически сгенерированный модуль.",
        "Содержит все JSON-файлы из np_bdd/datasets/ как Python-словарь.",
        "Это страховка: если на сервере папка datasets/ пустая (например,",
        "Bothost монтирует volume), данные распаковываются отсюда.",
        "",
        "Регенерация: python3 /home/z/my-project/scripts/generate_embedded_data.py",
        '"""',
        "from __future__ import annotations",
        "",
        "import json",
        "from pathlib import Path",
        "",
        "# {относительный_путь: содержимое_json}",
        f"_RAW: dict[str, str] = {json.dumps(files, ensure_ascii=False, indent=2)}",
        "",
        "",
        "def get_json(rel_path: str) -> dict | list | None:",
        '    """Возвращает распарсенный JSON по относительному пути (например, "vehicles/1106.json")."""',
        "    raw = _RAW.get(rel_path)",
        "    if raw is None:",
        "        return None",
        "    return json.loads(raw)",
        "",
        "",
        "def list_dir(prefix: str) -> list[str]:",
        '    """Список файлов в директории (по префиксу, например "vehicles/")."""',
        "    return sorted(k for k in _RAW if k.startswith(prefix))",
        "",
        "",
        "def extract_to_disk(target_dir: Path) -> None:",
        "    \"\"\"",
        "    Распаковывает все встроенные файлы в target_dir.",
        "    Используется как fallback, если data/ пустая на сервере.",
        "    \"\"\"",
        "    target_dir = Path(target_dir)",
        "    for rel, content in _RAW.items():",
        "        dst = target_dir / rel",
        "        dst.parent.mkdir(parents=True, exist_ok=True)",
        "        dst.write_text(content, encoding=\"utf-8\")",
        "    print(f\"[embedded_data] Extracted {len(_RAW)} files to {target_dir}\")",
        "",
        "",
        "def has_any_data() -> bool:",
        "    return len(_RAW) > 0",
        "",
        "",
        "if __name__ == \"__main__\":",
        "    # Диагностика: можно запустить и посмотреть, что внутри.",
        "    print(f\"Embedded files: {len(_RAW)}\")",
        "    for k in sorted(_RAW):",
        "        print(f\"  {k} ({len(_RAW[k])} bytes)\")",
        "",
    ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
