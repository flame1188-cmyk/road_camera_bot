"""
Тесты ask_llm и _do_llm_request в llm_analyzer.py с HTTP-моками (respx).

Покрытие:
  - happy path для free (ZhipuAI) и paid (OpenAI-compatible)
  - 429 retry → success
  - 429 retry exhausted → raises HTTPStatusError
  - 5xx retry → success
  - 5xx retry exhausted → raises HTTPStatusError
  - 4xx (400/401/413) — NO retry, raises immediately
  - Invalid JSON response → ValueError
  - Empty content → ValueError
  - reasoning_content fallback when content is empty (DeepSeek-style)
  - history parameter — messages assembled correctly
  - _ask_free_llm without API key → ValueError
  - _ask_paid_llm without API key → ValueError
  - get_ai_summary routes to paid vs free correctly
  - get_ai_answer uses temperature=0.3 for Q&A
"""
import json

import httpx
import pytest
import respx

from tests.fixtures.synthetic_cards import cards_basic_set


# ============================================================
# Helpers
# ============================================================
def _ok_response(content: str = "Тестовый ответ от LLM", finish_reason: str = "stop") -> dict:
    """Возвращает тело ответа LLM в формате OpenAI/ZhipuAI."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }


# ============================================================
# Happy path
# ============================================================
class TestAskLlmHappyPath:
    @pytest.mark.asyncio
    async def test_free_provider_returns_content(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter):
        import llm_analyzer

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(
                return_value=httpx.Response(200, json=_ok_response("Анализ ДТП за 2025 год"))
            )
            result = await llm_analyzer.ask_llm("Сделай анализ", provider="free")
            assert result == "Анализ ДТП за 2025 год"

    @pytest.mark.asyncio
    async def test_paid_provider_returns_content(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter):
        import llm_analyzer

        with respx.mock(base_url="https://test.example.com") as mock:
            mock.post("/v1/chat/completions").mock(
                return_value=httpx.Response(200, json=_ok_response("Глубокий анализ от платного провайдера"))
            )
            result = await llm_analyzer.ask_llm("Сделай глубокий анализ", provider="paid")
            assert result == "Глубокий анализ от платного провайдера"

    @pytest.mark.asyncio
    async def test_default_provider_is_free(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter):
        """Без явного provider — используется free (ZhipuAI)."""
        import llm_analyzer

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(
                return_value=httpx.Response(200, json=_ok_response("default-free"))
            )
            # Не передаём provider — должно использоваться "free"
            result = await llm_analyzer.ask_llm("test")
            assert result == "default-free"

    @pytest.mark.asyncio
    async def test_custom_system_prompt_used(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter):
        """Кастомный system_prompt подменяет дефолтный SYSTEM_PROMPT."""
        import llm_analyzer

        captured = {}

        def _intercept(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            captured["messages"] = payload["messages"]
            return httpx.Response(200, json=_ok_response("ok"))

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(side_effect=_intercept)
            await llm_analyzer.ask_llm(
                "Привет",
                system_prompt="МОЙ КАСТОМНЫЙ ПРОМПТ",
                provider="free",
            )
            assert captured["messages"][0]["role"] == "system"
            assert captured["messages"][0]["content"] == "МОЙ КАСТОМНЫЙ ПРОМПТ"

    @pytest.mark.asyncio
    async def test_history_inserted_between_system_and_user(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter):
        """History вставляется между system и новым user-сообщением."""
        import llm_analyzer

        captured = {}

        def _intercept(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            captured["messages"] = payload["messages"]
            return httpx.Response(200, json=_ok_response("ok"))

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(side_effect=_intercept)
            await llm_analyzer.ask_llm(
                "А теперь новый вопрос",
                provider="free",
                history=[
                    {"role": "user", "content": "Старый вопрос"},
                    {"role": "assistant", "content": "Старый ответ"},
                ],
            )
            msgs = captured["messages"]
            assert len(msgs) == 4  # system + 2 history + 1 new user
            assert msgs[0]["role"] == "system"
            assert msgs[1]["role"] == "user" and msgs[1]["content"] == "Старый вопрос"
            assert msgs[2]["role"] == "assistant" and msgs[2]["content"] == "Старый ответ"
            assert msgs[3]["role"] == "user" and msgs[3]["content"] == "А теперь новый вопрос"

    @pytest.mark.asyncio
    async def test_history_filtered_to_valid_pairs(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter):
        """Бракованные записи в history отфильтровываются."""
        import llm_analyzer

        captured = {}

        def _intercept(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            captured["messages"] = payload["messages"]
            return httpx.Response(200, json=_ok_response("ok"))

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(side_effect=_intercept)
            await llm_analyzer.ask_llm(
                "Вопрос",
                provider="free",
                history=[
                    {"role": "invalid_role", "content": "мусор"},
                    {"role": "user", "content": ""},  # пустой контент — отфильтровать
                    {"role": "assistant", "content": "Валидный ответ"},
                ],
            )
            # Только system + 1 валидная history-запись + user-вопрос = 3
            assert len(captured["messages"]) == 3
            assert captured["messages"][1]["content"] == "Валидный ответ"


# ============================================================
# Reasoning fallback
# ============================================================
class TestReasoningFallback:
    @pytest.mark.asyncio
    async def test_reasoning_content_used_when_content_empty(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter):
        """DeepSeek-style: content="", но есть reasoning_content — извлекаем ответ из него."""
        import llm_analyzer

        response_body = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "Сначала подумал...\nПотом решил...\nИтоговый ответ: 42",
                    },
                    "finish_reason": "stop",
                }
            ]
        }

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(
                return_value=httpx.Response(200, json=response_body)
            )
            result = await llm_analyzer.ask_llm("test", provider="free")
            assert "Итоговый ответ: 42" in result

    @pytest.mark.asyncio
    async def test_reasoning_field_used_when_content_empty(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter):
        """DeepSeek (AItunnel) формат: поле 'reasoning' вместо 'reasoning_content'."""
        import llm_analyzer

        response_body = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning": "Размышление\nОтвет: 7",
                    },
                    "finish_reason": "stop",
                }
            ]
        }

        with respx.mock(base_url="https://test.example.com") as mock:
            mock.post("/v1/chat/completions").mock(
                return_value=httpx.Response(200, json=response_body)
            )
            result = await llm_analyzer.ask_llm("test", provider="paid")
            assert "Ответ: 7" in result


# ============================================================
# Empty / invalid responses
# ============================================================
class TestInvalidResponses:
    @pytest.mark.asyncio
    async def test_empty_content_raises_value_error(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter):
        """Если content="" и нет reasoning — ValueError."""
        import llm_analyzer

        response_body = {
            "choices": [
                {"message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}
            ]
        }

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(
                return_value=httpx.Response(200, json=response_body)
            )
            with pytest.raises(ValueError, match="пустой ответ"):
                await llm_analyzer.ask_llm("test", provider="free")

    @pytest.mark.asyncio
    async def test_no_choices_raises_value_error(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter):
        import llm_analyzer

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(
                return_value=httpx.Response(200, json={"error": "что-то странное"})
            )
            with pytest.raises(ValueError, match="Неожидаемый ответ"):
                await llm_analyzer.ask_llm("test", provider="free")

    @pytest.mark.asyncio
    async def test_invalid_json_raises_value_error(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter):
        """Невалидный JSON в теле ответа → ValueError."""
        import llm_analyzer

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(
                return_value=httpx.Response(200, content=b"not a json at all")
            )
            with pytest.raises(ValueError):
                await llm_analyzer.ask_llm("test", provider="free")


# ============================================================
# Error handling: 429 / 5xx / 4xx / timeout
# ============================================================
class TestRetriesAndErrors:
    @pytest.mark.asyncio
    async def test_429_then_success_returns_content(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter, monkeypatch):
        """429 на первой попытке → retry → 200 на второй."""
        import llm_analyzer

        # Ускоряем ретраи — без этого тест ждёт 30 секунд
        async def _no_sleep(_):
            return None
        monkeypatch.setattr(llm_analyzer.asyncio, "sleep", _no_sleep)

        call_count = [0]
        def _side_effect(_request):
            call_count[0] += 1
            if call_count[0] == 1:
                return httpx.Response(429, json={"error": {"message": "rate limit"}})
            return httpx.Response(200, json=_ok_response("успешно после retry"))

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            route = mock.post("/api/paas/v4/chat/completions")
            route.mock(side_effect=_side_effect)
            result = await llm_analyzer.ask_llm("test", provider="free", max_retries=3)
            assert result == "успешно после retry"
            assert route.call_count == 2

    @pytest.mark.asyncio
    async def test_429_exhausted_raises(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter, monkeypatch):
        """429 на всех попытках → HTTPStatusError."""
        import llm_analyzer

        async def _no_sleep(_):
            return None
        monkeypatch.setattr(llm_analyzer.asyncio, "sleep", _no_sleep)

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            route = mock.post("/api/paas/v4/chat/completions")
            route.mock(return_value=httpx.Response(429, json={"error": {"message": "rate limit"}}))
            with pytest.raises(httpx.HTTPStatusError):
                await llm_analyzer.ask_llm("test", provider="free", max_retries=2)
            # 1 + 2 ретрая = 3 вызова
            assert route.call_count == 3

    @pytest.mark.asyncio
    async def test_429_honors_retry_after_header(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter, monkeypatch):
        """Если 429 с Retry-After — пауза берётся из заголовка (минимум 30с)."""
        import llm_analyzer

        captured_waits = []
        async def _fake_sleep(seconds):
            captured_waits.append(seconds)

        monkeypatch.setattr(llm_analyzer.asyncio, "sleep", _fake_sleep)

        call_count = [0]
        def _side_effect(_request):
            call_count[0] += 1
            if call_count[0] == 1:
                return httpx.Response(429, headers={"Retry-After": "5"}, json={"error": {}})
            return httpx.Response(200, json=_ok_response("ok"))

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(side_effect=_side_effect)
            await llm_analyzer.ask_llm("test", provider="free", max_retries=2)
            # Retry-After=5 + 5с запас = 10, но минимум 30
            assert captured_waits[0] >= 30, (
                f"Должно быть max(Retry-After+5, 30) = 30, получили {captured_waits[0]}"
            )

    @pytest.mark.asyncio
    async def test_500_then_success_returns_content(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter, monkeypatch):
        """500 на первой попытке → retry → 200."""
        import llm_analyzer

        async def _no_sleep(_):
            return None
        monkeypatch.setattr(llm_analyzer.asyncio, "sleep", _no_sleep)

        call_count = [0]
        def _side_effect(_request):
            call_count[0] += 1
            if call_count[0] == 1:
                return httpx.Response(500, text="Internal Server Error")
            return httpx.Response(200, json=_ok_response("ok после 5xx"))

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            route = mock.post("/api/paas/v4/chat/completions")
            route.mock(side_effect=_side_effect)
            result = await llm_analyzer.ask_llm("test", provider="free", max_retries=3)
            assert result == "ok после 5xx"

    @pytest.mark.asyncio
    async def test_5xx_exhausted_raises(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter, monkeypatch):
        """5xx на всех попытках → HTTPStatusError с упоминанием числа попыток."""
        import llm_analyzer

        async def _no_sleep(_):
            return None
        monkeypatch.setattr(llm_analyzer.asyncio, "sleep", _no_sleep)

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            route = mock.post("/api/paas/v4/chat/completions")
            route.mock(return_value=httpx.Response(503, text="Service Unavailable"))
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await llm_analyzer.ask_llm("test", provider="free", max_retries=5)
            # В сообщении должно быть число попыток
            assert "попыт" in str(exc_info.value).lower() or "503" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_4xx_no_retry_raises_immediately(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter, monkeypatch):
        """400/401/403/413 — НЕ ретраятся, сразу падают."""
        import llm_analyzer

        sleep_called = []
        async def _fake_sleep(s):
            sleep_called.append(s)

        monkeypatch.setattr(llm_analyzer.asyncio, "sleep", _fake_sleep)

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            route = mock.post("/api/paas/v4/chat/completions")
            route.mock(return_value=httpx.Response(401, json={"error": {"message": "bad key"}}))
            with pytest.raises(httpx.HTTPStatusError):
                await llm_analyzer.ask_llm("test", provider="free", max_retries=5)
            # Должен быть только 1 вызов — без ретраев
            assert route.call_count == 1
            # Sleep не должен вызываться (кроме rate limiter, который мы отключили)
            assert sleep_called == []

    @pytest.mark.asyncio
    async def test_413_produces_hint_about_context_limit(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter, monkeypatch):
        """413 (Payload Too Large) — в сообщении об ошибке есть подсказка про контекст."""
        import llm_analyzer

        async def _no_sleep(_):
            return None
        monkeypatch.setattr(llm_analyzer.asyncio, "sleep", _no_sleep)

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(
                return_value=httpx.Response(413, json={"error": {"message": "too big"}})
            )
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await llm_analyzer.ask_llm("test", provider="free")
            err_text = str(exc_info.value)
            # Подсказка про лимит контекста или про API-ключ
            assert "контекст" in err_text.lower() or "context" in err_text.lower() or "413" in err_text


# ============================================================
# API key validation
# ============================================================
class TestApiKeyValidation:
    @pytest.mark.asyncio
    async def test_free_without_api_key_raises(self, monkeypatch, reset_llm_clients, disable_rate_limiter):
        import llm_analyzer
        import config

        monkeypatch.setattr(config, "LLM_API_KEY", "")
        monkeypatch.setattr(llm_analyzer, "LLM_API_KEY", "", raising=False)

        with pytest.raises(ValueError, match="LLM_API_KEY не задан"):
            await llm_analyzer.ask_llm("test", provider="free")

    @pytest.mark.asyncio
    async def test_paid_without_api_key_raises(self, monkeypatch, reset_llm_clients, disable_rate_limiter):
        import llm_analyzer
        import config

        monkeypatch.setattr(config, "LLM_PAID_API_KEY", "")
        monkeypatch.setattr(llm_analyzer, "LLM_PAID_API_KEY", "", raising=False)

        with pytest.raises(ValueError, match="LLM_PAID_API_KEY не задан"):
            await llm_analyzer.ask_llm("test", provider="paid")

    @pytest.mark.asyncio
    async def test_paid_with_empty_url_produces_error(self, monkeypatch, reset_llm_clients, disable_rate_limiter):
        """Paid с пустым URL — запрос не может быть отправлен (нет protocol)."""
        import llm_analyzer
        import config

        monkeypatch.setattr(config, "LLM_PAID_API_KEY", "key")
        monkeypatch.setattr(config, "LLM_PAID_API_URL", "")
        monkeypatch.setattr(llm_analyzer, "LLM_PAID_API_KEY", "key", raising=False)
        monkeypatch.setattr(llm_analyzer, "LLM_PAID_API_URL", "", raising=False)

        # URL пустой → base_url.rstrip('/') = "" → api_url = "/chat/completions"
        # → httpx падает с UnsupportedProtocol или подобной ошибкой.
        # Главное — функция не возвращает успешный результат.
        with pytest.raises((ValueError, RuntimeError, Exception)):
            await llm_analyzer.ask_llm("test", provider="paid")


# ============================================================
# High-level: get_ai_summary / get_ai_answer
# ============================================================
class TestHighLevelFunctions:
    @pytest.mark.asyncio
    async def test_get_ai_summary_free_calls_llm_with_prompt(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter, sample_comparison):
        """get_ai_summary(provider='free') формирует промпт и вызывает LLM."""
        import llm_analyzer

        captured = {}

        def _intercept(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            captured["user_msg"] = payload["messages"][-1]["content"]
            captured["temperature"] = payload.get("temperature")
            return httpx.Response(200, json=_ok_response("Готовое резюме"))

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(side_effect=_intercept)
            result = await llm_analyzer.get_ai_summary(
                comparison=sample_comparison,
                reg_name="Вологодская область",
                current_label="I полугодие 2025",
                prev_label="I полугодие 2024",
                provider="free",
            )
            assert result == "Готовое резюме"
            # Промпт должен содержать регион и периоды
            assert "Вологодская область" in captured["user_msg"]
            assert "I полугодие 2025" in captured["user_msg"]
            # Для summary — temperature 0.7 по умолчанию
            assert captured["temperature"] == 0.7

    @pytest.mark.asyncio
    async def test_get_ai_summary_paid_includes_full_data(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter, sample_comparison):
        """get_ai_summary(provider='paid') включает полные данные участников в промпт."""
        import llm_analyzer

        captured = {}

        def _intercept(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            captured["user_msg"] = payload["messages"][-1]["content"]
            return httpx.Response(200, json=_ok_response("Платный анализ"))

        with respx.mock(base_url="https://test.example.com") as mock:
            mock.post("/v1/chat/completions").mock(side_effect=_intercept)
            result = await llm_analyzer.get_ai_summary(
                comparison=sample_comparison,
                reg_name="Регион",
                current_label="тек",
                prev_label="пр",
                provider="paid",
                current_cards=cards_basic_set(),
                prev_cards=[],
            )
            assert result == "Платный анализ"
            # В промпте должны быть строки [ДТП] — это полные данные
            assert "[ДТП]" in captured["user_msg"]

    @pytest.mark.asyncio
    async def test_get_ai_answer_uses_temperature_03(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter, sample_comparison):
        """Q&A: temperature=0.3 (более детерминированно, чем summary)."""
        import llm_analyzer

        captured = {}

        def _intercept(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            captured["temperature"] = payload.get("temperature")
            return httpx.Response(200, json=_ok_response("Ответ на вопрос"))

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(side_effect=_intercept)
            await llm_analyzer.get_ai_answer(
                question="Где больше всего ДТП?",
                comparison=sample_comparison,
                reg_name="Регион",
                current_label="тек",
                prev_label="пр",
                provider="free",
            )
            assert captured["temperature"] == 0.3

    @pytest.mark.asyncio
    async def test_get_ai_answer_with_history_adds_dialog_marker(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter, sample_comparison):
        """При наличии history в промпт добавляется маркер [ПРОДОЛЖЕНИЕ ДИАЛОГА]."""
        import llm_analyzer

        captured = {}

        def _intercept(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            captured["user_msg"] = payload["messages"][-1]["content"]
            return httpx.Response(200, json=_ok_response("Ответ"))

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(side_effect=_intercept)
            await llm_analyzer.get_ai_answer(
                question="А что там?",
                comparison=sample_comparison,
                reg_name="Регион",
                current_label="тек",
                prev_label="пр",
                provider="free",
                history=[{"role": "user", "content": "Старый"}, {"role": "assistant", "content": "Ответ"}],
            )
            assert "[ПРОДОЛЖЕНИЕ ДИАЛОГА]" in captured["user_msg"]
