#!/usr/bin/env python3
"""
precache_osm.py — предкэширование границ населённых пунктов из OpenStreetMap
для топ-N регионов РФ.

Назначение:
    Прогреть кэш OSM-границ для наиболее востребованных регионов, чтобы
    при расчёте очагов ДТП (команда «Очаги ДТП», аналитика с ИИ, динамика)
    не делать live-запрос к Overpass API (5–30 секунд на регион).

Стратегия:
    Для каждого региона:
      1. Получить bbox региона через Nominatim API (один запрос).
      2. Разбить bbox на тайлы 2°×2° (использует существующую логику
         _compute_bbox_tiles из concentration_points.py).
      3. Для каждого тайла сделать запрос к Overpass (с rate limiting
         и обходом 4 зеркал) — аналогично runtime.
      4. Агрегировать все элементы в один список.
      5. Сохранить в /app/data/osm_cache/region_{code}.json через
         _save_region_cache() — тот же формат, что использует runtime.

Использование:
    # По умолчанию — топ-23 региона (список DEFAULT_REGIONS ниже)
    python precache_osm.py

    # Конкретные регионы (через запятую)
    python precache_osm.py --codes 1145,1146,1147

    # Из файла (по строкам: code,name)
    python precache_osm.py --regions-file my_regions.txt

    # Сухой прогон (без реальных запросов к Overpass/Nominatim)
    python precache_osm.py --dry-run

    # С verbose-логом
    python precache_osm.py --verbose

    # Только пропустить уже закэшированные (по умолчанию)
    python precache_osm.py --skip-existing

    # Принудительно обновить кэш для всех регионов
    python precache_osm.py --force

Запуск на Bothost (после деплоя v7):
    docker exec -it <container> python precache_osm.py
    # или через SSH-вход в контейнер.

Примечание:
    Скрипт можно запускать и локально (результат положить в data/osm_cache/
    рядом с кодом), но для Bothost-деплоя лучше запускать внутри контейнера,
    чтобы кэш попал в persistent volume /app/data/osm_cache/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

# Импортируем функции из основного модуля — они уже реализуют всю
# сложную логику (запрос к Overpass, разбор элементов, дедупликация).
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Импортируем ДО обращения к concentration_points, чтобы переменная
# окружения CAMERA_DATA_DIR указывала на правильный путь (если задана).
import concentration_points as cp
from concentration_points import (
    _save_region_cache,
    _load_region_cache,
    _region_cache_path,
    _compute_bbox_tiles,
    _dedup_elements,
    _parse_overpass_elements_with_ids,
    OVERPASS_URLS,
    PLACE_FILTER,
    CACHE_DIR,
    REGION_CACHE_DIR,
    REGION_CACHE_TTL_SECONDS,
)

# Внешние HTTP-запросы (Nominatim для bbox региона)
import httpx


# ========================
# Логирование
# ========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("precache_osm")


# ========================
# Топ-23 региона (по статистике ДТП + запросы пользователя)
# ========================
# Источник ранжирования: усреднённая статистика ГИБДД 2022–2024.
# Коды взяты из regions_builtin.json (проверены вручную).
DEFAULT_REGIONS: list[tuple[str, str]] = [
    # --- Топ-20 по ДТП ---
    ("1145", "гор. Москва"),
    ("1146", "Московская область"),
    ("1103", "Краснодарский край"),
    ("1140", "гор. Санкт-Петербург"),
    ("1165", "Свердловская область"),
    ("1160", "Ростовская область"),
    ("1192", "Республика Татарстан (Татарстан)"),
    ("1175", "Челябинская область"),
    ("1122", "Нижегородская область"),
    ("1180", "Республика Башкортостан"),
    ("1141", "Ленинградская область"),
    ("1104", "Красноярский край"),
    ("1150", "Новосибирская область"),
    ("1120", "Воронежская область"),
    ("1136", "Самарская область"),
    ("1157", "Пермский край"),
    ("1171", "Тюменская область"),
    ("1118", "Волгоградская область"),
    ("1107", "Ставропольский край"),
    ("1125", "Иркутская область"),
    # --- Дополнительно по запросу пользователя ---
    ("1147", "Мурманская область"),
    ("1133", "Кировская область"),
    ("1119", "Вологодская область"),
]


# ========================
# Nominatim: получение bbox региона
# ========================
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {
    "User-Agent": "GIBDD-DTP-Bot/1.0 (osm-precache)",
    "Accept": "application/json",
}
# Nominatim требует max 1 запрос/сек по Usage Policy.
NOMINATIM_MIN_INTERVAL = 1.2  # сек
_nominatim_last_request: float = 0.0


# BBox'ы для топ-регионов (заранее зашиты, чтобы не дёргать Nominatim
# при каждом запуске). Значения в формате (lat_min, lon_min, lat_max, lon_max).
# Источник: Nominatim + открытые данные OSM (admin_level=4).
# Если регион не указан в словаре — будет запрошен через Nominatim.
HARDCODED_BBOXES: dict[str, tuple[float, float, float, float]] = {
    "1145": (55.18, 36.85, 56.20, 38.10),   # гор. Москва
    "1146": (54.85, 35.50, 57.20, 40.20),   # Московская область
    "1103": (43.45, 36.65, 46.95, 41.40),   # Краснодарский край
    "1140": (59.65, 29.70, 60.45, 30.95),   # гор. Санкт-Петербург
    "1165": (56.40, 57.20, 60.45, 66.60),   # Свердловская область
    "1160": (46.50, 38.10, 50.40, 44.10),   # Ростовская область
    "1192": (53.80, 47.50, 56.70, 54.40),   # Республика Татарстан
    "1175": (51.90, 57.20, 56.30, 62.50),   # Челябинская область
    "1122": (54.20, 41.50, 58.10, 47.30),   # Нижегородская область
    "1180": (51.40, 53.20, 56.50, 60.20),   # Республика Башкортостан
    "1141": (58.30, 27.50, 61.20, 35.50),   # Ленинградская область
    "1104": (51.10, 75.50, 81.50, 106.70),  # Красноярский край (огромный)
    "1150": (53.10, 75.10, 57.20, 84.20),   # Новосибирская область
    "1120": (49.50, 38.00, 52.50, 43.10),   # Воронежская область
    "1136": (51.40, 47.70, 54.80, 52.50),   # Самарская область
    "1157": (55.90, 51.30, 61.40, 60.10),   # Пермский край
    "1171": (55.20, 58.20, 60.10, 78.80),   # Тюменская область (без округов)
    "1118": (47.20, 41.20, 51.20, 47.10),   # Волгоградская область
    "1107": (43.50, 40.60, 46.40, 45.50),   # Ставропольский край
    "1125": (51.10, 96.50, 64.20, 119.10),  # Иркутская область
    "1147": (66.00, 28.20, 70.10, 41.50),   # Мурманская область
    "1133": (56.00, 47.20, 60.70, 54.40),   # Кировская область
    "1119": (58.20, 34.20, 60.90, 43.10),   # Вологодская область
    # --- Городовые регионы (гор. фед. значения) ---
    # Nominatim не отдаёт их с countrycodes=ru + featureType=state
    # (Севастополь — спорная территория в OSM), поэтому зашиваем напрямую.
    "1167": (44.39, 33.30, 44.85, 33.85),   # гор. Севастополь
    "1199": (43.40, 39.85, 44.20, 40.50),   # гор. Сочи (не регион ГИБДД, но используется)
}


async def _nominatim_rate_limit() -> None:
    """Соблюдает min интервал между запросами к Nominatim (1 запрос/сек)."""
    global _nominatim_last_request
    now = time.time()
    elapsed = now - _nominatim_last_request
    if elapsed < NOMINATIM_MIN_INTERVAL:
        await asyncio.sleep(NOMINATIM_MIN_INTERVAL - elapsed)
    _nominatim_last_request = time.time()


async def fetch_region_bbox(
    reg_code: str,
    reg_name: str,
    client: httpx.AsyncClient,
) -> Optional[tuple[float, float, float, float]]:
    """
    Получает bbox региона через Nominatim API.

    Returns:
        (lat_min, lon_min, lat_max, lon_max) или None при ошибке.
    """
    # Сначала проверяем зашитый bbox
    if reg_code in HARDCODED_BBOXES:
        bbox = HARDCODED_BBOXES[reg_code]
        logger.info(f"  {reg_name} ({reg_code}): hardcoded bbox {bbox}")
        return bbox

    # Защита от мусорного имени "Регион XXX" — Nominatim его не поймёт.
    # Если такое пришло — пробуем подгрузить правильное имя из regions_builtin.json.
    if reg_name.startswith("Регион ") and reg_name.split()[-1] == reg_code:
        names_map = _load_all_region_names()
        if reg_code in names_map:
            reg_name = names_map[reg_code]
            logger.info(f"  {reg_code}: имя подгружено из regions_builtin.json → {reg_name}")
        else:
            logger.error(
                f"  {reg_code}: имя не передано и в regions_builtin.json не найдено. "
                f"Nominatim запрос невозможен. "
                f"Укажите имя явно: /precache {reg_code},<Название региона>"
            )
            return None

    # Запрос к Nominatim: пробуем разные комбинации имени и параметров.
    # Для городовых регионов (гор. Москва, гор. Санкт-Петербург, гор. Севастополь)
    # featureType=state НЕ работает — нужен featureType=city или без него.
    # countrycodes=ru тоже мешает для Севастополя (OSM считает его Украиной).
    # Поэтому пробуем 4 комбинации: с/без countrycodes + с/без featureType.
    clean_name = reg_name.replace("гор. ", "").split(" (")[0]
    is_city = reg_name.startswith("гор. ")

    queries_to_try: list[tuple[str, dict]] = []
    # Для городовых — сначала без countrycodes, featureType=city
    if is_city:
        queries_to_try.append((
            f"{clean_name}, Россия",
            {"q": f"{clean_name}, Россия", "format": "json", "limit": 1, "featureType": "city"},
        ))
        queries_to_try.append((
            clean_name,
            {"q": clean_name, "format": "json", "limit": 1, "featureType": "city"},
        ))
        queries_to_try.append((
            f"{clean_name}, Россия",
            {"q": f"{clean_name}, Россия", "format": "json", "limit": 1},
        ))
    # Для областей/краёв/республик — featureType=state + countrycodes=ru
    else:
        queries_to_try.append((
            f"{clean_name}, Россия",
            {"q": f"{clean_name}, Россия", "format": "json", "limit": 1,
             "countrycodes": "ru", "featureType": "state"},
        ))
        queries_to_try.append((
            f"{clean_name}, Россия",
            {"q": f"{clean_name}, Россия", "format": "json", "limit": 1,
             "countrycodes": "ru"},
        ))
        queries_to_try.append((
            clean_name,
            {"q": clean_name, "format": "json", "limit": 1},
        ))

    for qi, (query_desc, params) in enumerate(queries_to_try):
        await _nominatim_rate_limit()
        try:
            logger.info(
                f"  {reg_name} ({reg_code}): Nominatim запрос {qi+1}/{len(queries_to_try)}: "
                f"'{query_desc}' params={{{', '.join(f'{k}={v}' for k, v in params.items() if k != 'q' and k != 'format')}}}"
            )
            resp = await client.get(
                NOMINATIM_URL,
                params=params,
                headers=NOMINATIM_HEADERS,
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data:
                logger.warning(
                    f"  {reg_name} ({reg_code}): Nominatim пустой ответ для запроса '{query_desc}'"
                )
                continue

            bbox_str = data[0].get("boundingbox", [])
            if len(bbox_str) != 4:
                logger.warning(f"  {reg_name} ({reg_code}): Nominatim bbox некорректный")
                continue

            # Nominatim возвращает: [south, north, west, east]
            # Переводим в (lat_min, lon_min, lat_max, lon_max)
            lat_min = float(bbox_str[0])
            lat_max = float(bbox_str[1])
            lon_min = float(bbox_str[2])
            lon_max = float(bbox_str[3])
            bbox = (lat_min, lon_min, lat_max, lon_max)
            display_name = data[0].get("display_name", "")[:60]
            logger.info(
                f"  {reg_name} ({reg_code}): Nominatim bbox "
                f"({lat_min:.2f}, {lon_min:.2f}, {lat_max:.2f}, {lon_max:.2f}) "
                f"→ {display_name}"
            )
            return bbox
        except Exception as e:
            logger.error(f"  {reg_name} ({reg_code}): Nominatim ошибка для '{query_desc}': {e}")
            continue

    logger.error(f"  {reg_name} ({reg_code}): все варианты Nominatim запросов не удались")
    return None


# ========================
# Overpass: запрос тайлов
# ========================
async def fetch_tile_elements(
    tile_bbox: str,
    client: httpx.AsyncClient,
    place_filter: str = PLACE_FILTER,
) -> Optional[list[dict]]:
    """
    Запрос к Overpass для одного тайла с обходом зеркал.

    Использует тот же формат запроса, что и runtime в
    concentration_points._fetch_overpass_parallel().
    """
    geom_query = (
        "[out:json][timeout:90];\n"
        "(\n"
        f'  relation["place"~"{place_filter}"]({tile_bbox});\n'
        f'  way["place"~"{place_filter}"]({tile_bbox});\n'
        ");\n"
        "out geom;\n"
    )

    # Обходим зеркала последовательно
    for url_idx, url in enumerate(OVERPASS_URLS):
        # Rate limit: 10 сек между запросами (как в runtime)
        await asyncio.sleep(10.0 if url_idx > 0 else 0)

        try:
            logger.info(f"    Overpass ({url.split('//')[1][:25]}): запрос...")
            resp = await client.post(
                url,
                data={"data": geom_query},
                headers={
                    "User-Agent": "GIBDD-DTP-Bot/1.0 (osm-precache)",
                    "Accept": "application/json",
                },
                timeout=120.0,
            )

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", "30"))
                logger.warning(
                    f"    Overpass: HTTP 429, ждём {retry_after:.0f}с"
                )
                await asyncio.sleep(retry_after)
                continue

            resp.raise_for_status()
            data = resp.json()
            elements = data.get("elements", [])

            # Проверяем, что ответ — реальные полигоны, а не bbox-фолбэк
            polygons, is_bbox, _ = _parse_overpass_elements_with_ids(elements)
            if polygons and not is_bbox:
                logger.info(
                    f"    Overpass: {len(elements)} элементов, "
                    f"{len(polygons)} полигонов"
                )
                return elements
            elif is_bbox:
                logger.warning(
                    f"    Overpass: ответ содержит bbox-фолбэк, "
                    f"пробуем следующее зеркало"
                )
                continue
            else:
                logger.warning(
                    f"    Overpass: 0 полигонов в ответе, "
                    f"пробуем следующее зеркало"
                )
                continue

        except Exception as e:
            logger.warning(f"    Overpass ({url}): {e}")
            continue

    logger.error(f"    Overpass: все зеркала недоступны для тайла {tile_bbox}")
    return None


# ========================
# Главная функция: предкэш одного региона
# ========================
async def precache_region(
    reg_code: str,
    reg_name: str,
    client: httpx.AsyncClient,
    force: bool = False,
) -> dict:
    """
    Предкэширует один регион.

    Returns:
        Словарь с результатом: {reg_code, reg_name, status, elements_count, ...}
    """
    result = {
        "reg_code": reg_code,
        "reg_name": reg_name,
        "status": "skipped",
        "tiles_total": 0,
        "tiles_ok": 0,
        "elements_count": 0,
        "polygons_count": 0,
        "size_mb": 0.0,
        "duration_sec": 0.0,
        "error": None,
    }

    start_time = time.time()

    # 1. Проверяем существующий кэш (если не force)
    if not force:
        existing = _load_region_cache(reg_code)
        if existing is not None:
            path = _region_cache_path(reg_code)
            size_mb = os.path.getsize(path) / (1024 * 1024)
            result.update(
                status="cached",
                elements_count=len(existing),
                size_mb=round(size_mb, 2),
                duration_sec=round(time.time() - start_time, 1),
            )
            logger.info(
                f"[{reg_code}] {reg_name}: УЖЕ В КЭШЕ "
                f"({len(existing)} элементов, {size_mb:.1f} МБ) — пропуск"
            )
            return result

    logger.info(f"[{reg_code}] {reg_name}: начинаем предкэширование")

    # 2. Получаем bbox региона
    bbox = await fetch_region_bbox(reg_code, reg_name, client)
    if bbox is None:
        result["status"] = "error"
        result["error"] = "Не удалось получить bbox региона"
        result["duration_sec"] = round(time.time() - start_time, 1)
        return result

    lat_min, lon_min, lat_max, lon_max = bbox

    # 3. Разбиваем на тайлы (использует ту же логику, что и runtime)
    tiles = _compute_bbox_tiles(lat_min, lon_min, lat_max, lon_max)
    result["tiles_total"] = len(tiles)
    logger.info(
        f"  {reg_name}: bbox ({lat_min:.2f},{lon_min:.2f},"
        f"{lat_max:.2f},{lon_max:.2f}) → {len(tiles)} тайл(ов)"
    )

    # 4. Запрашиваем каждый тайл
    all_elements: list[dict] = []
    seen_ids: set[tuple[str, int]] = set()
    total_polygons = 0

    for tile_idx, (t_lat_min, t_lon_min, t_lat_max, t_lon_max) in enumerate(tiles):
        tile_bbox = f"{t_lat_min},{t_lon_min},{t_lat_max},{t_lon_max}"
        logger.info(
            f"  Тайл {tile_idx + 1}/{len(tiles)}: "
            f"({t_lat_min:.2f},{t_lon_min:.2f},"
            f"{t_lat_max:.2f},{t_lon_max:.2f})"
        )

        elements = await fetch_tile_elements(tile_bbox, client)
        if elements is None:
            logger.warning(
                f"  Тайл {tile_idx + 1}/{len(tiles)}: failed"
            )
            continue

        result["tiles_ok"] += 1

        # Дедуплицируем по OSM ID (тайлы имеют перехлёст).
        # ВАЖНО: _parse_overpass_elements_with_ids возвращает tile_ids только
        # для успешно разобранных полигонов — длина может быть МЕНЬШЕ len(elements).
        # Поэтому строим map ID→element и ищем уникальные через него,
        # а НЕ через zip(elements, tile_ids) — тот ломается при рассинхроне длин.
        tile_polys, tile_is_bbox, tile_ids = _parse_overpass_elements_with_ids(
            elements
        )

        # Map: (type, id) → исходный element из Overpass
        id_to_element: dict[tuple[str, int], dict] = {}
        for el in elements:
            eid = (el.get("type", ""), el.get("id", 0))
            if eid not in id_to_element:
                id_to_element[eid] = el

        # Единственная дедупликация: проверяем seen_ids и добавляем новые ID.
        # Раньше тут был вызов _dedup_polygons_by_id, который ПОРТИЛ seen_ids
        # до цикла ниже — из-за этого unique_elements всегда был пустым.
        unique_elements: list[dict] = []
        new_polys: list = []
        for poly, eid in zip(tile_polys, tile_ids):
            if eid not in seen_ids:
                seen_ids.add(eid)
                el = id_to_element.get(eid)
                if el is not None:
                    unique_elements.append(el)
                    new_polys.append(poly)

        all_elements.extend(unique_elements)
        total_polygons += len(new_polys)

        logger.info(
            f"  Тайл {tile_idx + 1}/{len(tiles)}: +{len(unique_elements)} "
            f"уникальных элементов (всего: {len(all_elements)})"
        )

    if not all_elements:
        result["status"] = "error"
        result["error"] = "Не получено ни одного элемента из Overpass"
        result["duration_sec"] = round(time.time() - start_time, 1)
        logger.error(f"  {reg_name}: нет данных от Overpass")
        return result

    # 5. Сохраняем в регион-уровневый кэш
    _save_region_cache(reg_code, all_elements, region_name=reg_name)

    path = _region_cache_path(reg_code)
    size_mb = os.path.getsize(path) / (1024 * 1024)
    duration = time.time() - start_time

    result.update(
        status="ok",
        elements_count=len(all_elements),
        polygons_count=total_polygons,
        size_mb=round(size_mb, 2),
        duration_sec=round(duration, 1),
    )

    logger.info(
        f"  {reg_name}: ГОТОВО — {len(all_elements)} элементов, "
        f"{total_polygons} полигонов, {size_mb:.1f} МБ, "
        f"{duration:.0f}с"
    )

    return result


# ========================
# CLI
# ========================
def _load_all_region_names() -> dict[str, str]:
    """
    Загружает полный справочник регионов из regions_builtin.json.
    Возвращает {code: name} для всех 82 регионов РФ.

    Используется как fallback при --codes: если регион не в DEFAULT_REGIONS,
    но есть в regions_builtin — берём правильное имя для Nominatim.
    """
    try:
        # Ищем regions_builtin.json рядом с репозиторием
        path = Path(__file__).resolve().parent / "regions_builtin.json"
        if not path.exists():
            logger.warning(f"regions_builtin.json не найден: {path}")
            return {}

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return {str(r.get("code", "")): r.get("name", "") for r in data}
        elif isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
        return {}
    except Exception as e:
        logger.warning(f"Ошибка загрузки regions_builtin.json: {e}")
        return {}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Предкэширование OSM-границ для топ-N регионов РФ",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--codes",
        type=str,
        default=None,
        help="Список кодов регионов через запятую (например, 1145,1146,1147)",
    )
    p.add_argument(
        "--regions-file",
        type=str,
        default=None,
        help="Путь к файлу со списком регионов (формат: code,name по строкам)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Принудительно обновить кэш для всех регионов",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Сухой прогон: показать план без реальных запросов",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Подробное логирование",
    )
    return p.parse_args()


def load_regions(args: argparse.Namespace) -> list[tuple[str, str]]:
    """Возвращает список (code, name) для предкэширования."""
    if args.codes:
        # Парсим коды, имена берём из полного справочника regions_builtin.json
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        names_map = _load_all_region_names()
        return [(c, names_map.get(c, f"Регион {c}")) for c in codes]

    if args.regions_file:
        regions = []
        with open(args.regions_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(",", 1)
                if len(parts) == 2:
                    regions.append((parts[0].strip(), parts[1].strip()))
                else:
                    regions.append((parts[0].strip(), f"Регион {parts[0].strip()}"))
        return regions

    return DEFAULT_REGIONS


async def main() -> int:
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    regions = load_regions(args)

    print(f"\n{'=' * 60}")
    print(f" precache_osm — предкэширование OSM-границ")
    print(f"{'=' * 60}")
    print(f"Регионов: {len(regions)}")
    print(f"Кэш-директория: {REGION_CACHE_DIR}")
    print(f"TTL: {REGION_CACHE_TTL_SECONDS // 86400} дней")
    print(f"Force: {args.force}")
    print(f"Dry-run: {args.dry_run}")
    print(f"{'=' * 60}\n")

    # Проверяем, что директория кэша существует (создаём если нет)
    os.makedirs(REGION_CACHE_DIR, exist_ok=True)

    if args.dry_run:
        print("ПЛАН (dry-run):\n")
        for code, name in regions:
            path = _region_cache_path(code)
            exists = "✓ есть" if os.path.exists(path) else "✗ нет"
            bbox = HARDCODED_BBOXES.get(code, "→ Nominatim")
            print(f"  [{code}] {name}")
            print(f"     Кэш: {exists}")
            print(f"     BBox: {bbox}")
        return 0

    # HTTP-клиент для Nominatim + Overpass
    async with httpx.AsyncClient(verify=False) as client:
        results: list[dict] = []

        for i, (code, name) in enumerate(regions, 1):
            print(f"\n[{i}/{len(regions)}] === {name} ({code}) ===")
            result = await precache_region(code, name, client, force=args.force)
            results.append(result)

            # Пауза между регионами (для Overpass)
            if i < len(regions):
                pause = 15  # сек
                logger.info(f"  Пауза {pause}с перед следующим регионом...")
                await asyncio.sleep(pause)

    # Сводка
    print(f"\n{'=' * 60}")
    print(" СВОДКА")
    print(f"{'=' * 60}")
    print(f"{'Код':<6} {'Регион':<40} {'Статус':<10} {'Элементов':<10} {'МБ':<8} {'Сек':<6}")
    print("-" * 80)
    total_elements = 0
    total_mb = 0.0
    total_sec = 0.0
    ok_count = 0
    cached_count = 0
    err_count = 0
    for r in results:
        print(
            f"{r['reg_code']:<6} {r['reg_name'][:39]:<40} "
            f"{r['status']:<10} {r['elements_count']:<10} "
            f"{r['size_mb']:<8.1f} {r['duration_sec']:<6.1f}"
        )
        total_elements += r["elements_count"]
        total_mb += r["size_mb"]
        total_sec += r["duration_sec"]
        if r["status"] == "ok":
            ok_count += 1
        elif r["status"] == "cached":
            cached_count += 1
        elif r["status"] == "error":
            err_count += 1

    print("-" * 80)
    print(
        f"Итого: OK={ok_count}, Cached={cached_count}, Err={err_count} | "
        f"{total_elements} элементов, {total_mb:.1f} МБ, "
        f"{total_sec:.0f}с ({total_sec / 60:.1f} мин)"
    )

    # Сохраняем JSON-отчёт
    report_path = os.path.join(REGION_CACHE_DIR, "precache_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": time.time(),
                "total_regions": len(results),
                "ok": ok_count,
                "cached": cached_count,
                "errors": err_count,
                "total_elements": total_elements,
                "total_mb": round(total_mb, 2),
                "total_duration_sec": round(total_sec, 1),
                "regions": results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\nОтчёт: {report_path}")

    return 0 if err_count == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
