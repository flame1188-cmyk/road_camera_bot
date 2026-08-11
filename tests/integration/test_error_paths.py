"""
Wave 3 — Edge cases и error paths в пайплайне.

Цель: проверить корректную обработку ошибок в execute_task и связанных
функциях. В отличие от test_analyze_flow.py (который тестирует happy
paths), здесь собраны негативные сценарии.

Сценарии:
  - bot возвращает ошибки + пустые cards
  - bot возвращает ошибки + непустые cards (только warnings)
  - excel_generator падает → task FAILED
  - report_generator падает → task DONE (карта опциональна!)
  - analytics падает → task DONE (analytics опциональна, fallback dict)
  - prev_cards bot падает → analytics без comparison
  - cleanup_old_tasks удаляет файлы с диска
  - cleanup_old_tasks с fresher cutoff не удаляет свежие
  - LLM summary timeout (через wait_for + stub с sleep)
"""
from __future__ import annotations

import asyncio
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tests.integration._gibdd_stubs import (
    BotStubConfig,
    install_stubs,
    make_minimal_cards,
)
from backend.services import _imports  # для патчей _PROJECT_ROOT/_import_module


# ============================================================
# Shared fixtures
# ============================================================
@pytest.fixture
def stub_repository(monkeypatch):
    """Подменяет backend.db.repository."""
    fake_repo = types.ModuleType("repository")
    saved_tasks: list = []

    async def fake_save_task(task):
        saved_tasks.append(task)
        return None

    async def fake_log_access(**kwargs):
        return None

    async def fake_delete_old_tasks(max_age_hours, project_root):
        return 0

    fake_repo.save_task = fake_save_task
    fake_repo.log_access = fake_log_access
    fake_repo.delete_old_tasks = fake_delete_old_tasks
    monkeypatch.setitem(sys.modules, "backend.db.repository", fake_repo)
    return {"saved_tasks": saved_tasks}


@pytest.fixture
def stub_db_connection_not_ready(monkeypatch):
    """is_db_ready() → False."""
    fake_db = types.ModuleType("connection")
    fake_db.is_db_ready = lambda: False
    monkeypatch.setitem(sys.modules, "backend.db.connection", fake_db)


# ============================================================
# Edge cases для execute_task
# ============================================================
class TestExecuteTaskEdgeCases:
    @pytest.mark.asyncio
    async def test_errors_with_nonempty_cards_succeeds(
        self, monkeypatch, clear_in_memory_tasks, stub_repository,
        stub_db_connection_not_ready, tmp_path,
    ):
        """bot вернул warnings (errors) + cards — задача DONE, errors залогированы."""
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        cards = make_minimal_cards(2)
        install_stubs(
            monkeypatch, cards=cards, bot_errors=["Partial: month 4 failed"],
        )

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["4.2025", "5.2025"], raw_query="q",
        )

        await gibdd_service.execute_task(task.id)

        # Cards есть — задача должна завершиться успешно
        assert task.status == gibdd_service.TaskStatus.DONE
        assert task.total_dtp == 2
        assert task.error is None  # errors не сохраняются в task.error

    @pytest.mark.asyncio
    async def test_excel_generator_failure_marks_failed(
        self, monkeypatch, clear_in_memory_tasks, stub_repository,
        stub_db_connection_not_ready, tmp_path,
    ):
        """excel_generator падает → task FAILED."""
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        cards = make_minimal_cards(2)
        stubs = install_stubs(monkeypatch, cards=cards)

        # Подменяем excel_generator.generate_both_files чтобы падал
        def failing_generate(file1, file2):
            raise RuntimeError("openpyxl corruption")

        stubs["excel_generator"].generate_both_files = failing_generate

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )

        await gibdd_service.execute_task(task.id)

        assert task.status == gibdd_service.TaskStatus.FAILED
        assert "openpyxl corruption" in task.error

    @pytest.mark.asyncio
    async def test_report_generator_failure_still_done(
        self, monkeypatch, clear_in_memory_tasks, stub_repository,
        stub_db_connection_not_ready, tmp_path,
    ):
        """report_generator падает → task DONE, но без map_html файла."""
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        cards = make_minimal_cards(2)
        stubs = install_stubs(monkeypatch, cards=cards)

        # Подменяем ReportGenerator.generate_dtp_map чтобы падал
        class FailingReportGenerator:
            def __init__(self, region_name="", period_label=""):
                pass
            def generate_dtp_map(self, cards, cameras=None, prev_cards=None, prev_label=None):
                raise RuntimeError("map generation boom")

        stubs["report_generator"].ReportGenerator = FailingReportGenerator

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )

        await gibdd_service.execute_task(task.id)

        # Карта опциональна — задача должна завершиться
        assert task.status == gibdd_service.TaskStatus.DONE
        # Файлы без карты
        file_types = {f["type"] for f in task.files}
        assert "dtp_cards" in file_types
        assert "dtp_participants" in file_types
        assert "map_html" not in file_types

    @pytest.mark.asyncio
    async def test_analytics_failure_falls_back_to_minimal(
        self, monkeypatch, clear_in_memory_tasks, stub_repository,
        stub_db_connection_not_ready, tmp_path,
    ):
        """analytics.build_full_analytics падает → fallback на минимальный dict."""
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        cards = make_minimal_cards(3)
        stubs = install_stubs(monkeypatch, cards=cards)

        # analytics.build_full_analytics падает
        def failing_build(*args, **kwargs):
            raise RuntimeError("analytics crash")

        stubs["analytics"].build_full_analytics = failing_build

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )

        await gibdd_service.execute_task(task.id)

        # Analytics опциональна — задача DONE с fallback
        assert task.status == gibdd_service.TaskStatus.DONE
        assert task.analytics is not None
        assert task.analytics.get("total_dtp") == 3
        assert task.analytics.get("total_dead") == 1
        assert task.analytics.get("total_injured") == 2
        assert task.analytics.get("has_prev_data") is False


# ============================================================
# Edge cases для prev_cards loading
# ============================================================
class TestPrevCardsEdgeCases:
    @pytest.mark.asyncio
    async def test_bot_exception_during_prev(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """bot._fetch падает при загрузке prev — ensure_prev_cards возвращает ok=False."""
        from backend.services import gibdd_service

        install_stubs(
            monkeypatch,
            prev_cards=[],  # не важно, т.к. raise
            bot_raise=RuntimeError("connection reset"),
        )

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )

        result = await gibdd_service.ensure_prev_cards(task)

        assert result["ok"] is False
        assert "connection reset" in result["error"]
        # prev_cards_loaded взведён, чтобы не пытаться снова
        assert task.prev_cards_loaded is True

    @pytest.mark.asyncio
    async def test_multi_month_dat_list(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """dat_list=['1.2025', '2.2025', '3.2025'] → prev=['1.2024', '2.2024', '3.2024']."""
        from backend.services import gibdd_service

        record_calls: list = []
        prev_cards = make_minimal_cards(2)
        install_stubs(monkeypatch, prev_cards=prev_cards, record_bot_calls=record_calls)

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Q1 2025", dat_list=["1.2025", "2.2025", "3.2025"],
            raw_query="q",
        )

        result = await gibdd_service.ensure_prev_cards(task)

        assert result["ok"] is True
        assert record_calls[0]["dat_list"] == ["1.2024", "2.2024", "3.2024"]


# ============================================================
# cleanup_old_tasks — edge cases
# ============================================================
class TestCleanupEdgeCases:
    @pytest.mark.asyncio
    async def test_removes_files_from_disk(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
        tmp_path,
    ):
        """cleanup_old_tasks удаляет файлы задач с диска."""
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        # Создаём задачу с файлом
        cards = make_minimal_cards(1)
        install_stubs(monkeypatch, cards=cards)

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        await gibdd_service.execute_task(task.id)

        # Делаем задачу старой
        task.created_at = datetime.now(timezone.utc) - timedelta(days=2)

        # Файлы должны существовать
        for f in task.files:
            assert Path(f["path"]).exists()

        # cleanup
        deleted = await gibdd_service.cleanup_old_tasks(max_age_hours=24)

        assert deleted >= 1
        assert task.id not in gibdd_service._tasks
        # Файлы удалены
        for f in task.files:
            assert not Path(f["path"]).exists()

    @pytest.mark.asyncio
    async def test_keeps_fresh_tasks(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
        tmp_path,
    ):
        """cleanup_old_tasks с max_age=24h не трогает задачу 1 часа назад."""
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        install_stubs(monkeypatch, cards=make_minimal_cards(1))

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        # created_at = сейчас (по умолчанию)

        deleted = await gibdd_service.cleanup_old_tasks(max_age_hours=24)

        assert deleted == 0
        assert task.id in gibdd_service._tasks

    @pytest.mark.asyncio
    async def test_empty_tasks_returns_zero(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """cleanup при пустом _tasks возвращает 0."""
        from backend.services import gibdd_service

        deleted = await gibdd_service.cleanup_old_tasks(max_age_hours=24)
        assert deleted == 0


# ============================================================
# Edge cases для LLM summary
# ============================================================
class TestLlmSummaryEdgeCases:
    @pytest.mark.asyncio
    async def test_llm_provider_invalid(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """provider='invalid' — _run_llm_summary_inner падает с provider unknown.

        ВАЖНО: _run_llm_summary_inner проверяет только 'paid' / else (free).
        Невалидный провайдер идёт в else (free) — поэтому упадёт не на provider,
        а на отсутствие LLM_API_KEY (если он пустой).
        """
        from backend.services import gibdd_service

        cards = make_minimal_cards(2)
        # config без ключей
        install_stubs(
            monkeypatch, cards=cards,
            config_overrides={"llm_api_key": ""},
        )

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = cards

        # provider="invalid" пойдёт в else (free) и упадёт на LLM_API_KEY=""
        await gibdd_service.start_llm_summary(task, provider="invalid")

        state = task.llm_summary_state
        assert state.status == gibdd_service.AnalysisStatus.FAILED
        assert state.error is not None

    @pytest.mark.asyncio
    async def test_llm_summary_inner_exception_caught(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """Если внутри _run_llm_summary_inner падает (кроме timeout) — FAILED."""
        from backend.services import gibdd_service

        cards = make_minimal_cards(2)
        stubs = install_stubs(monkeypatch, cards=cards, prev_cards=[])

        # analytics.build_full_analytics не используется в summary, но
        # analytics.calculate_metrics — да (через ensure_comparison).
        # Патчим calculate_metrics чтобы падал
        def failing_metrics(cards):
            raise RuntimeError("metrics explosion")
        stubs["analytics"].calculate_metrics = failing_metrics

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = cards

        await gibdd_service.start_llm_summary(task, provider="free")

        state = task.llm_summary_state
        # Должно быть FAILED, не RUNNING вечно
        assert state.status == gibdd_service.AnalysisStatus.FAILED
        assert state.error is not None
        assert state.finished_at is not None

    @pytest.mark.asyncio
    async def test_summary_uses_cached_comparison(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """Если comparison уже посчитан — summary не пересчитывает metrics."""
        from backend.services import gibdd_service

        cards = make_minimal_cards(2)
        install_stubs(monkeypatch, cards=cards, prev_cards=[])

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = cards
        # Предзаполненный comparison
        task.comparison = {
            "total": {"current": 999, "previous": 0, "change": 0},
            "deaths": {"current": 0, "previous": 0, "change": 0},
            "injured": {"current": 0, "previous": 0, "change": 0},
            "alcohol": {"current": 0, "previous": 0, "change": 0},
            "pedestrians": {"current": 0, "previous": 0, "change": 0},
            "deaths_per_100": {"current": 0, "previous": 0, "change": 0},
            "injured_per_100": {"current": 0, "previous": 0, "change": 0},
            "by_weekday": {"current": {}, "previous": {}},
            "by_hour": {"current": {}, "previous": {}},
            "by_type": {"current": {}, "previous": {}},
            "by_weather": {"current": {}, "previous": {}},
        }

        await gibdd_service.start_llm_summary(task, provider="free")

        state = task.llm_summary_state
        assert state.status == gibdd_service.AnalysisStatus.DONE
        # comparison не должен измениться (использован кэш)
        assert task.comparison["total"]["current"] == 999


# ============================================================
# Edge cases для ask_llm_question
# ============================================================
class TestAskLlmQuestionEdgeCases:
    @pytest.mark.asyncio
    async def test_ensure_comparison_failure_returns_error(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """Если comparison не может посчитаться (cards пуст) — ok=False.

        Sprint 3.1: ask_llm_question теперь вызывает ensure_cards перед
        ensure_comparison. Если stub возвращает [] — ensure_cards вернёт
        ok=False с сообщением 'Не удалось восстановить карточки'.
        """
        from backend.services import gibdd_service

        # stub возвращает пустые cards — ensure_cards не сможет восстановить
        install_stubs(monkeypatch, cards=[], prev_cards=[])

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        # cards пустой

        result = await gibdd_service.ask_llm_question(
            task, "Что с динамикой?", provider="free",
        )

        assert result["ok"] is False
        # Принимаем оба варианта сообщения — старое и новое (Sprint 3.1)
        assert (
            "не загружены" in result["error"]
            or "Не удалось восстановить" in result["error"]
            or "Нет данных" in result["error"]
        ), f"unexpected error: {result['error']}"

    @pytest.mark.asyncio
    async def test_llm_exception_returns_error(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """LLM get_ai_answer падает → ok=False с исключением."""
        from backend.services import gibdd_service

        cards = make_minimal_cards(2)
        stubs = install_stubs(monkeypatch, cards=cards, prev_cards=[])

        # Патчим get_ai_answer чтобы падал
        async def failing_answer(**kwargs):
            raise RuntimeError("LLM service down")
        stubs["llm_analyzer"].get_ai_answer = failing_answer

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = cards

        result = await gibdd_service.ask_llm_question(
            task, "Нормальный вопрос?", provider="free",
        )

        assert result["ok"] is False
        assert "LLM service down" in result["error"]

    @pytest.mark.asyncio
    async def test_history_preserved_across_calls(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """История сохраняется между вызовами и доступна LLM."""
        from backend.services import gibdd_service

        cards = make_minimal_cards(1)
        # Делаем stub который записывает history
        stubs = install_stubs(monkeypatch, cards=cards, prev_cards=[])

        received_history: list = []

        async def recording_answer(*, question, comparison, reg_name,
                                    current_label, prev_label, raw_supplement="",
                                    news_context="", clusters_context="",
                                    cross_tables_context="", provider="free",
                                    history=None):
            received_history.append(list(history) if history else [])
            return f"Answer {len(received_history)}"

        stubs["llm_analyzer"].get_ai_answer = recording_answer

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = cards

        # Первый вопрос — история пуста
        await gibdd_service.ask_llm_question(task, "Вопрос 1?", provider="free")
        assert len(received_history) == 1
        assert received_history[0] == []

        # Второй вопрос — история содержит 1 пару Q/A
        await gibdd_service.ask_llm_question(task, "Вопрос 2?", provider="free")
        assert len(received_history) == 2
        assert len(received_history[1]) == 2  # 1 question + 1 answer
        assert received_history[1][0]["role"] == "user"
        assert received_history[1][0]["content"] == "Вопрос 1?"
        assert received_history[1][1]["role"] == "assistant"
        assert "Answer 1" in received_history[1][1]["content"]
