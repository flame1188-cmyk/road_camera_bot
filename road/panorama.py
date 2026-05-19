"""
Модуль получения изображений участка дороги.

Источники: Яндекс Static Map (схема), Mapillary (уличные фото), Яндекс Панорамы (проверка).
Спутниковые снимки и Google Maps исключены (требуют платных ключей).
API-ключи читаются напрямую из config.py — не нужно передавать через параметры.
"""

from __future__ import annotations

import base64
import logging
import math
from typing import Any

import httpx

logger = logging.getLogger(__name__)
logger.info("panorama.py загружен (v3 — без Google Maps)")

YANDEX_STATIC_MAP_URL = "https://static-maps.yandex.ru/1.x/"
MAPILLARY_SEARCH_URL = "https://graph.mapillary.com/images"

MAP_IMAGE_WIDTH = 600
MAP_IMAGE_HEIGHT = 450


def _get_config():
    """Ленивый импорт config — читает ключи напрямую."""
    try:
        from config import MAPILLARY_ACCESS_TOKEN
        return {"mapillary_token": MAPILLARY_ACCESS_TOKEN or None}
    except ImportError:
        return {"mapillary_token": None}


async def get_yandex_map_screenshot(
    lat: float, lon: float, zoom: int = 17, map_type: str = "map",
    width: int = MAP_IMAGE_WIDTH, height: int = MAP_IMAGE_HEIGHT,
) -> bytes | None:
    """Получает статичное изображение карты из Яндекс.Карт (бесплатно, без ключа)."""
    params = {
        "ll": f"{lon},{lat}", "z": str(zoom),
        "size": f"{width},{height}", "l": map_type,
        "pt": f"{lon},{lat},pm2rdm", "scale": "2",
    }
    try:
        async with httpx.AsyncClient(verify=False, timeout=30) as client:
            resp = await client.get(YANDEX_STATIC_MAP_URL, params=params)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "image" in content_type or len(resp.content) > 5000:
                return resp.content
    except Exception as e:
        logger.error(f"Яндекс Static Map: {e}")
    return None


async def check_yandex_panorama(lat: float, lon: float) -> dict[str, Any] | None:
    """Проверяет наличие Яндекс-панорамы в точке."""
    url = "https://panorama.maps.yandex.net/v1/panorama/2.0/"
    params = {"lat": str(lat), "lng": str(lon), "lang": "ru_RU", "source": "panoramas", "distance": "200"}
    headers = {"User-Agent": "RoadAssessmentBot/1.0", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(verify=False, timeout=15) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            panoramas = data.get("panoramas", [])
            if panoramas:
                return panoramas[0]
    except Exception as e:
        logger.debug(f"Яндекс панорамы: {e}")
    return None


def _meters_to_lon(meters: float, lat: float = 55.0) -> float:
    return meters / (111320.0 * (1 - 0.000006 * lat * lat))


def _meters_to_lat(meters: float) -> float:
    return meters / 110540.0


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = (math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(min(a, 1.0)))


async def _download_image_b64(url: str) -> str | None:
    try:
        async with httpx.AsyncClient(verify=False, timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return base64.b64encode(resp.content).decode("utf-8")
    except Exception as e:
        logger.warning(f"Скачивание изображения {url}: {e}")
        return None


async def get_mapillary_images(
    lat: float, lon: float, radius: int = 500, limit: int = 4,
) -> list[dict[str, Any]]:
    """Получает фотографии с улицы из Mapillary API. Ключ читается из config."""
    cfg = _get_config()
    access_token = cfg["mapillary_token"]
    if not access_token:
        logger.info("Mapillary: токен не задан, пропуск")
        return []

    headers = {
        "User-Agent": "RoadAssessmentBot/1.0",
        "Accept": "application/json",
    }
    params = {
        "fields": "id,thumb_1024_url,thumb_2048_url,computed_geometry,heading,captured_at,is_pano",
        "bbox": f"{lon - _meters_to_lon(radius, lat)},{lat - _meters_to_lat(radius)},"
                f"{lon + _meters_to_lon(radius, lat)},{lat + _meters_to_lat(radius)}",
        "limit": str(limit),
        "access_token": access_token,
    }

    try:
        async with httpx.AsyncClient(verify=False, timeout=30) as client:
            resp = await client.get(MAPILLARY_SEARCH_URL, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        raw_count = len(data.get('data', []))
        logger.info(f"Mapillary ответ: data содержит {raw_count} элементов")

        images = []
        features = data.get("data", [])
        if not features:
            features = data.get("features", [])
        for feature in features[:limit]:
            # Новое API: поля могут быть прямо в объекте
            props = feature.get("properties", {}) or feature
            geom = feature.get("geometry", feature.get("computed_geometry", {}))
            coords = geom.get("coordinates", []) if isinstance(geom, dict) else []
            img_url = (
                props.get("thumb_1024_url")
                or props.get("thumb_2048_url")
                or feature.get("thumb_1024_url")
                or feature.get("thumb_2048_url")
                or ""
            )
            if not img_url:
                logger.warning(f"Mapillary: нет URL у изображения {feature.get('id', '?')}")
                continue
            logger.info(f"Mapillary: скачивание {img_url[:80]}...")
            image_b64 = await _download_image_b64(img_url)
            if image_b64:
                images.append({
                    "base64": image_b64, "url": img_url,
                    "heading": props.get("heading", 0), "is_pano": props.get("is_pano", False),
                    "distance_m": round(haversine(lat, lon, coords[1] if len(coords) > 1 else lat, coords[0] if coords else lon), 1),
                    "source": "mapillary",
                })
            else:
                logger.warning(f"Mapillary: не удалось скачать {img_url[:80]}")
        logger.info(f"Mapillary: успешно загружено {len(images)} из {len(features)} изображений")
        return images
    except httpx.HTTPStatusError as e:
        body = e.response.text[:500]
        logger.error(f"Mapillary API {e.response.status_code}: {body}")
        return []
    except Exception as e:
        logger.error(f"Mapillary API: {e}")
        return []


async def collect_road_images(
    lat: float, lon: float,
    mapillary_api_key: str | None = None, mapillary_access_token: str | None = None,
) -> dict[str, Any]:
    """Собирает все доступные изображения для точки.

    API-ключ Mapillary читается из config.py напрямую.
    """
    result = {
        "map_image_b64": None, "satellite_image_b64": None,
        "street_images": [], "panorama_available": False, "sources_used": [],
    }

    # Схематичная карта Яндекс (бесплатно)
    map_img = await get_yandex_map_screenshot(lat, lon)
    if map_img:
        result["map_image_b64"] = base64.b64encode(map_img).decode("utf-8")
        result["sources_used"].append("yandex_map")

    # Уличные фото Mapillary (ключ из config)
    mly = await get_mapillary_images(lat, lon)
    if mly:
        result["street_images"].extend(mly)
        result["sources_used"].append(f"mapillary({len(mly)})")

    # Проверка панорамы Яндекс
    panorama = await check_yandex_panorama(lat, lon)
    if panorama:
        result["panorama_available"] = True
        result["panorama_data"] = panorama
        result["sources_used"].append("yandex_panorama")

    return result
