"""
Модуль получения изображений участка дороги.

Источники: Яндекс Static Map, Google Maps, Mapillary, Яндекс Панорамы.
API-ключи читаются напрямую из config.py — не нужно передавать через параметры.
"""

from __future__ import annotations

import base64
import logging
import math
from typing import Any

import httpx

logger = logging.getLogger(__name__)
logger.info("panorama.py загружен (v2 — ключи из config.py напрямую)")

YANDEX_STATIC_MAP_URL = "https://static-maps.yandex.ru/1.x/"
MAPILLARY_SEARCH_URL = "https://graph.mapillary.com/images"
GOOGLE_MAPS_STATIC_URL = "https://maps.googleapis.com/maps/api/staticmap"

MAP_IMAGE_WIDTH = 600
MAP_IMAGE_HEIGHT = 450


def _get_config():
    """Ленивый импорт config — читает ключи напрямую."""
    try:
        from config import YANDEX_API_KEY, GOOGLE_MAPS_API_KEY, MAPILLARY_ACCESS_TOKEN
        return {
            "yandex_api_key": YANDEX_API_KEY or None,
            "google_api_key": GOOGLE_MAPS_API_KEY or None,
            "mapillary_token": MAPILLARY_ACCESS_TOKEN or None,
        }
    except ImportError:
        return {"yandex_api_key": None, "google_api_key": None, "mapillary_token": None}


async def get_yandex_map_screenshot(
    lat: float, lon: float, zoom: int = 17, map_type: str = "map",
    width: int = MAP_IMAGE_WIDTH, height: int = MAP_IMAGE_HEIGHT,
) -> bytes | None:
    """Получает статичное изображение карты из Яндекс.Карт."""
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
    lat: float, lon: float, radius: int = 150, limit: int = 4,
) -> list[dict[str, Any]]:
    """Получает фотографии с улицы из Mapillary API. Ключ читается из config."""
    cfg = _get_config()
    access_token = cfg["mapillary_token"]
    if not access_token:
        return []

    headers = {
        "User-Agent": "RoadAssessmentBot/1.0",
        "Accept": "application/json",
    }
    params = {
        "fields": "id,thumb_1024_url,thumb_2048_url,computed_geometry,heading,captured_at,is_pano",
        "bbox": f"{lon - _meters_to_lon(radius, lat)},{lat - _meters_to_lat(radius)},"
                f"{lon + _meters_to_lon(radius, lat)},{lat + _meters_to_lat(radius)}",
        "limit": str(limit), "is_pano": "true",
        "access_token": access_token,
    }

    try:
        async with httpx.AsyncClient(verify=False, timeout=30) as client:
            resp = await client.get(MAPILLARY_SEARCH_URL, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        logger.info(f"Mapillary ответ: keys={list(data.keys())}, data_type={type(data.get('data')).__name__}")

        images = []
        features = data.get("data", [])
        if not features:
            features = data.get("features", [])
        logger.info(f"Mapillary найдено изображений: {len(features)}")
        for feature in features[:limit]:
            # Новое API: поля прямо в объекте, не в properties
            props = feature.get("properties", {}) or feature
            geom = feature.get("geometry", feature.get("computed_geometry", {}))
            coords = geom.get("coordinates", []) if isinstance(geom, dict) else []
            # Пробуем разные форматы URL
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
        logger.info(f"Mapillary: успешно загружено {len(images)} изображений")
        return images
    except httpx.HTTPStatusError as e:
        body = e.response.text[:500]
        logger.error(f"Mapillary API {e.response.status_code}: {body}")
        return []
    except Exception as e:
        logger.error(f"Mapillary API: {e}")
        return []


async def get_satellite_screenshot(lat: float, lon: float, zoom: int = 17) -> bytes | None:
    """Получает спутниковый снимок. Google (если есть ключ) → Яндекс (если есть ключ)."""
    cfg = _get_config()

    # Google Maps Static API
    if cfg["google_api_key"]:
        params = {
            "center": f"{lat},{lon}", "zoom": str(zoom),
            "size": f"{MAP_IMAGE_WIDTH}x{MAP_IMAGE_HEIGHT}", "maptype": "satellite",
            "scale": "2", "key": cfg["google_api_key"],
        }
        try:
            async with httpx.AsyncClient(verify=False, timeout=30) as client:
                resp = await client.get(GOOGLE_MAPS_STATIC_URL, params=params)
                resp.raise_for_status()
                if len(resp.content) > 5000:
                    return resp.content
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500]
            logger.error(f"Google Maps Static {e.response.status_code}: {body}")
        except Exception as e:
            logger.error(f"Google Maps Static: {e}")

    # Яндекс спутник: в бесплатном Static API слой 'sat' НЕ поддерживается.
    # Пропускаем Яндекс-спутник — используем только Google Maps.
    logger.debug("Яндекс спутник: слой 'sat' недоступен в бесплатном Static API")

    return None


async def collect_road_images(
    lat: float, lon: float,
    mapillary_api_key: str | None = None, mapillary_access_token: str | None = None,
) -> dict[str, Any]:
    """Собирает все доступные изображения для точки.

    API-ключи (Яндекс, Google, Mapillary) читаются из config.py напрямую.
    Параметры mapillary_api_key/mapillary_access_token оставлены для обратной совместимости,
    но игнорируются — ключ берётся из конфига.
    """
    result = {
        "map_image_b64": None, "satellite_image_b64": None,
        "street_images": [], "panorama_available": False, "sources_used": [],
    }

    # Схематичная карта (бесплатно, без ключа)
    map_img = await get_yandex_map_screenshot(lat, lon)
    if map_img:
        result["map_image_b64"] = base64.b64encode(map_img).decode("utf-8")
        result["sources_used"].append("yandex_map")

    # Спутник (Google → Яндекс, ключи из config)
    sat_img = await get_satellite_screenshot(lat, lon)
    if sat_img:
        result["satellite_image_b64"] = base64.b64encode(sat_img).decode("utf-8")
        if result["sources_used"] and "satellite" not in result["sources_used"][-1]:
            result["sources_used"].append("satellite")

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
