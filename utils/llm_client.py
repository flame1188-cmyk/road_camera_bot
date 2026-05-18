"""
GLM API клиент для текстовых и визуальных запросов.

Поддерживает:
  - get_ai_summary — анализ ДТП с использованием GLM текстовой модели
  - get_ai_answer — вопросы по данным
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Awaitable

import httpx

logger = logging.getLogger(__name__)

# Rate limiting
_last_llm_call_time: float = 0
_MIN_INTERVAL_SECONDS = 60


async def _rate_limit_wait(progress_callback: Callable[[str], Awaitable[None]] | None = None) -> None:
    """Ждёт, если с последнего вызова LLM прошло меньше 60 секунд."""
    global _last_llm_call_time
    now = time.time()
    elapsed = now - _last_llm_call_time
    if elapsed < _MIN_INTERVAL_SECONDS and _last_llm_call_time > 0:
        wait_time = _MIN_INTERVAL_SECONDS - elapsed
        if progress_callback:
            for remaining in range(int(wait_time), 0, -10):
                await progress_callback(f"Ожидание перед запросом к нейросети...\nОсталось: {remaining} сек")
                await asyncio.sleep(min(10, remaining))
        else:
            await asyncio.sleep(wait_time)
    _last_llm_call_time = time.time()


async def _call_glm_api(
    messages: list[dict[str, Any]],
    api_key: str,
    api_url: str = "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    model: str = "glm-4-flash",
) -> str:
    """Выполняет запрос к GLM API и возвращает текст ответа."""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 4096,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(verify=False, timeout=120) as client:
        resp = await client.post(api_url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    choices = data.get("choices", [])
    if not choices:
        raise ValueError("Пустой ответ от GLM API")
    return choices[0].get("message", {}).get("content", "")


async def get_ai_summary(
    comparison: dict[str, Any],
    reg_name: str,
    current_label: str,
    prev_label: str,
    raw_supplement: str,
    news_context: str = "",
    progress_callback: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """
    Запрашивает у GLM текстовое резюме сравнительного анализа ДТП.
    """
    from config import LLM_API_KEY, LLM_API_URL

    if not LLM_API_KEY:
        raise ValueError("LLM_API_KEY не задан")

    await _rate_limit_wait(progress_callback)

    system_prompt = (
        "Ты — эксперт по безопасности дорожного движения. "
        "Проанализируй данные о ДТП за два периода и дай структурированное резюме.\n\n"
        "Формат ответа:\n"
        "1. Краткое резюме (2-3 предложения)\n"
        "2. Основные изменения (рост/снижение)\n"
        "3. Наиболее опасные виды ДТП\n"
        "4. Рекомендации\n\n"
        "Пиши на русском языке."
    )

    # Формируем данные для промпта
    total_cur = comparison.get("total", {}).get("current", 0)
    total_prev = comparison.get("total", {}).get("previous", 0)
    deaths_cur = comparison.get("deaths", {}).get("current", 0)
    deaths_prev = comparison.get("deaths", {}).get("previous", 0)

    data_summary = (
        f"Регион: {reg_name}\n"
        f"Текущий период: {current_label} ({total_cur} ДТП, {deaths_cur} погибло)\n"
        f"Предыдущий период: {prev_label} ({total_prev} ДТП, {deaths_prev} погибло)\n\n"
    )

    top_5 = comparison.get("top_5_types", [])
    if top_5:
        data_summary += "Топ-5 видов ДТП (текущий):\n"
        for t, cnt in top_5:
            data_summary += f"  - {t}: {cnt}\n"
        data_summary += "\n"

    user_message = f"{data_summary}\nДетальные данные:\n{raw_supplement}"
    if news_context:
        user_message += f"\n\nНовости:\n{news_context}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    logger.info("LLM: запрос AI-резюме...")
    response = await _call_glm_api(messages, LLM_API_KEY, LLM_API_URL)
    logger.info(f"LLM: получено резюме ({len(response)} символов)")
    return response


async def get_ai_answer(
    question: str,
    context_data: str,
) -> str:
    """
    Отвечает на вопрос пользователя по данным ДТП.
    """
    from config import LLM_API_KEY, LLM_API_URL

    if not LLM_API_KEY:
        raise ValueError("LLM_API_KEY не задан")

    await _rate_limit_wait()

    messages = [
        {"role": "system", "content": (
            "Ты — эксперт по безопасности дорожного движения. "
            "Отвечай на вопросы пользователя на основе предоставленных данных о ДТП. "
            "Если данных недостаточно, так и скажи. Пиши на русском."
        )},
        {"role": "user", "content": f"Данные о ДТП:\n{context_data}\n\nВопрос: {question}"},
    ]

    logger.info(f"LLM: вопрос пользователя: {question[:100]}...")
    response = await _call_glm_api(messages, LLM_API_KEY, LLM_API_URL)
    return response
