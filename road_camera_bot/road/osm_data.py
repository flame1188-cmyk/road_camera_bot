"""
Модуль получения данных об участке дороги из OpenStreetMap.

Через Overpass API извлекает тип дороги, освещение, переходы, школы и т.д.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

OVERPASS_HEADERS = {"User-Agent": "RoadAssessmentBot/1.0", "Accept": "application/json"}

RADIUS_NEARBY = 100
RADIUS_AMENITIES = 300
RADIUS_STREETLAMPS = 200
RADIUS_BUS_STOPS = 300


def _meters_to_degrees(meters: float, lat: float = 55.0) -> float:
    m_per_deg = 111320.0 * (1 - 0.000006 * lat * lat)
    return meters / m_per_deg


def _build_road_query(lat: float, lon: float) -> str:
    return f'[out:json][timeout:30];\n(way["highway"](around:{RADIUS_NEARBY},{lat},{lon}););\nout body;\n>; out skel qt;\n'


def _build_streetlamps_query(lat: float, lon: float) -> str:
    return f'[out:json][timeout:30];\n(node["highway"="street_lamp"](around:{RADIUS_STREETLAMPS},{lat},{lon}););\nout body;\n'


def _build_crossings_query(lat: float, lon: float) -> str:
    return (
        f'[out:json][timeout:30];\n('
        f'node["highway"="crossing"](around:{RADIUS_NEARBY},{lat},{lon});\n'
        f'way["highway"="crossing"](around:{RADIUS_NEARBY},{lat},{lon});\n'
        f'node["footway"="crossing"](around:{RADIUS_NEARBY},{lat},{lon});\n'
        f');\nout body;\n>; out skel qt;\n'
    )


def _build_traffic_signals_query(lat: float, lon: float) -> str:
    return f'[out:json][timeout:30];\n(node["highway"="traffic_signals"](around:{RADIUS_NEARBY},{lat},{lon}););\nout body;\n'


def _build_bus_stops_query(lat: float, lon: float) -> str:
    return (
        f'[out:json][timeout:30];\n('
        f'node["highway"="bus_stop"](around:{RADIUS_BUS_STOPS},{lat},{lon});\n'
        f'node["public_transport"="stop_position"](around:{RADIUS_BUS_STOPS},{lat},{lon});\n'
        f');\nout body;\n'
    )


def _build_amenities_query(lat: float, lon: float) -> str:
    return (
        f'[out:json][timeout:30];\n('
        f'node["amenity"="school"](around:{RADIUS_AMENITIES},{lat},{lon});\n'
        f'node["amenity"="kindergarten"](around:{RADIUS_AMENITIES},{lat},{lon});\n'
        f');\nout body;\n'
    )


def _build_maxspeed_nearby_query(lat: float, lon: float) -> str:
    return f'[out:json][timeout:30];\n(way["highway"]["maxspeed"](around:{RADIUS_NEARBY},{lat},{lon}););\nout body;\n>; out skel qt;\n'


async def _overpass_request(query: str) -> dict | None:
    for url in OVERPASS_URLS:
        try:
            async with httpx.AsyncClient(verify=False, headers=OVERPASS_HEADERS, timeout=60) as client:
                resp = await client.post(url, data={"data": query})
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.warning(f"Overpass API ({url}): {e}")
            continue
    return None


def _parse_road_info(data: dict) -> dict[str, Any]:
    result = {
        "road_type": None, "road_name": None, "lanes": None, "surface": None,
        "lit": None, "oneway": None, "sidewalk": None, "maxspeed": None,
        "has_median": False, "median_type": None,
    }
    road_type_map = {
        "motorway": "Автомагистраль", "trunk": "Автодорога",
        "primary": "Городская магистральная", "secondary": "Городская районная",
        "tertiary": "Городская местная", "residential": "Жилая улица",
        "unclassified": "Не классифицирована", "service": "Служебная дорога",
        "living_street": "Жилая зона",
    }
    for elem in data.get("elements", []):
        if elem.get("type") != "way":
            continue
        tags = elem.get("tags", {})
        if not tags.get("highway"):
            continue
        hw = tags.get("highway", "")
        name = tags.get("name", "")
        if name:
            result["road_name"] = name
        result["road_type"] = road_type_map.get(hw, hw)
        if "lanes" in tags:
            try:
                result["lanes"] = int(tags["lanes"])
            except ValueError:
                pass
        result["surface"] = tags.get("surface")
        result["lit"] = tags.get("lit")
        result["oneway"] = tags.get("oneway")
        result["sidewalk"] = tags.get("sidewalk")
        result["maxspeed"] = tags.get("maxspeed")
        if "median" in tags:
            result["has_median"] = True
            result["median_type"] = tags.get("median")
        break
    return result


def _count_streetlamps(data: dict) -> int:
    return sum(1 for e in data.get("elements", [])
               if e.get("type") == "node" and e.get("tags", {}).get("highway") == "street_lamp")


def _parse_crossings(data: dict) -> list[dict[str, Any]]:
    crossings = []
    crossing_map = {
        "unmarked": "нерегулируемый (без разметки)", "marked": "нерегулируемый (зебра)",
        "traffic_signals": "регулируемый (со светофором)", "uncontrolled": "нерегулируемый",
        "zebra": "зебра",
    }
    for elem in data.get("elements", []):
        if elem.get("type") not in ("node", "way"):
            continue
        tags = elem.get("tags", {})
        ct = tags.get("crossing", "")
        crossings.append({
            "type": crossing_map.get(ct, ct or "не указан"),
            "lat": elem.get("lat") or elem.get("center", {}).get("lat"),
            "lon": elem.get("lon") or elem.get("center", {}).get("lon"),
        })
    return crossings


def _count_traffic_signals(data: dict) -> int:
    return sum(1 for e in data.get("elements", [])
               if e.get("type") == "node" and e.get("tags", {}).get("highway") == "traffic_signals")


def _parse_bus_stops(data: dict) -> list[dict[str, Any]]:
    stops = []
    seen = set()
    for elem in data.get("elements", []):
        if elem.get("type") != "node":
            continue
        tags = elem.get("tags", {})
        name = tags.get("name", "Без названия")
        if name in seen:
            continue
        seen.add(name)
        stops.append({"name": name, "type": "автобусная", "lat": elem.get("lat"), "lon": elem.get("lon")})
    return stops


def _parse_amenities(data: dict) -> list[dict[str, Any]]:
    amenities = []
    for elem in data.get("elements", []):
        tags = elem.get("tags", {})
        a = tags.get("amenity", "")
        if a not in ("school", "kindergarten"):
            continue
        amenities.append({
            "type": "Школа" if a == "school" else "Детский сад",
            "name": tags.get("name", ""),
            "lat": elem.get("lat"), "lon": elem.get("lon"),
        })
    return amenities


def _parse_maxspeed_nearby(data: dict) -> list[str]:
    speeds = []
    for elem in data.get("elements", []):
        if elem.get("type") != "way":
            continue
        tags = elem.get("tags", {})
        speed = tags.get("maxspeed", "")
        name = tags.get("name", "Без названия")
        if speed:
            speeds.append(f"{name}: {speed} км/ч")
    return speeds


async def get_road_data(lat: float, lon: float) -> dict[str, Any]:
    """Получает комплексные данные об участке дороги из OSM."""
    logger.info(f"OSM: запрос данных для {lat}, {lon}")

    queries = {
        "road": _build_road_query(lat, lon),
        "streetlamps": _build_streetlamps_query(lat, lon),
        "crossings": _build_crossings_query(lat, lon),
        "traffic_signals": _build_traffic_signals_query(lat, lon),
        "bus_stops": _build_bus_stops_query(lat, lon),
        "amenities": _build_amenities_query(lat, lon),
        "maxspeed": _build_maxspeed_nearby_query(lat, lon),
    }
    results = {}
    for key, query in queries.items():
        try:
            results[key] = await _overpass_request(query)
        except Exception as e:
            logger.error(f"OSM: ошибка запроса {key}: {e}")
            results[key] = None

    road_info = _parse_road_info(results.get("road") or {})
    streetlamp_count = _count_streetlamps(results.get("streetlamps") or {})
    crossings = _parse_crossings(results.get("crossings") or {})
    traffic_signal_count = _count_traffic_signals(results.get("traffic_signals") or {})
    bus_stops = _parse_bus_stops(results.get("bus_stops") or {})
    amenities = _parse_amenities(results.get("amenities") or {})
    maxspeeds = _parse_maxspeed_nearby(results.get("maxspeed") or {})

    road_category = "городская"
    if road_info.get("road_type") in ("Автомагистраль", "Автодорога"):
        road_category = "загородная/трасса"
    elif road_info.get("road_type") in ("Жилая улица", "Жилая зона"):
        road_category = "жилая зона"

    schools = [a for a in amenities if a["type"] == "Школа"]
    kindergartens = [a for a in amenities if a["type"] == "Детский сад"]

    return {
        "road_info": road_info, "road_category": road_category,
        "streetlamp_count": streetlamp_count, "crossings": crossings,
        "crossing_count": len(crossings), "traffic_signal_count": traffic_signal_count,
        "bus_stops": bus_stops, "bus_stop_count": len(bus_stops),
        "schools": schools, "school_count": len(schools),
        "kindergartens": kindergartens, "kindergarten_count": len(kindergartens),
        "maxspeeds": maxspeeds, "raw_data": results,
    }


def format_osm_summary(osm_data: dict[str, Any]) -> str:
    """Форматирует данные OSM в текстовое резюме."""
    lines = []
    road = osm_data.get("road_info", {})
    lines.append(f"Тип дороги: {road.get('road_type', 'не определён')}")
    lines.append(f"Категория: {osm_data.get('road_category', 'не определена')}")
    if road.get("road_name"):
        lines.append(f"Название: {road['road_name']}")
    if road.get("lanes"):
        lines.append(f"Количество полос: {road['lanes']}")
    surface_map = {"asphalt": "асфальт", "concrete": "бетон", "paving_stones": "брусчатка"}
    if road.get("surface"):
        lines.append(f"Покрытие: {surface_map.get(road['surface'], road['surface'])}")
    lit_map = {"yes": "есть", "no": "нет", "limited": "ограниченное"}
    if road.get("lit"):
        lines.append(f"Освещение (OSM): {lit_map.get(road['lit'], road['lit'])}")
    lines.append(f"Опоры освещения (200м): {osm_data.get('streetlamp_count', 0)} шт.")
    if road.get("maxspeed"):
        lines.append(f"Ограничение скорости: {road['maxspeed']} км/ч")
    lines.append(f"Пешеходных переходов (100м): {osm_data.get('crossing_count', 0)}")
    lines.append(f"Светофоров (100м): {osm_data.get('traffic_signal_count', 0)}")
    if osm_data.get("bus_stop_count", 0) > 0:
        lines.append(f"Остановок транспорта (300м): {osm_data['bus_stop_count']}")
    if osm_data.get("school_count", 0) > 0:
        lines.append(f"Школ (300м): {osm_data['school_count']}")
    if osm_data.get("kindergarten_count", 0) > 0:
        lines.append(f"Детских садов (300м): {osm_data['kindergarten_count']}")
    return "\n".join(lines)
