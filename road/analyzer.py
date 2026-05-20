"""
Главный модуль оценки участка дороги для установки комплекса фотовидеофиксации.

Оркестрирует: координаты -> регион GIBDD -> ДТП -> изображения -> OSM -> VLM -> LLM -> отчёт.
"""

from __future__ import annotations

import asyncio
import logging
import re
import math
from datetime import datetime
from typing import Any, Callable, Awaitable

from road.osm_data import get_road_data, format_osm_summary
from road.panorama import collect_road_images
from road.vlm import analyze_road_images, format_expert_assessment
from road.report import generate_excel_report, get_report_filename

logger = logging.getLogger(__name__)

RADIUS_NP_KM = 0.1
RADIUS_NON_NP_KM = 0.25


def parse_coordinates(text: str) -> tuple[float, float] | None:
    text = text.strip()
    text = re.sub(r'^/(check|road|assess|analyze)\s*', '', text, flags=re.IGNORECASE)
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
    g = re.search(r'google.*[?&]q=([-\d.]+),([-\d.]+)', text, re.IGNORECASE)
    if g:
        return (float(g.group(1)), float(g.group(2)))
    y = re.search(r'yandex.*[?&]ll=([-\d.]+)[%,2C]+([-\d.]+)', text, re.IGNORECASE)
    if y:
        return (float(y.group(2)), float(y.group(1)))
    y2 = re.search(r'yandex.*[?&]ll=([-\d.]+),([-\d.]+)', text, re.IGNORECASE)
    if y2:
        return (float(y2.group(2)), float(y2.group(1)))
    return None


async def geocode_address(lat: float, lon: float) -> tuple[str, dict]:
    """Получает адрес по координатам через Nominatim.
    Returns: (address_string, raw_nominatim_data).
    """
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
        return (address, data)
    except Exception as e:
        logger.warning(f"Геокодирование: {e}")
        return ("", {})


async def find_nearby_accidents(
    lat: float, lon: float, radius_km: float = 0.5,
    accidents: list[dict] | None = None,
) -> list[dict]:
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


async def _fetch_gibdd_accidents(region_code: str) -> list[dict[str, Any]]:
    from gibdd.api_client import fetch_dtp_data, extract_accident_cards
    now = datetime.now()
    prev_year = now.year - 1
    curr_year = now.year
    curr_month = now.month
    all_cards: list[dict[str, Any]] = []
    periods = []
    for m in range(1, 13):
        periods.append((f"{m}.{prev_year}", f"{prev_year}-{m:02d}"))
    for m in range(1, curr_month + 1):
        periods.append((f"{m}.{curr_year}", f"{curr_year}-{m:02d}"))
    logger.info(f"GIBDD: загрузка ДТП региона {region_code}, {len(periods)} периодов...")
    batch_size = 3
    for i in range(0, len(periods), batch_size):
        batch = periods[i:i + batch_size]
        async def _fetch_one(dat: str, label: str) -> list[dict]:
            try:
                resp = await fetch_dtp_data(dat=dat, reg=region_code)
                cards = extract_accident_cards(resp)
                if cards:
                    logger.info(f"GIBDD: {label} — {len(cards)} ДТП")
                return cards
            except Exception as e:
                logger.warning(f"GIBDD: {label} — ошибка: {e}")
                return []
        tasks = [_fetch_one(dat, label) for dat, label in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                all_cards.extend(r)
            elif isinstance(r, Exception):
                logger.warning(f"GIBDD: ошибка в батче: {r}")
        if i + batch_size < len(periods):
            await asyncio.sleep(1)
    seen = set()
    unique_cards = []
    for card in all_cards:
        card_id = card.get("empt_number", "")
        if card_id and card_id not in seen:
            seen.add(card_id)
            unique_cards.append(card)
    logger.info(f"GIBDD: всего {len(unique_cards)} уникальных ДТП (регион {region_code})")
    return unique_cards


def _determine_radius(road_category: str) -> float:
    if road_category and "загород" in road_category.lower():
        return RADIUS_NON_NP_KM
    return RADIUS_NP_KM


async def analyze_road_section(
    lat: float, lon: float,
    vlm_api_key: str | None = None,
    vlm_api_url: str = "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    vlm_model: str = "glm-4v-flash",
    progress_callback: Callable[[str], Awaitable[None]] | None = None,
    accidents: list[dict] | None = None,
    directions: list[float] | None = None,
    hotspot_context: str = "",
    auto_load_gibdd: bool = False,
) -> dict[str, Any]:
    result = {
        "coordinates": (lat, lon), "address": "", "osm_data": None,
        "images": None, "vlm_result": None, "formatted_message": "",
        "nearby_accidents": [], "excel_bytes": None, "excel_filename": "", "errors": [],
        "map_image_bytes": None, "gibdd_region": "",
        "accident_radius_m": 0, "dtp_analysis": "",
        "panorama_images": [],
    }

    async def update_progress(msg: str):
        if progress_callback:
            try:
                await progress_callback(msg)
            except Exception:
                pass

    # Шаг 1: Адрес
    await update_progress(f"Определение адреса...\nКоординаты: {lat}, {lon}")
    address, nominatim_data = await geocode_address(lat, lon)
    result["address"] = address

    # Шаг 2: Параллельно — изображения + OSM
    await update_progress(f"Сбор изображений и данных OSM...\nАдрес: {address or 'определяется...'}")
    images_task = collect_road_images(lat, lon, directions=directions)
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

    road_category = osm_data.get("road_category", "городская") if isinstance(osm_data, dict) else "городская"

    # Шаг 2.5: Загрузка ДТП из GIBDD
    if auto_load_gibdd:
        await update_progress("Определение региона и загрузка данных ДТП...")
        try:
            from gibdd.region_mapper import (
                find_region_by_coords, get_gibdd_code_by_region_name,
            )
            region_info = None
            naddr = nominatim_data.get("address", {}) if nominatim_data else {}
            region_raw = (naddr.get("state") or naddr.get("region") or "").strip()
            city_raw = (naddr.get("city") or "").strip()
            _FEDERAL_CITIES = {"москва", "санкт-петербург", "севастополь"}
            if city_raw.lower() in _FEDERAL_CITIES:
                region_info = get_gibdd_code_by_region_name(city_raw)
            elif region_raw:
                region_info = get_gibdd_code_by_region_name(region_raw)
            if not region_info:
                region_info = await find_region_by_coords(lat, lon)
            if region_info:
                region_code, region_name = region_info
                result["gibdd_region"] = f"{region_name} ({region_code})"
                logger.info(f"GIBDD: регион {region_name} ({region_code})")
                await update_progress(
                    f"Загрузка данных ДТП...\n"
                    f"Регион: {region_name}\n"
                    f"Это может занять 20-40 секунд..."
                )
                accidents = await _fetch_gibdd_accidents(region_code)
                if not accidents:
                    result["errors"].append("GIBDD: не удалось загрузить данные ДТП")
            else:
                result["errors"].append("GIBDD: не удалось определить регион")
        except Exception as e:
            result["errors"].append(f"GIBDD: {e}")
            logger.error(f"GIBDD: ошибка загрузки ДТП: {e}")

    # Шаг 3: VLM
    images_b64_list = []
    map_b64 = images_result.get("map_image_b64")
    if map_b64:
        images_b64_list.append(map_b64)
    for img in images_result.get("street_images", []):
        if img.get("base64"):
            images_b64_list.append(img["base64"])
    if vlm_api_key and images_b64_list:
        osm_summary = format_osm_summary(osm_data) if osm_data else "Нет данных"
        await update_progress(f"Анализ через нейросеть...\nИзображений: {len(images_b64_list)}")
        vlm_result = await analyze_road_images(
            images_b64_list=images_b64_list, osm_summary=osm_summary,
            api_key=vlm_api_key, api_url=vlm_api_url, model=vlm_model,
            hotspot_context=hotspot_context,
        )
    else:
        osm_summary = format_osm_summary(osm_data) if osm_data else "Нет данных"
        vlm_result = {"osm_based_assessment": True, "visual_notes": "Оценка только на основе OSM"}
    result["vlm_result"] = vlm_result

    # Шаг 4: ДТП поблизости
    accident_radius_km = _determine_radius(road_category)
    accident_radius_m = int(accident_radius_km * 1000)
    result["accident_radius_m"] = accident_radius_m
    if accidents:
        await update_progress(f"Проверка ДТП в радиусе {accident_radius_m} м...")
        result["nearby_accidents"] = await find_nearby_accidents(
            lat, lon, radius_km=accident_radius_km, accidents=accidents,
        )

    # Шаг 4.5: LLM-анализ ДТП
    if result["nearby_accidents"]:
        await update_progress(
            f"AI-анализ ДТП в радиусе {accident_radius_m} м...\n"
            f"Найдено: {len(result['nearby_accidents'])} ДТП"
        )
        try:
            from utils.llm_client import analyze_nearby_accidents as llm_analyze
            dtp_analysis = await llm_analyze(
                nearby_accidents=result["nearby_accidents"],
                address=address,
                road_category=road_category,
                radius_m=accident_radius_m,
                progress_callback=progress_callback,
            )
            result["dtp_analysis"] = dtp_analysis
        except Exception as e:
            logger.error(f"LLM-анализ ДТП: {e}")

    # Шаг 5: Форматирование
    await update_progress("Формирование отчёта...")
    formatted = format_expert_assessment(
        vlm_result, lat, lon, address, osm_data,
        dtp_analysis=result.get("dtp_analysis", ""),
    )
    if result["nearby_accidents"]:
        nc = len(result["nearby_accidents"])
        deaths = sum(int(c.get("pog", 0) or 0) for c in result["nearby_accidents"])
        injured = sum(int(c.get("ran", 0) or 0) for c in result["nearby_accidents"])
        formatted += (
            f"\n\nСТАТИСТИКА ДТП (радиус {accident_radius_m} м):"
            f"\n  ДТП: {nc}"
            f"\n  Погибло: {deaths}"
            f"\n  Ранено: {injured}"
        )

    # Ссылка на Яндекс Карты
    formatted += f"\n\nЯндекс Карты: https://yandex.ru/maps/?ll={lon}%2C{lat}&z=17&pt={lon}%2C{lat}"

    formatted += f"\n\nИсточники: {', '.join(images_result.get('sources_used', ['нет']))}"
    if result["gibdd_region"]:
        formatted += f"\nДанные ДТП: GIBDD {result['gibdd_region']}"
    if result["errors"]:
        formatted += "\nОшибки: " + "; ".join(result["errors"])
    result["formatted_message"] = formatted

    # Сохраняем байты для отправки
    result["map_image_bytes"] = images_result.get("map_image_bytes")
    result["panorama_images"] = [
        {"bytes": img["bytes"], "heading": img.get("heading", 0)}
        for img in images_result.get("street_images", [])
        if img.get("bytes")
    ]
    logger.info(
        f"Данные для отправки: карта={bool(result['map_image_bytes'])}, "
        f"панорамы={len(result['panorama_images'])} шт."
    )

    # Шаг 6: Excel
    try:
        logger.info(f"Генерация Excel...")
        excel_data = generate_excel_report(
            lat=lat, lon=lon, address=address,
            vlm_result=vlm_result, osm_data=osm_data,
            gibdd_nearby=result["nearby_accidents"],
            panorama_images=result.get("panorama_images"),
        )
        result["excel_bytes"] = excel_data
        result["excel_filename"] = get_report_filename(lat, lon)
        if excel_data:
            logger.info(f"Excel: OK ({len(excel_data)} байт)")
        else:
            logger.warning("Excel: пустой")
    except Exception as e:
        logger.exception(f"Excel: {e}")
        result["errors"].append(f"Excel: {e}")
        result["excel_bytes"] = None

    return result
