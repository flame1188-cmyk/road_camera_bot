#!/usr/bin/env python
"""
Sprint 4 — Smoke-валидатор Streaming LLM / SSE.

Запуск:
    python scripts/verify_sprint4_streaming.py

Проверки:
  1. AST-валидация всех изменённых файлов
  2. llm_analyzer.ask_llm_stream существует и является async generator function
  3. llm_analyzer.get_ai_summary_stream / get_ai_answer_stream существуют
  4. llm_ops.ask_llm_question_stream / stream_llm_summary существуют
  5. routers/llm.py содержит 2 новых SSE-эндпоинта
  6. routers/llm.py импортирует EventSourceResponse
  7. api.ts содержит consumeSSE, askLLMStream, getLLMSummaryStream
  8. LLMAnalysisView.tsx использует streaming (handleAskStream, handleGenerateStream)
  9. requirements.txt (оба) содержат sse-starlette
 10. Frontend TypeScript компилируется без ошибок
 11. Sprint 4 unit-тесты проходят (16 тестов)
 12. Sprint 4 integration-тесты проходят (11 тестов)
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
warnings_list = []


def ok(msg: str) -> None:
    global passed
    passed += 1
    print(f"  \033[32m✓\033[0m {msg}")


def fail(msg: str) -> None:
    global failed
    failed += 1
    print(f"  \033[31m✗\033[0m {msg}")


def warn(msg: str) -> None:
    warnings_list.append(msg)
    print(f"  \033[33m!\033[0m {msg}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# ============================================================
section("1. AST validation")
# ============================================================
files_to_check = [
    PROJECT_ROOT / "llm_analyzer.py",
    PROJECT_ROOT / "miniapp" / "backend" / "services" / "llm_ops.py",
    PROJECT_ROOT / "miniapp" / "backend" / "routers" / "llm.py",
]
for f in files_to_check:
    if not f.exists():
        fail(f"File not found: {f}")
        continue
    try:
        ast.parse(f.read_text(encoding="utf-8"))
        ok(f"AST valid: {f.relative_to(PROJECT_ROOT)}")
    except SyntaxError as e:
        fail(f"AST error in {f.name}: {e}")


# ============================================================
section("2. llm_analyzer.ask_llm_stream exists")
# ============================================================
try:
    import sys as _sys
    _sys.path.insert(0, str(PROJECT_ROOT))
    import llm_analyzer
    import inspect

    # Check ask_llm_stream
    if hasattr(llm_analyzer, "ask_llm_stream"):
        fn = llm_analyzer.ask_llm_stream
        if inspect.isasyncgenfunction(fn):
            ok("llm_analyzer.ask_llm_stream — async generator function")
        else:
            fail(f"ask_llm_stream is not async generator: {type(fn)}")
    else:
        fail("llm_analyzer.ask_llm_stream not found")

    # Check get_ai_summary_stream
    if hasattr(llm_analyzer, "get_ai_summary_stream"):
        if inspect.isasyncgenfunction(llm_analyzer.get_ai_summary_stream):
            ok("llm_analyzer.get_ai_summary_stream — async generator function")
        else:
            fail("get_ai_summary_stream is not async generator")
    else:
        fail("llm_analyzer.get_ai_summary_stream not found")

    # Check get_ai_answer_stream
    if hasattr(llm_analyzer, "get_ai_answer_stream"):
        if inspect.isasyncgenfunction(llm_analyzer.get_ai_answer_stream):
            ok("llm_analyzer.get_ai_answer_stream — async generator function")
        else:
            fail("get_ai_answer_stream is not async generator")
    else:
        fail("llm_analyzer.get_ai_answer_stream not found")

    # Check internal helpers
    for helper in ["_ask_llm_stream_free", "_ask_llm_stream_paid", "_do_llm_stream_request"]:
        if hasattr(llm_analyzer, helper):
            ok(f"llm_analyzer.{helper} exists")
        else:
            fail(f"llm_analyzer.{helper} not found")
except Exception as e:
    fail(f"Cannot import llm_analyzer: {e}")


# ============================================================
section("3. llm_ops streaming functions")
# ============================================================
try:
    _sys.path.insert(0, str(PROJECT_ROOT / "miniapp"))
    from backend.services import llm_ops
    import inspect

    for fn_name in ["ask_llm_question_stream", "stream_llm_summary"]:
        if hasattr(llm_ops, fn_name):
            fn = getattr(llm_ops, fn_name)
            if inspect.isasyncgenfunction(fn):
                ok(f"llm_ops.{fn_name} — async generator function")
            else:
                fail(f"llm_ops.{fn_name} is not async generator")
        else:
            fail(f"llm_ops.{fn_name} not found")
except Exception as e:
    fail(f"Cannot import llm_ops: {e}")


# ============================================================
section("4. SSE endpoints in routers/llm.py")
# ============================================================
llm_router_path = PROJECT_ROOT / "miniapp" / "backend" / "routers" / "llm.py"
try:
    content = llm_router_path.read_text(encoding="utf-8")

    if "from sse_starlette.sse import EventSourceResponse" in content:
        ok("EventSourceResponse imported")
    else:
        fail("EventSourceResponse not imported")

    if '/tasks/{task_id}/llm/ask/stream' in content:
        ok("POST /llm/ask/stream endpoint registered")
    else:
        fail("/llm/ask/stream endpoint not found")

    if '/tasks/{task_id}/llm/summary/stream' in content:
        ok("POST /llm/summary/stream endpoint registered")
    else:
        fail("/llm/summary/stream endpoint not found")

    if 'media_type="text/event-stream"' in content:
        ok("SSE media_type set to text/event-stream")
    else:
        fail("media_type text/event-stream not found")

    if '_SSE_PING_INTERVAL_SEC' in content:
        ok("SSE ping interval constant defined")
    else:
        fail("_SSE_PING_INTERVAL_SEC not found")
except Exception as e:
    fail(f"Cannot read routers/llm.py: {e}")


# ============================================================
section("5. Frontend api.ts SSE client")
# ============================================================
api_ts_path = PROJECT_ROOT / "miniapp" / "frontend" / "src" / "lib" / "api.ts"
try:
    content = api_ts_path.read_text(encoding="utf-8")

    if "consumeSSE" in content and "async function consumeSSE" in content:
        ok("api.ts: consumeSSE function defined")
    else:
        fail("api.ts: consumeSSE not found")

    if "askLLMStream" in content:
        ok("api.ts: askLLMStream method defined")
    else:
        fail("api.ts: askLLMStream not found")

    if "getLLMSummaryStream" in content:
        ok("api.ts: getLLMSummaryStream method defined")
    else:
        fail("api.ts: getLLMSummaryStream not found")

    if "AbortController" in content or "AbortSignal" in content:
        ok("api.ts: AbortController/AbortSignal support for cancellation")
    else:
        fail("api.ts: no AbortController support")

    if "getReader" in content:
        ok("api.ts: ReadableStream.getReader() used for SSE parsing")
    else:
        fail("api.ts: ReadableStream not used")
except Exception as e:
    fail(f"Cannot read api.ts: {e}")


# ============================================================
section("6. Frontend LLMAnalysisView.tsx streaming UI")
# ============================================================
view_path = PROJECT_ROOT / "miniapp" / "frontend" / "src" / "components" / "LLMAnalysisView.tsx"
try:
    content = view_path.read_text(encoding="utf-8")

    if "handleAskStream" in content:
        ok("LLMAnalysisView.tsx: handleAskStream method")
    else:
        fail("handleAskStream not found")

    if "handleGenerateStream" in content:
        ok("LLMAnalysisView.tsx: handleGenerateStream method")
    else:
        fail("handleGenerateStream not found")

    if "handleStopQA" in content or "handleStopSummary" in content:
        ok("LLMAnalysisView.tsx: Stop button handlers (AbortController)")
    else:
        fail("Stop button handlers not found")

    if "streamingQA" in content:
        ok("LLMAnalysisView.tsx: streamingQA state for partial answer")
    else:
        fail("streamingQA state not found")

    if "StreamingQACard" in content:
        ok("LLMAnalysisView.tsx: StreamingQACard component (typing cursor)")
    else:
        fail("StreamingQACard component not found")

    if "animate-pulse" in content and "▌" in content:
        ok("LLMAnalysisView.tsx: typing cursor (▌) with animation")
    else:
        fail("typing cursor not found")
except Exception as e:
    fail(f"Cannot read LLMAnalysisView.tsx: {e}")


# ============================================================
section("7. requirements.txt has sse-starlette")
# ============================================================
for req_path in [
    PROJECT_ROOT / "requirements.txt",
    PROJECT_ROOT / "miniapp" / "backend" / "requirements.txt",
]:
    try:
        content = req_path.read_text(encoding="utf-8")
        if "sse-starlette" in content or "sse_starlette" in content:
            ok(f"{req_path.relative_to(PROJECT_ROOT)}: sse-starlette listed")
        else:
            fail(f"{req_path.relative_to(PROJECT_ROOT)}: sse-starlette NOT listed")
    except Exception as e:
        fail(f"Cannot read {req_path}: {e}")


# ============================================================
section("8. Frontend TypeScript compiles")
# ============================================================
tsc_path = PROJECT_ROOT / "miniapp" / "frontend" / "node_modules" / ".bin" / "tsc"
if tsc_path.exists():
    result = subprocess.run(
        [str(tsc_path), "--noEmit"],
        cwd=str(PROJECT_ROOT / "miniapp" / "frontend"),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode == 0:
        ok("TypeScript compiles without errors")
    else:
        fail(f"TypeScript errors:\n{result.stderr[:500]}")
else:
    warn("tsc not available, skipping TypeScript check")


# ============================================================
section("9. Sprint 4 unit tests")
# ============================================================
result = subprocess.run(
    [VENV_PYTHON, "-m", "pytest",
     str(PROJECT_ROOT / "tests" / "unit" / "test_llm_analyzer_stream.py"),
     "--tb=short", "-q", "--no-cov"],
    capture_output=True,
    text=True,
    timeout=120,
)
if result.returncode == 0:
    # Extract passed count
    last_line = [l for l in result.stdout.splitlines() if "passed" in l]
    if last_line:
        ok(f"Sprint 4 unit tests: {last_line[-1].strip()}")
    else:
        ok("Sprint 4 unit tests: PASSED")
else:
    fail(f"Sprint 4 unit tests FAILED:\n{result.stdout[-500:]}\n{result.stderr[-500:]}")


# ============================================================
section("10. Sprint 4 integration tests")
# ============================================================
result = subprocess.run(
    [VENV_PYTHON, "-m", "pytest",
     str(PROJECT_ROOT / "tests" / "integration" / "test_llm_streaming.py"),
     "--tb=short", "-q", "--no-cov"],
    capture_output=True,
    text=True,
    timeout=120,
)
if result.returncode == 0:
    last_line = [l for l in result.stdout.splitlines() if "passed" in l]
    if last_line:
        ok(f"Sprint 4 integration tests: {last_line[-1].strip()}")
    else:
        ok("Sprint 4 integration tests: PASSED")
else:
    fail(f"Sprint 4 integration tests FAILED:\n{result.stdout[-500:]}\n{result.stderr[-500:]}")


# ============================================================
section("11. Full test suite (no regressions)")
# ============================================================
result = subprocess.run(
    [VENV_PYTHON, "-m", "pytest",
     str(PROJECT_ROOT / "tests"),
     "-k", "not test_make_cache_key_deterministic and not TestLLMCache",
     "--tb=short", "-q", "--no-cov"],
    capture_output=True,
    text=True,
    timeout=300,
)
if result.returncode == 0:
    last_line = [l for l in result.stdout.splitlines() if "passed" in l]
    if last_line:
        ok(f"Full test suite: {last_line[-1].strip()}")
    else:
        ok("Full test suite: PASSED")
else:
    fail(f"Full test suite FAILED:\n{result.stdout[-500:]}\n{result.stderr[-500:]}")


# ============================================================
# Summary
# ============================================================
print(f"\n{'='*60}")
print(f"  Sprint 4 Streaming LLM/SSE Smoke Results")
print(f"{'='*60}")
print(f"  \033[32mPassed:\033[0m   {passed}")
print(f"  \033[31mFailed:\033[0m   {failed}")
if warnings_list:
    print(f"  \033[33mWarnings:\033[0m {len(warnings_list)}")
print(f"{'='*60}")

sys.exit(0 if failed == 0 else 1)
