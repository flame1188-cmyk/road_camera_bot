"""
Модуль анализа изображений участка дороги через VLM (GLM-4V).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

EXPERT_PROMPT = """Ты — российский эксперт-дорожник, оценивающий участок дороги для установки комплекса фотовидеофиксации нарушений ПДД (камера трёхмерного измерения скорости, камера фиксации проезда на красный, камера контроля полосы движения и т.д.).

На снимках — Яндекс Панорамы участка дороги (несколько направлений). Определи по ним дорожную обстановку и дополни данные OSM.

Оцени по 5 разделам:

1. ИНФРАСТРУКТУРА:
- Опоры освещения: есть/нет/не видно
- Количество опор в зоне видимости
- Провода (подвод питания к опорам): есть/нет
- Тип дороги: городская магистральная / городская районная / жилая улица / загородная / автомагистраль
- Количество полос движения (в одном направлении)
- Разделительная полоса: есть/нет
- Тротуары: есть/нет

2. ДОРОЖНЫЕ ОБЪЕКТЫ:
- Дорожные знаки (перечислить: ограничения скорости, перекрёсток, пешеходный переход и т.д.)
- Разметка: горизонтальная (осевые, стоп-линии, зебра)
- Пешеходный переход: тип (регулируемый/нерегулируемый/островок безопасности)
- Светофор: есть/нет, тип (транспортный/пешеходный)
- Остановки общественного транспорта

3. ДОРОЖНАЯ ОБСТАНОВКА:
- Интенсивность движения: низкая/средняя/высокая
- Жилая застройка рядом: да/нет
- Школа/детский сад рядом: да/нет
- Парковка вдоль дороги: да/нет

4. ЦЕЛЕСООБРАЗНОСТЬ УСТАНОВКИ КАМЕРЫ:
- Вероятные нарушения с указанием статей КоАП РФ:
  - ст. 12.9 (превышение скорости)
  - ст. 12.12 (проезд на запрещающий сигнал)
  - ст. 12.15 (выезд на полосу встречного движения)
  - ст. 12.16 (невыполнение требований знаков и разметки)
  - ст. 12.18 (непредоставление преимущества пешеходам)
  - ст. 12.24 (нарушение правил остановки/стоянки)
- Оценка целесообразности от 1 до 10
- Рекомендуемый тип комплекса (трёхмерная измерительная / проезд на красный / контроль полосы / комбинированный)
- Рекомендуемые направления съёмки (азимуты в градусах)

5. ТЕХНИЧЕСКАЯ ВОЗМОЖНОСТЬ УСТАНОВКИ:
- Питание: от опор освещения / отдельная линия / нет
- Установка на существующую опору: возможно/нет
- Необходим фундамент: да/нет
- Помехи обзорности (деревья, столбы, здания): перечислить
- Общая оценка обзорности: отличная/хорошая/удовлетворительная/плохая

Данные OpenStreetMap:
{osm_data}
{hotspot_section}
Ответ ОБЯЗАТЕЛЬНО в формате JSON (только JSON, без пояснений):
{{
  "infrastructure": {{"lighting_poles": "", "pole_count": 0, "wires_visible": "", "road_type": "", "lane_count": 0, "median": "", "sidewalk": ""}},
  "road_objects": {{"signs": [], "marking": [], "crosswalk": {{"present": false, "type": ""}}, "traffic_light": false, "bus_stop": false}},
  "road_conditions": {{"traffic_intensity": "", "residential_area": false, "school_nearby": false, "kindergarten_nearby": false, "parking": false}},
  "expediency": {{"possible_violations": [], "efficiency_score": 0, "recommended_type": "", "recommended_directions": []}},
  "technical_feasibility": {{"power_supply": "", "install_on_existing_pole": false, "foundation_needed": false, "obstructions": [], "visibility_assessment": ""}},
  "visual_notes": ""
}}
"""


def _build_hotspot_section(hotspot_context: str) -> str:
    """Формирует секцию с данными об очаге ДТП для промпта."""
    if not hotspot_context:
        return ""
    return f"\nДополнительный контекст — это ОЧАГ ДТП (место концентрации аварийности):\n{hotspot_context}\n"


async def analyze_road_images(
    images_b64_list: list[str],
    osm_summary: str = "",
    api_key: str | None = None,
    api_url: str = "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    model: str = "glm-4v-flash",
    hotspot_context: str = "",
) -> dict[str, Any]:
    """Отправляет изображения в VLM для анализа."""
    if not images_b64_list:
        return {"error": "Нет изображений для анализа"}
    if not api_key:
        return {"error": "Не указан API-ключ VLM"}

    hotspot_section = _build_hotspot_section(hotspot_context)
    prompt = EXPERT_PROMPT.format(osm_data=osm_summary or "Данные OSM недоступны.", hotspot_section=hotspot_section)
    content = []
    for b64 in images_b64_list:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    content.append({"type": "text", "text": prompt})

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.3, "max_tokens": 4096,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    retry_delays = [15, 30, 45, 60, 90]
    max_retries = len(retry_delays)
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(verify=False, timeout=120) as client:
                resp = await client.post(api_url, json=payload, headers=headers)
                if resp.status_code == 429:
                    wait = retry_delays[attempt]
                    logger.warning(f"VLM 429, повтор через {wait}с (попытка {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                return {"error": "Пустой ответ от VLM"}
            text = choices[0].get("message", {}).get("content", "")
            return _parse_vlm_response(text)
        except httpx.HTTPStatusError as e:
            return {"error": f"Ошибка VLM: {e}"}
        except Exception as e:
            return {"error": f"Ошибка VLM: {e}"}
    return {"error": f"Ошибка VLM: превышен лимит запросов (429) после {max_retries} попыток"}


def _parse_vlm_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if "```json" in cleaned:
        start = cleaned.find("```json") + 7
        end = cleaned.find("```", start)
        cleaned = cleaned[start:end if end != -1 else len(cleaned)].strip()
    elif "```" in cleaned:
        start = cleaned.find("```") + 3
        end = cleaned.find("```", start)
        cleaned = cleaned[start:end if end != -1 else len(cleaned)].strip()
    if not cleaned.startswith("{"):
        brace_idx = cleaned.find("{")
        if brace_idx != -1:
            cleaned = cleaned[brace_idx:]
        last_brace = cleaned.rfind("}")
        if last_brace != -1:
            cleaned = cleaned[:last_brace + 1]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse error: {e}", "raw_text": text}


def format_expert_assessment(
    vlm_result: dict[str, Any], lat: float, lon: float,
    address: str = "", osm_data: dict | None = None,
) -> str:
    """Форматирует результаты анализа в сообщение для Telegram."""
    if "error" in vlm_result:
        return f"Ошибка анализа участка\n\nКоординаты: {lat}, {lon}\nПричина: {vlm_result['error']}"

    lines = ["ЭКСПЕРТНАЯ ОЦЕНКА УЧАСТКА ДОРОГИ", f"Координаты: {lat}, {lon}"]
    if address:
        lines.append(f"Адрес: {address}")
    lines.append("")

    infra = vlm_result.get("infrastructure", {})
    if infra:
        lines.append("ИНФРАСТРУКТУРА:")
        p = infra.get("lighting_poles", "не видно")
        lines.append(f"  {'✅' if p == 'да' else ('⚠️' if p == 'не видно' else '❌')} Опоры: {p} ({infra.get('pole_count', 0)} шт.)")
        w = infra.get("wires_visible", "не видно")
        lines.append(f"  {'✅' if w == 'да' else '❌'} Провода: {w}")
        lines.append(f"  Тип дороги: {infra.get('road_type', '?')}")
        lines.append(f"  Полосы: {infra.get('lane_count', '?')}")
        m = infra.get("median", "нет")
        lines.append(f"  {'✅' if m == 'да' else '❌'} Разделительная: {m}")
        sw = infra.get("sidewalk", "")
        if sw:
            lines.append(f"  Тротуар: {sw}")
        lines.append("")

    conditions = vlm_result.get("road_conditions", {})
    if conditions:
        lines.append("ДОРОЖНАЯ ОБСТАНОВКА:")
        lines.append(f"  Интенсивность: {conditions.get('traffic_intensity', '?')}")
        for key, label in [("residential_area", "Жилая зона"), ("school_nearby", "Школа"),
                           ("kindergarten_nearby", "Детский сад"), ("parking", "Парковка")]:
            val = conditions.get(key)
            if val:
                lines.append(f"  {label}: {'✅ да' if val else '❌ нет'}")
        lines.append("")

    objects = vlm_result.get("road_objects", {})
    if objects:
        lines.append("ДОРОЖНЫЕ ОБЪЕКТЫ:")
        signs = objects.get("signs", [])
        lines.append(f"  Знаки: {', '.join(str(s) for s in signs[:5]) if signs else 'не обнаружены'}")
        cw = objects.get("crosswalk", {})
        lines.append(f"  {'✅' if cw.get('present') else '❌'} Пешеходный переход: {cw.get('type', 'нет')}")
        lines.append(f"  {'✅' if objects.get('traffic_light') else '❌'} Светофор")
        lines.append("")

    exp = vlm_result.get("expediency", {})
    if exp:
        score = exp.get("efficiency_score", 0)
        lines.append(f"ЦЕЛЕСООБРАЗНОСТЬ: {score}/10")
        violations = exp.get("possible_violations", [])
        if violations:
            lines.append("Возможные нарушения:")
            for i, v in enumerate(violations[:7], 1):
                lines.append(f"  {i}. {v}")
        lines.append(f"Тип: {exp.get('recommended_type', '?')}")
        lines.append("")

    tech = vlm_result.get("technical_feasibility", {})
    if tech:
        lines.append(f"ТЕХНИЧЕСКАЯ ВОЗМОЖНОСТЬ: {tech.get('visibility_assessment', '?')}")
        lines.append(f"  Питание: {tech.get('power_supply', '?')}")
        lines.append(f"  Установка на опору: {'✅ да' if tech.get('install_on_existing_pole') else '❌ нет'}")
        lines.append(f"  Фундамент: {'⚠️ необходим' if tech.get('foundation_needed') else '✅ не требуется'}")
        obstructions = tech.get("obstructions", [])
        if obstructions:
            lines.append(f"  Помехи: {', '.join(str(o) for o in obstructions)}")
        lines.append("")

    notes = vlm_result.get("visual_notes", "")
    if notes:
        lines.append(f"ЗАМЕТКИ: {notes}")

    return "\n".join(lines)
