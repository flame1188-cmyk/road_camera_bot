"""
Модуль получения изображений участка дороги.

Источники:
  1. Яндекс Static Map (схема) — бесплатно, работает стабильно
  2. Яндекс Панорамы через Playwright (headless Chromium) — основной источник уличных фото
  3. Mapillary — отключён (API возвращает 0 результатов)
  4. Google Maps — исключён (требует платного ключа)

Chromium устанавливается через apt-get в Dockerfile (системный пакет).
Playwright использует системный Chromium по пути /usr/bin/chromium.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

YANDEX_STATIC_MAP_URL = "https://static-maps.yandex.ru/1.x/"
MAP_IMAGE_WIDTH = 600
MAP_IMAGE_HEIGHT = 450

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


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
    timeout_ms: int = 30000,
) -> list[dict[str, Any]]:
    """Получает скриншоты Яндекс Панорамы через Playwright (headless Chromium).

    Chromium устанавливается автоматически при первом вызове.
    Возвращает список dict с ключами: base64, source, heading.
    """
    if directions is None:
        directions = [0.0]

    results: list[dict[str, Any]] = []

    # Импортируем Playwright
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("Playwright Python-пакет не установлен")
        return results

    logger.info(f"Яндекс Панорама (Playwright): {lat}, {lon}, направления {directions}")

    chromium_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--no-zygote",
        "--single-process",
    ]

    # Путь к системному Chromium (установлен через apt-get в Dockerfile)
    chromium_path = "/usr/bin/chromium"

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                executable_path=chromium_path,
                args=chromium_args,
            )
            try:
                page = await browser.new_page(
                    viewport={"width": width, "height": height},
                    user_agent=_USER_AGENT,
                )

                # Блокируем лишние ресурсы для скорости
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
                            await page.wait_for_selector("canvas", timeout=15000)
                        except Exception:
                            logger.debug(f"  направление {direction}°: canvas не найден")

                        # Пауза для рендеринга тайлов панорамы
                        await asyncio.sleep(3)

                        screenshot_bytes = await page.screenshot(type="jpeg", quality=80)

                        if screenshot_bytes and len(screenshot_bytes) >= 15000:
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
        logger.error(f"Яндекс Панорама (Playwright): ошибка браузера — {e}")

    return results


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

    # 3. Проверка наличия панорамы (для информации)
    if not result["panorama_available"]:
        pano_check = await check_yandex_panorama(lat, lon)
        if pano_check:
            result["panorama_available"] = True
            result["panorama_data"] = pano_check

    return result
