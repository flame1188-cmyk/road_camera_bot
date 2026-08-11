"""
Очаги концентрации ДТП: расчёт, карта, Excel.

- start_clusters_calculation() — длительная операция (15-30 сек):
  OSM Overpass + классификация + кластеризация + динамика vs прошлый год
- generate_clusters_map_html() — полноценная Leaflet-карта через ReportGenerator
- _build_clusters_map_html() — простая fallback-карта (без слоёв)
- _serialize_cluster() / _color_for_severity() — хелперы
- generate_clusters_excel() — 4-листный Excel (очаги/динамика/детализация/предочаги)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import _imports
from .models import AnalysisStatus, Task
from .pipeline import ensure_cards, ensure_prev_cards

logger = logging.getLogger(__name__)


async def start_clusters_calculation(task: Task) -> None:
    """
    Асинхронный расчёт очагов концентрации ДТП.

    Длительная операция (15-30 сек): OSM Overpass + классификация +
    кластеризация + динамика vs прошлый год.

    Результат сохраняется в task.clusters_state.result.
    """
    state = task.clusters_state
    state.status = AnalysisStatus.RUNNING
    state.progress = 5
    state.stage = "Подготовка данных..."
    state.started_at = datetime.now(timezone.utc)
    state.error = None
    state.result = None

    try:
        # Sprint 3.1: восстанавливаем task.cards из cards_cache, если
        # задача была выгружена из in-memory LRU или после рестарта.
        # Раньше тут была жёсткая проверка `if not task.cards: raise`
        # — она стреляла для старых задач.
        cards_result = await ensure_cards(task)
        if not cards_result.get("ok"):
            raise RuntimeError(
                cards_result.get(
                    "error",
                    "Карточки текущего периода не загружены",
                )
            )

        # === Этап 4: проверяем кэш очагов в PostgreSQL ===
        # Если для данного (reg_code, current_dat, prev_dat) уже есть
        # свежий result — берём его без пересчёта (15-30 сек → <100 мс).
        # prev_dat_list вычисляем заранее, чтобы ключ кэша совпал с тем,
        # что будет использоваться при PUT в конце функции.
        try:
            prev_dat_list_for_cache: List[str] = []
            for dat in task.dat_list:
                try:
                    m, y = dat.split(".")
                    prev_dat_list_for_cache.append(f"{m}.{int(y) - 1}")
                except Exception:
                    continue

            from ..db.clusters_cache import get_cached_clusters
            cached = await get_cached_clusters(
                reg_code=task.region_code,
                current_dat_list=task.dat_list,
                prev_dat_list=prev_dat_list_for_cache if prev_dat_list_for_cache else None,
            )
            if cached is not None:
                # Кэш хит. Если в кэше есть raw_clusters / raw_preclusters —
                # восстанавливаем их и выходим (15-30 сек → <100 мс).
                # Если они None (старая запись, созданная до Stage 4 fix,
                # когда raw_clusters не сохранялся) — игнорируем кэш и идём
                # штатным путём, иначе карта упадёт в simple map, а Excel
                # вернёт None (Sprint 3.2 fix).
                cached_result = cached["result"]
                cached_raw_clusters = cached.get("raw_clusters")
                cached_raw_preclusters = cached.get("raw_preclusters")

                has_raw = bool(cached_raw_clusters or cached_raw_preclusters)

                if not has_raw:
                    # Старая запись без raw-данных. Протухнет сама по TTL,
                    # а пока — игнорируем и пересчитываем. После пересчёта
                    # put_cached_clusters сохранит уже полную запись (с raw).
                    logger.info(
                        f"Task {task.id}: clusters cache HIT, но raw_clusters/"
                        f"raw_preclusters=None (старая запись) — "
                        f"игнорируем кэш, пересчитываем"
                    )
                    # Важно: не выходим из функции, идём к штатному расчёту.
                else:
                    task.clusters_state.result = cached_result
                    task.clusters_state.status = AnalysisStatus.DONE
                    task.clusters_state.progress = 100
                    task.clusters_state.stage = "Готово (из кэша)"
                    task.clusters_state.started_at = datetime.now(timezone.utc)
                    task.clusters_state.finished_at = datetime.now(timezone.utc)

                    # Восстанавливаем raw-данные для карты/Excel.
                    if cached_raw_clusters is not None:
                        task.raw_clusters = cached_raw_clusters
                    if cached_raw_preclusters is not None:
                        task.raw_preclusters = cached_raw_preclusters

                    logger.info(
                        f"Task {task.id}: clusters loaded from cache — "
                        f"{cached_result.get('total_clusters', 0)} очагов, "
                        f"{cached_result.get('total_preclusters', 0)} предочагов, "
                        f"raw=yes"
                    )
                    return
        except Exception as exc:
            logger.debug(
                f"Task {task.id}: clusters cache lookup failed: {exc}"
            )
            # Не роняем расчёт — просто идём штатным путём.

        conc_module = _imports._import_module("concentration_points")

        # Загружаем прошлый год (если ещё нет)
        state.progress = 10
        state.stage = "Загрузка данных за прошлый год..."
        if not task.prev_cards_loaded:
            await ensure_prev_cards(task)
        prev_cards = task.prev_cards or []

        async def progress_cb(text: str) -> None:
            state.stage = text
            state.progress = min(85, state.progress + 5)

        state.progress = 20
        state.stage = "Загрузка границ НП из OpenStreetMap..."

        clusters, _saved_polys, preclusters_raw = await conc_module.calculate_concentration_dynamics(
            current_cards=task.cards,
            prev_cards=prev_cards,
            progress_callback=progress_cb,
            reg_code=task.region_code,
        )

        state.progress = 90
        state.stage = "Обогащение камерами..."

        # Обогащение камерами (если есть в кэше)
        try:
            camera_cache_module = _imports._import_module("camera_cache")
            if camera_cache_module.has_cached_cameras(task.region_code):
                cameras = camera_cache_module.load_cameras_from_cache(
                    task.region_code
                )
                if cameras:
                    current_only = [
                        c for c in clusters if not c.get("_is_lost", False)
                    ]
                    conc_module.enrich_clusters_with_cameras(
                        current_only, cameras,
                    )
                    lost = [
                        c for c in clusters if c.get("_is_lost", False)
                    ]
                    if lost:
                        conc_module.enrich_clusters_with_cameras(lost, cameras)
                    # Предочаги тоже обогащаем камерами — раньше это делалось
                    # только в Telegram-боте (bot.py:2574), а в MiniApp
                    # пропускалось. Без этого пользователь MiniApp видел
                    # предочаги без статуса «закрыт/открыт камерой».
                    if preclusters_raw:
                        conc_module.enrich_clusters_with_cameras(
                            preclusters_raw, cameras,
                        )
                        logger.info(
                            f"Task {task.id}: cameras attached to "
                            f"{len(preclusters_raw)} preclusters"
                        )
        except Exception as exc:
            logger.warning(
                f"Task {task.id}: camera enrichment failed: {exc}"
            )

        state.progress = 95
        state.stage = "Формирование результата..."

        # Сериализуем очаги для JSON-ответа
        clusters_data = [_serialize_cluster(c) for c in clusters]

        # Статистика
        # current_only — текущие очаги (без lost и без prev_matched,
        # т.к. prev_matched — это очаг АППГ, повторённый в текущем;
        # он показывается отдельной строкой, но не входит в «текущие»).
        current_only = [
            c for c in clusters
            if not c.get("_is_lost", False) and not c.get("_is_prev_matched", False)
        ]
        lost_clusters = [c for c in clusters if c.get("_is_lost", False)]
        prev_matched_clusters = [c for c in clusters if c.get("_is_prev_matched", False)]

        # Динамика — агрегат по всем статусам.
        # Новые ключи (методология пикетаж + сосед):
        #   repeated_growing, repeated_shrinking, repeated_stable, repeated_merged,
        #   new, new_with_neighbor, prev_matched, lost
        # prev_matched — это очаг прошлого года, который повторился в текущем
        # (отдельная строка в Excel/карте со ссылкой на текущий №).
        # Старые ключи (growing/shrinking/stable) оставлены для обратной совместимости
        # с сохранёнными задачами и старым фронтендом.
        dynamics_summary = {
            "repeated_growing": 0,
            "repeated_shrinking": 0,
            "repeated_stable": 0,
            "repeated_merged": 0,
            "new": 0,
            "new_with_neighbor": 0,
            "prev_matched": 0,
            "lost": 0,
        }
        for c in clusters:
            d = c.get("dynamics") or {}
            status = d.get("status", "new")
            if status in dynamics_summary:
                dynamics_summary[status] += 1
            else:
                # Неизвестный статус — добавим динамически,
                # чтобы не потерять данные при будущих изменениях.
                dynamics_summary[status] = 1

        # Предочаги — приходят отдельно от calculate_concentration_dynamics,
        # чтобы они не терялись, когда очагов нет (малые регионы).
        preclusters_raw = preclusters_raw or []
        # Backward-compat: предочаги всё ещё прикреплены к clusters[0]["_preclusters"]
        # когда clusters непустой (см. concentration_points.py).
        preclusters = [_serialize_cluster(p) for p in preclusters_raw]

        result = {
            "total_clusters": len(current_only),
            "total_lost": len(lost_clusters),
            "total_prev_matched": len(prev_matched_clusters),
            "total_preclusters": len(preclusters),
            "current_total_dtp": sum(
                c.get("total_accidents", 0) for c in current_only
            ),
            "current_deaths": sum(
                c.get("deaths", 0) for c in current_only
            ),
            "current_injured": sum(
                c.get("injured", 0) for c in current_only
            ),
            "dynamics": dynamics_summary,
            "clusters": clusters_data,
            "preclusters": preclusters,
            "has_prev_data": bool(prev_cards),
            "prev_label": task.prev_label if prev_cards else None,
            "current_label": task.period_label,
            "region_name": task.region_name,
            # Карта очагов будет сгенерирована отдельно по запросу
        }

        state.result = result
        # Сохраняем raw очаги (с cards) для Excel-выгрузки и продвинутой карты
        task.raw_clusters = clusters
        # Сохраняем raw предочаги отдельно — на случай, когда очагов нет,
        # но предочаги есть (нужно для Excel и карты)
        task.raw_preclusters = preclusters_raw
        state.status = AnalysisStatus.DONE
        state.progress = 100
        state.stage = "Готово"
        state.finished_at = datetime.now(timezone.utc)

        # === Этап 4: сохраняем result + raw_clusters в кэш очагов ===
        # Ключ: (reg_code, current_dat_hash, prev_dat_hash).
        # prev_dat_list вычислен в начале функции (prev_dat_list_for_cache).
        # Если совпадёт с повторным запросом — следующий пользователь
        # получит результат мгновенно (cache hit).
        #
        # Важно: сохраняем не только result, но и raw_clusters +
        # raw_preclusters. Иначе при cache hit карта упадёт в
        # fallback (simple map без слоёв), а Excel вернёт None —
        # оба метода итерируют cluster["cards"], которых в result нет.
        try:
            from ..db.clusters_cache import put_cached_clusters
            await put_cached_clusters(
                reg_code=task.region_code,
                current_dat_list=task.dat_list,
                prev_dat_list=prev_dat_list_for_cache if prev_dat_list_for_cache else None,
                result=result,
                raw_clusters=clusters,
                raw_preclusters=preclusters_raw,
            )
        except Exception as exc:
            logger.debug(
                f"Task {task.id}: clusters cache put failed: {exc}"
            )

        # Персистим clusters_result в БД (чтобы пережил рестарт)
        try:
            from ..db.repository import save_task
            await save_task(task)
        except Exception as exc:
            logger.debug(f"Task {task.id}: clusters persist failed: {exc}")

        logger.info(
            f"Task {task.id}: clusters done — "
            f"{len(current_only)} очагов, "
            f"{len(prev_matched_clusters)} АППГ-повторённых, "
            f"{len(preclusters)} предочагов, "
            f"{len(lost_clusters)} исчезнувших"
        )

    except Exception as exc:
        logger.exception(f"Task {task.id}: clusters calculation failed")
        state.status = AnalysisStatus.FAILED
        state.error = str(exc)
        state.stage = "Ошибка"
        state.finished_at = datetime.now(timezone.utc)
        # Персистим failed-статус
        try:
            from ..db.repository import save_task
            await save_task(task)
        except Exception:
            pass


def _serialize_cluster(c: dict) -> dict:
    """Сериализует очаг в JSON-совместимый dict."""
    center = c.get("center")
    return {
        "road": c.get("road", ""),
        "zone_type": c.get("zone_type", ""),
        "total_accidents": c.get("total_accidents", 0),
        "deaths": c.get("deaths", 0),
        "injured": c.get("injured", 0),
        # None (смешанный тип, 5+ ДТП разных видов) -> пустая строка для UI
        "dominant_type": c.get("dominant_type") or "",
        "type_counter": dict(c.get("type_counter", {})),
        "center": {"lat": center[0], "lon": center[1]} if center else None,
        "start_pos": c.get("start_pos"),
        "end_pos": c.get("end_pos"),
        "dates": c.get("dates", []),
        # dynamics теперь содержит расширенные поля:
        # - status: repeated_*/new/new_with_neighbor/prev_matched/lost
        # - matched_prev_numbers: [int, ...] — для ссылки «Да, №N»
        # - matched_curr_numbers: [int, ...] — для prev_matched,
        #   на какие текущие № ссылается этот АППГ-очаг
        # - neighbors: [{prev_number, distance_m}, ...] — для new_with_neighbor
        # - prev_total, prev_deaths, prev_injured — суммы по сматченным
        "dynamics": c.get("dynamics", {}),
        "camera_match": c.get("camera_match"),
        # Флаги для фильтрации на фронтенде/карте
        "is_lost": c.get("_is_lost", False),
        "is_prev_matched": c.get("_is_prev_matched", False),
    }


async def generate_clusters_map_html(task: Task) -> Optional[str]:
    """
    Генерирует HTML-карту очагов через ReportGenerator.generate_cluster_map().

    Полноценная карта из Telegram-бота:
      - Слои (Очаги / ДТП в очагах / Предочаги / Камеры)
      - Popups на каждом ДТП и очаге
      - Линейка для измерения расстояний
      - Фильтр камер по моделям
      - Convex hull (зона очага)
      - Динамика (новые/рост/снижение/стабильный/исчезнувший)
      - Камеры с кластеризацией
    """
    if not task.clusters_state.result:
        return None

    try:
        report_gen_module = _imports._import_module("report_generator")
        camera_cache_module = _imports._import_module("camera_cache")

        # Raw очаги с cards внутри (сохранены в start_clusters_calculation)
        raw_clusters = task.raw_clusters or []
        # Raw предочаги (сохранены отдельно — могут быть, даже если очагов нет)
        raw_preclusters = task.raw_preclusters or []
        if not raw_clusters and not raw_preclusters:
            # Нет ни очагов, ни предочагов — простая карта покажет заглушку
            logger.warning(
                f"Task {task.id}: raw_clusters and raw_preclusters empty, "
                f"fallback to simple map"
            )
            return _build_clusters_map_html(task)

        # Если есть хотя бы что-то одно (очаги или предочаги) —
        # используем продвинутую карту через ReportGenerator.
        # Раньше при пустом raw_clusters всегда включался fallback на простую
        # карту — это приводило к тому, что для малых регионов (например,
        # Севастополь с 0 очагов и 8 предочагами) показывалась простая карта
        # без слоёв, попапов, линейки и т.д. ReportGenerator.generate_cluster_map
        # умеет работать с пустым списком clusters и непустыми preclusters.

        # Разделяем: текущие очаги + АППГ-повторённые + исчезнувшие (отдельно)
        # prev_matched — это очаги прошлого периода, которые повторились
        # в текущем. Они отображаются на карте отдельным слоем (светло-голубые
        # маркеры с пунктирной границей), чтобы пользователь видел, какие
        # именно АППГ-очаги «превратились» в текущие.
        current_only = [
            c for c in raw_clusters
            if not c.get("_is_lost", False) and not c.get("_is_prev_matched", False)
        ]
        lost_clusters = [c for c in raw_clusters if c.get("_is_lost", False)]
        prev_matched_clusters = [c for c in raw_clusters if c.get("_is_prev_matched", False)]

        # Предочаги — из отдельного поля task.raw_preclusters
        # (раньше брались из raw_clusters[0]["_preclusters"], что ломалось
        # при пустом списке очагов)
        preclusters = raw_preclusters

        # Камеры (если есть в кэше)
        cameras = []
        try:
            if camera_cache_module.has_cached_cameras(task.region_code):
                cameras = camera_cache_module.load_cameras_from_cache(
                    task.region_code
                ) or []
        except Exception as exc:
            logger.warning(f"Task {task.id}: camera load for map failed: {exc}")

        # Добавляем исчезнувшие и АППГ-повторённые очаги в основной список
        # с пометкой dynamics.status ('lost' или 'prev_matched'),
        # чтобы ReportGenerator отрисовал их как отдельные слои через dynamics.
        all_clusters_for_map = (
            list(current_only)
            + list(prev_matched_clusters)
            + list(lost_clusters)
        )

        # Генерируем карту через ReportGenerator
        gen = report_gen_module.ReportGenerator(
            region_name=task.region_name,
            period_label=task.period_label,
        )
        html = await asyncio.to_thread(
            gen.generate_cluster_map,
            all_clusters_for_map,
            preclusters if preclusters else None,
            cameras if cameras else None,
        )

        # Добавляем плашку про исчезнувшие очаги (если есть)
        if lost_clusters:
            lost_count = len(lost_clusters)
            lost_dtp = sum(c.get("total_accidents", 0) for c in lost_clusters)
            lost_deaths = sum(c.get("deaths", 0) for c in lost_clusters)
            banner = f"""
<div class="lost-banner" style="
  position:absolute;top:10px;right:10px;z-index:1000;
  background:#fff3e0;border:1px solid #ff9800;border-radius:6px;
  padding:8px 12px;font:12px/1.4 -apple-system,system-ui,sans-serif;
  box-shadow:0 2px 8px rgba(0,0,0,0.2);max-width:240px;">
  <b style="color:#d32f2f;">❌ Исчезнувшие очаги: {lost_count}</b><br>
  <span style="color:#555;">Это очаги, которые были в прошлом периоде,
  но не подтвердились в текущем.</span><br>
  <span style="color:#888;font-size:11px;">
  ДТП прошлого периода: {lost_dtp} | Погибло: {lost_deaths}</span>
</div>"""
            # Вставляем плашку после <body>
            html = html.replace(
                "<body>", f"<body>{banner}", 1
            ) if "<body>" in html else html + banner

        logger.info(
            f"Task {task.id}: clusters map generated — "
            f"{len(current_only)} текущих, "
            f"{len(prev_matched_clusters)} АППГ-повторённых, "
            f"{len(lost_clusters)} исчезнувших, "
            f"{len(preclusters)} предочагов, {len(cameras)} камер"
        )
        return html

    except Exception as exc:
        logger.exception(f"Task {task.id}: clusters map generation failed")
        return None


def _build_clusters_map_html(task: Task) -> str:
    """Простая Leaflet-карта очагов с маркерами и popup."""
    result = task.clusters_state.result
    if not result:
        return "<html><body>Нет данных</body></html>"

    clusters = result.get("clusters", [])
    preclusters = result.get("preclusters", [])

    # Разделяем: текущие / АППГ-повторённые / исчезнувшие
    # по dynamics.status. Каждая группа — отдельный слой на карте.
    current_clusters = []
    lost_clusters = []
    prev_matched_clusters = []
    for c in clusters:
        st = c.get("dynamics", {}).get("status")
        if st == "lost":
            lost_clusters.append(c)
        elif st == "prev_matched":
            prev_matched_clusters.append(c)
        else:
            current_clusters.append(c)

    # Leaflet-карта
    def _build_marker_js(c: dict, color: str, layer_var: str) -> str:
        center = c.get("center")
        if not center:
            return ""
        lat, lon = center["lat"], center["lon"]
        road = (c.get("road") or "Не указана").replace("'", "\\'")
        total = c.get("total_accidents", 0)
        deaths = c.get("deaths", 0)
        injured = c.get("injured", 0)
        zone = c.get("zone_type", "")
        popup_html = (
            f"<b>{road}</b><br>"
            f"ДТП: {total} | Погибло: {deaths} | Ранено: {injured}<br>"
            f"Тип: {zone}"
        ).replace('"', "&quot;")
        radius = max(8, min(30, total * 2))
        return (
            f"L.circleMarker([{lat}, {lon}], "
            f"{{radius: {radius}, color: '{color}', "
            f"fillColor: '{color}', fillOpacity: 0.6}})"
            f".addTo({layer_var}).bindPopup(\"{popup_html}\");"
        )

    markers_js = []
    # Слои: current — обычные цвета по тяжести, prev_matched — голубой,
    # lost — серый. Layer groups позволяют включать/выключать группы через UI Leaflet.
    markers_js.append("var currentLayer = L.layerGroup().addTo(map);")
    markers_js.append("var prevMatchedLayer = L.layerGroup().addTo(map);")
    markers_js.append("var lostLayer = L.layerGroup().addTo(map);")
    for c in current_clusters:
        markers_js.append(_build_marker_js(c, _color_for_severity(c), "currentLayer"))
    for c in prev_matched_clusters:
        # АППГ-повторённые — голубой (#5ac8fa), чтобы визуально отличить
        # от текущих и от исчезнувших (серый).
        markers_js.append(_build_marker_js(c, "#5ac8fa", "prevMatchedLayer"))
    for c in lost_clusters:
        # Исчезнувшие — светло-серый цвет, чтобы визуально отделить от активных очагов
        markers_js.append(_build_marker_js(c, "#c0c0c0", "lostLayer"))

    for p in preclusters:
        center = p.get("center")
        if not center:
            continue
        lat, lon = center["lat"], center["lon"]
        road = (p.get("road") or "Не указана").replace("'", "\\'")
        total = p.get("total_accidents", 0)
        popup_html = (
            f"<b>Предочаг:</b> {road}<br>"
            f"ДТП: {total}"
        ).replace('"', "&quot;")
        markers_js.append(
            f"L.circleMarker([{lat}, {lon}], "
            f"{{radius: 8, color: '#ff9500', "
            f"fillColor: '#ff9500', fillOpacity: 0.4, "
            f"dashArray: '4,4'}})"
            f".addTo(map).bindPopup(\"{popup_html}\");"
        )

    # Центр карты — первый очаг или дефолт
    if clusters and clusters[0].get("center"):
        center_lat = clusters[0]["center"]["lat"]
        center_lon = clusters[0]["center"]["lon"]
    else:
        center_lat, center_lon = 55.75, 37.62

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Очаги ДТП — {task.region_name}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
html, body, #map {{ margin: 0; padding: 0; height: 100%; width: 100%; }}
.legend {{
  position: absolute; bottom: 10px; left: 10px; z-index: 1000;
  background: white; padding: 8px 12px; border-radius: 6px;
  font: 12px/1.4 -apple-system, system-ui, sans-serif;
  box-shadow: 0 2px 8px rgba(0,0,0,0.2);
}}
.legend-item {{ display: flex; align-items: center; gap: 6px; margin: 2px 0; }}
.legend-dot {{ width: 12px; height: 12px; border-radius: 50%; }}
</style>
</head>
<body>
<div id="map"></div>
<div class="legend">
<div class="legend-item"><span class="legend-dot" style="background:#5ac8fa;border:2px dashed #007aff;"></span>АППГ (повторён в текущем)</div>
<div class="legend-item"><span class="legend-dot" style="background:#c0c0c0;border:2px dashed #9e9e9e;"></span>Исчезнувший очаг</div>
<div class="legend-item"><span class="legend-dot" style="background:#2481cc"></span>Очаг (низкая тяжесть)</div>
<div class="legend-item"><span class="legend-dot" style="background:#ff9500"></span>Очаг (высокая тяжесть)</div>
<div class="legend-item"><span class="legend-dot" style="background:#34c759;opacity:0.5"></span>Предочаг</div>
</div>
<script>
var map = L.map('map').setView([{center_lat}, {center_lon}], 11);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 18, attribution: '&copy; OpenStreetMap'
}}).addTo(map);
{chr(10).join(markers_js)}
L.control.layers({{}}, {{
  "Текущие очаги": currentLayer,
  "АППГ (повторённые)": prevMatchedLayer,
  "Исчезнувшие очаги": lostLayer
}}, {{collapsed: false}}).addTo(map);
</script>
</body>
</html>"""
    return html


def _color_for_severity(cluster: dict) -> str:
    """Цвет очага по тяжести."""
    deaths = cluster.get("deaths", 0)
    if deaths >= 3:
        return "#ff3b30"
    if deaths >= 1:
        return "#ff9500"
    return "#2481cc"


# ============================================================
# Excel-выгрузка: очаги (4 листа)
# ============================================================
async def generate_clusters_excel(task: Task) -> Optional[bytes]:
    """
    Генерирует Excel-файл с очагами ДТП (4 листа):
      Лист 1 «Очаги ДТП» — текущие очаги
      Лист 2 «Динамика очагов» — текущие + исчезнувшие со статусом
      Лист 3 «Детализация ДТП» — все ДТП по периодам
      Лист 4 «Предочаги» — места, не дотянувшие до очага

    Использует excel_generator.generate_concentration_dynamics_file() из бота.
    """
    if not task.raw_clusters and not task.raw_preclusters:
        # Нет ни очагов, ни предочагов — выгружать нечего
        return None

    try:
        conc_module = _imports._import_module("concentration_points")
        excel_module = _imports._import_module("excel_generator")

        raw_clusters = task.raw_clusters or []
        raw_preclusters = task.raw_preclusters or []
        # current_only — только текущие очаги (без lost и prev_matched)
        # для листа 1 «Очаги ДТП». Лист 2 «Динамика очагов» использует
        # raw_clusters целиком (текущие + prev_matched + lost).
        current_only = [
            c for c in raw_clusters
            if not c.get("_is_lost", False) and not c.get("_is_prev_matched", False)
        ]
        # Предочаги — из отдельного поля (раньше raw_clusters[0]["_preclusters"],
        # что ломалось при пустом списке очагов)
        preclusters = raw_preclusters

        # Лист 1: очаги текущего года
        current_data = conc_module.build_concentration_excel_data(current_only)
        current_columns = conc_module.get_concentration_column_names()

        # Лист 2: динамика (текущие + исчезнувшие)
        dynamics_data = conc_module.build_dynamics_excel_data(raw_clusters)
        dynamics_columns = conc_module.get_dynamics_column_names()

        # Лист 3: детализация ДТП
        detail_data = conc_module.build_dynamics_detail_data(
            raw_clusters,
            task.period_label,
            task.prev_label or "",
        )
        detail_columns = conc_module.get_dynamics_detail_column_names()

        # Лист 4: предочаги
        precluster_data = None
        precluster_columns = None
        if preclusters:
            precluster_data = conc_module.build_precluster_excel_data(preclusters)
            precluster_columns = conc_module.get_precluster_column_names()

        # Генерация Excel (тяжёлая операция — в потоке)
        xlsx_bytes = await asyncio.to_thread(
            excel_module.generate_concentration_dynamics_file,
            current_data, current_columns,
            dynamics_data, dynamics_columns,
            detail_data, detail_columns,
            precluster_data, precluster_columns,
        )

        logger.info(
            f"Task {task.id}: clusters Excel generated — "
            f"{len(current_data)} очагов, {len(dynamics_data)} в динамике, "
            f"{len(detail_data)} ДТП, {len(preclusters)} предочагов"
        )
        return xlsx_bytes

    except Exception as exc:
        logger.exception(f"Task {task.id}: clusters Excel generation failed")
        return None
