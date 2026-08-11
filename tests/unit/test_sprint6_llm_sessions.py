"""
Sprint 6 — unit-тесты для LLM Sessions persistence.

Покрывает:
  1. save_llm_session — no-op при отсутствии БД (in-memory fallback)
  2. append_qa_entry — no-op при отсутствии БД
  3. load_llm_session — возвращает None при отсутствии БД
  4. _try_restore_llm_session — корректно восстанавливает summary + qa_history
  5. _try_restore_llm_session — no-op когда оба поля уже заполнены
  6. _try_restore_llm_session — no-op когда в БД нет записи

Тесты изолированы от реальной БД: патчат is_db_ready() и load_llm_session().
"""
import sys
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from enum import Enum

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

MINIAPP_ROOT = PROJECT_ROOT / "miniapp"
if str(MINIAPP_ROOT) not in sys.path:
    sys.path.insert(0, str(MINIAPP_ROOT))


# ============================================================
# Stubs для тестирования без реальной БД
# ============================================================

class StubAnalysisStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class StubAnalysisState:
    status: StubAnalysisStatus = StubAnalysisStatus.IDLE
    progress: int = 0
    stage: str = ""
    result: Optional[Any] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


@dataclass
class StubTask:
    """Минимальный stub Task — только поля, читаемые _try_restore_llm_session."""
    id: str = "test-task-001"
    user_id: int = 123456789
    llm_summary_state: StubAnalysisState = field(default_factory=StubAnalysisState)
    llm_qa_history: List[Dict[str, str]] = field(default_factory=list)


# ============================================================
# Тесты repository: in-memory fallback (no-op при отсутствии БД)
# ============================================================

class TestRepositoryNoDbFallback:
    """Все Sprint 6 функции должны быть no-op при is_db_ready() == False."""

    def test_save_llm_session_no_db(self, monkeypatch):
        """save_llm_session не падает и ничего не делает без БД."""
        from miniapp.backend.db import repository
        monkeypatch.setattr(repository, "is_db_ready", lambda: False)

        # Должно вернуться без исключения
        import asyncio
        asyncio.run(repository.save_llm_session(
            task_id="test",
            user_id=1,
            summary_text="test",
            summary_provider="free",
        ))

    def test_append_qa_entry_no_db(self, monkeypatch):
        """append_qa_entry не падает и ничего не делает без БД."""
        from miniapp.backend.db import repository
        monkeypatch.setattr(repository, "is_db_ready", lambda: False)

        import asyncio
        asyncio.run(repository.append_qa_entry(
            task_id="test",
            user_id=1,
            question="Q?",
            answer="A.",
            provider="free",
        ))

    def test_load_llm_session_no_db(self, monkeypatch):
        """load_llm_session возвращает None без БД."""
        from miniapp.backend.db import repository
        monkeypatch.setattr(repository, "is_db_ready", lambda: False)

        import asyncio
        result = asyncio.run(repository.load_llm_session("test"))
        assert result is None

    def test_load_llm_session_no_pool(self, monkeypatch):
        """load_llm_session возвращает None если пул не создан."""
        from miniapp.backend.db import repository
        monkeypatch.setattr(repository, "is_db_ready", lambda: True)
        monkeypatch.setattr(repository, "get_pool", lambda: None)

        import asyncio
        result = asyncio.run(repository.load_llm_session("test"))
        assert result is None


# ============================================================
# Тесты _try_restore_llm_session
# ============================================================

class TestTryRestoreLlmSession:
    """Тестирует восстановление LLM-сессии при открытии задачи."""

    def test_restore_summary_and_qa(self, monkeypatch):
        """Если в БД есть и summary и qa_history — оба восстанавливаются."""
        from miniapp.backend.services import task_registry

        # Stub: в БД есть полная сессия
        async def fake_load(task_id):
            return {
                "summary_text": "Тестовое резюме по региону.",
                "summary_provider": "free",
                "summary_generated_at": datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
                "qa_history": [
                    {"question": "Q1?", "answer": "A1.", "provider": "free",
                     "timestamp": "2026-08-10T12:01:00+00:00"},
                    {"question": "Q2?", "answer": "A2.", "provider": "free",
                     "timestamp": "2026-08-10T12:02:00+00:00"},
                ],
            }

        monkeypatch.setattr(
            "miniapp.backend.db.repository.load_llm_session", fake_load
        )

        # Stub AnalysisStatus (чтобы не тянуть весь gibdd_service)
        monkeypatch.setattr(
            "miniapp.backend.services.gibdd_service.AnalysisStatus",
            StubAnalysisStatus,
        )

        task = StubTask()  # оба поля пустые

        import asyncio
        asyncio.run(task_registry._try_restore_llm_session(task))

        # summary восстановлен
        assert task.llm_summary_state.status == StubAnalysisStatus.DONE
        assert task.llm_summary_state.progress == 100
        assert "Готово (восстановлено из БД)" in task.llm_summary_state.stage
        assert task.llm_summary_state.result is not None
        assert task.llm_summary_state.result["text"] == "Тестовое резюме по региону."
        assert task.llm_summary_state.result["provider"] == "free"
        assert task.llm_summary_state.result["from_session_db"] is True

        # qa_history восстановлен
        assert len(task.llm_qa_history) == 2
        assert task.llm_qa_history[0]["question"] == "Q1?"
        assert task.llm_qa_history[1]["answer"] == "A2."

    def test_no_restore_when_already_filled(self, monkeypatch):
        """Если summary и qa уже заполнены — не затираем."""
        from miniapp.backend.services import task_registry

        # Stub: в БД тоже есть данные (не должны использоваться)
        async def fake_load(task_id):
            return {
                "summary_text": "ЭТО НЕ ДОЛЖНО ПРИМЕНЯТЬСЯ",
                "summary_provider": "paid",
                "summary_generated_at": None,
                "qa_history": [{"question": "VOID", "answer": "VOID",
                                "provider": "paid", "timestamp": ""}],
            }

        monkeypatch.setattr(
            "miniapp.backend.db.repository.load_llm_session", fake_load
        )
        monkeypatch.setattr(
            "miniapp.backend.services.gibdd_service.AnalysisStatus",
            StubAnalysisStatus,
        )

        task = StubTask()
        task.llm_summary_state = StubAnalysisState(
            status=StubAnalysisStatus.DONE,
            progress=100,
            stage="Готово",
            result={"text": "Актуальное резюме", "provider": "free"},
        )
        task.llm_qa_history = [
            {"question": "Текущий вопрос", "answer": "Текущий ответ",
             "provider": "free", "timestamp": "2026-08-10T12:00:00+00:00"},
        ]

        import asyncio
        asyncio.run(task_registry._try_restore_llm_session(task))

        # Ничего не затёрто
        assert task.llm_summary_state.result["text"] == "Актуальное резюме"
        assert len(task.llm_qa_history) == 1
        assert task.llm_qa_history[0]["question"] == "Текущий вопрос"

    def test_no_restore_when_db_empty(self, monkeypatch):
        """Если в БД нет записи — ничего не делаем."""
        from miniapp.backend.services import task_registry

        async def fake_load(task_id):
            return None  # записи нет

        monkeypatch.setattr(
            "miniapp.backend.db.repository.load_llm_session", fake_load
        )

        task = StubTask()

        import asyncio
        asyncio.run(task_registry._try_restore_llm_session(task))

        # Ничего не изменилось
        assert task.llm_summary_state.status == StubAnalysisStatus.IDLE
        assert task.llm_qa_history == []

    def test_restore_summary_only(self, monkeypatch):
        """Если в БД только summary (qa пустая) — восстанавливаем только summary."""
        from miniapp.backend.services import task_registry

        async def fake_load(task_id):
            return {
                "summary_text": "Только резюме.",
                "summary_provider": "free",
                "summary_generated_at": datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
                "qa_history": [],  # пустая история
            }

        monkeypatch.setattr(
            "miniapp.backend.db.repository.load_llm_session", fake_load
        )
        monkeypatch.setattr(
            "miniapp.backend.services.gibdd_service.AnalysisStatus",
            StubAnalysisStatus,
        )

        task = StubTask()

        import asyncio
        asyncio.run(task_registry._try_restore_llm_session(task))

        # summary восстановлен
        assert task.llm_summary_state.status == StubAnalysisStatus.DONE
        assert task.llm_summary_state.result["text"] == "Только резюме."
        # qa осталась пустой
        assert task.llm_qa_history == []

    def test_restore_qa_only(self, monkeypatch):
        """Если в БД только qa (summary=None) — восстанавливаем только qa."""
        from miniapp.backend.services import task_registry

        async def fake_load(task_id):
            return {
                "summary_text": None,  # summary не было
                "summary_provider": None,
                "summary_generated_at": None,
                "qa_history": [
                    {"question": "Q1?", "answer": "A1.", "provider": "free",
                     "timestamp": "2026-08-10T12:00:00+00:00"},
                ],
            }

        monkeypatch.setattr(
            "miniapp.backend.db.repository.load_llm_session", fake_load
        )

        task = StubTask()

        import asyncio
        asyncio.run(task_registry._try_restore_llm_session(task))

        # summary НЕ восстановлен (был None)
        assert task.llm_summary_state.status == StubAnalysisStatus.IDLE
        assert task.llm_summary_state.result is None
        # qa восстановлена
        assert len(task.llm_qa_history) == 1
        assert task.llm_qa_history[0]["question"] == "Q1?"

    def test_restore_handles_db_error(self, monkeypatch):
        """Если load_llm_session падает — _try_restore не роняет caller."""
        from miniapp.backend.services import task_registry

        async def fake_load(task_id):
            raise RuntimeError("DB connection lost")

        monkeypatch.setattr(
            "miniapp.backend.db.repository.load_llm_session", fake_load
        )

        task = StubTask()

        import asyncio
        # Не должно бросить
        asyncio.run(task_registry._try_restore_llm_session(task))

        # Состояние не изменилось
        assert task.llm_summary_state.status == StubAnalysisStatus.IDLE
        assert task.llm_qa_history == []


# ============================================================
# Тесты schema.sql — структура таблицы
# ============================================================

class TestSchemaSprint6:
    """Проверяем, что schema.sql содержит всё нужное для Sprint 6."""

    def test_llm_sessions_table_exists(self):
        schema = (PROJECT_ROOT / "miniapp/backend/db/schema.sql").read_text("utf-8")
        assert "CREATE TABLE IF NOT EXISTS llm_sessions" in schema

    def test_llm_sessions_has_all_columns(self):
        schema = (PROJECT_ROOT / "miniapp/backend/db/schema.sql").read_text("utf-8")
        # Берём только секцию llm_sessions
        start = schema.find("CREATE TABLE IF NOT EXISTS llm_sessions")
        end = schema.find(";", start) + 1
        table_block = schema[start:end]

        for col in ["task_id", "user_id", "summary_text", "summary_provider",
                    "summary_generated_at", "qa_history", "updated_at"]:
            assert col in table_block, f"Колонка {col} отсутствует в llm_sessions"

    def test_llm_sessions_has_index_and_trigger(self):
        schema = (PROJECT_ROOT / "miniapp/backend/db/schema.sql").read_text("utf-8")
        assert "idx_llm_sessions_user" in schema
        assert "trg_llm_sessions_updated_at" in schema

    def test_llm_sessions_uses_update_updated_at_column(self):
        """Триггер должен переиспользовать существующую функцию."""
        schema = (PROJECT_ROOT / "miniapp/backend/db/schema.sql").read_text("utf-8")
        # Функция определена в верхней части schema.sql
        assert "CREATE OR REPLACE FUNCTION update_updated_at_column" in schema
        # И используется в триггере для llm_sessions
        llm_block = schema[schema.find("trg_llm_sessions_updated_at"):]
        assert "update_updated_at_column" in llm_block[:500]


# ============================================================
# Тесты frontend bundle markers
# ============================================================

class TestFrontendBundleSprint6:
    """Проверяем, что в собранном bundle есть Sprint 6 UI-элементы."""

    def test_bundle_contains_sprint6_markers(self):
        bundle_dir = PROJECT_ROOT / "miniapp/frontend/dist/assets"
        bundles = list(bundle_dir.glob("index-*.js"))
        assert bundles, "Frontend bundle не найден — нужен npm run build"

        bundle = bundles[0].read_text("utf-8")
        # Маркеры Sprint 6 UI
        assert "Копировать" in bundle, "Нет кнопки «Копировать»"
        assert "Повторить" in bundle, "Нет кнопки «Повторить»"
        assert "Скопировано" in bundle, "Нет фидбека «Скопировано»"
        # Fallback для не-secure context (Telegram WebView на HTTP)
        assert "execCommand" in bundle, "Нет fallback для clipboard"
