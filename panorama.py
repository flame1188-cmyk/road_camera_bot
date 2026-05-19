"""
Модуль получения изображений участка дороги.

Источники: Яндекс Static Map, Mapillary, Яндекс Панорамы.
"""

from __future__ import annotations

import base64
import logging
import math
from typing import Any

import httpx

logger = logging.getLogger(__name__)

YANDEX_STATIC_MAP_URL = "https://static-maps.yandex.ru/1.x/"
MAPILLARY_SEARCH_URL = "https://graph.mapillary.com/images/search"

MAP_IMAGE_WIDTH = 600
MAP_IMAGE_HEIGHT = 450


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
    api_key: str | None = None, access_token: str | None = None,
) -> list[dict[str, Any]]:
    """Получает фотографии с улицы из Mapillary API."""
    if not api_key and not access_token:
        return []
    headers = {"User-Agent": "RoadAssessmentBot/1.0", "Accept": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
        params = {
            "fields": "id,thumb_1024_url,computed_geometry,heading,captured_at,is_pano",
            "bbox": f"{lon - _meters_to_lon(radius, lat)},{lat - _meters_to_lat(radius)},"
                    f"{lon + _meters_to_lon(radius, lat)},{lat + _meters_to_lat(radius)}",
            "limit": str(limit), "is_pano": "true",
            "access_token": access_token,
        }
    else:
        params = {"client_id": api_key, "lookat": f"{lat},{lon}", "radius": str(radius), "limit": str(limit)}

    try:
        async with httpx.AsyncClient(verify=False, timeout=30) as client:
            resp = await client.get(MAPILLARY_SEARCH_URL, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        images = []
        features = data.get("features", [])
        if not features and "data" in data:
            features = data["data"]
        for feature in features[:limit]:
            props = feature.get("properties", {}) or feature
            geom = feature.get("geometry", {})
            coords = geom.get("coordinates", [])
            img_url = props.get("thumb_1024_url", "")
            image_b64 = await _download_image_b64(img_url) if img_url else None
            images.append({
                "base64": image_b64, "url": img_url,
                "heading": props.get("heading", 0), "is_pano": props.get("is_pano", False),
                "distance_m": round(haversine(lat, lon, coords[1] if coords else lat, coords[0] if coords else lon), 1),
                "source": "mapillary",
            })
        return images
    except Exception as e:
        logger.error(f"Mapillary API: {e}")
        return []


GOOGLE_MAPS_STATIC_URL = "https://maps.googleapis.com/maps/api/staticmap"


async def get_google_satellite(lat: float, lon: float, zoom: int = 17,
                               width: int = MAP_IMAGE_WIDTH, height: int = MAP_IMAGE_HEIGHT,
                               api_key: str | None = None) -> bytes | None:
    """Получает спутниковый снимок через Google Maps Static API."""
    if not api_key:
        return None
    params = {
        "center": f"{lat},{lon}", "zoom": str(zoom),
        "size": f"{width}x{height}", "maptype": "satellite",
        "scale": "2", "key": api_key,
    }
    try:
        async with httpx.AsyncClient(verify=False, timeout=30) as client:
            resp = await client.get(GOOGLE_MAPS_STATIC_URL, params=params)
            resp.raise_for_status()
            if len(resp.content) > 5000:
                return resp.content
    except Exception as e:
        logger.error(f"Google Maps Static: {e}")
    return None


async def get_yandex_satellite(lat: float, lon: float, zoom: int = 17,
                               api_key: str | None = None) -> bytes | None:
    """Получает спутниковый снимок через Яндекс Static API."""
    params = {
        "ll": f"{lon},{lat}", "z": str(zoom),
        "size": f"{MAP_IMAGE_WIDTH},{MAP_IMAGE_HEIGHT}", "l": "sat",
        "pt": f"{lon},{lat},pm2rdm", "scale": "2",
    }
    if api_key:
        params["apikey"] = api_key
    try:
        async with httpx.AsyncClient(verify=False, timeout=30) as client:
            resp = await client.get(YANDEX_STATIC_MAP_URL, params=params)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "image" in content_type or len(resp.content) > 5000:
                return resp.content
    except Exception as e:
        logger.debug(f"Яндекс спутник: {e}")
    return None


async def get_satellite_screenshot(lat: float, lon: float, zoom: int = 17,
                                  yandex_api_key: str | None = None,
                                  google_api_key: str | None = None) -> bytes | None:
    """Получает спутниковый снимок. Google → Яндекс (fallback)."""
    if google_api_key:
        img = await get_google_satellite(lat, lon, zoom=zoom, api_key=google_api_key)
        if img:
            return img
    return await get_yandex_satellite(lat, lon, zoom=zoom, api_key=yandex_api_key)


async def collect_road_images(
    lat: float, lon: float,
    mapillary_api_key: str | None = None, mapillary_access_token: str | None = None,
    yandex_api_key: str | None = None, google_api_key: str | None = None,
) -> dict[str, Any]:
    """Собирает все доступные изображения для точки."""
    result = {
        "map_image_b64": None, "satellite_image_b64": None,
        "street_images": [], "panorama_available": False, "sources_used": [],
    }
    map_img = await get_yandex_map_screenshot(lat, lon)
    if map_img:
        result["map_image_b64"] = base64.b64encode(map_img).decode("utf-8")
        result["sources_used"].append("yandex_map")

    sat_img = await get_satellite_screenshot(lat, lon, yandex_api_key=yandex_api_key, google_api_key=google_api_key)
    if sat_img:
        result["satellite_image_b64"] = base64.b64encode(sat_img).decode("utf-8")
        result["sources_used"].append("yandex_satellite")

    if mapillary_api_key or mapillary_access_token:
        mly = await get_mapillary_images(lat, lon, api_key=mapillary_api_key, access_token=mapillary_access_token)
        if mly:
            result["street_images"].extend(mly)
            result["sources_used"].append(f"mapillary({len(mly)})")

    panorama = await check_yandex_panorama(lat, lon)
    if panorama:
        result["panorama_available"] = True
        result["panorama_data"] = panorama
        result["sources_used"].append("yandex_panorama")

    return result
