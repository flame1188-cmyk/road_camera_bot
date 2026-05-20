"""
Модуль получения изображений участка дороги.

Источники:
  1. Яндекс Static Map (схема) — бесплатно, работает стабильно
  2. Яндекс Панорамы через Playwright (headless Chromium) — основной источник уличных фото
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
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

CHROMIUM_PATH = "/usr/bin/chromium"


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


async def check_yandex_panorama(lat: float, lon: float) -> dict[str, Any] | None:
    """Проверяет наличие Яндекс-панорамы в точке."""
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


def _build_panorama_url(lat: float, lon: float, direction: float = 0.0) -> str:
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
    """Скриншоты Яндекс Панорамы через Playwright.

    Стратегия: ОДНА страница — загружаем панораму один раз, затем вращаем
    стрелками клавиатуры (ArrowRight / ArrowLeft).

    Проверено на практике: одно нажатие keyboard.press() ~ 0.55 градуса.
    Для поворота на 90° нужно ~165 нажатий. Используем 180 с запасом.
    """
    if directions is None:
        directions = [0.0]

    results: list[dict[str, Any]] = []

    if not Path(CHROMIUM_PATH).exists():
        logger.error(f"Chromium не найден — панорамы пропущены")
        return results

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("Playwright не установлен")
        return results

    logger.info(f"Яндекс Панорама (Playwright): {lat}, {lon}, направления {directions}")

    chromium_args = [
        "--no-sandbox", "--disable-setuid-sandbox",
        "--disable-dev-shm-usage", "--disable-gpu",
        "--disable-software-rasterizer",
    ]

    _CAPTCHA_KEYWORDS = [
        "капч", "captcha", "robot", "проверк",
        "smart captcha", "i'm not a robot", "not a robot",
        "подтвердите", "confirm", "security",
    ]

    async def _is_captcha(pg) -> bool:
        try:
            page_text = await pg.inner_text("body")
            if page_text:
                lower = page_text.lower()
                return any(kw in lower for kw in _CAPTCHA_KEYWORDS)
        except Exception:
            pass
        return False

    # 1 нажатие keyboard.press() ~ 0.55° (проверено: 45 нажатий дают ~25°)
    # Берём 0.55° с запасом = 0.5°. Для 90° = 180 нажатий.
    _DEG_PER_PRESS = 0.5
    _PRESS_INTERVAL = 0.04   # 40мс между нажатиями
    _TILE_LOAD_WAIT = 4      # загрузка тайлов после поворота (сек)
    _INITIAL_LOAD_WAIT = 8   # загрузка панорамы при открытии (сек)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, executable_path=CHROMIUM_PATH, args=chromium_args,
            )
            try:
                page = await browser.new_page(
                    viewport={"width": width, "height": height},
                    user_agent=_USER_AGENT,
                )

                # Блокируем ТОЛЬКО шрифты и аналитику (НЕ изображения — тайлы нужны)
                await page.route("**/*.{woff,woff2,ttf,eot}", lambda route: route.abort())
                await page.route("**/*analytics*", lambda route: route.abort())
                await page.route("**/*metric*", lambda route: route.abort())

                # Загружаем панораму (направление 0° по умолчанию)
                url = _build_panorama_url(lat, lon, 0.0)
                logger.info(f"  загрузка панорамы (начальный ракурс 0°)...")
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

                try:
                    await page.wait_for_selector("canvas", timeout=15000)
                except Exception:
                    logger.debug("  canvas не найден")

                await asyncio.sleep(_INITIAL_LOAD_WAIT)

                if await _is_captcha(page):
                    logger.warning("  капча при загрузке, все направления пропущены")
                    await page.close()
                    return results

                # Фокусируемся на canvas для приёма клавиш
                try:
                    await page.click("canvas")
                    await asyncio.sleep(0.5)
                except Exception:
                    logger.debug("  не удалось кликнуть по canvas")

                logger.info(f"  панорама загружена, URL: {page.url[:120]}...")

                # === Снимаем все направления на ОДНОЙ странице ===
                current_dir = 0.0

                for idx, target_dir in enumerate(directions):
                    try:
                        diff = (target_dir - current_dir) % 360

                        if idx > 0 and diff > 0.1:
                            if diff <= 180:
                                key = "ArrowRight"
                                turn_degrees = diff
                            else:
                                key = "ArrowLeft"
                                turn_degrees = 360 - diff

                            num_presses = max(int(round(turn_degrees / _DEG_PER_PRESS)), 1)
                            logger.info(
                                f"  направление {target_dir}°: поворот "
                                f"({key}, {turn_degrees:.0f}°, {num_presses} нажатий)..."
                            )

                            for _ in range(num_presses):
                                await page.keyboard.press(key)
                                await asyncio.sleep(_PRESS_INTERVAL)

                            await asyncio.sleep(_TILE_LOAD_WAIT)
                        else:
                            if idx == 0:
                                logger.info(f"  направление {target_dir}°: снимок...")

                        if await _is_captcha(page):
                            logger.warning(f"  направление {target_dir}°: капча, остальные пропущены")
                            break

                        screenshot_bytes = await page.screenshot(type="jpeg", quality=80)

                        if screenshot_bytes and len(screenshot_bytes) < 40000:
                            logger.warning(
                                f"  направление {target_dir}°: маленький "
                                f"скриншот ({len(screenshot_bytes)} байт) — возможно капча, пропуск"
                            )
                            continue

                        if screenshot_bytes and len(screenshot_bytes) >= 15000:
                            b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
                            results.append({
                                "base64": b64,
                                "bytes": screenshot_bytes,
                                "source": "yandex_panorama",
                                "heading": target_dir,
                            })
                            logger.info(f"  направление {target_dir}°: OK ({len(screenshot_bytes)} байт)")
                        else:
                            logger.warning(
                                f"  направление {target_dir}°: "
                                f"скриншот маленький ({len(screenshot_bytes) if screenshot_bytes else 0} байт)"
                            )

                        current_dir = target_dir

                    except Exception as e:
                        logger.error(f"  направление {target_dir}°: {e}")
                        continue

                await page.close()
            finally:
                await browser.close()
    except Exception as e:
        logger.error(f"Ошибка браузера: {e}")

    return results


async def collect_road_images(
    lat: float, lon: float,
    directions: list[float] | None = None,
) -> dict[str, Any]:
    """Собирает все доступные изображения для точки."""
    if directions is None:
        directions = [0.0, 180.0]

    result = {
        "map_image_b64": None,
        "map_image_bytes": None,
        "street_images": [],
        "panorama_available": False,
        "sources_used": [],
    }

    # 1. Яндекс Static Map (схема с точкой)
    map_img = await get_yandex_map_screenshot(lat, lon)
    if map_img:
        result["map_image_bytes"] = map_img
        result["map_image_b64"] = base64.b64encode(map_img).decode("utf-8")
        result["sources_used"].append("yandex_map")

    # 2. Яндекс Панорама через Playwright
    panorama_shots = await get_yandex_panorama_screenshots(
        lat, lon, directions=directions,
    )
    if panorama_shots:
        result["street_images"].extend(panorama_shots)
        result["panorama_available"] = True
        result["sources_used"].append(f"yandex_panorama({len(panorama_shots)} фото)")

    # 3. Проверка наличия панорамы (API)
    if not result["panorama_available"]:
        pano_check = await check_yandex_panorama(lat, lon)
        if pano_check:
            result["panorama_available"] = True
            result["panorama_data"] = pano_check

    return result
