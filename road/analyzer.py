"""
Главный модуль оценки участка дороги для установки комплекса фотовидеофиксации.

Оркестрирует: координаты → изображения → OSM → VLM → отчёт.
"""

from __future__ import annotations

import asyncio
import logging
import re
import math
from typing import Any, Callable, Awaitable

from road.osm_data import get_road_data, format_osm_summary
from road.panorama import collect_road_images
from road.vlm import analyze_road_images, format_expert_assessment
from road.report import generate_excel_report, get_report_filename

logger = logging.getLogger(__name__)


def parse_coordinates(text: str) -> tuple[float, float] | None:
    """Извлекает координаты из текста сообщения пользователя."""
    text = text.strip()
    text = re.sub(r'^/(check|road|assess|analyze)\s*', '', text, flags=re.IGNORECASE)

    # Пару чисел
    coord_pattern = r'(\d{1,2}[.,]\d{3,})\s*[,;\s]\s*(\d{1,3}[.,]\d{3,})'
    match = re.search(coord_pattern, text)
    if match:
        lat = float(match.group(1).replace(',', '.'))
        lon = float(match.group(2).replace(',', '.'))
        if 41.0 <= lat <= 82.0 and 19.0 <= lon <= 180.0:
            return (lat, lon)
        if 41.0 <= lon <= 82.0 and 19.0 <= lat <= 180.0:
            return (lon, lat)
        return (lat, lon)

    # Google Maps URL
    g = re.search(r'google.*[?&]q=([-\d.]+),([-\d.]+)', text, re.IGNORECASE)
    if g:
        return (float(g.group(1)), float(g.group(2)))

    # Яндекс Карты URL
    y = re.search(r'yandex.*[?&]ll=([-\d.]+)[%,2C]+([-\d.]+)', text, re.IGNORECASE)
    if y:
        return (float(y.group(2)), float(y.group(1)))

    y2 = re.search(r'yandex.*[?&]ll=([-\d.]+),([-\d.]+)', text, re.IGNORECASE)
    if y2:
        return (float(y2.group(2)), float(y2.group(1)))

    return None


async def geocode_address(lat: float, lon: float) -> str:
    """Получает адрес по координатам через Nominatim."""
    import httpx
    url = "https://nominatim.openstreetmap.org/reverse"
    params = {"lat": str(lat), "lon": str(lon), "format": "json", "accept-language": "ru", "zoom": 18}
    try:
        async with httpx.AsyncClient(verify=False, timeout=15) as client:
            resp = await client.get(url, params=params, headers={"User-Agent": "RoadAssessmentBot/1.0"})
            resp.raise_for_status()
            data = resp.json()
        address = data.get("display_name", "")
        parts = address.split(", ")
        if len(parts) > 5:
            address = ", ".join(parts[:5])
        return address
    except Exception as e:
        logger.warning(f"Геокодирование: {e}")
        return ""


async def find_nearby_accidents(
    lat: float, lon: float, radius_km: float = 0.5,
    accidents: list[dict] | None = None,
) -> list[dict]:
    """Находит ДТП в радиусе от точки."""
    if not accidents:
        return []
    R = 6371.0
    lat_r = math.radians(lat)
    nearby = []
    for card in accidents:
        try:
            card_lat = float(str(card.get("coord_w", "")).strip())
            card_lon = float(str(card.get("coord_l", "")).strip())
            if card_lat == 0 or card_lon == 0:
                continue
            dlat = math.radians(card_lat - lat)
            dlon = math.radians(card_lon - lon)
            a = (math.sin(dlat / 2) ** 2 + math.cos(lat_r) * math.cos(math.radians(card_lat)) * math.sin(dlon / 2) ** 2)
            distance = R * 2 * math.asin(math.sqrt(min(a, 1.0)))
            if distance <= radius_km:
                card["_distance_m"] = round(distance * 1000, 0)
                nearby.append(card)
        except (ValueError, TypeError):
            continue
    nearby.sort(key=lambda x: x.get("_distance_m", float("inf")))
    return nearby


async def analyze_road_section(
    lat: float, lon: float,
    vlm_api_key: str | None = None,
    mapillary_api_key: str | None = None,
    mapillary_access_token: str | None = None,
    vlm_api_url: str = "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    vlm_model: str = "glm-4v-flash",
    progress_callback: Callable[[str], Awaitable[None]] | None = None,
    accidents: list[dict] | None = None,
) -> dict[str, Any]:
    """Полный анализ участка дороги."""
    result = {
        "coordinates": (lat, lon), "address": "", "osm_data": None,
        "images": None, "vlm_result": None, "formatted_message": "",
        "nearby_accidents": [], "excel_bytes": None, "excel_filename": "", "errors": [],
    }

    async def update_progress(msg: str):
        if progress_callback:
            try:
                await progress_callback(msg)
            except Exception:
                pass

    # Шаг 1: Адрес
    await update_progress(f"Определение адреса...\nКоординаты: {lat}, {lon}")
    address = await geocode_address(lat, lon)
    result["address"] = address

    # Шаг 2: Параллельно: изображения + OSM
    await update_progress(f"Сбор изображений и данных OSM...\nАдрес: {address or 'определяется...'}")
    images_task = collect_road_images(lat, lon)
    osm_task = get_road_data(lat, lon)
    images_result, osm_data = await asyncio.gather(images_task, osm_task, return_exceptions=True)

    if isinstance(images_result, Exception):
        result["errors"].append(f"Изображения: {images_result}")
        images_result = {"street_images": [], "sources_used": []}
    else:
        result["images"] = images_result

    if isinstance(osm_data, Exception):
        result["errors"].append(f"OSM: {osm_data}")
        osm_data = {}
    else:
        result["osm_data"] = osm_data

    # Шаг 3: VLM
    images_b64_list = [img["base64"] for img in (images_result.get("street_images", [])) if img.get("base64")]
    if vlm_api_key and images_b64_list:
        osm_summary = format_osm_summary(osm_data) if osm_data else "Нет данных"
        await update_progress(f"Анализ через нейросеть...\nИзображений: {len(images_b64_list)}")
        vlm_result = await analyze_road_images(
            images_b64_list=images_b64_list, osm_summary=osm_summary,
            api_key=vlm_api_key, api_url=vlm_api_url, model=vlm_model,
        )
    else:
        osm_summary = format_osm_summary(osm_data) if osm_data else "Нет данных"
        vlm_result = {"osm_based_assessment": True, "visual_notes": "Оценка только на основе OSM"}
    result["vlm_result"] = vlm_result

    # Шаг 4: ДТП поблизости
    if accidents:
        await update_progress("Проверка ДТП поблизости...")
        result["nearby_accidents"] = await find_nearby_accidents(lat, lon, radius_km=0.5, accidents=accidents)

    # Шаг 5: Форматирование
    await update_progress("Формирование отчёта...")
    formatted = format_expert_assessment(vlm_result, lat, lon, address, osm_data)
    if result["nearby_accidents"]:
        nc = len(result["nearby_accidents"])
        deaths = sum(int(c.get("pog", 0) or 0) for c in result["nearby_accidents"])
        injured = sum(int(c.get("ran", 0) or 0) for c in result["nearby_accidents"])
        formatted += f"\n\nСТАТИСТИКА ДТП В РАДИУСЕ 500 М:\n  ДТП: {nc}\n  Погибло: {deaths}\n  Ранено: {injured}"
    formatted += f"\n\nИсточники: {', '.join(images_result.get('sources_used', ['нет']))}"
    if result["errors"]:
        formatted += "\nОшибки: " + "; ".join(result["errors"])
    result["formatted_message"] = formatted

    # Шаг 6: Excel
    try:
        result["excel_bytes"] = generate_excel_report(lat, lon, address, vlm_result, osm_data, result["nearby_accidents"])
        result["excel_filename"] = get_report_filename(lat, lon)
    except Exception as e:
        result["errors"].append(f"Excel: {e}")

    return result
