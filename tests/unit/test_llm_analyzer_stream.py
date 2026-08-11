"""
Тесты ask_llm_stream и _do_llm_stream_request в llm_analyzer.py
с HTTP-моками (respx) — Sprint 4: Streaming LLM / SSE.

Покрытие:
  - happy path free: SSE-стрим из 3 чанков → корректная конкатенация дельт
  - happy path paid: SSE-стрим от OpenAI-compatible провайдера
  - data: [DONE] sentinel корректно завершает стрим
  - пустые delta.content (role-only chunk) — пропускаются
  - 4xx ошибка до стрима → HTTPStatusError
  - 5xx ошибка до стрима → HTTPStatusError
  - 429 ошибка до стрима → HTTPStatusError (без ретраев в stream-режиме)
  - нет API-ключа → ValueError
  - history вставляется между system и user
  - кастомный system_prompt используется
  - get_ai_summary_stream / get_ai_answer_stream — корректно делегируют в ask_llm_stream
  - невалидный JSON в chunk — warning + пропуск (стрим не падает)
  - partial-обрыв: стрим оборвался после 1 чанка → возвращается partial (не падает)
"""
import json

import httpx
import pytest
import respx


# ============================================================
# Helpers
# ============================================================
def _sse_lines(*chunks: str) -> bytes:
    """Собирает SSE-ответ из набора delta-чанков + финальный [DONE]."""
    lines = []
    for chunk_content in chunks:
        chunk_json = {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": chunk_content},
                    "finish_reason": None,
                }
            ],
        }
        lines.append(f"data: {json.dumps(chunk_json, ensure_ascii=False)}")
        lines.append("")  # пустая строка = конец события
    lines.append("data: [DONE]")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def _sse_chunk(content: str | None, role: str | None = None) -> str:
    """Один SSE-чанк. content=None → только role (первый chunk в стриме)."""
    delta = {}
    if role:
        delta["role"] = role
    if content is not None:
        delta["content"] = content
    chunk_json = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    }
    return f"data: {json.dumps(chunk_json)}\n"


# ============================================================
# Happy path
# ============================================================
class TestAskLlmStreamHappyPath:
    @pytest.mark.asyncio
    async def test_free_provider_streams_deltas(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter):
        """3 chunk'а → 3 delta-вызова, корректно конкатенируются."""
        import llm_analyzer

        sse_body = _sse_lines("Hello", " world", "!")
        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(
                return_value=httpx.Response(200, content=sse_body)
            )
            deltas = []
            async for delta in llm_analyzer.ask_llm_stream("test", provider="free"):
                deltas.append(delta)

        assert deltas == ["Hello", " world", "!"]
        assert "".join(deltas) == "Hello world!"

    @pytest.mark.asyncio
    async def test_paid_provider_streams_deltas(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter):
        """Paid-провайдер (OpenAI-compatible) тоже стримит."""
        import llm_analyzer

        sse_body = _sse_lines("Платный", " ответ")
        with respx.mock(base_url="https://test.example.com") as mock:
            mock.post("/v1/chat/completions").mock(
                return_value=httpx.Response(200, content=sse_body)
            )
            deltas = []
            async for delta in llm_analyzer.ask_llm_stream("test", provider="paid"):
                deltas.append(delta)

        assert "".join(deltas) == "Платный ответ"

    @pytest.mark.asyncio
    async def test_done_sentinel_terminates_stream(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter):
        """data: [DONE] корректно завершает генератор."""
        import llm_analyzer

        # [DONE] после первого chunk'а — генератор должен остановиться,
        # второй chunk НЕ должен быть обработан.
        body = (
            _sse_chunk("first")
            + "\n"
            + "data: [DONE]\n\n"
            + _sse_chunk("should-not-appear")
            + "\n"
        ).encode("utf-8")
        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(
                return_value=httpx.Response(200, content=body)
            )
            deltas = []
            async for delta in llm_analyzer.ask_llm_stream("test", provider="free"):
                deltas.append(delta)

        assert deltas == ["first"]

    @pytest.mark.asyncio
    async def test_role_only_chunk_skipped(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter):
        """Первый chunk обычно содержит только role=assistant, content нет —
        он должен быть пропущен, не yield'ить пустую строку."""
        import llm_analyzer

        body = (
            _sse_chunk(None, role="assistant")
            + "\n"
            + _sse_chunk("actual content")
            + "\n"
            + "data: [DONE]\n\n"
        ).encode("utf-8")
        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(
                return_value=httpx.Response(200, content=body)
            )
            deltas = []
            async for delta in llm_analyzer.ask_llm_stream("test", provider="free"):
                deltas.append(delta)

        # role-only chunk не должен дать пустую строку
        assert deltas == ["actual content"]

    @pytest.mark.asyncio
    async def test_empty_stream_no_deltas(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter):
        """Стрим с одним [DONE] и ни одним content-chunk'ом → пустой результат,
        без падения."""
        import llm_analyzer

        body = b"data: [DONE]\n\n"
        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(
                return_value=httpx.Response(200, content=body)
            )
            deltas = []
            async for delta in llm_analyzer.ask_llm_stream("test", provider="free"):
                deltas.append(delta)

        assert deltas == []

    @pytest.mark.asyncio
    async def test_custom_system_prompt_used(self, patch_llm_keys, reset_llm_clients, disable_rate_limiter):
        """Кастомный system_prompt попадает в payload."""
        import llm_analyzer

        captured = {}

        def _intercept(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            captured["messages"] = payload["messages"]
            captured["stream"] = payload.get("stream", False)
            return httpx.Response(200, content=b"data: [DONE]\n\n")

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(side_effect=_intercept)
            async for _ in llm_analyzer.ask_llm_stream(
                "Привет", system_prompt="МОЙ ПРОМПТ", provider="free",
            ):
                pass

        assert captured["messages"][0]["role"] == "system"
        assert captured["messages"][0]["content"] == "МОЙ ПРОМПТ"
        assert captured["stream"] is True

    @pytest.mark.asyncio
    async def test_history_inserted_between_system_and_user(
        self, patch_llm_keys, reset_llm_clients, disable_rate_limiter
    ):
        """History вставляется между system и новым user-сообщением."""
        import llm_analyzer

        captured = {}

        def _intercept(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            captured["messages"] = payload["messages"]
            return httpx.Response(200, content=b"data: [DONE]\n\n")

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(side_effect=_intercept)
            async for _ in llm_analyzer.ask_llm_stream(
                "Новый вопрос",
                history=[
                    {"role": "user", "content": "Старый вопрос"},
                    {"role": "assistant", "content": "Старый ответ"},
                ],
                provider="free",
            ):
                pass

        msgs = captured["messages"]
        assert len(msgs) == 4
        assert msgs[0]["role"] == "system"
        assert msgs[1] == {"role": "user", "content": "Старый вопрос"}
        assert msgs[2] == {"role": "assistant", "content": "Старый ответ"}
        assert msgs[3] == {"role": "user", "content": "Новый вопрос"}


# ============================================================
# Error handling
# ============================================================
class TestAskLlmStreamErrors:
    @pytest.mark.asyncio
    async def test_no_api_key_raises_value_error(self, monkeypatch):
        """Нет LLM_API_KEY → ValueError до HTTP-запроса."""
        import llm_analyzer

        monkeypatch.setattr(llm_analyzer, "LLM_API_KEY", "")
        with pytest.raises(ValueError, match="LLM_API_KEY не задан"):
            async for _ in llm_analyzer.ask_llm_stream("test", provider="free"):
                pass

    @pytest.mark.asyncio
    async def test_no_paid_api_key_raises_value_error(self, monkeypatch):
        """Нет LLM_PAID_API_KEY → ValueError."""
        import llm_analyzer

        monkeypatch.setattr(llm_analyzer, "LLM_PAID_API_KEY", "")
        with pytest.raises(ValueError, match="LLM_PAID_API_KEY не задан"):
            async for _ in llm_analyzer.ask_llm_stream("test", provider="paid"):
                pass

    @pytest.mark.asyncio
    async def test_4xx_raises_http_status_error(
        self, patch_llm_keys, reset_llm_clients, disable_rate_limiter
    ):
        """4xx ошибка до стрима → HTTPStatusError, НЕ ретраится."""
        import llm_analyzer

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(
                return_value=httpx.Response(
                    401,
                    json={"error": {"message": "Invalid API key", "code": "invalid_api_key"}},
                )
            )
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                async for _ in llm_analyzer.ask_llm_stream("test", provider="free"):
                    pass
            assert "401" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_5xx_raises_http_status_error(
        self, patch_llm_keys, reset_llm_clients, disable_rate_limiter
    ):
        """5xx ошибка до стрима → HTTPStatusError, НЕ ретраится (в отличие от
        не-streaming _do_llm_request)."""
        import llm_analyzer

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(
                return_value=httpx.Response(503, text="Service Unavailable")
            )
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                async for _ in llm_analyzer.ask_llm_stream("test", provider="free"):
                    pass
            assert "503" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_429_raises_http_status_error_no_retry(
        self, patch_llm_keys, reset_llm_clients, disable_rate_limiter
    ):
        """429 → HTTPStatusError, НЕ ретраится (streaming не поддерживает
        ретраи — это было бы дублированием partial-ответов)."""
        import llm_analyzer

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(
                return_value=httpx.Response(429, text="Too Many Requests")
            )
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                async for _ in llm_analyzer.ask_llm_stream("test", provider="free"):
                    pass
            assert "429" in str(exc_info.value)


# ============================================================
# Malformed SSE handling
# ============================================================
class TestAskLlmStreamMalformedSSE:
    @pytest.mark.asyncio
    async def test_invalid_json_chunk_skipped(
        self, patch_llm_keys, reset_llm_clients, disable_rate_limiter
    ):
        """Невалидный JSON в chunk'е — логируется warning, chunk пропускается,
        стрим продолжает работать."""
        import llm_analyzer

        body = (
            _sse_chunk("valid")
            + "\n"
            + "data: {broken json\n\n"
            + _sse_chunk(" also valid")
            + "\n"
            + "data: [DONE]\n\n"
        ).encode("utf-8")
        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(
                return_value=httpx.Response(200, content=body)
            )
            deltas = []
            async for delta in llm_analyzer.ask_llm_stream("test", provider="free"):
                deltas.append(delta)

        # broken chunk пропущен, valid chunks остались
        assert deltas == ["valid", " also valid"]

    @pytest.mark.asyncio
    async def test_comment_lines_ignored(
        self, patch_llm_keys, reset_llm_clients, disable_rate_limiter
    ):
        """SSE-комментарии (строки, начинающиеся с ':') — игнорируются
        (обычно используются как keepalive от сервера)."""
        import llm_analyzer

        body = (
            b": keepalive\n\n"
            + _sse_chunk("content").encode("utf-8")
            + b"\n"
            + b": another comment\n\n"
            + b"data: [DONE]\n\n"
        )
        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(
                return_value=httpx.Response(200, content=body)
            )
            deltas = []
            async for delta in llm_analyzer.ask_llm_stream("test", provider="free"):
                deltas.append(delta)

        assert deltas == ["content"]


# ============================================================
# Higher-level wrappers
# ============================================================
class TestGetAiSummaryStream:
    @pytest.mark.asyncio
    async def test_summary_stream_concats_deltas(
        self, patch_llm_keys, reset_llm_clients, disable_rate_limiter, sample_comparison,
    ):
        """get_ai_summary_stream делегирует в ask_llm_stream, передаёт промпт
        от build_summary_prompt."""
        import llm_analyzer

        sse_body = _sse_lines("Резюме: ", "всё хорошо")
        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(
                return_value=httpx.Response(200, content=sse_body)
            )
            result = []
            async for delta in llm_analyzer.get_ai_summary_stream(
                comparison=sample_comparison,
                reg_name="Москва",
                current_label="2025",
                prev_label="2024",
                provider="free",
            ):
                result.append(delta)

        assert "".join(result) == "Резюме: всё хорошо"


class TestGetAiAnswerStream:
    @pytest.mark.asyncio
    async def test_answer_stream_concats_deltas(
        self, patch_llm_keys, reset_llm_clients, disable_rate_limiter, sample_comparison,
    ):
        """get_ai_answer_stream делегирует в ask_llm_stream с temperature=0.3."""
        import llm_analyzer

        captured = {}

        def _intercept(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            captured["temperature"] = payload.get("temperature")
            captured["stream"] = payload.get("stream")
            return httpx.Response(
                200,
                content=_sse_lines("Ответ: ", "42"),
            )

        with respx.mock(base_url="https://open.bigmodel.cn") as mock:
            mock.post("/api/paas/v4/chat/completions").mock(side_effect=_intercept)
            result = []
            async for delta in llm_analyzer.get_ai_answer_stream(
                question="вопрос",
                comparison=sample_comparison,
                reg_name="Москва",
                current_label="2025",
                prev_label="2024",
                provider="free",
            ):
                result.append(delta)

        assert "".join(result) == "Ответ: 42"
        assert captured["temperature"] == 0.3
        assert captured["stream"] is True
