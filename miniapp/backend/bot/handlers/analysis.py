"""
analysis.py — анализ ДТП: выгрузка, парсинг, аналитика, Excel (Фаза 2.6 facade).

Самый сложный модуль в проекте. Содержит основной пайплайн:
  _run_analysis (~458 строк) — выгрузка, parsing, analytics, Excel, map
  _run_concentration_points (~415 строк) — очаги, кластеры, OSM, камеры

Функции для миграции в Фазе 3 (по убыванию сложности):
  _run_analysis             (1908-2365)   — самый сложный
  _run_concentration_points (2381-2800)
  _send_analytics_html      (2938-2988)
  _send_clusters_html       (2989-3047)
  _html_map_menu            (2801-2830)
  _generate_and_send_dtp_map (2831-2937)
  _start_point_stats        (3048-3123)
  _handle_point_stats_radius (3124-3144)
  _send_point_stats_excel   (3145-3240)
  _send_point_stats_html    (3241-3335)
  _process_point_stats      (3336-3431)
  _handle_analytics_question (3453-3589)
  _clear_analytics_data     (2366-2380)
  _get_current_cards / _get_prev_cards / _has_analytics_data / _get_card_count
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[5])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


_ALLOWED = {
    "_run_analysis",
    "_run_concentration_points",
    "_send_analytics_html",
    "_send_clusters_html",
    "_html_map_menu",
    "_generate_and_send_dtp_map",
    "_start_point_stats",
    "_handle_point_stats_radius",
    "_send_point_stats_excel",
    "_send_point_stats_html",
    "_process_point_stats",
    "_handle_analytics_question",
    "_clear_analytics_data",
    "_get_current_cards",
    "_get_prev_cards",
    "_has_analytics_data",
    "_get_card_count",
}


def __getattr__(name: str):
    if name in _ALLOWED:
        import bot as _b
        return getattr(_b, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
