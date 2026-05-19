"""
Модуль получения изображений участка дороги.

Источники:
  1. Яндекс Static Map (схема) — бесплатно, работает стабильно
  2. Яндекс Панорамы через Playwright (headless Chromium) — основной источник уличных фото
  3. Mapillary — отключён (API возвращает 0 результатов)
  4. Google Maps — исключён (требует платного ключа, российские карты не принимаются)

API-ключи читаются напрямую из config.py — не нужно передавать через параметры.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)
logger.info("panorama.py загружен (v5 — Playwright Яндекс Панорамы)")

YANDEX_STATIC_MAP_URL = "https://static-maps.yandex.ru/1.x/"
MAP_IMAGE_WIDTH = 600
MAP_IMAGE_HEIGHT = 450

# Playwright browser launch args (headless Chromium)
_CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--no-zygote",
    "--single-process",
]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Минимальный размер скриншота в байтах (меньше — скорее всего заглушка/ошибка)
_MIN_SCREENSHOT_BYTES = 15_000


# ========================
# Яндекс Static Map (схема)
# ========================

async def get_yandex_map_screenshot(
    lat: float, lon: float, zoom: int = 17,
    map_type: str = "map",
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


# ========================
# Яндекс Панорамы — проверка наличия
# ========================

async def check_yandex_panorama(lat: float, lon: float) -> dict[str, Any] | None:
    """Проверяет наличие Яндекс-панорамы в точке (без скриншота)."""
    url = "https://panorama.maps.yandex.net/v1/panorama/2.0/"
    params = {
        "lat": str(lat), "lng": str(lon),
        "lang": "ru_RU", "source": "panoramas", "distance": "200",
    }
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(verify=False, timeout=15) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            panoramas = data.get("panoramas", [])
            if panoramas:
                return panoramas[0]
    except Exception as e:
        logger.debug(f"Яндекс панорамы (проверка): {e}")
    return None


# ========================
# Яндекс Панорамы — скриншот через Playwright
# ========================

def _build_panorama_url(lat: float, lon: float, direction: float = 0.0) -> str:
    """Строит URL для открытия Яндекс Панорамы в заданном направлении."""
    return (
        f"https://yandex.ru/maps/?from=map&ll={lon}%2C{lat}&z=17"
        f"&panorama%5Bpoint%5D={lon}%2C{lat}"
        f"&panorama%5Bdirection%5D={direction}"
        f"&panorama%5Bspan%5D=90.0"
        f"&mode=panorama"
    )


async def get_yandex_panorama_screenshots(
    lat: float, lon: float,
    directions: list[float] | None = None,
    width: int = 1280, height: int = 720,
    timeout_ms: int = 25000,
) -> list[dict[str, Any]]:
    """Получает скриншоты Яндекс Панорамы через Playwright (headless Chromium).

    Возвращает список dict с ключами: base64, source, heading.
    По умолчанию делает один снимок (направление 0°).
    """
    if directions is None:
        directions = [0.0]

    results: list[dict[str, Any]] = []

    # Проверяем, что Playwright установлен
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("Playwright не установлен — панорамы недоступны")
        return results

    logger.info(f"Яндекс Панорама (Playwright): запуск для {lat}, {lon}, направления {directions}")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=_CHROMIUM_ARGS,
            )
            try:
                page = await browser.new_page(
                    viewport={"width": width, "height": height},
                    user_agent=_USER_AGENT,
                )
                # Отключаем лишние ресурсы для скорости
                await page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,eot}",
                                 lambda route: route.abort())
                await page.route("**/*analytics*", lambda route: route.abort())
                await page.route("**/*metric*", lambda route: route.abort())

                for direction in directions:
                    url = _build_panorama_url(lat, lon, direction)
                    try:
                        logger.info(f"  направление {direction}°: загрузка...")
                        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

                        # Ждём появления canvas (панорама рендерится в canvas)
                        try:
                            await page.wait_for_selector(
                                "canvas",
                                timeout=12000,
                            )
                        except Exception:
                            # Canvas не появился — возможно нет панорамы в этой точке
                            logger.debug(f"  направление {direction}°: canvas не найден")
                            # Проверяем — может есть сообщение об отсутствии панорамы
                            content = await page.content()
                            if "панорам" in content.lower() and "нет" in content.lower():
                                logger.info(f"  направление {direction}°: панорама отсутствует")
                            # Всё равно пробуем сделать скриншот (могут быть тайлы карты)

                        # Дополнительная пауза для загрузки тайлов панорамы
                        await asyncio.sleep(2)

                        screenshot_bytes = await page.screenshot(type="jpeg", quality=80)

                        if screenshot_bytes and len(screenshot_bytes) >= _MIN_SCREENSHOT_BYTES:
                            b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
                            results.append({
                                "base64": b64,
                                "source": "yandex_panorama",
                                "heading": direction,
                            })
                            logger.info(f"  направление {direction}°: OK ({len(screenshot_bytes)} байт)")
                        else:
                            logger.warning(
                                f"  направление {direction}°: "
                                f"скриншот слишком маленький ({len(screenshot_bytes) if screenshot_bytes else 0} байт)"
                            )
                    except Exception as e:
                        logger.error(f"  направление {direction}°: ошибка — {e}")
            finally:
                await browser.close()
    except Exception as e:
        logger.error(f"Яндекс Панорама (Playwright): ошибка запуска браузера — {e}")

    return results


# ========================
# Mapillary (ОТКЛЮЧЁН — API не работает)
# ========================

# Mapillary API стабильно возвращает 0 результатов или 500 ошибку
# после поглощения компанией Meta. Оставляем код как fallback,
# но фактически не используется.

async def get_mapillary_images(
    lat: float, lon: float, radius: int = 100, limit: int = 1,
) -> list[dict[str, Any]]:
    """DISABLED: Mapillary API returns empty results after Meta acquisition."""
    return []


# ========================
# Главный сборщик
# ========================

async def collect_road_images(
    lat: float, lon: float,
) -> dict[str, Any]:
    """Собирает все доступные изображения для точки.

    Цепочка источников:
    1. Яндекс Static Map (схема) — всегда
    2. Яндекс Панорама через Playwright (уличные фото) — основной
    3. Mapillary — отключён (API мёртв)
    """
    result = {
        "map_image_b64": None,
        "street_images": [],
        "panorama_available": False,
        "sources_used": [],
    }

    # 1. Схематичная карта Яндекс (бесплатно, без ключа)
    map_img = await get_yandex_map_screenshot(lat, lon)
    if map_img:
        result["map_image_b64"] = base64.b64encode(map_img).decode("utf-8")
        result["sources_used"].append("yandex_map")

    # 2. Яндекс Панорама через Playwright — основной источник уличных фото
    panorama_shots = await get_yandex_panorama_screenshots(
        lat, lon, directions=[0.0, 180.0],
    )
    if panorama_shots:
        result["street_images"].extend(panorama_shots)
        result["panorama_available"] = True
        result["sources_used"].append(f"yandex_panorama({len(panorama_shots)} фото)")

    # 3. Mapillary fallback (отключён)
    if not result["street_images"]:
        mly = await get_mapillary_images(lat, lon)
        if mly:
            result["street_images"].extend(mly)
            result["sources_used"].append(f"mapillary({len(mly)})")

    # 4. Проверка наличия панорамы (для информации)
    if not result["panorama_available"]:
        pano_check = await check_yandex_panorama(lat, lon)
        if pano_check:
            result["panorama_available"] = True
            result["panorama_data"] = pano_check

    return result
