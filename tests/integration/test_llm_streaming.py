"""
Sprint 4 — Интеграционные тесты SSE streaming endpoints.

Покрытие:
  - POST /dtp/tasks/{task_id}/llm/ask/stream — happy path: 1 delta + done
  - POST /dtp/tasks/{task_id}/llm/ask/stream — error event при exception в генераторе
  - POST /dtp/tasks/{task_id}/llm/ask/stream — 400 при невалидном provider
  - POST /dtp/tasks/{task_id}/llm/ask/stream — 404 при несуществующей задаче
  - POST /dtp/tasks/{task_id}/llm/summary/stream — happy path (cache miss)
  - POST /dtp/tasks/{task_id}/llm/summary/stream — cache hit (один delta + done)
  - SSE-формат: корректные event:/data: строки, разделитель \n\n
  - Content-Type: text/event-stream

Моки:
  - _gibdd_stubs.install_stubs — подменяет llm_analyzer, analytics, etc.
    на stub'ы, которые возвращают предсказуемый текст без HTTP-вызовов.
  - llm_cache stub — отключает или подменяет кэш summary.
  - Для тестов error-handling — патчим stub-функцию, чтобы она raising.
"""
from __future__ import annotations

import json
import sys
import types
from typing import Any

import pytest

from tests.integration._gibdd_stubs import install_stubs, make_minimal_cards
from backend.services import _imports


# ============================================================
# SSE helpers
# ============================================================
def _parse_sse_events(raw_text: str) -> list[tuple[str, str]]:
    """Парсит SSE-текст в список (event, data).
    Поддерживает оба варианта разделителей: \n\n (unix) и \r\n\r\n (HTTP-стандарт).
    """
    # Нормализуем CRLF → LF, чтобы корректно сплитить
    normalized = raw_text.replace("\r\n", "\n")
    events = []
    for raw_event in normalized.split("\n\n"):
        if not raw_event.strip():
            continue
        event_type = "message"
        data_lines = []
        for line in raw_event.split("\n"):
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        events.append((event_type, "\n".join(data_lines)))
    return events


# ============================================================
# Shared fixtures
# ============================================================
@pytest.fixture
def stub_repository(monkeypatch):
    fake_repo = types.ModuleType("repository")
    async def fake_save_task(t): return None
    async def fake_log_access(**kw): return None
    fake_repo.save_task = fake_save_task
    fake_repo.log_access = fake_log_access
    monkeypatch.setitem(sys.modules, "backend.db.repository", fake_repo)


@pytest.fixture
def stub_db_not_ready(monkeypatch):
    fake_db = types.ModuleType("connection")
    fake_db.is_db_ready = lambda: False
    monkeypatch.setitem(sys.modules, "backend.db.connection", fake_db)


@pytest.fixture
def stub_llm_cache(monkeypatch):
    """Отключает llm_cache (всегда cache miss)."""
    fake_cache = types.ModuleType("llm_cache")
    async def fake_get(*a, **kw): return None
    async def fake_put(*a, **kw): return None
    fake_cache.get_cached_summary = fake_get
    fake_cache.put_cached_summary = fake_put
    fake_cache.make_cache_key = lambda **kw: ("key", "hash", "v1")
    monkeypatch.setitem(sys.modules, "backend.db.llm_cache", fake_cache)


@pytest.fixture
def stub_llm_cache_hit(monkeypatch):
    """llm_cache всегда возвращает кэшированный текст."""
    fake_cache = types.ModuleType("llm_cache")
    cached_text = "Кэшированное резюме за прошлый запуск."
    async def fake_get(*a, **kw): return cached_text
    async def fake_put(*a, **kw): return None
    fake_cache.get_cached_summary = fake_get
    fake_cache.put_cached_summary = fake_put
    fake_cache.make_cache_key = lambda **kw: ("key", "hash", "v1")
    monkeypatch.setitem(sys.modules, "backend.db.llm_cache", fake_cache)


@pytest.fixture
def ready_task(monkeypatch, clear_in_memory_tasks, stub_repository, stub_db_not_ready, tmp_path):
    """Создаёт задачу со статусом done, с cards готовы.
    user_id=999999 — совпадает с fastapi_test_user.id."""
    from backend.services import gibdd_service

    monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

    install_stubs(monkeypatch)
    task = gibdd_service.create_task(
        user_id=999999, region_code="1101", region_name="Тестовая область",
        period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
    )
    task.cards = make_minimal_cards(3)
    task.status = "done"
    return task


# ============================================================
# POST /llm/ask/stream
# ============================================================
class TestAskLLMStreamEndpoint:
    def test_happy_path_streams_deltas(
        self, fastapi_client, ready_task, stub_llm_cache,
    ):
        """Stub возвращает текст → 1 delta-событие + done."""
        response = fastapi_client.post(
            f"/dtp/tasks/{ready_task.id}/llm/ask/stream",
            json={"question": "Что растёт?", "provider": "free"},
        )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        events = _parse_sse_events(response.text)
        event_types = [e[0] for e in events]
        assert "delta" in event_types
        assert event_types[-1] == "done"

        # Конкатенация delta data = полный ответ
        delta_data = [data for ev, data in events if ev == "delta"]
        full_answer = "".join(delta_data)
        assert "Mock LLM" in full_answer  # stub возвращает "Mock LLM summary"

    def test_llm_exception_emits_error_event(
        self, fastapi_client, ready_task, stub_llm_cache, monkeypatch,
    ):
        """Если ask_llm_question_stream падает — SSE error event, HTTP 200."""
        # Патчим stub, чтобы get_ai_answer_stream raising
        from backend.services import _imports as imp_mod
        original_import = imp_mod._import_module

        def patched_import(name):
            mod = original_import(name)
            if name == "llm_analyzer":
                async def raising_stream(**kwargs):
                    raise RuntimeError("LLM exploded")
                    yield  # noqa: never reached, but makes it a generator
                mod.get_ai_answer_stream = raising_stream
            return mod

        monkeypatch.setattr(imp_mod, "_import_module", patched_import)
        # Также патчим в gibdd_service (т.к. некоторые импорты идут через него)
        from backend.services import gibdd_service
        monkeypatch.setattr(gibdd_service, "_import_module", patched_import)

        response = fastapi_client.post(
            f"/dtp/tasks/{ready_task.id}/llm/ask/stream",
            json={"question": "Что растёт?", "provider": "free"},
        )

        assert response.status_code == 200
        events = _parse_sse_events(response.text)
        event_types = [e[0] for e in events]
        assert "error" in event_types
        err_events = [data for ev, data in events if ev == "error"]
        assert len(err_events) >= 1
        err_data = json.loads(err_events[-1])
        assert "error" in err_data
        assert "LLM exploded" in err_data["error"]

    def test_invalid_provider_returns_400(
        self, fastapi_client, ready_task,
    ):
        """provider='invalid' → 400 Bad Request до стрима."""
        response = fastapi_client.post(
            f"/dtp/tasks/{ready_task.id}/llm/ask/stream",
            json={"question": "вопрос", "provider": "invalid"},
        )
        assert response.status_code == 400
        assert "provider must be" in response.json()["detail"]

    def test_short_question_emits_error_event(
        self, fastapi_client, ready_task, stub_llm_cache,
    ):
        """Вопрос, проходящий Pydantic (3+ chars), но fail в генераторе
        после strip() — "ab " (3 chars, stripped = 2 < 3) → SSE error event."""
        response = fastapi_client.post(
            f"/dtp/tasks/{ready_task.id}/llm/ask/stream",
            json={"question": "ab ", "provider": "free"},
        )

        assert response.status_code == 200
        events = _parse_sse_events(response.text)
        event_types = [e[0] for e in events]
        assert "error" in event_types

    def test_nonexistent_task_returns_404(self, fastapi_client):
        """Несуществующая задача → 404."""
        response = fastapi_client.post(
            "/dtp/tasks/nonexistent-id/llm/ask/stream",
            json={"question": "вопрос", "provider": "free"},
        )
        assert response.status_code == 404


# ============================================================
# POST /llm/summary/stream
# ============================================================
class TestLLMSummaryStreamEndpoint:
    def test_happy_path_streams_summary(
        self, fastapi_client, ready_task, stub_llm_cache,
    ):
        """Cache miss → стриминг резюме (stub возвращает один delta)."""
        response = fastapi_client.post(
            f"/dtp/tasks/{ready_task.id}/llm/summary/stream",
            json={"provider": "free"},
        )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        events = _parse_sse_events(response.text)
        event_types = [e[0] for e in events]
        assert "delta" in event_types
        assert event_types[-1] == "done"

        delta_data = [data for ev, data in events if ev == "delta"]
        full_summary = "".join(delta_data)
        assert "Mock LLM" in full_summary

    def test_cache_hit_emits_single_delta(
        self, fastapi_client, ready_task, stub_llm_cache_hit,
    ):
        """Cache hit → один delta со всем текстом + done (мгновенно, без LLM)."""
        response = fastapi_client.post(
            f"/dtp/tasks/{ready_task.id}/llm/summary/stream",
            json={"provider": "free"},
        )

        assert response.status_code == 200
        events = _parse_sse_events(response.text)
        event_types = [e[0] for e in events]
        assert "delta" in event_types
        assert event_types[-1] == "done"

        # Один delta со всем кэшированным текстом
        delta_data = [data for ev, data in events if ev == "delta"]
        full_text = "".join(delta_data)
        assert "Кэшированное резюме" in full_text

    def test_invalid_provider_returns_400(
        self, fastapi_client, ready_task,
    ):
        """provider='invalid' → 400 Bad Request."""
        response = fastapi_client.post(
            f"/dtp/tasks/{ready_task.id}/llm/summary/stream",
            json={"provider": "invalid"},
        )
        assert response.status_code == 400

    def test_nonexistent_task_returns_404(self, fastapi_client):
        """Несуществующая задача → 404."""
        response = fastapi_client.post(
            "/dtp/tasks/nonexistent-id/llm/summary/stream",
            json={"provider": "free"},
        )
        assert response.status_code == 404


# ============================================================
# SSE format validation
# ============================================================
class TestSSEFormat:
    def test_content_type_is_event_stream(
        self, fastapi_client, ready_task, stub_llm_cache,
    ):
        """Content-Type ответа — text/event-stream."""
        response = fastapi_client.post(
            f"/dtp/tasks/{ready_task.id}/llm/ask/stream",
            json={"question": "тест вопроса", "provider": "free"},
        )
        ct = response.headers.get("content-type", "")
        assert "text/event-stream" in ct, f"expected text/event-stream, got {ct}"

    def test_events_have_correct_structure(
        self, fastapi_client, ready_task, stub_llm_cache,
    ):
        """Каждое SSE-событие имеет event: и data: строки, разделено \n\n."""
        response = fastapi_client.post(
            f"/dtp/tasks/{ready_task.id}/llm/ask/stream",
            json={"question": "тест вопроса", "provider": "free"},
        )

        # Должны найти "event: delta" и "event: done"
        assert "event: delta" in response.text
        assert "event: done" in response.text
        # data: должна присутствовать
        assert "data: Mock LLM" in response.text
