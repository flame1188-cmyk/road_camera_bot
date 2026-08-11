"""
Wave 3 — интеграционные тесты полного пайплайна execute_task.

Цель: покрыть строки 533-841 в gibdd_service.py (главный pipeline):
  - FETCHING → вызов bot._fetch_cards_for_period
  - PARSING → gibdd_parser.build_file1_data + build_file2_data (через to_thread)
  - ANALYTICS → analytics.build_full_analytics + ensure_prev_cards
  - GENERATING → excel_generator.generate_both_files + report_generator
  - DONE → task.files содержит 3 файла (cards, participants, map)

Все внешние зависимости (bot, gibdd_parser, analytics, excel_generator,
report_generator, camera_cache, config) подменяются stub'ами через
глобальный smart_import. Тестируется «склейка» слоёв в gibdd_service,
а не реальные вычисления.

Дополнительно покрываются:
  - execute_task: outer Semaphore wrapper + exception → FAILED status
  - _execute_task_impl: каждая стадия обновляет status/progress
  - persist (через stub repository)
"""
from __future__ import annotations

import asyncio
import sys
import types
from datetime import datetime, timezone
from typing import Any

import pytest

from tests.integration._gibdd_stubs import (
    BotStubConfig,
    install_stubs,
    make_minimal_cards,
)
from backend.services import _imports  # для патчей _PROJECT_ROOT/_import_module


# ============================================================
# Fixtures специфичные для этого файла
# ============================================================
@pytest.fixture
def stub_repository(monkeypatch):
    """Подменяет backend.db.repository.save_task / log_access / etc."""
    fake_repo = types.ModuleType("repository")

    saved_tasks: list = []

    async def fake_save_task(task):
        saved_tasks.append(task)
        return None

    async def fake_log_access(**kwargs):
        return None

    fake_repo.save_task = fake_save_task
    fake_repo.log_access = fake_log_access
    monkeypatch.setitem(sys.modules, "backend.db.repository", fake_repo)
    return {"saved_tasks": saved_tasks}


@pytest.fixture
def stub_db_connection_not_ready(monkeypatch):
    """Делает is_db_ready() → False, чтобы get_task_async не лез в БД."""
    fake_db = types.ModuleType("connection")
    fake_db.is_db_ready = lambda: False
    monkeypatch.setitem(sys.modules, "backend.db.connection", fake_db)


# ============================================================
# Happy path — полный пайплайн
# ============================================================
class TestExecuteTaskHappyPath:
    @pytest.mark.asyncio
    async def test_full_pipeline_done(
        self, monkeypatch, clear_in_memory_tasks, stub_repository, stub_db_connection_not_ready,
        tmp_path,
    ):
        """Полный happy path: PENDING → FETCHING → PARSING → ANALYTICS → GENERATING → DONE."""
        from backend.services import gibdd_service

        # Подменяем _PROJECT_ROOT на временный, чтобы файлы писались в tmp
        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        cards = make_minimal_cards(3)
        install_stubs(monkeypatch, cards=cards, prev_cards=[])

        # Создаём задачу
        task = gibdd_service.create_task(
            user_id=1,
            region_code="1101",
            region_name="Тестовый регион",
            period_label="Май 2025",
            dat_list=["5.2025"],
            raw_query="тест",
        )

        # Выполняем
        await gibdd_service.execute_task(task.id)

        # Проверяем финальный статус
        assert task.status == gibdd_service.TaskStatus.DONE
        assert task.progress == 100
        assert task.error is None
        assert task.total_dtp == 3
        # В карточках: 1 погиб (i=1), остальные 0 → 1 погибший всего
        assert task.total_dead == 1
        # 2 раненых (i=0, i=2), i=1 — 0 → 2 раненых
        assert task.total_injured == 2
        assert len(task.cards) == 3
        # analytics должны быть посчитаны
        assert task.analytics is not None
        assert task.analytics.get("current_metrics", {}).get("total") == 3

        # Файлы: dtp_cards, dtp_participants, map_html
        file_types = {f["type"] for f in task.files}
        assert "dtp_cards" in file_types
        assert "dtp_participants" in file_types
        assert "map_html" in file_types

        # Все файлы должны существовать на диске
        for f in task.files:
            from pathlib import Path
            assert Path(f["path"]).exists(), f"Файл {f['type']} не записан"

    @pytest.mark.asyncio
    async def test_pipeline_transitions_status(
        self, monkeypatch, clear_in_memory_tasks, stub_repository, stub_db_connection_not_ready,
        tmp_path,
    ):
        """Проверяем, что статусы обновляются в правильном порядке."""
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        cards = make_minimal_cards(2)
        install_stubs(monkeypatch, cards=cards)

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )

        # Подменяем _persist, чтобы записывать статусы в момент сохранения
        seen_statuses: list = []
        original_persist = None

        async def recording_persist():
            seen_statuses.append(task.status)

        # Патчим save_task в repository, чтобы записывать статус
        fake_repo = sys.modules["backend.db.repository"]
        original_save = fake_repo.save_task

        async def recording_save(t):
            seen_statuses.append(t.status)
            await original_save(t)

        fake_repo.save_task = recording_save

        await gibdd_service.execute_task(task.id)

        # Статусы идут в порядке: FETCHING → PARSING → ANALYTICS → GENERATING → DONE
        # (PENDING не persist'ится, потому что create_task не вызывает save_task для in-memory)
        status_values = [s.value for s in seen_statuses]
        assert "fetching" in status_values
        assert "parsing" in status_values
        assert "analytics" in status_values
        assert "generating" in status_values
        assert "done" in status_values
        # Порядок сохранён
        assert status_values.index("fetching") < status_values.index("parsing")
        assert status_values.index("parsing") < status_values.index("analytics")
        assert status_values.index("analytics") < status_values.index("generating")
        assert status_values.index("generating") < status_values.index("done")


# ============================================================
# Error paths
# ============================================================
class TestExecuteTaskErrorPaths:
    @pytest.mark.asyncio
    async def test_empty_cards_marks_failed(
        self, monkeypatch, clear_in_memory_tasks, stub_repository, stub_db_connection_not_ready,
        tmp_path,
    ):
        """Если bot вернул пустой cards → status=FAILED с понятной ошибкой."""
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        install_stubs(monkeypatch, cards=[], bot_errors=["API timeout"])

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )

        await gibdd_service.execute_task(task.id)

        assert task.status == gibdd_service.TaskStatus.FAILED
        assert "Не удалось получить данные" in task.error
        assert "API timeout" in task.error

    @pytest.mark.asyncio
    async def test_bot_raises_exception_marks_failed(
        self, monkeypatch, clear_in_memory_tasks, stub_repository, stub_db_connection_not_ready,
        tmp_path,
    ):
        """Если bot._fetch поднимает исключение → execute_task ловит и FAILED."""
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        install_stubs(
            monkeypatch,
            bot_raise=RuntimeError("Network unreachable"),
        )

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )

        await gibdd_service.execute_task(task.id)

        # execute_task ловит исключение в outer wrapper и ставит FAILED
        assert task.status == gibdd_service.TaskStatus.FAILED
        assert "Network unreachable" in task.error

    @pytest.mark.asyncio
    async def test_task_not_found_silently_returns(
        self, monkeypatch, clear_in_memory_tasks, stub_repository, stub_db_connection_not_ready,
    ):
        """execute_task с несуществующим id — не падает, просто выходит."""
        from backend.services import gibdd_service

        install_stubs(monkeypatch)

        # Не должно падать — просто return
        await gibdd_service.execute_task("несуществующий-id-0000")

        # Ничего не должно добавиться в _tasks
        assert "несуществующий-id-0000" not in gibdd_service._tasks


# ============================================================
# Тесты prev_cards loading (АППГ)
# ============================================================
class TestEnsurePrevCardsViaStubs:
    @pytest.mark.asyncio
    async def test_ensure_prev_cards_computes_year_minus_one(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """dat_list=['5.2025'] → bot._fetch вызывается с ['5.2024']."""
        from backend.services import gibdd_service

        record_calls: list = []
        prev_cards = make_minimal_cards(2)
        install_stubs(
            monkeypatch,
            prev_cards=prev_cards,
            record_bot_calls=record_calls,
        )

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )

        result = await gibdd_service.ensure_prev_cards(task)

        assert result["ok"] is True
        assert result["prev_label"] == "Май 2024"
        assert len(result["prev_cards"]) == 2
        # bot._fetch был вызван с прошлым годом
        assert len(record_calls) == 1
        assert record_calls[0]["dat_list"] == ["5.2024"]
        assert record_calls[0]["reg_code"] == "1101"
        # Кэш loaded flag взведён
        assert task.prev_cards_loaded is True

    @pytest.mark.asyncio
    async def test_ensure_prev_cards_skips_if_already_loaded(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """Если prev_cards_loaded=True — не делает повторный запрос."""
        from backend.services import gibdd_service

        record_calls: list = []
        install_stubs(monkeypatch, record_bot_calls=record_calls)

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.prev_cards = [{"existing": "card"}]
        task.prev_label = "Май 2024"
        task.prev_cards_loaded = True

        result = await gibdd_service.ensure_prev_cards(task)

        # bot не должен был вызываться
        assert len(record_calls) == 0
        assert result["ok"] is True
        assert result["prev_cards"] == [{"existing": "card"}]

    @pytest.mark.asyncio
    async def test_ensure_prev_cards_invalid_dat_list(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """Если dat_list нельзя распарсить — возвращает ok=False."""
        from backend.services import gibdd_service

        record_calls: list = []
        install_stubs(monkeypatch, record_bot_calls=record_calls)

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Период", dat_list=["некорректный"], raw_query="q",
        )

        result = await gibdd_service.ensure_prev_cards(task)

        assert result["ok"] is False
        assert "прошлый период" in result["error"].lower()
        assert len(record_calls) == 0
        assert task.prev_cards_loaded is True  # не повторяем

    @pytest.mark.asyncio
    async def test_ensure_prev_cards_bot_returns_empty(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """Если bot вернул пустой prev_cards — ok=False с понятной ошибкой."""
        from backend.services import gibdd_service

        install_stubs(monkeypatch, prev_cards=[])

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )

        result = await gibdd_service.ensure_prev_cards(task)

        assert result["ok"] is False
        assert "Нет данных" in result["error"]
        assert task.prev_cards_loaded is True
        assert task.prev_cards == []


# ============================================================
# Тесты ensure_comparison — расчёт сравнения
# ============================================================
class TestEnsureComparison:
    @pytest.mark.asyncio
    async def test_with_prev_data(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """Есть prev_cards → comparison содержит change для всех метрик."""
        from backend.services import gibdd_service

        cards = make_minimal_cards(3)
        prev_cards = make_minimal_cards(5)
        install_stubs(monkeypatch, cards=cards, prev_cards=prev_cards)

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = cards

        result = await gibdd_service.ensure_comparison(task)

        assert result["ok"] is True
        comp = result["comparison"]
        # current=3, prev=5 → change вычислен
        assert comp["total"]["current"] == 3
        assert comp["total"]["previous"] == 5
        # Comparison закэширован на task
        assert task.comparison is comp

    @pytest.mark.asyncio
    async def test_without_prev_data(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """Нет prev_cards → comparison формируется урезанным (previous=0)."""
        from backend.services import gibdd_service

        cards = make_minimal_cards(2)
        install_stubs(monkeypatch, cards=cards, prev_cards=[])

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = cards

        result = await gibdd_service.ensure_comparison(task)

        assert result["ok"] is True
        comp = result["comparison"]
        assert comp["total"]["current"] == 2
        assert comp["total"]["previous"] == 0
        assert comp["total"]["change"] == 0

    @pytest.mark.asyncio
    async def test_returns_cached_comparison(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """Повторный вызов возвращает закэшированный comparison без пересчёта."""
        from backend.services import gibdd_service

        cards = make_minimal_cards(2)
        install_stubs(monkeypatch, cards=cards, prev_cards=[])

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = cards

        # Первый вызов — считает
        result1 = await gibdd_service.ensure_comparison(task)
        assert result1["ok"] is True
        assert task.comparison is not None

        # Второй вызов — возвращает кэш
        result2 = await gibdd_service.ensure_comparison(task)
        assert result2["ok"] is True
        assert result2["comparison"] is task.comparison  # тот же объект

    @pytest.mark.asyncio
    async def test_empty_cards_returns_error(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """Если task.cards пустой — ok=False с понятной ошибкой."""
        from backend.services import gibdd_service

        install_stubs(monkeypatch)

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        # cards пустой

        result = await gibdd_service.ensure_comparison(task)

        assert result["ok"] is False
        assert "не загружены" in result["error"]


# ============================================================
# Тесты compute_point_stats
# ============================================================
class TestComputePointStats:
    @pytest.mark.asyncio
    async def test_happy_path(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """compute_point_stats возвращает структуру с current/prev."""
        from backend.services import gibdd_service

        cards = make_minimal_cards(3)
        prev_cards = make_minimal_cards(2)
        install_stubs(monkeypatch, cards=cards, prev_cards=prev_cards)

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = cards

        result = await gibdd_service.compute_point_stats(
            task=task, lat=59.22, lon=39.88, radius_m=500,
        )

        assert result["ok"] is True
        assert result["center"] == {"lat": 59.22, "lon": 39.88}
        assert result["radius_m"] == 500
        assert result["current_label"] == "Май 2025"
        assert result["current"]["total"] == 3  # все cards в радиусе (stub)
        assert result["current"]["cards_count"] == 3
        # prev есть
        assert result["prev"] is not None
        # Закэшировано
        assert task.last_point_stats is not None
        assert task.last_point_params == {"lat": 59.22, "lon": 39.88, "radius_m": 500}
        assert len(task.last_point_cards_current) == 3
        assert len(task.last_point_cards_prev) == 2

    @pytest.mark.asyncio
    async def test_empty_cards_returns_error(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """Если task.cards пустой и восстановить нельзя — ok=False.

        Sprint 3.1: ensure_cards пытается восстановить cards из cards_cache
        через _fetch_cards_for_period. Если stub возвращает [] — ensure_cards
        возвращает ok=False с сообщением 'Не удалось восстановить карточки'.
        """
        from backend.services import gibdd_service

        # stub возвращает пустой список cards — ensure_cards не сможет восстановить
        install_stubs(monkeypatch, cards=[], prev_cards=[])

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )

        result = await gibdd_service.compute_point_stats(
            task=task, lat=59.22, lon=39.88, radius_m=500,
        )

        assert result["ok"] is False
        # Сообщение может быть либо старое "не загружены" (если ensure_cards
        # не вызывался), либо новое "Не удалось восстановить" (Sprint 3.1).
        # Принимаем оба — главное, что ok=False.
        assert (
            "не загружены" in result["error"]
            or "Не удалось восстановить" in result["error"]
            or "Нет данных" in result["error"]
        ), f"unexpected error: {result['error']}"


# ============================================================
# Тесты start_llm_summary — happy + timeout + config
# ============================================================
class TestStartLlmSummary:
    @pytest.mark.asyncio
    async def test_happy_path_free_provider(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """start_llm_summary(free) — happy path, проходит все стадии."""
        from backend.services import gibdd_service

        cards = make_minimal_cards(2)
        prev_cards = make_minimal_cards(3)
        install_stubs(
            monkeypatch,
            cards=cards,
            prev_cards=prev_cards,
            llm_answer="Тестовое саммари от LLM",
        )

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = cards

        await gibdd_service.start_llm_summary(task, provider="free")

        state = task.llm_summary_state
        assert state.status == gibdd_service.AnalysisStatus.DONE
        assert state.progress == 100
        assert state.stage == "Готово"
        assert state.result is not None
        assert state.result["text"] == "Тестовое саммари от LLM"
        assert state.result["provider"] == "free"
        assert state.error is None
        assert state.finished_at is not None

    @pytest.mark.asyncio
    async def test_no_api_key_fails(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """Если LLM_API_KEY пустой — статус FAILED."""
        from backend.services import gibdd_service

        cards = make_minimal_cards(2)
        install_stubs(
            monkeypatch,
            cards=cards,
            config_overrides={"llm_api_key": ""},
        )

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = cards

        await gibdd_service.start_llm_summary(task, provider="free")

        state = task.llm_summary_state
        assert state.status == gibdd_service.AnalysisStatus.FAILED
        assert "не настроен" in state.error
        assert state.finished_at is not None

    @pytest.mark.asyncio
    async def test_paid_provider_no_key_fails(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """Если LLM_PAID_API_KEY пустой — статус FAILED для paid."""
        from backend.services import gibdd_service

        cards = make_minimal_cards(2)
        install_stubs(
            monkeypatch,
            cards=cards,
            config_overrides={"llm_paid_api_key": "", "llm_paid_api_url": ""},
        )

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = cards

        await gibdd_service.start_llm_summary(task, provider="paid")

        state = task.llm_summary_state
        assert state.status == gibdd_service.AnalysisStatus.FAILED
        assert "платный" in state.error.lower() or "paid" in state.error.lower()


# ============================================================
# Тесты ask_llm_question — дополнительно к существующим unit
# ============================================================
class TestAskLlmQuestionDeeper:
    @pytest.mark.asyncio
    async def test_happy_path_with_history(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """ask_llm_question happy path — история сохраняется."""
        from backend.services import gibdd_service

        cards = make_minimal_cards(2)
        prev_cards = make_minimal_cards(3)
        install_stubs(
            monkeypatch,
            cards=cards,
            prev_cards=prev_cards,
            llm_answer="Ответ на ваш вопрос",
        )

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = cards
        # Без comparison — пусть считает

        result = await gibdd_service.ask_llm_question(
            task, "Что с динамикой ДТП?", provider="free",
        )

        assert result["ok"] is True
        assert result["answer"] == "Ответ на ваш вопрос"
        assert result["provider"] == "free"
        # История должна быть сохранена
        assert len(task.llm_qa_history) == 1
        assert task.llm_qa_history[0]["question"] == "Что с динамикой ДТП?"
        assert task.llm_qa_history[0]["answer"] == "Ответ на ваш вопрос"

    @pytest.mark.asyncio
    async def test_history_capped_at_10(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """История Q&A ограничена 10 записями."""
        from backend.services import gibdd_service

        cards = make_minimal_cards(2)
        install_stubs(monkeypatch, cards=cards, prev_cards=[])

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = cards

        # 15 вопросов
        for i in range(15):
            await gibdd_service.ask_llm_question(
                task, f"Вопрос номер {i}?", provider="free",
            )

        assert len(task.llm_qa_history) == 10
        # Последние 10 сохранены
        assert task.llm_qa_history[-1]["question"] == "Вопрос номер 14?"
        assert task.llm_qa_history[0]["question"] == "Вопрос номер 5?"

    @pytest.mark.asyncio
    async def test_paid_provider_without_key_returns_error(
        self, monkeypatch, clear_in_memory_tasks, stub_db_connection_not_ready,
    ):
        """ask_llm_question paid без ключа — ok=False."""
        from backend.services import gibdd_service

        cards = make_minimal_cards(2)
        install_stubs(
            monkeypatch,
            cards=cards,
            config_overrides={"llm_paid_api_key": "", "llm_paid_api_url": ""},
        )

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = cards

        result = await gibdd_service.ask_llm_question(
            task, "Нормальный вопрос?", provider="paid",
        )

        assert result["ok"] is False
        assert "платный" in result["error"].lower() or "paid" in result["error"].lower()


# ============================================================
# get_llm_providers_status — дополнительно
# ============================================================
class TestGetLlmProvidersStatusExtra:
    def test_paid_status_depends_on_url_too(
        self, monkeypatch, clear_in_memory_tasks,
    ):
        """paid=True только если и KEY, и URL заданы."""
        from backend.services import gibdd_service

        install_stubs(
            monkeypatch,
            config_overrides={"llm_paid_api_key": "key", "llm_paid_api_url": ""},
        )

        status = gibdd_service.get_llm_providers_status()
        assert status["paid"] is False  # URL пустой

    def test_exception_returns_empty_status(
        self, monkeypatch, clear_in_memory_tasks,
    ):
        """Если _import_module падает — возвращаем дефолтный статус."""
        from backend.services import gibdd_service

        def raising_import(name):
            raise RuntimeError("import failed")

        # Патчим в _imports (источник) — service-модули используют
        # _imports._import_module() через атрибут модуля.
        monkeypatch.setattr(_imports, "_import_module", raising_import)
        monkeypatch.setattr(gibdd_service, "_import_module", raising_import)
        monkeypatch.setattr(_imports, "_import_module", raising_import)

        status = gibdd_service.get_llm_providers_status()
        assert status == {"free": False, "paid": False, "free_model": "", "paid_model": ""}
