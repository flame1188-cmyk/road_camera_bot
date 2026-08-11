"""
Интеграционные тесты FastAPI routes с TestClient.

Аутентификация переопределена через dependency_overrides — все эндпоинты
видят тестового пользователя без проверки Telegram-подписи.

Покрытие:
  - GET  /miniapp/health         — health-check
  - POST /parse                  — парсинг запроса
  - GET  /regions                — список регионов
  - GET  /regions/search?q=...   — поиск по подстроке
  - POST /dtp/tasks              — создание задачи (structured mode)
  - GET  /dtp/tasks              — список задач пользователя
  - GET  /dtp/tasks/{id}         — статус задачи
  - GET  /dtp/tasks/{id}         — 404 для несуществующей
  - GET  /dtp/tasks/{id}         — 403 для чужой задачи
  - GET  /dtp/tasks/{id}/llm/providers — статус LLM-провайдеров
  - POST /dtp/tasks/{id}/llm/ask — Q&A к LLM (мок через _import_module)
"""
import types

import pytest
from backend.services import _imports  # для патчей _PROJECT_ROOT/_import_module


# ============================================================
# Helper: отключаем фоновый execute_task
# ============================================================
# POST /dtp/tasks запускает asyncio.create_task(execute_task(task_id)) —
# это приводит к попытке импорта bot и реальной выгрузки ГИБДД.
# В тестах мы хотим проверить логику эндпоинта, а не выгрузку.
# Патчим execute_task на no-op в САМОМ РОУТЕРЕ (там имя импортируется
# через `from ..services.gibdd_service import execute_task`).
@pytest.fixture(autouse=True)
def disable_execute_task(monkeypatch):
    from backend.routers import dtp as dtp_router

    async def _noop(task_id):
        return None

    monkeypatch.setattr(dtp_router, "execute_task", _noop)


# ============================================================
# Health-check
# ============================================================
class TestHealthEndpoint:
    def test_health_returns_ok(self, fastapi_client):
        response = fastapi_client.get("/miniapp/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["service"] == "gibdd-miniapp"


# ============================================================
# /parse
# ============================================================
class TestParseEndpoint:
    def test_parse_valid_query_returns_structured(self, fastapi_client, monkeypatch, clear_in_memory_tasks):
        """POST /parse → распарсенный регион+период."""
        import types
        from backend.services import gibdd_service

        fake_parser = types.ModuleType("user_request_parser")

        class FakePeriod:
            label = "Май 2025"
            def get_dat_list(self):
                return ["5.2025"]

        class FakeParsed:
            region_code = "1101"
            region_name = "Вологодская область"
            period = FakePeriod()

        async def fake_parse(text):
            return FakeParsed()

        fake_parser.parse_user_message = fake_parse
        monkeypatch.setattr(gibdd_service, "_import_module", lambda name: fake_parser)
        monkeypatch.setattr(_imports, "_import_module", lambda name: fake_parser)

        response = fastapi_client.post(
            "/parse",
            json={"query": "Вологодская область май 2025"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["region_code"] == "1101"
        assert body["region_name"] == "Вологодская область"
        assert body["period"] == "Май 2025"
        assert body["dat_list"] == ["5.2025"]

    def test_parse_too_short_query_returns_422(self, fastapi_client):
        """query < 2 символов → Pydantic validation error (422)."""
        response = fastapi_client.post("/parse", json={"query": "а"})
        assert response.status_code == 422

    def test_parse_unrecognized_query_returns_ok_false(self, fastapi_client, monkeypatch, clear_in_memory_tasks):
        """Парсер вернул None → ok=False с понятной ошибкой."""
        import types
        from backend.services import gibdd_service

        fake_parser = types.ModuleType("user_request_parser")

        async def fake_parse(text):
            return None

        fake_parser.parse_user_message = fake_parse
        monkeypatch.setattr(gibdd_service, "_import_module", lambda name: fake_parser)
        monkeypatch.setattr(_imports, "_import_module", lambda name: fake_parser)

        response = fastapi_client.post(
            "/parse",
            json={"query": "абракадабра непонятная"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "error" in body


# ============================================================
# /regions
# ============================================================
class TestRegionsEndpoint:
    def test_list_regions_returns_array(self, fastapi_client, monkeypatch, clear_in_memory_tasks):
        import types
        from backend.services import gibdd_service

        fake_parser = types.ModuleType("user_request_parser")

        async def fake_ensure():
            return [
                {"code": "1101", "name": "Алтайский край"},
                {"code": "1102", "name": "Вологодская область"},
            ]

        fake_parser.ensure_regions_loaded = fake_ensure
        monkeypatch.setattr(gibdd_service, "_import_module", lambda name: fake_parser)
        monkeypatch.setattr(_imports, "_import_module", lambda name: fake_parser)

        response = fastapi_client.get("/regions")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2
        assert body[0]["code"] == "1101"

    def test_search_regions_by_substring(self, fastapi_client, monkeypatch, clear_in_memory_tasks):
        import types
        from backend.services import gibdd_service

        fake_parser = types.ModuleType("user_request_parser")

        async def fake_ensure():
            return [
                {"code": "1101", "name": "Алтайский край"},
                {"code": "1102", "name": "Вологодская область"},
                {"code": "1103", "name": "Краснодарский край"},
            ]

        fake_parser.ensure_regions_loaded = fake_ensure
        monkeypatch.setattr(gibdd_service, "_import_module", lambda name: fake_parser)
        monkeypatch.setattr(_imports, "_import_module", lambda name: fake_parser)

        response = fastapi_client.get("/regions/search?q=край")
        assert response.status_code == 200
        body = response.json()
        # Должны найтись 2 региона с "край" в названии
        names = [r["name"] for r in body]
        assert "Алтайский край" in names
        assert "Краснодарский край" in names
        assert "Вологодская область" not in names

    def test_search_regions_empty_q_returns_first_20(self, fastapi_client, monkeypatch, clear_in_memory_tasks):
        import types
        from backend.services import gibdd_service

        fake_parser = types.ModuleType("user_request_parser")

        async def fake_ensure():
            return [{"code": f"1{i:03d}", "name": f"Регион {i}"} for i in range(30)]

        fake_parser.ensure_regions_loaded = fake_ensure
        monkeypatch.setattr(gibdd_service, "_import_module", lambda name: fake_parser)
        monkeypatch.setattr(_imports, "_import_module", lambda name: fake_parser)

        response = fastapi_client.get("/regions/search?q=")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 20  # ограничение в коде


# ============================================================
# /dtp/tasks
# ============================================================
class TestDtpTasksEndpoint:
    def test_create_task_structured_mode(self, fastapi_client, clear_in_memory_tasks, monkeypatch):
        """Structured mode: region_code + dat_list → задача создаётся."""
        # Подменяем repository.log_access, чтобы не падать без БД
        import types
        fake_repo = types.ModuleType("repository")
        async def fake_log_access(**kwargs):
            return None
        fake_repo.log_access = fake_log_access
        import sys
        monkeypatch.setitem(sys.modules, "backend.db.repository", fake_repo)

        response = fastapi_client.post(
            "/dtp/tasks",
            json={
                "region_code": "1101",
                "region_name": "Вологодская область",
                "dat_list": ["5.2025", "6.2025"],
                "period_label": "Май-Июнь 2025",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert "task_id" in body
        assert len(body["task_id"]) == 12
        assert body["region_code"] == "1101"
        assert body["region_name"] == "Вологодская область"
        assert body["period"] == "Май-Июнь 2025"
        assert body["status"] == "pending"

    def test_create_task_text_mode(self, fastapi_client, monkeypatch, clear_in_memory_tasks):
        """Text mode: query → парсится через user_request_parser."""
        import types
        from backend.services import gibdd_service

        fake_parser = types.ModuleType("user_request_parser")

        class FakePeriod:
            label = "Май 2025"
            def get_dat_list(self):
                return ["5.2025"]

        class FakeParsed:
            region_code = "1101"
            region_name = "Вологодская область"
            period = FakePeriod()

        async def fake_parse(text):
            return FakeParsed()

        fake_parser.parse_user_message = fake_parse
        monkeypatch.setattr(gibdd_service, "_import_module", lambda name: fake_parser)
        monkeypatch.setattr(_imports, "_import_module", lambda name: fake_parser)

        # Также подменяем repository.log_access
        fake_repo = types.ModuleType("repository")
        async def fake_log_access(**kwargs):
            return None
        fake_repo.log_access = fake_log_access
        import sys
        monkeypatch.setitem(sys.modules, "backend.db.repository", fake_repo)

        response = fastapi_client.post(
            "/dtp/tasks",
            json={"query": "Вологодская область май 2025"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["region_code"] == "1101"
        assert body["period"] == "Май 2025"

    def test_create_task_missing_both_modes_returns_400(self, fastapi_client, clear_in_memory_tasks):
        """Ни structured, ни text — 400 Bad Request."""
        response = fastapi_client.post(
            "/dtp/tasks",
            json={"query": "а"},  # слишком короткий, без structured
        )
        assert response.status_code == 400

    def test_get_task_status_existing(self, fastapi_client, clear_in_memory_tasks, monkeypatch):
        """GET /dtp/tasks/{id} — задача существует."""
        import types
        fake_repo = types.ModuleType("repository")
        async def fake_log_access(**kwargs):
            return None
        fake_repo.log_access = fake_log_access
        import sys
        monkeypatch.setitem(sys.modules, "backend.db.repository", fake_repo)

        create_response = fastapi_client.post(
            "/dtp/tasks",
            json={
                "region_code": "1101",
                "region_name": "Регион",
                "dat_list": ["5.2025"],
                "period_label": "Май 2025",
            },
        )
        task_id = create_response.json()["task_id"]

        response = fastapi_client.get(f"/dtp/tasks/{task_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["task_id"] == task_id
        assert body["status"] == "pending"

    def test_get_task_status_not_found(self, fastapi_client, clear_in_memory_tasks):
        """GET /dtp/tasks/несуществующий → 404."""
        response = fastapi_client.get("/dtp/tasks/несуществующий-id")
        assert response.status_code == 404

    def test_get_task_status_forbidden_user(
        self, fastapi_client, clear_in_memory_tasks, monkeypatch, fastapi_test_user,
    ):
        """GET задачи другого пользователя → 403."""
        from backend.services import gibdd_service

        # Создаём задачу от имени другого пользователя напрямую
        other_task = gibdd_service.create_task(
            user_id=fastapi_test_user.id + 999,  # другой пользователь
            region_code="1101", region_name="Регион",
            period_label="Период", dat_list=["1.2025"], raw_query="q",
        )

        response = fastapi_client.get(f"/dtp/tasks/{other_task.id}")
        assert response.status_code == 403

    def test_list_tasks_returns_only_user_tasks(
        self, fastapi_client, clear_in_memory_tasks, monkeypatch, fastapi_test_user,
    ):
        """GET /dtp/tasks — только задачи текущего пользователя."""
        from backend.services import gibdd_service

        # 2 задачи текущего пользователя + 1 чужая
        for i in range(2):
            gibdd_service.create_task(
                user_id=fastapi_test_user.id,
                region_code="1101", region_name="Рег",
                period_label=f"Период{i}", dat_list=["1.2025"], raw_query=f"q-{i}",
            )
        gibdd_service.create_task(
            user_id=fastapi_test_user.id + 1,
            region_code="1101", region_name="Чужой",
            period_label="Чужой период", dat_list=["1.2025"], raw_query="other",
        )

        response = fastapi_client.get("/dtp/tasks")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2
        assert all(t["region_name"] == "Рег" for t in body)

    def test_list_task_files_empty_initially(self, fastapi_client, clear_in_memory_tasks, monkeypatch):
        """GET /dtp/tasks/{id}/files — пустой список сразу после создания."""
        import types
        fake_repo = types.ModuleType("repository")
        async def fake_log_access(**kwargs):
            return None
        fake_repo.log_access = fake_log_access
        import sys
        monkeypatch.setitem(sys.modules, "backend.db.repository", fake_repo)

        create_response = fastapi_client.post(
            "/dtp/tasks",
            json={
                "region_code": "1101",
                "region_name": "Регион",
                "dat_list": ["5.2025"],
                "period_label": "Май 2025",
            },
        )
        task_id = create_response.json()["task_id"]

        response = fastapi_client.get(f"/dtp/tasks/{task_id}/files")
        assert response.status_code == 200
        assert response.json() == []


# ============================================================
# /dtp/tasks/{id}/llm/providers и /llm/ask
# ============================================================
class TestLlmEndpoints:
    def test_llm_providers_requires_done_task(
        self, fastapi_client, clear_in_memory_tasks, monkeypatch,
    ):
        """LLM endpoints требуют task.status == 'done' — иначе 409."""
        import types
        fake_repo = types.ModuleType("repository")
        async def fake_log_access(**kwargs):
            return None
        fake_repo.log_access = fake_log_access
        import sys
        monkeypatch.setitem(sys.modules, "backend.db.repository", fake_repo)

        # Создаём задачу (status=pending, не done)
        create_response = fastapi_client.post(
            "/dtp/tasks",
            json={
                "region_code": "1101",
                "region_name": "Регион",
                "dat_list": ["5.2025"],
                "period_label": "Май 2025",
            },
        )
        task_id = create_response.json()["task_id"]

        # /llm/providers — должно быть 409 (требует done)
        response = fastapi_client.get(f"/dtp/tasks/{task_id}/llm/providers")
        assert response.status_code == 409

    def test_llm_providers_returns_status_when_done(
        self, fastapi_client, clear_in_memory_tasks, monkeypatch,
    ):
        """Если task.status=done — /llm/providers возвращает статус."""
        from backend.services import gibdd_service

        # Создаём задачу и сразу переводим в done
        task = gibdd_service.create_task(
            user_id=999999,  # fastapi_test_user.id
            region_code="1101", region_name="Регион",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.status = gibdd_service.TaskStatus.DONE
        task.cards = [{"kart_id": "1"}]  # минимальный cards, чтобы task был валидным

        # Подменяем config для get_llm_providers_status
        fake_config = types.ModuleType("config")
        fake_config.LLM_API_KEY = "key"
        fake_config.LLM_MODEL = "test-model"
        fake_config.LLM_PAID_API_KEY = ""
        fake_config.LLM_PAID_API_URL = ""
        fake_config.LLM_PAID_MODEL = ""
        monkeypatch.setattr(gibdd_service, "_import_module", lambda name: fake_config)
        monkeypatch.setattr(_imports, "_import_module", lambda name: fake_config)

        response = fastapi_client.get(f"/dtp/tasks/{task.id}/llm/providers")
        assert response.status_code == 200
        body = response.json()
        assert body["free"] is True
        assert body["paid"] is False
        assert body["free_model"] == "test-model"

    def test_llm_ask_returns_answer(
        self, fastapi_client, clear_in_memory_tasks, monkeypatch,
    ):
        """POST /dtp/tasks/{id}/llm/ask — happy path с замоканным LLM."""
        import types
        from backend.services import gibdd_service

        # Создаём задачу и переводим в done
        task = gibdd_service.create_task(
            user_id=999999,
            region_code="1101", region_name="Регион",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.status = gibdd_service.TaskStatus.DONE
        task.cards = [{"kart_id": "1"}]
        # Имитируем, что comparison уже посчитан (иначе будет попытка загрузить prev_cards)
        task.comparison = {
            "total": {"current": 10, "previous": 8, "change": 25.0},
            "deaths": {"current": 1, "previous": 2, "change": -50.0},
            "injured": {"current": 12, "previous": 10, "change": 20.0},
            "alcohol": {"current": 0, "previous": 1, "change": -100.0},
            "pedestrians": {"current": 2, "previous": 3, "change": -33.3},
            "deaths_per_100": {"current": 10.0, "previous": 25.0, "change": -60.0},
            "injured_per_100": {"current": 120.0, "previous": 125.0, "change": -4.0},
            "by_weekday": {"current": {}, "previous": {}},
            "by_hour": {"current": {}, "previous": {}},
            "by_type": {"current": {}, "previous": {}},
            "by_weather": {"current": {}, "previous": {}},
        }

        # Подменяем config
        fake_config = types.ModuleType("config")
        fake_config.LLM_API_KEY = "free-key"
        fake_config.LLM_MODEL = "glm-4-flash"
        fake_config.LLM_PAID_API_KEY = ""
        fake_config.LLM_PAID_API_URL = ""
        fake_config.LLM_PAID_MODEL = ""

        # Подменяем llm_analyzer.get_ai_answer
        fake_llm = types.ModuleType("llm_analyzer")
        async def fake_get_answer(question, comparison, reg_name, current_label, prev_label,
                                   raw_supplement="", news_context="", clusters_context="",
                                   cross_tables_context="", provider="free", history=None):
            return "Ответ от замоканной нейросети"
        fake_llm.get_ai_answer = fake_get_answer
        fake_llm.format_cross_tables_for_prompt = lambda *a, **kw: ""
        fake_llm.format_statistical_metrics_for_prompt = lambda *a, **kw: ""
        fake_llm.format_clusters_for_prompt = lambda *a, **kw: ""

        # Подменяем analytics
        fake_analytics = types.ModuleType("analytics")
        fake_analytics.calculate_statistical_metrics = lambda x: {}

        def smart_import(name):
            if name == "config":
                return fake_config
            if name == "llm_analyzer":
                return fake_llm
            if name == "analytics":
                return fake_analytics
            raise ImportError(f"unexpected module: {name}")

        monkeypatch.setattr(gibdd_service, "_import_module", smart_import)

        monkeypatch.setattr(_imports, "_import_module", smart_import)

        response = fastapi_client.post(
            f"/dtp/tasks/{task.id}/llm/ask",
            json={"question": "Где больше всего ДТП?", "provider": "free"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["answer"] == "Ответ от замоканной нейросети"

    def test_llm_ask_short_question_returns_error(
        self, fastapi_client, clear_in_memory_tasks, monkeypatch,
    ):
        """POST /dtp/tasks/{id}/llm/ask со слишком коротким вопросом → ok=False."""
        from backend.services import gibdd_service

        task = gibdd_service.create_task(
            user_id=999999,
            region_code="1101", region_name="Регион",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.status = gibdd_service.TaskStatus.DONE
        task.cards = [{"kart_id": "1"}]

        response = fastapi_client.post(
            f"/dtp/tasks/{task.id}/llm/ask",
            json={"question": "а", "provider": "free"},
        )
        # Pydantic min_length=3 — 422
        assert response.status_code == 422

    def test_llm_qa_history_empty_initially(
        self, fastapi_client, clear_in_memory_tasks, monkeypatch,
    ):
        """GET /dtp/tasks/{id}/llm/qa-history — пустой список сразу после done."""
        from backend.services import gibdd_service

        task = gibdd_service.create_task(
            user_id=999999,
            region_code="1101", region_name="Регион",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.status = gibdd_service.TaskStatus.DONE
        task.cards = [{"kart_id": "1"}]

        response = fastapi_client.get(f"/dtp/tasks/{task.id}/llm/qa-history")
        assert response.status_code == 200
        assert response.json() == []
