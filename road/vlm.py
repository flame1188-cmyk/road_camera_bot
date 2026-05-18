"""
Модуль анализа изображений участка дороги через VLM (GLM-4V).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

EXPERT_PROMPT = """Ты — эксперт-дорожник, оценивающий участок дороги для установки комплекса фотовидеофиксации нарушений ПДД. Изучи предоставленные снимки и дополнительные данные из OSM и заполни оценку:

1. ИНФРАСТРУКТУРА:
- Опоры освещения (да/нет/не видно)
- Количество опор в зоне видимости
- Провода (подвод питания)
- Тип дороги (городская/загородная/трасса)
- Полосы движения
- Разделительная полоса

2. ДОРОЖНЫЕ ОБЪЕКТЫ:
- Знаки, разметка
- Пешеходный переход
- Светофор
- Остановки транспорта

3. ДОРОЖНАЯ ОБСТАНОВКА:
- Интенсивность движения
- Жилая застройка
- Школа/детский сад

4. ЦЕЛЕСООБРАЗНОСТЬ:
- Возможные нарушения (статьи КоАП РФ)
- Оценка эффективности (1-10)
- Рекомендуемый тип комплекса
- Направления съёмки (азимуты)

5. ТЕХНИЧЕСКАЯ ВОЗМОЖНОСТЬ:
- Питание
- Установка на опору
- Фундамент
- Обзорность

Данные OSM:
{osm_data}

Ответ ОБЯЗАТЕЛЬНО в формате JSON (только JSON):
{{
  "infrastructure": {{"lighting_poles": "", "pole_count": 0, "wires_visible": "", "road_type": "", "lane_count": 0, "median": ""}},
  "road_objects": {{"signs": [], "marking": [], "crosswalk": {{"present": false, "type": ""}}, "traffic_light": false, "bus_stop": false}},
  "road_conditions": {{"traffic_intensity": "", "residential_area": false, "school_nearby": false, "kindergarten_nearby": false}},
  "expediency": {{"possible_violations": [], "efficiency_score": 0, "recommended_type": "", "recommended_directions": []}},
  "technical_feasibility": {{"power_supply": "", "install_on_existing_pole": false, "foundation_needed": false, "obstructions": [], "visibility_assessment": ""}},
  "visual_notes": ""
}}
"""


async def analyze_road_images(
    images_b64_list: list[str],
    osm_summary: str = "",
    api_key: str | None = None,
    api_url: str = "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    model: str = "glm-4v-flash",
) -> dict[str, Any]:
    """Отправляет изображения в VLM для анализа."""
    if not images_b64_list:
        return {"error": "Нет изображений для анализа"}
    if not api_key:
        return {"error": "Не указан API-ключ VLM"}

    prompt = EXPERT_PROMPT.format(osm_data=osm_summary or "Данные OSM недоступны.")
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

    try:
        async with httpx.AsyncClient(verify=False, timeout=120) as client:
            resp = await client.post(api_url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return {"error": "Пустой ответ от VLM"}
        text = choices[0].get("message", {}).get("content", "")
        return _parse_vlm_response(text)
    except Exception as e:
        return {"error": f"Ошибка VLM: {e}"}


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
        lines.append(f"ТЕХНИЧЕСКАЯ ВОЗМОЖНОСТЬ: {tech.get('power_supply', '?')}")
        lines.append(f"  Установка на опору: {'да' if tech.get('install_on_existing_pole') else 'нет'}")
        lines.append(f"  Фундамент: {'необходим' if tech.get('foundation_needed') else 'не требуется'}")
        lines.append("")

    notes = vlm_result.get("visual_notes", "")
    if notes:
        lines.append(f"ЗАМЕТКИ: {notes}")

    return "\n".join(lines)
