#!/usr/bin/env python
"""
Sprint 6 — Smoke-валидатор LLM Sessions + QA Buttons.

Запуск:
    python scripts/verify_sprint6_llm_sessions.py

Проверки:
  1. AST-валидация всех изменённых backend-файлов
  2. schema.sql содержит таблицу llm_sessions с нужными колонками
  3. repository.py содержит save_llm_session, load_llm_session, append_qa_entry
  4. llm_ops.py вызывает save_llm_session (3 места: sync, stream, cache-hit)
     и append_qa_entry (2 места: sync, stream)
  5. task_registry.py содержит _try_restore_llm_session и вызывает её
     в get_task_async (2 ветки: in-memory hit и DB load)
  6. Frontend bundle содержит Sprint 6 маркеры
     (CopyButton, Повторить, execCommand, Скопировано)
  7. Импорты работают (без circular imports)
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path("/home/z/my-project/gibdd-bot")
VENV_PYTHON = "/home/z/.venv/bin/python"

passed = 0
failed = 0


def ok(msg: str) -> None:
    global passed
    passed += 1
    print(f"  \033[32m✓\033[0m {msg}")


def fail(msg: str) -> None:
    global failed
    failed += 1
    print(f"  \033[31m✗\033[0m {msg}")


def check_ast(path: Path) -> bool:
    try:
        ast.parse(path.read_text(encoding="utf-8"))
        return True
    except SyntaxError as e:
        print(f"  \033[31m✗\033[0m {path.name}: {e}")
        return False


def grep_count(path: Path, pattern: str) -> int:
    return sum(1 for _ in path.read_text(encoding="utf-8").splitlines()
               if pattern in _)


def main() -> int:
    print("=" * 60)
    print("Sprint 6 — LLM Sessions + QA Buttons — Smoke Validation")
    print("=" * 60)

    # === 1. AST validation ===
    print("\n1. AST validation of changed files:")
    files = [
        PROJECT_ROOT / "miniapp/backend/db/repository.py",
        PROJECT_ROOT / "miniapp/backend/services/llm_ops.py",
        PROJECT_ROOT / "miniapp/backend/services/task_registry.py",
    ]
    for f in files:
        if check_ast(f):
            ok(f"{f.relative_to(PROJECT_ROOT)} — syntax OK")
        else:
            fail(f"{f.relative_to(PROJECT_ROOT)} — syntax error")

    # === 2. schema.sql: llm_sessions table ===
    print("\n2. schema.sql — llm_sessions table:")
    schema_path = PROJECT_ROOT / "miniapp/backend/db/schema.sql"
    schema_text = schema_path.read_text(encoding="utf-8")
    if "CREATE TABLE IF NOT EXISTS llm_sessions" in schema_text:
        ok("CREATE TABLE llm_sessions присутствует")
    else:
        fail("CREATE TABLE llm_sessions отсутствует")

    required_columns = [
        "task_id",
        "user_id",
        "summary_text",
        "summary_provider",
        "summary_generated_at",
        "qa_history",
        "updated_at",
    ]
    for col in required_columns:
        if col in schema_text:
            ok(f"колонка {col} определена")
        else:
            fail(f"колонка {col} отсутствует")

    if "idx_llm_sessions_user" in schema_text:
        ok("индекс idx_llm_sessions_user присутствует")
    else:
        fail("индекс idx_llm_sessions_user отсутствует")

    if "trg_llm_sessions_updated_at" in schema_text:
        ok("триггер trg_llm_sessions_updated_at присутствует")
    else:
        fail("триггер trg_llm_sessions_updated_at отсутствует")

    # === 3. repository.py: 3 функции ===
    print("\n3. repository.py — 3 Sprint 6 функции:")
    repo_path = PROJECT_ROOT / "miniapp/backend/db/repository.py"
    repo_text = repo_path.read_text(encoding="utf-8")

    for func_name in ["save_llm_session", "load_llm_session", "append_qa_entry"]:
        if f"async def {func_name}" in repo_text:
            ok(f"async def {func_name}() определена")
        else:
            fail(f"async def {func_name}() отсутствует")

    # Проверка что append_qa_entry использует jsonb trim до 10
    if "jsonb_array_length" in repo_text and "10" in repo_text:
        ok("append_qa_entry тримит qa_history до 10 записей")
    else:
        fail("append_qa_entry не делает trim до 10")

    # Проверка что save_llm_session использует ON CONFLICT (upsert)
    if "ON CONFLICT (task_id) DO UPDATE" in repo_text:
        ok("save_llm_session использует upsert (ON CONFLICT)")
    else:
        fail("save_llm_session не использует upsert")

    # === 4. llm_ops.py: 3 save_llm_session + 2 append_qa_entry ===
    print("\n4. llm_ops.py — интеграция Sprint 6:")
    ops_path = PROJECT_ROOT / "miniapp/backend/services/llm_ops.py"
    ops_text = ops_path.read_text(encoding="utf-8")

    save_count = ops_text.count("save_llm_session(")
    if save_count >= 3:
        ok(f"save_llm_session вызывается {save_count} раз (≥3: sync/cache-hit/stream)")
    else:
        fail(f"save_llm_session вызывается только {save_count} раз (нужно ≥3)")

    append_count = ops_text.count("append_qa_entry(")
    if append_count >= 2:
        ok(f"append_qa_entry вызывается {append_count} раз (≥2: sync/stream)")
    else:
        fail(f"append_qa_entry вызывается только {append_count} раз (нужно ≥2)")

    # Проверка маркера Sprint 6 в комментариях
    sprint6_markers = ops_text.count("Sprint 6")
    if sprint6_markers >= 5:
        ok(f"Sprint 6 комментариев: {sprint6_markers} (≥5)")
    else:
        fail(f"Sprint 6 комментариев мало: {sprint6_markers} (нужно ≥5)")

    # === 5. task_registry.py: _try_restore_llm_session ===
    print("\n5. task_registry.py — восстановление сессии:")
    tr_path = PROJECT_ROOT / "miniapp/backend/services/task_registry.py"
    tr_text = tr_path.read_text(encoding="utf-8")

    if "async def _try_restore_llm_session" in tr_text:
        ok("_try_restore_llm_session() определена")
    else:
        fail("_try_restore_llm_session() отсутствует")

    if "from_session_db" in tr_text:
        ok("восстановление помечает результат маркером from_session_db")
    else:
        fail("маркер from_session_db отсутствует")

    # Вызывается в 2 местах get_task_async
    restore_calls = tr_text.count("await _try_restore_llm_session(task)")
    if restore_calls >= 2:
        ok(f"_try_restore_llm_session вызывается {restore_calls} раз (≥2: in-memory + DB)")
    else:
        fail(f"_try_restore_llm_session вызывается {restore_calls} раз (нужно ≥2)")

    # === 6. Frontend bundle markers ===
    print("\n6. Frontend bundle — Sprint 6 маркеры:")
    bundle_dir = PROJECT_ROOT / "miniapp/frontend/dist/assets"
    bundle_files = list(bundle_dir.glob("index-*.js"))
    if not bundle_files:
        fail("Frontend bundle не найден — забыл npm run build?")
    else:
        bundle = bundle_files[0].read_text(encoding="utf-8")

        for marker, min_count in [
            ("Копировать", 1),
            ("Повторить", 2),
            ("Скопировано", 1),
            ("execCommand", 1),  # fallback для не-secure context
        ]:
            count = bundle.count(marker)
            if count >= min_count:
                ok(f"маркер '{marker}': {count} вхождений (≥{min_count})")
            else:
                fail(f"маркер '{marker}': {count} вхождений (нужно ≥{min_count})")

    # === 7. Import check ===
    print("\n7. Import check (без circular imports):")
    result = subprocess.run(
        [VENV_PYTHON, "-c",
         "import sys; sys.path.insert(0, '/home/z/my-project/gibdd-bot'); "
         "from miniapp.backend.db import repository; "
         "from miniapp.backend.services import task_registry, llm_ops; "
         "print('All imports OK')"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode == 0 and "All imports OK" in result.stdout:
        ok("Все Sprint 6 модули импортируются без ошибок")
    else:
        fail(f"Импорт упал: {result.stderr[:300]}")

    # === Summary ===
    print("\n" + "=" * 60)
    print(f"  PASSED: {passed}")
    print(f"  FAILED: {failed}")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
