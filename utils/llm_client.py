"""
GLM API клиент для текстовых и визуальных запросов.

Поддерживает:
  - get_ai_summary — анализ ДТП с использованием GLM текстовой модели
  - get_ai_answer — вопросы по данным
  - analyze_nearby_accidents — LLM-анализ ДТП рядом с точкой оценки
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


async def analyze_nearby_accidents(
    nearby_accidents: list[dict[str, Any]],
    address: str,
    road_category: str = "",
    radius_m: int = 100,
    progress_callback: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """
    Анализирует ДТП рядом с оцениваемой точкой через текстовую LLM.

    Формирует структурированное резюме:
    - Сколько ДТП, погибло, ранено
    - Типы ДТП и нарушения ПДД
    - Связь с целесообразностью установки камеры
    - Рекомендации

    Args:
        nearby_accidents: Список карточек ДТП рядом с точкой (от find_nearby_accidents).
        address: Адрес точки оценки.
        road_category: Категория дороги из OSM (городская/загородная).
        radius_m: Радиус поиска ДТП в метрах.
        progress_callback: Callback для отображения прогресса.

    Returns:
        Текстовое резюме от LLM или пустую строку при ошибке.
    """
    from config import LLM_API_KEY, LLM_API_URL

    if not LLM_API_KEY:
        logger.warning("LLM_API_KEY не задан — анализ ДТП пропущен")
        return ""

    if not nearby_accidents:
        return ""

    await _rate_limit_wait(progress_callback)

    # Формируем сводку ДТП
    total = len(nearby_accidents)
    deaths = sum(int(c.get("pog", 0) or 0) for c in nearby_accidents)
    injured = sum(int(c.get("ran", 0) or 0) for c in nearby_accidents)

    accident_details = []
    for i, card in enumerate(nearby_accidents[:20], 1):  # max 20 карточек
        date = card.get("date_dtp", "?")
        time_val = card.get("time", "?")
        dtpv = card.get("dtpv", "?")
        dist = card.get("_distance_m", "?")
        np = card.get("np", "")
        street = card.get("street", "")
        dor = card.get("dor", "")
        place = ", ".join(p for p in [np, street, dor] if p) or "Не указано"

        # Нарушения ПДД
        npdd_list = card.get("_npdd", "")
        violations = str(npdd_list)[:100] if npdd_list else "Не указано"

        detail = (
            f"  {i}. {date} {time_val} | Вид: {dtpv} | Место: {place} "
            f"| Расст.: {dist}м | Погибло: {card.get('pog', 0)} | Ранено: {card.get('ran', 0)} "
            f"| Нарушение: {violations}"
        )
        accident_details.append(detail)

    accidents_text = "\n".join(accident_details)

    zone_type = "в населённом пункте" if "город" in road_category.lower() else "на загородной дороге"

    system_prompt = (
        "Ты — российский эксперт по безопасности дорожного движения, оценивающий участок дороги "
        "для установки комплекса фотовидеофиксации нарушений ПДД.\n\n"
        "Проанализируй данные о ДТП рядом с оцениваемой точкой и дай краткое экспертное заключение.\n\n"
        "Формат ответа (строго 2-3 предложения, без нумерации):\n"
        "- Упомяни количество и тяжесть ДТП\n"
        "- Укажи характер типичных нарушений\n"
        "- Сделай вывод о том, подтверждают ли ДТП целесообразность установки камеры\n\n"
        "Пиши на русском языке. Будь кратким и конкретным."
    )

    user_message = (
        f"Точка оценки: {address or 'Координаты'}\n"
        f"Тип зоны: {zone_type}\n"
        f"Радиус поиска ДТП: {radius_m} м\n"
        f"Всего ДТП найдено: {total}\n"
        f"Погибло: {deaths}, Ранено: {injured}\n\n"
        f"Детализация ДТП:\n{accidents_text}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    try:
        logger.info(f"LLM: анализ {total} ДТП рядом с точкой...")
        response = await _call_glm_api(messages, LLM_API_KEY, LLM_API_URL)
        logger.info(f"LLM: получен анализ ДТП ({len(response)} символов)")
        return response.strip()
    except Exception as e:
        logger.error(f"LLM: ошибка анализа ДТП: {e}")
        return ""
