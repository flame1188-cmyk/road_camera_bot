"""
Модуль расчёта очагов концентрации ДТП (мест концентрации аварийности).

Два алгоритма:
  1. Населённые пункты (НП) — 3 прохода (перекрёстки 50 м, дороги 200 м, точки 100 м)
  2. Вне НП (автодороги) — группировка по названию дороги, скользящее окно 1 км

Порог: 3+ ДТП одного вида ИЛИ 5+ ДТП любых видов.

Определение НП/не НП через OSM Overpass API с реальными полигонами (Shapely).
Границы кэшируются на диске (TTL 24 ч).
"""

from __future__ import annotations

import math
import json
import os
import time
import hashlib
import logging
from collections import Counter
from typing import Any, Callable, Awaitable

import httpx
from shapely.geometry import Polygon, MultiPolygon, Point, LineString
from shapely.ops import linemerge, polygonize, unary_union
from shapely.prepared import prep

from gibdd.analytics import _safe_int

logger = logging.getLogger(__name__)

# ========================
# Константы
# ========================

EARTH_RADIUS_KM = 6371.0

SETTLEMENT_INTERSECTION_RADIUS_M = 50
SETTLEMENT_OTHER_RADIUS_M = 100
SETTLEMENT_ROAD_WINDOW_KM = 0.2

NON_SETTLEMENT_WINDOW_KM = 1.0
NON_SETTLEMENT_NO_PK_WINDOW_KM = 0.2

SAME_TYPE_THRESHOLD = 3
ANY_TYPE_THRESHOLD = 5

INTERSECTION_KEYWORDS = [
    "перекрёсток", "перекресток",
    "перекрёстка", "перекрестка",
    "перекрёстку", "перекрестку",
    "перекрёстке", "перекрестке",
    "перекрёстков", "перекрестков",
    "круговое движение",
    "круговым движением",
]

EXCLUDED_SDOR_ALWAYS = [
    "внутридворовая территория",
    "отделенная от проезжей части",
]
EXCLUDED_K_UL = "иные места"
EXCLUDED_SDOR_FOR_KUL = [
    "выезд с прилегающей территории",
    "тротуар, пешеходная дорожка",
    "иное место",
]

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
CACHE_TTL_SECONDS = 24 * 60 * 60


# ========================
# Вспомогательные функции
# ========================

def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние в метрах между двумя точками по формуле Гаверсинуса."""
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(min(a, 1.0)))
    return EARTH_RADIUS_KM * c * 1000.0


def _parse_coords(card: dict) -> tuple[float, float] | None:
    """Извлечь координаты из карточки. Возвращает (lat, lon) или None."""
    try:
        lat = float(str(card.get("coord_w", "")).strip())
        lon = float(str(card.get("coord_l", "")).strip())
        if lat != 0 and lon != 0:
            return (lat, lon)
    except (ValueError, TypeError):
        pass
    return None


def _is_intersection(card: dict) -> bool:
    """Является ли место ДТП перекрёстком (по полю sdor)."""
    dor_usl = card.get("dor_usl") or {}
    sdor_list = dor_usl.get("sdor") or []
    if isinstance(sdor_list, list):
        for item in sdor_list:
            item_lower = str(item).strip().lower()
            for keyword in INTERSECTION_KEYWORDS:
                if keyword in item_lower:
                    return True
    return False


def _is_off_road(card: dict) -> bool:
    """Произошло ли ДТП вне дороги."""
    dor_usl = card.get("dor_usl") or {}
    sdor_list = dor_usl.get("sdor") or []
    sdor_lower = []
    if isinstance(sdor_list, list):
        sdor_lower = [str(item).strip().lower() for item in sdor_list]

    for item_lower in sdor_lower:
        for keyword in EXCLUDED_SDOR_ALWAYS:
            if keyword in item_lower:
                return True

    k_ul = str(card.get("k_ul", "")).strip().lower()
    if k_ul == EXCLUDED_K_UL:
        for item_lower in sdor_lower:
            for keyword in EXCLUDED_SDOR_FOR_KUL:
                if keyword in item_lower:
                    return True
    return False


def _get_dtp_type(card: dict) -> str:
    return str(card.get("dtpv", "")).strip()


def _get_road_name(card: dict) -> str:
    dor = str(card.get("dor", "")).strip()
    if dor:
        return dor
    return str(card.get("street", "")).strip()


def _get_date(card: dict) -> str:
    return str(card.get("date_dtp", "")).strip()


def _get_km_m(card: dict) -> float | None:
    """Пикетаж как float (км.ddd)."""
    km_str = str(card.get("km", "")).strip()
    m_str = str(card.get("m", "")).strip()
    if km_str:
        try:
            km_val = float(km_str)
            m_val = float(m_str) if m_str else 0.0
            total = km_val + m_val / 1000.0
            if total == 0.0:
                return None
            return total
        except ValueError:
            pass
    return None


def _has_road_and_piketazh(card: dict) -> bool:
    return bool(_get_road_name(card)) and _get_km_m(card) is not None


def _check_cluster_criteria(type_counter: Counter, total: int) -> tuple[bool, str | None]:
    """Проверяет, выполняется ли критерий очага."""
    for dtp_type, count in type_counter.most_common():
        if count >= SAME_TYPE_THRESHOLD:
            return True, dtp_type
    if total >= ANY_TYPE_THRESHOLD:
        return True, None
    return False, None


# ========================
# Кэширование границ НП
# ========================

def _cache_path(bbox_str: str) -> str:
    h = hashlib.md5(bbox_str.encode()).hexdigest()[:12]
    return os.path.join(CACHE_DIR, f"settlements_{h}.json")


def _load_cache(bbox_str: str) -> list[dict] | None:
    path = _cache_path(bbox_str)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        age = time.time() - data.get("timestamp", 0)
        if age > CACHE_TTL_SECONDS:
            return None
        return data.get("elements", [])
    except Exception as e:
        logger.warning(f"Ошибка чтения кэша: {e}")
        return None


def _save_cache(bbox_str: str, elements: list[dict]) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = _cache_path(bbox_str)
        data = {"timestamp": time.time(), "bbox": bbox_str, "count": len(elements), "elements": elements}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Ошибка записи кэша: {e}")


# ========================
# OSM: Разбор полигонов
# ========================

def _way_to_polygon(element: dict) -> Polygon | None:
    geom = element.get("geometry", [])
    if len(geom) < 4:
        return None
    try:
        coords = [(n["lon"], n["lat"]) for n in geom]
        poly = Polygon(coords)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.area < 1e-10:
            return None
        return poly
    except Exception:
        return None


def _relation_to_polygon(element: dict) -> Polygon | MultiPolygon | None:
    members = element.get("members", [])
    if not members:
        return None
    outer_rings: list[list[tuple[float, float]]] = []
    inner_rings: list[list[tuple[float, float]]] = []
    for member in members:
        geom = member.get("geometry", [])
        if len(geom) < 2:
            continue
        coords = [(n["lon"], n["lat"]) for n in geom]
        role = member.get("role", "outer")
        if role == "inner":
            inner_rings.append(coords)
        else:
            outer_rings.append(coords)
    if not outer_rings:
        return None
    try:
        outer_lines = [LineString(ring) for ring in outer_rings]
        merged = linemerge(outer_lines)
        polygons: list[Polygon] = []
        if merged.geom_type == "LineString":
            if merged.is_closed:
                polygons.append(Polygon(merged))
        elif merged.geom_type == "MultiLineString":
            polygons.extend(polygonize(merged))
        else:
            return None
        if not polygons:
            return None
        if inner_rings:
            for i, poly in enumerate(polygons):
                for hole_coords in inner_rings:
                    try:
                        hole_line = LineString(hole_coords)
                        if hole_line.is_closed and poly.contains(hole_line):
                            polygons[i] = poly.difference(Polygon(hole_coords))
                    except Exception:
                        pass
        valid_polygons: list[Polygon] = []
        for p in polygons:
            if not p.is_valid:
                p = p.buffer(0)
            if not p.is_empty and p.area > 1e-10:
                valid_polygons.append(p)
        if not valid_polygons:
            return None
        if len(valid_polygons) == 1:
            return valid_polygons[0]
        return MultiPolygon(valid_polygons)
    except Exception:
        return None


def _parse_overpass_elements(elements: list[dict]) -> list[Polygon | MultiPolygon]:
    polygons: list[Polygon | MultiPolygon] = []
    has_geom = False
    for element in elements:
        if element.get("type") == "way" and element.get("geometry"):
            has_geom = True
        elif element.get("type") == "relation" and element.get("members"):
            has_geom = True
    if has_geom:
        for element in elements:
            if element.get("type") == "way":
                poly = _way_to_polygon(element)
                if poly is not None:
                    polygons.append(poly)
            elif element.get("type") == "relation":
                poly = _relation_to_polygon(element)
                if poly is not None:
                    polygons.append(poly)
    if polygons:
        return polygons
    for element in elements:
        if "bounds" in element:
            b = element["bounds"]
            coords = [(b["minlon"], b["minlat"]), (b["maxlon"], b["minlat"]),
                       (b["maxlon"], b["maxlat"]), (b["minlon"], b["maxlat"])]
            try:
                poly = Polygon(coords)
                if poly.is_valid and poly.area > 0:
                    polygons.append(poly)
            except Exception:
                pass
    return polygons


# ========================
# OSM: Получение границ НП
# ========================

async def fetch_settlement_boundaries(
    cards: list[dict],
    progress_callback: Callable[[str], Awaitable[None]] | None = None,
) -> list[Polygon | MultiPolygon]:
    """Получает полигоны границ НП через Overpass API."""
    valid_coords = [_parse_coords(c) for c in cards]
    valid_coords = [c for c in valid_coords if c is not None]
    if not valid_coords:
        return []

    lats = [c[0] for c in valid_coords]
    lons = [c[1] for c in valid_coords]
    margin = 0.1
    lat_min = max(min(lats) - margin, 41.0)
    lon_min = max(min(lons) - margin, 19.0)
    lat_max = min(max(lats) + margin, 70.0)
    lon_max = min(max(lons) + margin, 180.0)
    bbox = f"{lat_min},{lon_min},{lat_max},{lon_max}"

    cached_elements = _load_cache(bbox)
    if cached_elements is not None:
        polygons = _parse_overpass_elements(cached_elements)
        if polygons:
            return polygons

    if progress_callback:
        await progress_callback(f"Загрузка границ НП из OpenStreetMap...\nBBOX: {bbox}")

    overpass_urls = [
        "https://overpass-api.de/api/interpreter",
        "https://lz4.overpass-api.de/api/interpreter",
        "https://z.overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ]
    headers = {"User-Agent": "GIBDD-DTP-Bot/1.0", "Accept": "application/json"}
    place_filter = "city|town|village|hamlet"

    geom_query = (
        f'[out:json][timeout:90];\n(\n'
        f'  relation["place"~"{place_filter}"]({bbox});\n'
        f'  way["place"~"{place_filter}"]({bbox});\n);\nout geom;\n'
    )
    bb_query = (
        f'[out:json][timeout:90];\n(\n'
        f'  relation["place"~"{place_filter}"]({bbox});\n'
        f'  way["place"~"{place_filter}"]({bbox});\n);\nout bb;\n'
    )

    for url in overpass_urls:
        elements = await _overpass_request(url, geom_query, headers, "geom")
        if elements is not None:
            polygons = _parse_overpass_elements(elements)
            if polygons:
                _save_cache(bbox, elements)
                return polygons
        elements = await _overpass_request(url, bb_query, headers, "bb")
        if elements is not None:
            polygons = _parse_overpass_elements(elements)
            if polygons:
                _save_cache(bbox, elements)
                return polygons

    return []


async def _overpass_request(url: str, query: str, headers: dict, mode: str) -> list[dict] | None:
    try:
        async with httpx.AsyncClient(verify=False, headers=headers) as client:
            resp = await client.post(url, data={"data": query}, timeout=120)
            resp.raise_for_status()
            data = resp.json()
        return data.get("elements", [])
    except Exception as e:
        logger.warning(f"Overpass API ({url}, mode={mode}): {e}")
        return None


# ========================
# Классификация ДТП: НП / вне НП
# ========================

def classify_cards(
    cards: list[dict],
    settlement_polygons: list[Polygon | MultiPolygon],
) -> tuple[list[dict], list[dict]]:
    """Разделяет карточки на НП и вне НП."""
    if not settlement_polygons:
        return [], list(cards)

    settlement_cards = []
    non_settlement_cards = []

    try:
        merged = unary_union(settlement_polygons)
        prepared = prep(merged)
        use_prepared = True
    except Exception:
        prepared = None
        use_prepared = False

    for card in cards:
        coords = _parse_coords(card)
        if coords is None:
            non_settlement_cards.append(card)
            continue
        point = Point(coords[1], coords[0])
        in_settlement = False
        try:
            if use_prepared and prepared is not None:
                in_settlement = prepared.contains(point)
            else:
                for poly in settlement_polygons:
                    try:
                        if poly.contains(point):
                            in_settlement = True
                            break
                    except Exception:
                        continue
        except Exception:
            pass
        if in_settlement:
            settlement_cards.append(card)
        else:
            non_settlement_cards.append(card)

    logger.info(f"Классификация: {len(settlement_cards)} в НП, {len(non_settlement_cards)} вне НП")
    return settlement_cards, non_settlement_cards


# ========================
# Алгоритм: НП
# ========================

def _build_cluster(
    cards: list[dict], center: tuple[float, float] | None, zone_type: str,
    road_name: str = "", start_pos: float | None = None, end_pos: float | None = None,
) -> dict:
    """Формирует словарь очага из группы карточек."""
    total_deaths = sum(_safe_int(c.get("pog")) for c in cards)
    total_injured = sum(_safe_int(c.get("ran")) for c in cards)
    dates = [_get_date(c) for c in cards]
    type_counter = Counter(_get_dtp_type(c) for c in cards)
    dominant = None
    for t, cnt in type_counter.most_common():
        if cnt >= SAME_TYPE_THRESHOLD:
            dominant = t
            break
    road = road_name or _get_road_name(cards[0])
    first_coords = _parse_coords(cards[0])
    last_coords = _parse_coords(cards[-1])
    return {
        "zone_type": zone_type, "road": road,
        "total_accidents": len(cards), "deaths": total_deaths, "injured": total_injured,
        "dates": dates, "type_counter": dict(type_counter), "dominant_type": dominant,
        "first_coords": first_coords, "last_coords": last_coords,
        "center": center or first_coords or (0, 0),
        "start_pos": start_pos, "end_pos": end_pos, "cards": cards,
    }


def find_settlement_concentration_points(cards: list[dict]) -> list[dict]:
    """Поиск очагов в населённых пунктах — 3 прохода."""
    if not cards:
        return []

    indexed = [(i, c) for i, c in enumerate(cards)]
    indexed.sort(key=lambda x: _get_date(x[1]))
    indexed_with_coords = [(i, c) for i, c in indexed if _parse_coords(c)]
    assigned: set[int] = set()
    clusters: list[dict] = []

    # --- 1-й проход: перекрёстки 50 м ---
    # Шаг 1a: с пикетажем
    for idx, card in indexed_with_coords:
        if idx in assigned or not _is_intersection(card) or not _has_road_and_piketazh(card):
            continue
        center_road = _get_road_name(card)
        center_km = _get_km_m(card)
        center = _parse_coords(card)
        if center is None:
            continue

        # По пикетажу
        piketazh_candidates = []
        for j, c in indexed_with_coords:
            if j in assigned or j == idx:
                continue
            if _get_road_name(c) != center_road:
                continue
            other_km = _get_km_m(c)
            if other_km is None or abs(center_km - other_km) * 1000.0 > SETTLEMENT_INTERSECTION_RADIUS_M:
                continue
            if not _is_intersection(c):
                continue
            piketazh_candidates.append((j, c))

        if piketazh_candidates:
            group_cards = [card] + [c for _, c in piketazh_candidates]
            type_counter = Counter(_get_dtp_type(c) for c in group_cards)
            if _check_cluster_criteria(type_counter, len(group_cards))[0]:
                assigned.add(idx)
                for j, _ in piketazh_candidates:
                    assigned.add(j)
                group_cards.sort(key=lambda c: _get_date(c))
                clusters.append(_build_cluster(group_cards, center, "settlement_intersection"))
                continue

        # Fallback: радиус 50 м по GPS
        group_indices = [idx]
        group_cards = [card]
        for j, c in indexed_with_coords:
            if j in assigned or j == idx or not _is_intersection(c):
                continue
            coords = _parse_coords(c)
            if coords is None:
                continue
            dist = haversine_meters(center[0], center[1], coords[0], coords[1])
            if dist > SETTLEMENT_INTERSECTION_RADIUS_M:
                continue
            other_road = _get_road_name(c)
            other_km = _get_km_m(c)
            if other_road == center_road and other_km is not None:
                if abs(other_km - center_km) * 1000.0 > SETTLEMENT_INTERSECTION_RADIUS_M:
                    continue
            group_indices.append(j)
            group_cards.append(c)

        type_counter = Counter(_get_dtp_type(c) for c in group_cards)
        if _check_cluster_criteria(type_counter, len(group_cards))[0]:
            assigned.update(group_indices)
            group_cards.sort(key=lambda c: _get_date(c))
            clusters.append(_build_cluster(group_cards, center, "settlement_intersection"))

    # Шаг 1b: без пикетажа
    for idx, card in indexed_with_coords:
        if idx in assigned or not _is_intersection(card) or _has_road_and_piketazh(card):
            continue
        center = _parse_coords(card)
        if center is None:
            continue
        group_indices = [idx]
        group_cards = [card]
        for j, c in indexed_with_coords:
            if j in assigned or j == idx or not _is_intersection(c):
                continue
            coords = _parse_coords(c)
            if coords is None:
                continue
            if haversine_meters(center[0], center[1], coords[0], coords[1]) <= SETTLEMENT_INTERSECTION_RADIUS_M:
                group_indices.append(j)
                group_cards.append(c)
        type_counter = Counter(_get_dtp_type(c) for c in group_cards)
        if _check_cluster_criteria(type_counter, len(group_cards))[0]:
            assigned.update(group_indices)
            group_cards.sort(key=lambda c: _get_date(c))
            clusters.append(_build_cluster(group_cards, center, "settlement_intersection"))

    # --- 2-й проход: дороги с пикетажем, окно 200 м ---
    road_cards_with_km = [(idx, card) for idx, card in indexed_with_coords
                          if idx not in assigned and _has_road_and_piketazh(card)]
    road_groups: dict[str, list[tuple[int, dict]]] = {}
    for idx, card in road_cards_with_km:
        road_groups.setdefault(_get_road_name(card), []).append((idx, card))

    for road_name, items in road_groups.items():
        items_pos = [(idx, card, pos) for idx, card in items if (pos := _get_km_m(card)) is not None]
        if not items_pos:
            continue
        items_pos.sort(key=lambda x: x[2])
        for i, (idx, card, pos) in enumerate(items_pos):
            if idx in assigned:
                continue
            window_end = pos + SETTLEMENT_ROAD_WINDOW_KM
            group_indices = [idx]
            group_cards = [card]
            for j in range(i + 1, len(items_pos)):
                other_idx, other_card, other_pos = items_pos[j]
                if other_idx not in assigned and other_pos <= window_end:
                    group_indices.append(other_idx)
                    group_cards.append(other_card)
            type_counter = Counter(_get_dtp_type(c) for c in group_cards)
            if _check_cluster_criteria(type_counter, len(group_cards))[0]:
                assigned.update(group_indices)
                group_cards.sort(key=lambda c: _get_date(c))
                center = _parse_coords(card)
                clusters.append(_build_cluster(group_cards, center, "settlement_road",
                                               road_name=road_name, start_pos=pos, end_pos=window_end))

    # --- 3-й проход: радиус 100 м ---
    for idx, card in indexed_with_coords:
        if idx in assigned:
            continue
        center = _parse_coords(card)
        if center is None:
            assigned.add(idx)
            continue
        center_road = _get_road_name(card)
        center_km = _get_km_m(card)
        group_indices = [idx]
        group_cards = [card]
        for j, c in indexed_with_coords:
            if j in assigned or j == idx:
                continue
            coords = _parse_coords(c)
            if coords is None:
                continue
            dist = haversine_meters(center[0], center[1], coords[0], coords[1])
            if dist > SETTLEMENT_OTHER_RADIUS_M:
                continue
            # Если на той же дороге с пикетажем — проверяем окно 200 м
            other_road = _get_road_name(c)
            other_km = _get_km_m(c)
            if (other_road and other_road == center_road
                    and other_km is not None and center_km is not None):
                if abs(other_km - center_km) > SETTLEMENT_ROAD_WINDOW_KM:
                    continue
            group_indices.append(j)
            group_cards.append(c)
        type_counter = Counter(_get_dtp_type(c) for c in group_cards)
        if _check_cluster_criteria(type_counter, len(group_cards))[0]:
            assigned.update(group_indices)
            group_cards.sort(key=lambda c: _get_date(c))
            clusters.append(_build_cluster(group_cards, center, "settlement_generic"))

    logger.info(f"НП: найдено {len(clusters)} очагов")
    return clusters


# ========================
# Алгоритм: Вне НП (автодороги)
# ========================

def find_non_settlement_concentration_points(cards: list[dict]) -> list[dict]:
    """
    Поиск очагов вне населённых пунктов (автодороги).

    Алгоритм: группировка по названию дороги, скользящее окно 1 км.
    Порог: 3+ ДТП одного вида ИЛИ 5+ ДТП любых видов.
    """
    if not cards:
        return []

    indexed = [(i, c) for i, c in enumerate(cards)]
    indexed.sort(key=lambda x: _get_date(x[1]))
    indexed_with_coords = [(i, c) for i, c in indexed if _parse_coords(c)]

    # Группируем по названию дороги
    road_groups: dict[str, list[tuple[int, dict, float | None]]] = {}
    for idx, card in indexed_with_coords:
        road = _get_road_name(card)
        if road:
            pos = _get_km_m(card)
            road_groups.setdefault(road, []).append((idx, card, pos))

    assigned: set[int] = set()
    clusters: list[dict] = []

    for road_name, items in road_groups.items():
        # Карточки с пикетажем
        items_with_km = [(idx, card, pos) for idx, card, pos in items if pos is not None]
        # Без пикетажа
        items_without_km = [(idx, card) for idx, card, pos in items if pos is None]

        # Обработка с пикетажем: скользящее окно 1 км
        if items_with_km:
            items_with_km.sort(key=lambda x: x[2])
            for i, (idx, card, pos) in enumerate(items_with_km):
                if idx in assigned:
                    continue
                window_end = pos + NON_SETTLEMENT_WINDOW_KM
                group_indices = [idx]
                group_cards = [card]
                for j in range(i + 1, len(items_with_km)):
                    other_idx, other_card, other_pos = items_with_km[j]
                    if other_idx not in assigned and other_pos <= window_end:
                        group_indices.append(other_idx)
                        group_cards.append(other_card)
                type_counter = Counter(_get_dtp_type(c) for c in group_cards)
                if _check_cluster_criteria(type_counter, len(group_cards))[0]:
                    assigned.update(group_indices)
                    group_cards.sort(key=lambda c: _get_date(c))
                    center = _parse_coords(card)
                    clusters.append(_build_cluster(group_cards, center, "non_settlement_road",
                                                   road_name=road_name, start_pos=pos, end_pos=window_end))

        # Обработка без пикетажа: радиус 200 м
        if items_without_km:
            for idx, card in items_without_km:
                if idx in assigned:
                    continue
                center = _parse_coords(card)
                if center is None:
                    continue
                group_indices = [idx]
                group_cards = [card]
                for j, c in items_without_km:
                    if j in assigned or j == idx:
                        continue
                    coords = _parse_coords(c)
                    if coords is None:
                        continue
                    dist = haversine_meters(center[0], center[1], coords[0], coords[1])
                    if dist <= NON_SETTLEMENT_NO_PK_WINDOW_KM * 1000:
                        group_indices.append(j)
                        group_cards.append(c)
                type_counter = Counter(_get_dtp_type(c) for c in group_cards)
                if _check_cluster_criteria(type_counter, len(group_cards))[0]:
                    assigned.update(group_indices)
                    group_cards.sort(key=lambda c: _get_date(c))
                    clusters.append(_build_cluster(group_cards, center, "non_settlement_generic",
                                                   road_name=road_name))

    logger.info(f"Вне НП: найдено {len(clusters)} очагов")
    return clusters


# ========================
# Объединённый расчёт
# ========================

async def calculate_concentration_points(
    cards: list[dict],
    progress_callback: Callable[[str], Awaitable[None]] | None = None,
) -> list[dict]:
    """
    Полный расчёт очагов: классификация НП/не НП + оба алгоритма.
    """
    # Фильтруем ДТП вне дороги
    filtered = [c for c in cards if not _is_off_road(c)]
    if not filtered:
        logger.info("Нет карточек после фильтрации (все вне дороги)")
        return []

    logger.info(f"Карточек для расчёта очагов: {len(filtered)} (из {len(cards)} всего)")

    # Получаем границы НП
    polygons = await fetch_settlement_boundaries(filtered, progress_callback)

    # Классифицируем
    settlement_cards, non_settlement_cards = classify_cards(filtered, polygons)

    # Запускаем оба алгоритма
    settlement_clusters = find_settlement_concentration_points(settlement_cards)
    non_settlement_clusters = find_non_settlement_concentration_points(non_settlement_cards)

    all_clusters = settlement_clusters + non_settlement_clusters
    logger.info(f"Всего очагов найдено: {len(all_clusters)}")
    return all_clusters


# ========================
# Excel-данные
# ========================

def build_concentration_excel_data(points: list[dict]) -> list[dict[str, Any]]:
    """Строит данные для Excel-файла очагов."""
    rows = []
    for i, pt in enumerate(points, 1):
        center = pt.get("center", (0, 0))
        zone_type_map = {
            "settlement_intersection": "НП — перекрёсток",
            "settlement_road": "НП — участок дороги",
            "settlement_generic": "НП — зона 100 м",
            "non_settlement_road": "Вне НП — автодорога",
            "non_settlement_generic": "Вне НП — зона 200 м",
        }
        dominant = pt.get("dominant_type", "")
        type_counter = pt.get("type_counter", {})
        type_str = "; ".join(f"{t}: {c}" for t, c in sorted(type_counter.items(), key=lambda x: -x[1]))

        start_pos = pt.get("start_pos")
        end_pos = pt.get("end_pos")
        if start_pos is not None and end_pos is not None:
            location = f"{pt['road']} (км {start_pos:.2f} — {end_pos:.2f})"
        else:
            location = pt.get("road", "")

        rows.append({
            "№": i,
            "Тип зоны": zone_type_map.get(pt.get("zone_type", ""), pt.get("zone_type", "")),
            "Дорога/Улица": location,
            "Широта": round(center[0], 6) if center else "",
            "Долгота": round(center[1], 6) if center else "",
            "ДТП в очаге": pt.get("total_accidents", 0),
            "Погибло": pt.get("deaths", 0),
            "Ранено": pt.get("injured", 0),
            "Доминирующий вид ДТП": dominant if dominant else "Разнородные",
            "Виды ДТП": type_str,
            "Даты": "; ".join(pt.get("dates", [])[:5]),
        })
    return rows


def build_concentration_detail_data(points: list[dict]) -> list[dict[str, Any]]:
    """Строит подробные данные по каждому ДТП в очагах."""
    rows = []
    row_num = 1
    for pt in points:
        for card in pt.get("cards", []):
            rows.append({
                "№": row_num,
                "Дорога": pt.get("road", ""),
                "Дата": _get_date(card),
                "Вид ДТП": _get_dtp_type(card),
                "Погибло": _safe_int(card.get("pog")),
                "Ранено": _safe_int(card.get("ran")),
                "Широта": str(card.get("coord_w", "")),
                "Долгота": str(card.get("coord_l", "")),
                "Улица": str(card.get("street", "")),
                "НП": str(card.get("np", "")),
                "Км": str(card.get("km", "")),
            })
            row_num += 1
    return rows


def get_concentration_column_names() -> list[str]:
    """Названия колонок для сводного файла очагов."""
    return ["№", "Тип зоны", "Дорога/Улица", "Широта", "Долгота",
            "ДТП в очаге", "Погибло", "Ранено", "Доминирующий вид ДТП",
            "Виды ДТП", "Даты"]


def get_detail_column_names() -> list[str]:
    """Названия колонок для подробного файла очагов."""
    return ["№", "Дорога", "Дата", "Вид ДТП", "Погибло", "Ранено",
            "Широта", "Долгота", "Улица", "НП", "Км"]
