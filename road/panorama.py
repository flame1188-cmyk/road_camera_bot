"""
Модуль получения изображений участка дороги.

Источники:
  1. Яндекс Static Map (схема) — бесплатно, работает стабильно
  2. Яндекс Панорамы через Playwright (headless Chromium) — основной источник уличных фото

Требования: Chromium установлен в системе (/usr/bin/chromium).
В Docker-окружении Amvera устанавливается через apt-get в Dockerfile.
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


# ========================
# Яндекс Народная карта — скриншот через Playwright
# ========================

async def get_narodnaya_map_screenshot(
    lat: float, lon: float,
    zoom: int = 17,
    width: int = 1280, height: int = 720,
    timeout_ms: int = 30000,
) -> dict[str, Any] | None:
    """Скриншот Яндекс.Карт с включённым слоем Народной карты.

    Народная карта показывает: скоростные ограничения, камеры фиксации,
    опасные участки, лежащих полицейских, ямы на дорогах.
    """
    if not Path(CHROMIUM_PATH).exists():
        logger.error(f"Chromium не найден — Народная карта пропущена")
        return None

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("Playwright не установлен")
        return None

    url = (
        f"https://yandex.ru/maps/?from=map&ll={lon}%2C{lat}&z={zoom}"
        f"&l=map,narodmap"
    )

    chromium_args = [
        "--no-sandbox", "--disable-setuid-sandbox",
        "--disable-dev-shm-usage", "--disable-gpu",
        "--disable-software-rasterizer",
    ]

    logger.info(f"Народная карта (Playwright): {lat}, {lon}, z={zoom}")

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

                # Блокируем лишние ресурсы
                await page.route("**/*.{woff,woff2,ttf,eot}", lambda route: route.abort())
                await page.route("**/*analytics*", lambda route: route.abort())
                await page.route("**/*metric*", lambda route: route.abort())

                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

                # Ждём загрузки карты
                try:
                    await page.wait_for_selector(".ground-pane", timeout=15000)
                except Exception:
                    logger.debug("Народная карта: ground-pane не найден, пробуем дальше")

                # Дополнительно ждём, чтобы слой Народной карты прогрузился
                await asyncio.sleep(5)

                screenshot_bytes = await page.screenshot(type="jpeg", quality=85)

                if screenshot_bytes and len(screenshot_bytes) >= 10000:
                    b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
                    logger.info(f"Народная карта: OK ({len(screenshot_bytes)} байт)")
                    return {
                        "base64": b64,
                        "bytes": screenshot_bytes,
                        "source": "narodnaya_map",
                    }
                else:
                    logger.warning(
                        f"Народная карта: скриншот маленький "
                        f"({len(screenshot_bytes) if screenshot_bytes else 0} байт)"
                    )
            finally:
                await browser.close()
    except Exception as e:
        logger.error(f"Народная карта: {e}")

    return None


# ========================
# Яндекс Панорамы — скриншот через Playwright
# ========================

def _build_panorama_url(lat: float, lon: float, direction: float = 0.0) -> str:
    """Строит URL для открытия Яндекс Панорамы."""
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
    """Получает скриншоты Яндекс Панорамы через Playwright + системный Chromium.

    Стратегия: ОДНА страница — загружаем панораму один раз, затем вращаем
    стрелками клавиатуры (ArrowRight / ArrowLeft). Это гарантирует:
      - Панорама не перезагружается (нет лишних запросов → меньше шансов на капчу)
      - Стрелки клавиатуры — стандартный способ вращения в Яндекс.Картах
      - Каждое нажатие ~15°, 6 нажатий ≈ 90°
    """
    if directions is None:
        directions = [0.0]

    results: list[dict[str, Any]] = []

    # Проверяем наличие Chromium
    if not Path(CHROMIUM_PATH).exists():
        logger.error(f"Chromium не найден по пути {CHROMIUM_PATH} — панорамы пропущены")
        return results

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("Playwright не установлен (pip install playwright)")
        return results

    logger.info(f"Яндекс Панорама (Playwright): {lat}, {lon}, направления {directions}")

    chromium_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-software-rasterizer",
    ]

    # Ключевые слова для обнаружения капчи / Smart Captcha Яндекса
    _CAPTCHA_KEYWORDS = [
        "капч", "captcha", "robot", "проверк",
        "smart captcha", "i'm not a robot", "not a robot",
        "подтвердите", "confirm", "security",
    ]

    async def _is_captcha(pg) -> bool:
        """Проверяет наличие капчи по тексту страницы."""
        try:
            page_text = await pg.inner_text("body")
            if page_text:
                lower = page_text.lower()
                return any(kw in lower for kw in _CAPTCHA_KEYWORDS)
        except Exception:
            pass
        return False

    # Расстояние клавиш для поворота: ~6 нажатий ArrowRight ≈ 90°
    _KEY_PRESSES_PER_90 = 6
    _KEY_STEP_DELAY = 0.4   # пауза между нажатиями (сек)
    _TILE_LOAD_WAIT = 6     # ожидание загрузки тайлов после поворота (сек)
    _INITIAL_LOAD_WAIT = 8  # ожидание загрузки панорамы при открытии (сек)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                executable_path=CHROMIUM_PATH,
                args=chromium_args,
            )
            try:
                page = await browser.new_page(
                    viewport={"width": width, "height": height},
                    user_agent=_USER_AGENT,
                )

                # Блокируем ТОЛЬКО шрифты и аналитику.
                # НЕ блокируем изображения — тайлы панорамы нужны для canvas.
                await page.route("**/*.{woff,woff2,ttf,eot}", lambda route: route.abort())
                await page.route("**/*analytics*", lambda route: route.abort())
                await page.route("**/*metric*", lambda route: route.abort())

                # Загружаем панораму (направление 0° по умолчанию)
                url = _build_panorama_url(lat, lon, 0.0)
                logger.info(f"  загрузка панорамы (начальный ракурс 0°)...")
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

                # Ждём canvas (панорама рендерится в canvas)
                try:
                    await page.wait_for_selector("canvas", timeout=15000)
                except Exception:
                    logger.debug("  canvas не найден")

                # Полная загрузка тайлов панорамы
                await asyncio.sleep(_INITIAL_LOAD_WAIT)

                # Проверяем капчу сразу после загрузки
                if await _is_captcha(page):
                    logger.warning("  обнаружена проверка при загрузке, все направления пропущены")
                    await page.close()
                    return results

                # Фокусируемся на canvas для приёма клавиш
                try:
                    await page.click("canvas")
                    await asyncio.sleep(0.5)
                except Exception:
                    logger.debug("  не удалось кликнуть по canvas")

                # Логируем финальный URL для отладки
                final_url = page.url
                logger.info(f"  панорама загружена, URL: {final_url[:120]}...")

                # === Снимаем все направления на ОДНОЙ странице ===
                current_dir = 0.0

                for idx, target_dir in enumerate(directions):
                    try:
                        # Рассчитываем поворот от текущего направления
                        diff = (target_dir - current_dir) % 360

                        # Если нужно повернуться
                        if idx > 0 and diff > 0.1:
                            # Выбираем направление поворота (короткий путь)
                            if diff <= 180:
                                key = "ArrowRight"
                                steps = round(diff / 15.0)  # ~15° за нажатие
                            else:
                                key = "ArrowLeft"
                                steps = round((360 - diff) / 15.0)

                            steps = max(steps, 1)
                            logger.info(f"  направление {target_dir}°: поворот ({key} x{steps})...")

                            for _ in range(steps):
                                await page.keyboard.press(key)
                                await asyncio.sleep(_KEY_STEP_DELAY)

                            # Ждём загрузку тайлов для нового ракурса
                            await asyncio.sleep(_TILE_LOAD_WAIT)
                        else:
                            if idx == 0:
                                logger.info(f"  направление {target_dir}°: снимок...")

                        # Проверяем капчу
                        if await _is_captcha(page):
                            logger.warning(f"  направление {target_dir}°: обнаружена проверка, остальные пропущены")
                            break

                        # Скриншот
                        screenshot_bytes = await page.screenshot(type="jpeg", quality=80)

                        # Проверка: капча может быть визуальной (без текста) —
                        # тогда скриншот будет маленьким (< 40 KB)
                        if screenshot_bytes and len(screenshot_bytes) < 40000:
                            logger.warning(
                                f"  направление {target_dir}°: подозрительно маленький "
                                f"скриншот ({len(screenshot_bytes)} байт) — возможно, проверка/капча, пропуск"
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


# ========================
# Главный сборщик
# ========================

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
        "narodnaya_map": None,
    }

    # 1. Яндекс Static Map (схема)
    map_img = await get_yandex_map_screenshot(lat, lon)
    if map_img:
        result["map_image_bytes"] = map_img
        result["map_image_b64"] = base64.b64encode(map_img).decode("utf-8")
        result["sources_used"].append("yandex_map")

    # 2. Яндекс Народная карта (скоростные ограничения, камеры)
    narodnaya = await get_narodnaya_map_screenshot(lat, lon, zoom=17)
    if narodnaya:
        result["narodnaya_map"] = narodnaya
        result["sources_used"].append("narodnaya_map")

    # 3. Яндекс Панорама через Playwright
    panorama_shots = await get_yandex_panorama_screenshots(
        lat, lon, directions=directions,
    )
    if panorama_shots:
        result["street_images"].extend(panorama_shots)
        result["panorama_available"] = True
        result["sources_used"].append(f"yandex_panorama({len(panorama_shots)} фото)")

    # 4. Проверка наличия панорамы (API)
    if not result["panorama_available"]:
        pano_check = await check_yandex_panorama(lat, lon)
        if pano_check:
            result["panorama_available"] = True
            result["panorama_data"] = pano_check

    return result
