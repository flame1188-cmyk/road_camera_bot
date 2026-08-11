"""
Wave 3 — End-to-end lifecycle задачи через FastAPI TestClient.

В отличие от test_routes.py (который тестирует эндпоинты с замоканным
execute_task), здесь мы тестируем ПОЛНЫЙ жизненный цикл:

  POST /dtp/tasks → polling GET /dtp/tasks/{id} → task.status=done
  → POST /dtp/tasks/{id}/llm/summary → polling → текст резюме

execute_task выполняется реально (с замоканными bot/parser/analytics/
excel_generator/report_generator). Это позволяет поймать регрессии в
сложной логике склейки слоёв.

Маркированы @pytest.mark.slow, потому что включают asyncio sleep для
ожидания фоновой задачи (~100-300 ms каждый).
"""
from __future__ import annotations

import asyncio
import sys
import time
import types
from pathlib import Path
from typing import Any

import pytest

from tests.integration._gibdd_stubs import (
    install_stubs,
    make_minimal_cards,
)
from backend.services import _imports  # для патчей _PROJECT_ROOT/_import_module


# ============================================================
# Helpers
# ============================================================
async def _wait_for_status(
    client, task_id: str, target_status: str, max_sec: float = 5.0
):
    """Poll'им GET /dtp/tasks/{id} пока status не станет target_status."""
    deadline = time.monotonic() + max_sec
    last_body = None
    while time.monotonic() < deadline:
        response = client.get(f"/dtp/tasks/{task_id}")
        assert response.status_code == 200, f"poll failed: {response.status_code}"
        last_body = response.json()
        if last_body["status"] == target_status:
            return last_body
        await asyncio.sleep(0.05)
    raise AssertionError(
        f"Task {task_id} not reached status '{target_status}' in {max_sec}s "
        f"(last: {last_body})"
    )


@pytest.fixture
def stub_repository(monkeypatch):
    """Подменяет backend.db.repository для всех тестов в этом файле."""
    fake_repo = types.ModuleType("repository")
    async def fake_save_task(task):
        return None
    async def fake_log_access(**kwargs):
        return None
    fake_repo.save_task = fake_save_task
    fake_repo.log_access = fake_log_access
    monkeypatch.setitem(sys.modules, "backend.db.repository", fake_repo)


@pytest.fixture
def stub_db_connection_not_ready(monkeypatch):
    """is_db_ready() → False, чтобы get_task_async не падал."""
    fake_db = types.ModuleType("connection")
    fake_db.is_db_ready = lambda: False
    monkeypatch.setitem(sys.modules, "backend.db.connection", fake_db)


@pytest.fixture(autouse=True)
def _enable_real_execute_task(monkeypatch):
    """В отличие от test_routes.py, мы НЕ патчим execute_task.

    Тестируем полный pipeline.
    """
    # В test_routes.py есть autouse fixture disable_execute_task,
    # но она применяется только в том файле. Здесь выполняем реальный execute_task.
    pass


# ============================================================
# Lifecycle: create → poll → done → list files
# ============================================================
@pytest.mark.slow
class TestTaskLifecycleE2E:
    def test_full_lifecycle_structured_mode(
        self, fastapi_client, clear_in_memory_tasks, monkeypatch,
        stub_repository, stub_db_connection_not_ready, tmp_path,
    ):
        """Полный lifecycle: POST → polling → DONE → GET files."""
        from backend.services import gibdd_service

        # Файлы должны писаться в tmp_path
        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        cards = make_minimal_cards(3)
        install_stubs(monkeypatch, cards=cards, prev_cards=[])

        # 1. Создаём задачу
        create_resp = fastapi_client.post(
            "/dtp/tasks",
            json={
                "region_code": "1101",
                "region_name": "Тестовый регион",
                "dat_list": ["5.2025"],
                "period_label": "Май 2025",
            },
        )
        assert create_resp.status_code == 200
        task_id = create_resp.json()["task_id"]
        assert len(task_id) == 12

        # 2. Poll'им до DONE
        import asyncio as _a
        loop = _a.new_event_loop()
        try:
            body = loop.run_until_complete(
                _wait_for_status(fastapi_client, task_id, "done", max_sec=5.0)
            )
        finally:
            loop.close()

        assert body["status"] == "done"
        assert body["progress"] == 100
        assert body["total_dtp"] == 3
        # 1 погибший (карточка i=1)
        assert body["total_dead"] == 1
        # 2 раненых (i=0, i=2)
        assert body["total_injured"] == 2
        assert body["region_code"] == "1101"
        assert body["region_name"] == "Тестовый регион"
        assert body["period"] == "Май 2025"
        # analytics должны быть посчитаны
        assert body["analytics"] is not None
        # error пустой
        assert body["error"] is None

        # 3. GET /dtp/tasks/{id}/files — должны быть 3 файла
        files_resp = fastapi_client.get(f"/dtp/tasks/{task_id}/files")
        assert files_resp.status_code == 200
        files = files_resp.json()
        file_types = {f["type"] for f in files}
        assert "dtp_cards" in file_types
        assert "dtp_participants" in file_types
        assert "map_html" in file_types

    def test_lifecycle_text_mode_with_real_parser(
        self, fastapi_client, clear_in_memory_tasks, monkeypatch,
        stub_repository, stub_db_connection_not_ready, tmp_path,
    ):
        """Text mode с настоящим user_request_parser (регион из builtin)."""
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        # НЕ патчим _import_module полностью — только bot, gibdd_parser, etc.
        # user_request_parser и regions_builtin остаются настоящими
        cards = make_minimal_cards(2)
        install_stubs(monkeypatch, cards=cards, prev_cards=[])

        # Используем валидный запрос — "Вологодская область за 2025 год"
        create_resp = fastapi_client.post(
            "/dtp/tasks",
            json={"query": "Вологодская область за 2025 год"},
        )
        assert create_resp.status_code == 200
        body = create_resp.json()
        # Должен распознать регион и период
        assert body["region_code"]  # не пустой
        assert "2025" in body["period"]

        task_id = body["task_id"]

        # Poll'им
        import asyncio as _a
        loop = _a.new_event_loop()
        try:
            final = loop.run_until_complete(
                _wait_for_status(fastapi_client, task_id, "done", max_sec=5.0)
            )
        finally:
            loop.close()

        assert final["status"] == "done"
        assert final["total_dtp"] == 2

    def test_failed_task_returns_error_in_response(
        self, fastapi_client, clear_in_memory_tasks, monkeypatch,
        stub_repository, stub_db_connection_not_ready, tmp_path,
    ):
        """Если bot вернул пустой cards → task FAILED, error в ответе."""
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        install_stubs(
            monkeypatch,
            cards=[],
            bot_errors=["API timeout", "stat.gibdd.ru unavailable"],
        )

        create_resp = fastapi_client.post(
            "/dtp/tasks",
            json={
                "region_code": "1101",
                "region_name": "Регион",
                "dat_list": ["5.2025"],
                "period_label": "Май 2025",
            },
        )
        task_id = create_resp.json()["task_id"]

        import asyncio as _a
        loop = _a.new_event_loop()
        try:
            final = loop.run_until_complete(
                _wait_for_status(fastapi_client, task_id, "failed", max_sec=5.0)
            )
        finally:
            loop.close()

        assert final["status"] == "failed"
        assert "Не удалось получить данные" in final["error"]
        assert "API timeout" in final["error"]


# ============================================================
# Lifecycle: LLM summary после done
# ============================================================
@pytest.mark.slow
class TestLlmSummaryLifecycleE2E:
    def test_llm_summary_polling(
        self, fastapi_client, clear_in_memory_tasks, monkeypatch,
        stub_repository, stub_db_connection_not_ready, tmp_path,
    ):
        """POST /llm/summary → poll → DONE с текстом."""
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        cards = make_minimal_cards(2)
        prev_cards = make_minimal_cards(3)
        install_stubs(
            monkeypatch,
            cards=cards,
            prev_cards=prev_cards,
            llm_answer="Анализ: ДТП выросло на 50%.",
        )

        # Создаём задачу
        create_resp = fastapi_client.post(
            "/dtp/tasks",
            json={
                "region_code": "1101",
                "region_name": "Регион",
                "dat_list": ["5.2025"],
                "period_label": "Май 2025",
            },
        )
        task_id = create_resp.json()["task_id"]

        # Ждём done
        import asyncio as _a
        loop = _a.new_event_loop()
        try:
            loop.run_until_complete(
                _wait_for_status(fastapi_client, task_id, "done", max_sec=5.0)
            )

            # 3. POST /llm/summary — запускает async генерацию
            summary_resp = fastapi_client.post(
                f"/dtp/tasks/{task_id}/llm/summary",
                json={"provider": "free"},
            )
            assert summary_resp.status_code == 200
            state = summary_resp.json()["state"]
            assert state["status"] in ("idle", "running")

            # 4. Poll'им GET /llm/summary до done
            deadline = time.monotonic() + 5.0
            final_summary = None
            while time.monotonic() < deadline:
                get_resp = fastapi_client.get(f"/dtp/tasks/{task_id}/llm/summary")
                assert get_resp.status_code == 200
                body = get_resp.json()
                if body["state"]["status"] == "done":
                    final_summary = body
                    break
                if body["state"]["status"] == "failed":
                    raise AssertionError(f"LLM summary failed: {body['state'].get('error')}")
                # async sleep
                loop.run_until_complete(_a.sleep(0.05))

            assert final_summary is not None, "LLM summary не завершилась за 5 сек"
            assert final_summary["result"] is not None
            assert "Анализ" in final_summary["result"]["text"]
            assert final_summary["result"]["provider"] == "free"
        finally:
            loop.close()

    def test_llm_summary_already_done_returns_cached(
        self, fastapi_client, clear_in_memory_tasks, monkeypatch,
        stub_repository, stub_db_connection_not_ready, tmp_path,
    ):
        """Повторный POST /llm/summary с тем же provider — возвращает готовое."""
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        cards = make_minimal_cards(2)
        install_stubs(
            monkeypatch, cards=cards, prev_cards=[],
            llm_answer="Первое саммари.",
        )

        # Создаём + ждём done
        create_resp = fastapi_client.post(
            "/dtp/tasks",
            json={
                "region_code": "1101", "region_name": "Рег",
                "dat_list": ["5.2025"], "period_label": "Май 2025",
            },
        )
        task_id = create_resp.json()["task_id"]

        import asyncio as _a
        loop = _a.new_event_loop()
        try:
            loop.run_until_complete(
                _wait_for_status(fastapi_client, task_id, "done", max_sec=5.0)
            )

            # Первый запуск summary
            first_resp = fastapi_client.post(
                f"/dtp/tasks/{task_id}/llm/summary",
                json={"provider": "free"},
            )
            assert first_resp.status_code == 200

            # Ждём done
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                get_resp = fastapi_client.get(f"/dtp/tasks/{task_id}/llm/summary")
                body = get_resp.json()
                if body["state"]["status"] == "done":
                    break
                loop.run_until_complete(_a.sleep(0.05))

            # Второй POST — должен вернуть готовое (status=done)
            second_resp = fastapi_client.post(
                f"/dtp/tasks/{task_id}/llm/summary",
                json={"provider": "free"},
            )
            assert second_resp.status_code == 200
            body = second_resp.json()
            assert body["state"]["status"] == "done"
            assert body["result"] is not None
            assert body["result"]["text"] == "Первое саммари."
        finally:
            loop.close()


# ============================================================
# Lifecycle: QA history через эндпоинты
# ============================================================
@pytest.mark.slow
class TestQaHistoryE2E:
    def test_qa_ask_and_history(
        self, fastapi_client, clear_in_memory_tasks, monkeypatch,
        stub_repository, stub_db_connection_not_ready, tmp_path,
    ):
        """POST /llm/ask → сохраняется в /llm/qa-history."""
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        cards = make_minimal_cards(2)
        prev_cards = make_minimal_cards(3)
        install_stubs(
            monkeypatch, cards=cards, prev_cards=prev_cards,
            llm_answer="Ответ на вопрос.",
        )

        # Создаём задачу и ждём done
        create_resp = fastapi_client.post(
            "/dtp/tasks",
            json={
                "region_code": "1101", "region_name": "Рег",
                "dat_list": ["5.2025"], "period_label": "Май 2025",
            },
        )
        task_id = create_resp.json()["task_id"]

        import asyncio as _a
        loop = _a.new_event_loop()
        try:
            loop.run_until_complete(
                _wait_for_status(fastapi_client, task_id, "done", max_sec=5.0)
            )

            # История пуста изначально
            hist_resp = fastapi_client.get(f"/dtp/tasks/{task_id}/llm/qa-history")
            assert hist_resp.status_code == 200
            assert hist_resp.json() == []

            # Задаём вопрос
            ask_resp = fastapi_client.post(
                f"/dtp/tasks/{task_id}/llm/ask",
                json={"question": "Где больше всего ДТП?", "provider": "free"},
            )
            assert ask_resp.status_code == 200
            body = ask_resp.json()
            assert body["ok"] is True
            assert body["answer"] == "Ответ на вопрос."

            # История содержит 1 запись
            hist_resp2 = fastapi_client.get(f"/dtp/tasks/{task_id}/llm/qa-history")
            assert hist_resp2.status_code == 200
            history = hist_resp2.json()
            assert len(history) == 1
            assert history[0]["question"] == "Где больше всего ДТП?"
            assert history[0]["answer"] == "Ответ на вопрос."
            assert history[0]["provider"] == "free"
        finally:
            loop.close()
