"""
Модуль аналитики ДТП: сравнение текущего периода с аналогичным периодом прошлого года.

Вычисляет ключевые метрики:
  - Всего ДТП, погибших, раненых
  - ДТП с участием нетрезвых водителей
  - ДТП с пешеходами
  - Распределение по дням недели, часам, видам ДТП
  - Процентные изменения между периодами
"""

import logging
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)


# ========================
# Названия дней недели и часов
# ========================

DAY_NAMES = {
    0: "Понедельник", 1: "Вторник", 2: "Среда",
    3: "Четверг", 4: "Пятница", 5: "Суббота", 6: "Воскресенье",
}

DAY_SHORT = {
    0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Вс",
}


# ========================
# Подсчёт метрик по карточкам ДТП
# ========================

def _safe_int(val: Any) -> int:
    """Безопасное приведение к int."""
    if val is None:
        return 0
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def _safe_float(val: Any) -> float:
    """Безопасное приведение к float."""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _get_hour(time_str: str) -> int | None:
    """Извлекает час из строки времени (формат 'HH:MM' или 'H:MM')."""
    if not time_str:
        return None
    try:
        parts = time_str.strip().split(":")
        hour = int(parts[0])
        if 0 <= hour <= 23:
            return hour
        return None
    except (ValueError, IndexError):
        return None


def _get_weekday(date_str: str) -> int | None:
    """Извлекает день недели из строки даты (формат 'DD.MM.YYYY')."""
    if not date_str:
        return None
    try:
        from datetime import datetime
        dt = datetime.strptime(date_str.strip()[:10], "%d.%m.%Y")
        return dt.weekday()  # 0=Пн, 6=Вс
    except (ValueError, IndexError):
        return None


def _has_alcohol(card: dict) -> bool:
    """Проверяет, есть ли в ДТП нетрезвый участник."""
    # Проверяем водителей из ts_info
    ts_list = card.get("ts_info", []) or []
    for ts in ts_list:
        ts_uch_list = ts.get("ts_uch", []) or []
        for uch in ts_uch_list:
            kt = str(uch.get("kt_uch", "")).lower()
            alco = str(uch.get("alco", "")).strip()
            if kt == "водитель" and alco and alco not in ("0", "00", ""):
                return True
    return False


def _has_pedestrian(card: dict) -> bool:
    """Проверяет, есть ли в ДТП пешеход.

    Два способа:
    1. По полю uch_info — категория участника «пешеход».
       Это основной источник (API ГИБДД и web_fallback).
    2. Fallback по dtpv (вид ДТП) — для карточек, где uch_info
       отсутствует или не содержит пешехода.
    """
    # Способ 1: по участникам без ТС
    uch_list = card.get("uch_info", []) or []
    for uch in uch_list:
        kt = str(uch.get("kt_uch", "")).lower()
        if kt == "пешеход":
            return True

    # Способ 2: fallback по виду ДТП
    dtpv = str(card.get("dtpv", "")).lower()
    if "пешеход" in dtpv or "сим" in dtpv:
        return True

    return False


# ============================================================
# Группировка видов ДТП в 9 канонических категорий
# (для читабельных графиков в Mini App)
# ============================================================
DTP_TYPE_GROUPS: list[tuple[str, list[str]]] = [
    ("Столкновение", ["столкновение"]),
    ("Наезд на пешехода", ["наезд на пешехода"]),
    ("Наезд на велосипедиста", ["наезд на велосипед"]),
    ("Наезд на стоящее ТС", ["наезд на стоящее"]),
    ("Съезд с дороги", ["съезд с дороги"]),
    ("Опрокидывание", ["опрокидывание"]),
    ("Наезд на препятствие", ["наезд на препятствие"]),
    (
        "Наезд на лицо, использующее СИМ",
        ["сим", "средство индивидуальной мобильности", "электросамокат"],
    ),
    # "Иные ДТП" — все остальные (добавляется автоматически в group_dtp_type)
]

# Порядок категорий для отображения на графике
DTP_TYPE_ORDER: list[str] = [g[0] for g in DTP_TYPE_GROUPS] + ["Иные ДТП"]


# ============================================================
# Группировка значений дороги (поле dor_z) в канонические
# категории: Федеральные / Региональные / Межмуниципальные /
# Муниципальные / Иные.
# ============================================================
ROAD_SIGNIFICANCE_GROUPS: list[tuple[str, list[str]]] = [
    ("Федеральные", ["федеральн"]),
    ("Региональные", ["региональн"]),
    ("Межмуниципальные", ["межмуниципальн"]),
    ("Муниципальные", ["муниципальн"]),
    # "Иные" — все остальные, включая пустое значение
]

ROAD_SIGNIFICANCE_ORDER: list[str] = (
    [g[0] for g in ROAD_SIGNIFICANCE_GROUPS] + ["Иные"]
)


def group_road_significance(raw_value: str) -> str:
    """Приводит произвольное значение dor_z к одной из канонических категорий.

    ГИБДД хранит значения вроде:
      - «Федерального значения»
      - «Регионального или межмуниципального значения»
      - «Муниципального значения»
    Здесь мы проверяем подстроки по приоритету: федеральные → региональные →
    межмуниципальные → муниципальные. Если ничего не подошло — «Иные».

    Пустое значение также классифицируется как «Иные».
    """
    if not raw_value:
        return "Иные"
    t = str(raw_value).lower().strip()
    for canonical, aliases in ROAD_SIGNIFICANCE_GROUPS:
        for alias in aliases:
            if alias in t:
                return canonical
    return "Иные"


def group_dtp_type(raw_type: str) -> str:
    """Приводит произвольный вид ДТП к одной из 9 канонических категорий.

    Если ни одна из категорий не подошла — возвращает "Иные ДТП".
    Пустая строка также классифицируется как "Иные ДТП".
    """
    if not raw_type:
        return "Иные ДТП"
    t = str(raw_type).lower().strip()
    for canonical, aliases in DTP_TYPE_GROUPS:
        for alias in aliases:
            if alias in t:
                return canonical
    return "Иные ДТП"


def _month_name_from_date(date_str: str) -> str | None:
    """Возвращает русское название месяца по строке даты 'DD.MM.YYYY'.

    Возвращает None, если дата не распарсена.
    """
    if not date_str:
        return None
    try:
        from datetime import datetime
        m = datetime.strptime(str(date_str).strip()[:10], "%d.%m.%Y").month
        names = {
            1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
            5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
            9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
        }
        return names.get(m)
    except (ValueError, IndexError):
        return None


def calculate_metrics(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Считает все метрики по списку карточек ДТП.

    Returns:
        Словарь с метриками:
          - total: всего ДТП
          - deaths: погибших
          - injured: раненых
          - alcohol: ДТП с нетрезвыми водителями
          - pedestrians: ДТП с пешеходами
          - deaths_per_100: погибших на 100 ДТП
          - injured_per_100: раненых на 100 ДТП
          - by_weekday: {0: count, 1: count, ...}
          - by_hour: {0: count, 1: count, ...}
          - by_type: {вид_ДТП: count, ...}
          - by_weather: {погода: count, ...}
    """
    total = len(cards)
    deaths = 0
    injured = 0
    alcohol_count = 0
    pedestrian_count = 0

    weekday_counter = Counter()
    hour_counter = Counter()
    type_counter = Counter()
    type_grouped_counter = Counter()
    weather_counter = Counter()
    road_counter = Counter()
    month_counter: dict[str, dict[str, int]] = {}

    # Severity-варианты для переключателя ДТП / Погибшие / Раненые в Mini App.
    # Хранятся как {ключ: {"dtp": N, "deaths": N, "injured": N}} —
    # старые простые Counter'ы сохранены для обратной совместимости
    # (используются в build_analytics_message и тестах).
    weekday_severity: dict[int, dict[str, int]] = {}
    hour_severity: dict[int, dict[str, int]] = {}
    type_grouped_severity: dict[str, dict[str, int]] = {}
    weather_severity: dict[str, dict[str, int]] = {}
    road_significance_severity: dict[str, dict[str, int]] = {}

    def _bump_severity(
        table: dict, key, deaths_add: int, injured_add: int
    ) -> None:
        bucket = table.setdefault(
            key, {"dtp": 0, "deaths": 0, "injured": 0}
        )
        bucket["dtp"] += 1
        bucket["deaths"] += deaths_add
        bucket["injured"] += injured_add

    for card in cards:
        # Погибшие и раненые
        card_deaths = _safe_int(card.get("pog"))
        card_injured = _safe_int(card.get("ran"))
        deaths += card_deaths
        injured += card_injured

        # Нетрезвые водители
        if _has_alcohol(card):
            alcohol_count += 1

        # Пешеходы
        if _has_pedestrian(card):
            pedestrian_count += 1

        # День недели
        wd = _get_weekday(str(card.get("date_dtp", "")))
        if wd is not None:
            weekday_counter[wd] += 1
            _bump_severity(weekday_severity, wd, card_deaths, card_injured)

        # Час
        hour = _get_hour(str(card.get("time", "")))
        if hour is not None:
            hour_counter[hour] += 1
            _bump_severity(hour_severity, hour, card_deaths, card_injured)

        # Вид ДТП (raw — как в данных ГИБДД)
        dtp_type = str(card.get("dtpv", "")).strip()
        if dtp_type:
            type_counter[dtp_type] += 1

        # Вид ДТП (сгруппированный — 9 категорий)
        grouped = group_dtp_type(dtp_type)
        type_grouped_counter[grouped] += 1
        _bump_severity(
            type_grouped_severity, grouped, card_deaths, card_injured
        )

        # Погодные условия
        dor_usl = card.get("dor_usl", {}) or {}
        weather_list = dor_usl.get("spog", []) or []
        if isinstance(weather_list, list):
            for w in weather_list:
                w_str = str(w).strip()
                if w_str:
                    weather_counter[w_str] += 1
                    _bump_severity(
                        weather_severity, w_str, card_deaths, card_injured
                    )

        # Дорога (наименование — поле "dor")
        road = str(card.get("dor", "")).strip()
        if road:
            road_counter[road] += 1

        # Значение дороги (поле "dor_z") — Федеральные / Региональные /
        # Муниципальные / Иные. Группируем сырые значения ГИБДД в
        # канонические категории для читаемого графика.
        road_sig = group_road_significance(str(card.get("dor_z", "")).strip())
        _bump_severity(
            road_significance_severity, road_sig, card_deaths, card_injured
        )

        # Месяц × тяжесть
        m_name = _month_name_from_date(str(card.get("date_dtp", "")))
        if m_name:
            bucket = month_counter.setdefault(
                m_name, {"dtp": 0, "deaths": 0, "injured": 0}
            )
            bucket["dtp"] += 1
            bucket["deaths"] += card_deaths
            bucket["injured"] += card_injured

    deaths_per_100 = round(deaths / total * 100, 1) if total > 0 else 0
    injured_per_100 = round(injured / total * 100, 1) if total > 0 else 0

    return {
        "total": total,
        "deaths": deaths,
        "injured": injured,
        "alcohol": alcohol_count,
        "pedestrians": pedestrian_count,
        "deaths_per_100": deaths_per_100,
        "injured_per_100": injured_per_100,
        "by_weekday": dict(weekday_counter),
        "by_hour": dict(hour_counter),
        "by_type": dict(type_counter),
        "by_type_grouped": dict(type_grouped_counter),
        "by_weather": dict(weather_counter),
        "by_road": dict(road_counter),
        "by_month": month_counter,
        # Severity-варианты (новые поля, не ломают старый код):
        "by_weekday_severity": {
            str(k): v for k, v in weekday_severity.items()
        },
        "by_hour_severity": {
            str(k): v for k, v in hour_severity.items()
        },
        "by_type_grouped_severity": type_grouped_severity,
        "by_weather_severity": weather_severity,
        "by_road_significance": road_significance_severity,
    }


def compare_metrics(
    current: dict[str, Any],
    previous: dict[str, Any],
) -> dict[str, Any]:
    """
    Сравнивает метрики текущего и предыдущего периода.

    Returns:
        Словарь с результатами сравнения.
    """
    def pct_change(new: float, old: float) -> float:
        """Вычисляет процент изменения."""
        if old == 0:
            return 0.0 if new == 0 else 100.0
        return round((new - old) / old * 100, 1)

    result = {
        "total": {
            "current": current["total"],
            "previous": previous["total"],
            "change": pct_change(current["total"], previous["total"]),
            "abs_change": current["total"] - previous["total"],
        },
        "deaths": {
            "current": current["deaths"],
            "previous": previous["deaths"],
            "change": pct_change(current["deaths"], previous["deaths"]),
            "abs_change": current["deaths"] - previous["deaths"],
        },
        "injured": {
            "current": current["injured"],
            "previous": previous["injured"],
            "change": pct_change(current["injured"], previous["injured"]),
            "abs_change": current["injured"] - previous["injured"],
        },
        "alcohol": {
            "current": current["alcohol"],
            "previous": previous["alcohol"],
            "change": pct_change(current["alcohol"], previous["alcohol"]),
            "abs_change": current["alcohol"] - previous["alcohol"],
        },
        "pedestrians": {
            "current": current["pedestrians"],
            "previous": previous["pedestrians"],
            "change": pct_change(current["pedestrians"], previous["pedestrians"]),
            "abs_change": current["pedestrians"] - previous["pedestrians"],
        },
        "deaths_per_100": {
            "current": current["deaths_per_100"],
            "previous": previous["deaths_per_100"],
            "change": round(current["deaths_per_100"] - previous["deaths_per_100"], 1),
            "abs_change": round(current["deaths_per_100"] - previous["deaths_per_100"], 1),
        },
        "injured_per_100": {
            "current": current["injured_per_100"],
            "previous": previous["injured_per_100"],
            "change": round(current["injured_per_100"] - previous["injured_per_100"], 1),
            "abs_change": round(current["injured_per_100"] - previous["injured_per_100"], 1),
        },
    }

    # Распределения
    result["by_weekday"] = {
        "current": current["by_weekday"],
        "previous": previous["by_weekday"],
    }
    result["by_hour"] = {
        "current": current["by_hour"],
        "previous": previous["by_hour"],
    }
    result["by_type"] = {
        "current": current["by_type"],
        "previous": previous["by_type"],
    }
    result["by_type_grouped"] = {
        "current": current.get("by_type_grouped", {}),
        "previous": previous.get("by_type_grouped", {}),
    }
    result["by_weather"] = {
        "current": current["by_weather"],
        "previous": previous["by_weather"],
    }
    result["by_road"] = {
        "current": current.get("by_road", {}),
        "previous": previous.get("by_road", {}),
    }
    result["by_month"] = {
        "current": current.get("by_month", {}),
        "previous": previous.get("by_month", {}),
    }
    # Severity-варианты для переключателя метрик в Mini App
    result["by_weekday_severity"] = {
        "current": current.get("by_weekday_severity", {}),
        "previous": previous.get("by_weekday_severity", {}),
    }
    result["by_hour_severity"] = {
        "current": current.get("by_hour_severity", {}),
        "previous": previous.get("by_hour_severity", {}),
    }
    result["by_type_grouped_severity"] = {
        "current": current.get("by_type_grouped_severity", {}),
        "previous": previous.get("by_type_grouped_severity", {}),
    }
    result["by_weather_severity"] = {
        "current": current.get("by_weather_severity", {}),
        "previous": previous.get("by_weather_severity", {}),
    }
    result["by_road_significance"] = {
        "current": current.get("by_road_significance", {}),
        "previous": previous.get("by_road_significance", {}),
    }

    return result


def build_full_analytics(
    current_cards: list[dict[str, Any]],
    prev_cards: list[dict[str, Any]] | None = None,
    prev_label: str | None = None,
) -> dict[str, Any]:
    """Собирает расширенный аналитический блок для Mini App.

    Возвращает dict со следующей структурой:
      {
        "current": {…результат calculate_metrics для текущего периода…},
        "previous": {…то же для прошлого периода или None…} | None,
        "comparison": {…результат compare_metrics или None…} | None,
        "has_prev_data": bool,
        "prev_label": "Январь-Июнь 2025" | None,
        "current_label": "Январь-Июнь 2026"  // не задаётся здесь, копируется позже
      }

    Функция safe для пустых списков: вернёт корректный zero-state.
    """
    current = calculate_metrics(current_cards or [])

    if prev_cards:
        previous = calculate_metrics(prev_cards)
        comparison = compare_metrics(current, previous)
        has_prev = True
    else:
        previous = None
        comparison = None
        has_prev = False

    return {
        "current": current,
        "previous": previous,
        "comparison": comparison,
        "has_prev_data": has_prev,
        "prev_label": prev_label if has_prev else None,
    }


def format_change(value: float) -> str:
    """Форматирует процент изменения со знаком и стрелкой."""
    if value > 0:
        return f"+{value}% \u2191"
    elif value < 0:
        return f"{value}% \u2193"
    else:
        return "0% \u2194"


def build_analytics_message(
    comparison: dict[str, Any],
    reg_name: str,
    current_label: str,
    previous_label: str,
) -> str:
    """
    Формирует текстовое сообщение с результатами анализа.

    Args:
        comparison: Результат compare_metrics()
        reg_name: Название региона
        current_label: Подпись текущего периода
        previous_label: Подпись предыдущего периода

    Returns:
        Текст сообщения в Markdown
    """
    lines = []
    lines.append(f"\U0001F4CA <b>АНАЛИТИКА: {reg_name}</b>")
    lines.append(f"Период: {current_label}")
    lines.append(f"Сравнение: {previous_label}")
    lines.append("")

    # Таблица основных показателей
    lines.append("<b>\u2500\u2500\u2500 Основные показатели \u2500\u2500\u2500</b>")
    lines.append("")

    metrics_table = [
        ("Всего ДТП", "total"),
        ("Погибло", "deaths"),
        ("Ранено", "injured"),
        ("ДТП с нетрезвыми", "alcohol"),
        ("ДТП с пешеходами", "pedestrians"),
        ("Погибло на 100 ДТП", "deaths_per_100"),
        ("Ранено на 100 ДТП", "injured_per_100"),
    ]

    for label, key in metrics_table:
        m = comparison[key]
        change = format_change(m["change"])
        abs_change = m["abs_change"]
        if abs_change > 0:
            abs_str = f"(+{abs_change})"
        elif abs_change < 0:
            abs_str = f"({abs_change})"
        else:
            abs_str = "(=)"
        lines.append(f"<b>{label}:</b> {m['current']} | {change} {abs_str}")

    lines.append("")

    # Пиковый день недели
    lines.append("<b>\u2500\u2500\u2500 По дням недели \u2500\u2500\u2500</b>")
    lines.append("")
    cur_wd = comparison["by_weekday"]["current"]
    prev_wd = comparison["by_weekday"]["previous"]

    if cur_wd:
        sorted_days = sorted(cur_wd.items(), key=lambda x: x[1], reverse=True)
        peak_day_num, peak_day_count = sorted_days[0]
        peak_day_name = DAY_SHORT.get(peak_day_num, str(peak_day_num))

        total_current = sum(cur_wd.values())
        avg_per_day = total_current / 7 if total_current > 0 else 0
        pct_of_avg = round(peak_day_count / avg_per_day * 100, 0) if avg_per_day > 0 else 0

        lines.append(f"Пиковый день: <b>{DAY_NAMES.get(peak_day_num, '')}</b> ({peak_day_count} ДТП, {pct_of_avg}% от среднего)")

        # Таблица по дням
        for day_num in range(7):
            day_name = DAY_SHORT[day_num]
            cur = cur_wd.get(day_num, 0)
            prv = prev_wd.get(day_num, 0)
            if prv > 0:
                change = round((cur - prv) / prv * 100, 1)
                arrow = "\u2191" if change > 0 else ("\u2193" if change < 0 else "\u2194")
                lines.append(f"  {day_name}: {cur} ({change:+.0f}%{arrow})")
            else:
                lines.append(f"  {day_name}: {cur}")
    else:
        lines.append("Нет данных для анализа по дням недели")

    lines.append("")

    # Пиковый час
    lines.append("<b>\u2500\u2500\u2500 По часам суток \u2500\u2500\u2500</b>")
    lines.append("")

    cur_hour = comparison["by_hour"]["current"]
    prev_hour = comparison["by_hour"]["previous"]

    if cur_hour:
        # Группируем по 3-часовым интервалам
        intervals = {}
        for h in range(24):
            interval_start = (h // 3) * 3
            interval_end = interval_start + 2
            interval_key = f"{interval_start:02d}-{interval_end:02d}"
            intervals.setdefault(interval_key, 0)
            intervals[interval_key] += cur_hour.get(h, 0)

        sorted_intervals = sorted(intervals.items(), key=lambda x: x[1], reverse=True)
        peak_interval, peak_count = sorted_intervals[0]

        total_current = sum(cur_hour.values())
        avg_per_interval = total_current / 8 if total_current > 0 else 0
        pct_of_avg = round(peak_count / avg_per_interval * 100, 0) if avg_per_interval > 0 else 0

        lines.append(f"Пиковый интервал: <b>{peak_interval}</b> ({peak_count} ДТП, {pct_of_avg}% от среднего)")

        # Топ-3 опасных часа
        sorted_hours = sorted(cur_hour.items(), key=lambda x: x[1], reverse=True)
        top_hours = sorted_hours[:3]
        hours_str = ", ".join(f"{h:02d}:00 ({c})" for h, c in top_hours)
        lines.append(f"Топ-3 часа: {hours_str}")
    else:
        lines.append("Нет данных для анализа по часам")

    lines.append("")

    # Типы ДТП
    lines.append("<b>\u2500\u2500\u2500 По видам ДТП \u2500\u2500\u2500</b>")
    lines.append("")

    cur_type = comparison["by_type"]["current"]
    prev_type = comparison["by_type"]["previous"]

    if cur_type:
        sorted_types = sorted(cur_type.items(), key=lambda x: x[1], reverse=True)
        for tp_name, tp_count in sorted_types[:7]:
            prv = prev_type.get(tp_name, 0)
            if prv > 0:
                change = round((tp_count - prv) / prv * 100, 1)
                arrow = "\u2191" if change > 0 else ("\u2193" if change < 0 else "\u2194")
                lines.append(f"  {tp_name}: {tp_count} ({change:+.0f}%{arrow})")
            else:
                lines.append(f"  {tp_name}: {tp_count}")
    else:
        lines.append("Нет данных для анализа по видам ДТП")

    lines.append("")

    # Вывод
    lines.append("<b>\u2500\u2500\u2500 Вывод \u2500\u2500\u2500</b>")
    lines.append("")

    total_change = comparison["total"]["change"]
    deaths_change = comparison["deaths"]["change"]
    alcohol_change = comparison["alcohol"]["change"]
    ped_change = comparison["pedestrians"]["change"]

    # Общая оценка
    if total_change <= -5:
        lines.append(f"\u2705 Общее количество ДТП снизилось на {abs(total_change):.1f}% \u2014 положительная динамика.")
    elif total_change >= 5:
        lines.append(f"\u26A0\uFE0F Общее количество ДТП выросло на {total_change:.1f}% \u2014 отрицательная динамика.")
    else:
        lines.append(f"\u2194 Общее количество ДТП осталось на прежнем уровне (изменение {total_change:+.1f}%).")

    # Погибшие
    if deaths_change < 0:
        lines.append(f"\u2705 Число погибших снизилось на {abs(deaths_change):.1f}%.")
    elif deaths_change > 0:
        lines.append(f"\u274C Число погибших выросло на {deaths_change:.1f}% \u2014 требует внимания.")

    # Нетрезвые
    if alcohol_change > 5:
        lines.append(f"\U0001F976 Доля ДТП с нетрезвыми водителями выросла на {alcohol_change:.1f}%.")

    # Пешеходы
    if ped_change > 5:
        lines.append(f"\U0001F6B6 ДТП с пешеходами выросли на {ped_change:.1f}% \u2014 требует внимания.")

    return "\n".join(lines)


def build_analytics_excel_data(
    comparison: dict[str, Any],
    reg_name: str,
    current_label: str,
    previous_label: str,
) -> list[dict[str, str]]:
    """
    Строит данные для Excel-файла аналитики.

    Returns:
        Список словарей с данными для таблицы
    """
    rows = []

    # Заголовок
    rows.append({
        "Показатель": "РЕГИОН",
        current_label: reg_name,
        previous_label: reg_name,
        "Изменение, %": "",
        "Изменение, абс.": "",
    })

    # Основные метрики
    metrics = [
        ("Всего ДТП", "total"),
        ("Погибло, чел.", "deaths"),
        ("Ранено, чел.", "injured"),
        ("ДТП с нетрезвыми водителями", "alcohol"),
        ("ДТП с пешеходами", "pedestrians"),
        ("Погибло на 100 ДТП", "deaths_per_100"),
        ("Ранено на 100 ДТП", "injured_per_100"),
    ]

    for label, key in metrics:
        m = comparison[key]
        cur = m["current"]
        prv = m["previous"]
        change = m["change"]
        abs_change = m["abs_change"]
        rows.append({
            "Показатель": label,
            current_label: cur,
            previous_label: prv,
            "Изменение, %": change,
            "Изменение, абс.": abs_change,
        })

    # Пустая строка-разделитель
    rows.append({
        "Показатель": "",
        current_label: "",
        previous_label: "",
        "Изменение, %": "",
        "Изменение, абс.": "",
    })

    # По дням недели
    rows.append({
        "Показатель": "ПО ДНЯМ НЕДЕЛИ",
        current_label: "",
        previous_label: "",
        "Изменение, %": "",
        "Изменение, абс.": "",
    })

    cur_wd = comparison["by_weekday"]["current"]
    prev_wd = comparison["by_weekday"]["previous"]

    for day_num in range(7):
        day_name = DAY_NAMES[day_num]
        cur = cur_wd.get(day_num, 0)
        prv = prev_wd.get(day_num, 0)
        if prv > 0:
            change = round((cur - prv) / prv * 100, 1)
        else:
            change = 0
        rows.append({
            "Показатель": day_name,
            current_label: cur,
            previous_label: prv,
            "Изменение, %": change,
            "Изменение, абс.": cur - prv,
        })

    # Пустая строка-разделитель
    rows.append({
        "Показатель": "",
        current_label: "",
        previous_label: "",
        "Изменение, %": "",
        "Изменение, абс.": "",
    })

    # По часам суток (интервалы по 3 часа)
    rows.append({
        "Показатель": "ПО ЧАСАМ СУТОК (интервалы 3 ч)",
        current_label: "",
        previous_label: "",
        "Изменение, %": "",
        "Изменение, абс.": "",
    })

    cur_hour = comparison["by_hour"]["current"]
    prev_hour = comparison["by_hour"]["previous"]

    for interval_start in range(0, 24, 3):
        interval_end = interval_start + 2
        interval_label = f"{interval_start:02d}:00 - {interval_end:02d}:59"

        cur = sum(cur_hour.get(h, 0) for h in range(interval_start, interval_start + 3))
        prv = sum(prev_hour.get(h, 0) for h in range(interval_start, interval_start + 3))
        if prv > 0:
            change = round((cur - prv) / prv * 100, 1)
        else:
            change = 0
        rows.append({
            "Показатель": interval_label,
            current_label: cur,
            previous_label: prv,
            "Изменение, %": change,
            "Изменение, абс.": cur - prv,
        })

    # Пустая строка-разделитель
    rows.append({
        "Показатель": "",
        current_label: "",
        previous_label: "",
        "Изменение, %": "",
        "Изменение, абс.": "",
    })

    # По видам ДТП
    rows.append({
        "Показатель": "ПО ВИДАМ ДТП",
        current_label: "",
        previous_label: "",
        "Изменение, %": "",
        "Изменение, абс.": "",
    })

    cur_type = comparison["by_type"]["current"]
    prev_type = comparison["by_type"]["previous"]

    all_types = sorted(set(list(cur_type.keys()) + list(prev_type.keys())))
    sorted_types = sorted(all_types, key=lambda x: cur_type.get(x, 0) + prev_type.get(x, 0), reverse=True)

    for tp_name in sorted_types:
        cur = cur_type.get(tp_name, 0)
        prv = prev_type.get(tp_name, 0)
        if prv > 0:
            change = round((cur - prv) / prv * 100, 1)
        else:
            change = 0
        rows.append({
            "Показатель": tp_name,
            current_label: cur,
            previous_label: prv,
            "Изменение, %": change,
            "Изменение, абс.": cur - prv,
        })

    # Пустая строка-разделитель
    rows.append({
        "Показатель": "",
        current_label: "",
        previous_label: "",
        "Изменение, %": "",
        "Изменение, абс.": "",
    })

    # По погодным условиям
    rows.append({
        "Показатель": "ПО ПОГОДНЫМ УСЛОВИЯМ",
        current_label: "",
        previous_label: "",
        "Изменение, %": "",
        "Изменение, абс.": "",
    })

    cur_weather = comparison["by_weather"]["current"]
    prev_weather = comparison["by_weather"]["previous"]

    all_weather = sorted(set(list(cur_weather.keys()) + list(prev_weather.keys())))
    sorted_weather = sorted(all_weather, key=lambda x: cur_weather.get(x, 0) + prev_weather.get(x, 0), reverse=True)

    for w_name in sorted_weather:
        cur = cur_weather.get(w_name, 0)
        prv = prev_weather.get(w_name, 0)
        if prv > 0:
            change = round((cur - prv) / prv * 100, 1)
        else:
            change = 0
        rows.append({
            "Показатель": w_name,
            current_label: cur,
            previous_label: prv,
            "Изменение, %": change,
            "Изменение, абс.": cur - prv,
        })

    return rows


def get_analytics_column_names(
    current_label: str,
    previous_label: str,
) -> list[str]:
    """Возвращает названия колонок для Excel-файла аналитики."""
    return ["Показатель", current_label, previous_label, "Изменение, %", "Изменение, абс."]


# ============================================================
# Извлечение детальных данных из сырых карточек для LLM
# ============================================================

def _get_card_alcohol_detail(card: dict) -> str | None:
    """Извлекает детали по алкоголю из карточки."""
    ts_list = card.get("ts_info", []) or []
    for ts in ts_list:
        ts_uch_list = ts.get("ts_uch", []) or []
        for uch in ts_uch_list:
            kt = str(uch.get("kt_uch", "")).lower()
            alco = str(uch.get("alco", "")).strip()
            if kt == "водитель" and alco and alco not in ("0", "00", ""):
                return alco
    return None


def _get_card_violations(card: dict) -> list[str]:
    """Извлекает нарушения ПДД из карточки."""
    violations = []
    for ts in (card.get("ts_info", []) or []):
        for uch in (ts.get("ts_uch", []) or []):
            npdd_list = uch.get("npdd", []) or []
            if isinstance(npdd_list, list):
                violations.extend(str(v).strip() for v in npdd_list if str(v).strip())
    for uch in (card.get("uch_info", []) or []):
        npdd_list = uch.get("npdd", []) or []
        if isinstance(npdd_list, list):
            violations.extend(str(v).strip() for v in npdd_list if str(v).strip())
    return violations


def _get_card_vehicles(card: dict) -> list[str]:
    """Извлекает типы ТС из карточки."""
    vehicles = []
    for ts in (card.get("ts_info", []) or []):
        t_ts = str(ts.get("t_ts", "")).strip()
        if t_ts:
            vehicles.append(t_ts)
    return vehicles


def _get_card_road_state(card: dict) -> list[str]:
    """Извлекает состояние дороги из карточки."""
    states = []
    dor_usl = card.get("dor_usl", {}) or {}
    sdor = dor_usl.get("sdor", []) or []
    if isinstance(sdor, list):
        states.extend(str(s).strip() for s in sdor if str(s).strip())
    return states


def extract_raw_supplement(
    cards: list[dict[str, Any]],
    label: str,
    max_cards: int = 50,
) -> str:
    """
    Извлекает из сырых карточек дополнительные данные,
    которых нет в базовой агрегации.

    Включает:
      - Типы ТС (статистика)
      - Нарушения ПДД (статистика, топ-15)
      - Состояние дороги (статистика)
      - Районы/населённые пункты (топ-15)
      - Детали смертельных ДТП
      - Детали ДТП с нетрезвыми
      - Детали ДТП с пешеходами
      - Контрольные суммы по агрегации

    Args:
        cards: Список сырых карточек ДТП
        label: Подпись периода (например "I квартал 2026")
        max_cards: Максимум карточек в деталях (срез по тяжести)

    Returns:
        Текстовый блок для добавления в промпт LLM
    """
    if not cards:
        return f"\nПОДРОБНЫЕ ДАННЫЕ ({label}): нет данных\n"

    lines = []
    lines.append(f"\nПОДРОБНЫЕ ДАННЫЕ ({label}):")

    # --- Типы ТС ---
    vehicle_counter = Counter()
    for card in cards:
        for v in _get_card_vehicles(card):
            vehicle_counter[v] += 1
    if vehicle_counter:
        lines.append("\nТипы транспортных средств:")
        for v, cnt in vehicle_counter.most_common(12):
            lines.append(f"  - {v}: {cnt}")

    # --- Нарушения ПДД ---
    violation_counter = Counter()
    for card in cards:
        for v in _get_card_violations(card):
            violation_counter[v] += 1
    if violation_counter:
        lines.append("\nНарушения ПДД (топ-15):")
        for v, cnt in violation_counter.most_common(15):
            lines.append(f"  - {v}: {cnt}")

    # --- Состояние дороги ---
    road_counter = Counter()
    for card in cards:
        for r in _get_card_road_state(card):
            road_counter[r] += 1
    if road_counter:
        lines.append("\nСостояние дорожного покрытия:")
        for r, cnt in road_counter.most_common(10):
            lines.append(f"  - {r}: {cnt}")

    # --- Районы / населённые пункты ---
    district_counter = Counter()
    for card in cards:
        d = str(card.get("district", "")).strip()
        np_val = str(card.get("np", "")).strip()
        loc = d if d else np_val
        if loc:
            district_counter[loc] += 1
    if district_counter:
        lines.append("\nРайоны/населённые пункты (топ-15):")
        for d, cnt in district_counter.most_common(15):
            lines.append(f"  - {d}: {cnt}")

    # --- Детали по категории: смертельные, алкоголь, пешеходы ---
    fatal_cards = [c for c in cards if _safe_int(c.get("pog")) > 0]
    alcohol_cards = [c for c in cards if _has_alcohol(c)]
    ped_cards = [c for c in cards if _has_pedestrian(c)]

    # Собираем уникальные ID, чтобы не дублировать
    detailed_ids = set()
    detailed_cards = []

    # Приоритет: смертельные + алкоголь > смертельные > алкоголь > пешеходные
    priority_cards = []
    for c in fatal_cards:
        if c.get("empt_number") not in detailed_ids:
            detailed_ids.add(c.get("empt_number"))
            priority_cards.append((c, 0 if _has_alcohol(c) else 1))
    for c in alcohol_cards:
        if c.get("empt_number") not in detailed_ids:
            detailed_ids.add(c.get("empt_number"))
            priority_cards.append((c, 2))
    for c in ped_cards:
        if c.get("empt_number") not in detailed_ids:
            detailed_ids.add(c.get("empt_number"))
            priority_cards.append((c, 3))

    # Сортируем по приоритету и берём max_cards
    priority_cards.sort(key=lambda x: x[1])
    detailed_cards = [c for c, _ in priority_cards[:max_cards]]

    if detailed_cards:
        lines.append(f"\nДетали ДТП (смертельные/алкогольные/с пешеходами, {len(detailed_cards)} шт.):")
        for card in detailed_cards:
            date = str(card.get("date_dtp", "")).strip()
            time = str(card.get("time", "")).strip()
            dtp_type = str(card.get("dtpv", "")).strip()
            deaths = _safe_int(card.get("pog"))
            injured = _safe_int(card.get("ran"))
            district = str(card.get("district", "")).strip() or str(card.get("np", "")).strip()
            street = str(card.get("street", "")).strip()
            viol = ", ".join(_get_card_violations(card)[:3])
            alco = _get_card_alcohol_detail(card)
            vehicles = ", ".join(_get_card_vehicles(card)[:3])
            road = ", ".join(_get_card_road_state(card)[:2])

            tags = []
            if deaths > 0:
                tags.append(f"погибло={deaths}")
            if injured > 0:
                tags.append(f"ранено={injured}")
            if alco:
                tags.append(f"алкоголь={alco} промилле")
            if _has_pedestrian(card):
                tags.append("пешеход")

            location = district
            if street:
                location = f"{location}, {street}" if location else street

            line = f"  [{date} {time}] {dtp_type} | {'; '.join(tags)}"
            if location:
                line += f" | {location}"
            if viol:
                line += f" | нарушение: {viol}"
            if vehicles:
                line += f" | ТС: {vehicles}"
            if road:
                line += f" | дорога: {road}"
            lines.append(line)

    text = "\n".join(lines)
    logger.info(f"Raw supplement для LLM ({label}): {len(cards)} карточек, {len(text)} символов")
    return text


# ========================
# Кросс-таблицы для расширенного промпта (бесплатный метод)
# ========================

def _get_all_participants(card: dict) -> list[dict]:
    """
    Извлекает всех участников ДТП с привязкой к данным ТС.
    Возвращает список словарей:
      {kt_uch, s_t, npdd, sop_npdd, v_st, alco, safety_belt, pol, t_ts, vehicle}
    """
    participants = []
    ts_list = card.get("ts_info", []) or []
    for vehicle in ts_list:
        ts_uch_list = vehicle.get("ts_uch", []) or []
        for uch in ts_uch_list:
            participants.append({
                "kt_uch": str(uch.get("kt_uch", "")).strip(),
                "s_t": str(uch.get("s_t", "")).strip(),
                "npdd": uch.get("npdd") or [],
                "sop_npdd": uch.get("sop_npdd") or [],
                "v_st": str(uch.get("v_st", "")).strip(),
                "alco": str(uch.get("alco", "")).strip(),
                "safety_belt": str(uch.get("safety_belt", "")).strip(),
                "pol": str(uch.get("pol", "")).strip(),
                "t_ts": str(vehicle.get("t_ts", "")).strip(),
                "vehicle": vehicle,
            })
    uch_list = card.get("uch_info", []) or []
    for uch in uch_list:
        participants.append({
            "kt_uch": str(uch.get("kt_uch", "")).strip(),
            "s_t": str(uch.get("s_t", "")).strip(),
            "npdd": uch.get("npdd") or [],
            "sop_npdd": uch.get("sop_npdd") or [],
            "v_st": "",
            "alco": "",
            "safety_belt": "",
            "pol": str(uch.get("pol", "")).strip(),
            "t_ts": "",
            "vehicle": None,
        })
    return participants


def _classify_experience(st_val: str) -> str:
    """Группирует стаж водителя: 0-2, 3-5, 6-10, 10+, нет данных."""
    if not st_val or st_val in ("", "95", "96", "0"):
        return "не указан"
    try:
        years = int(float(st_val))
        if years <= 2:
            return "0-2 года"
        elif years <= 5:
            return "3-5 лет"
        elif years <= 10:
            return "6-10 лет"
        else:
            return "10+ лет"
    except (ValueError, TypeError):
        return "не указан"


def _classify_hour_3(h: int | None) -> str:
    """Группирует часы в 3-часовые интервалы."""
    if h is None:
        return "не указано"
    start = (h // 3) * 3
    end = start + 2
    return f"{start:02d}:00-{end:02d}:59"


def _is_alcohol_dtp(card: dict) -> bool:
    """Есть ли в ДТП нетрезвый водитель."""
    return _has_alcohol(card)


def _has_pedestrian_dtp(card: dict) -> bool:
    """Есть ли в ДТП пешеход."""
    return _has_pedestrian(card)


def _get_violations(uch: dict) -> list[str]:
    """Извлекает все нарушения ПДД (непосредственные + сопутствующие)."""
    result = []
    for v in (uch.get("npdd") or []):
        v_str = str(v).strip()
        if v_str and v_str != "Нет нарушений":
            result.append(v_str)
    for v in (uch.get("sop_npdd") or []):
        v_str = str(v).strip()
        if v_str and v_str != "Нет нарушений":
            result.append(v_str)
    return result


def calculate_cross_tables(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Строит кросс-таблицы по карточкам ДТП для расширенного анализа.

    Возвращает словарь с кросс-таблицами:
      - hour_x_severity: {interval: {dtp, deaths, injured}}
      - weekday_x_severity: {day_name: {dtp, deaths, injured}}
      - experience_x_severity: {group: {participants, deaths, injured, unhurt}}
      - experience_x_violations: {group: Counter(violation)}
      - vehicle_type_x_severity: {type: {participants, deaths, injured, unhurt}}
      - road_value_x_severity: {value: {dtp, deaths, injured}}
      - weather_x_dtp_type: {weather: Counter(dtp_type)}
      - lighting_x_pedestrian_share: {lighting: {dtp_with_ped, total_dtp}}
      - belt_x_severity: {belt_status: {participants, deaths, injured, unhurt}}
      - alcohol_x_weekday: {"да"/"нет": Counter(weekday_num)}
      - alcohol_x_hour: {"да"/"нет": Counter(hour_interval)}
      - gender_x_severity: {gender: {participants, deaths, injured, unhurt}}
      - participant_category_x_severity: {category: {participants, deaths, injured, unhurt}}
      - dtp_type_x_severity: {type: {dtp, deaths, injured}}
      - road_value_x_dtp_type: {road_value: Counter(dtp_type)}
      - weather_x_severity: {weather: {dtp, deaths, injured}}
      - lighting_x_severity: {lighting: {dtp, deaths, injured}}
      - month_x_severity: {month: {dtp, deaths, injured}}
      - district_x_severity: {district_name: {dtp, deaths, injured}}
      - road_name_x_severity: {road_name: {dtp, deaths, injured, road_value}}
        (road_value — каноническая категория: Федеральные/Региональные/...)
      - dtp_type_x_district: {district: Counter(dtp_type)} — вид ДТП по районам
      - dtp_type_x_hour: {hour_interval: Counter(dtp_type)} — вид ДТП по времени суток
      - dtp_type_x_road_value: {road_category: Counter(dtp_type)} — вид ДТП по категории дороги
      - alcohol_x_district: {district: Counter("да"/"нет")} — опьянение по районам
      - alcohol_x_road_value: {road_category: Counter("да"/"нет")} — опьянение по категориям дорог
      - street_x_severity: {street_name: {dtp, deaths, injured}} — топ-15 улиц (k_ul/street + np)

      Этап 1 «БДД-экспертиза» (по dor_usl):
      - ndu_x_severity: {ndu_item: {dtp, deaths, injured}} — недостатки УДС
      - objects_addr_x_severity: {obj_dtp_item: {dtp, deaths, injured}} — объекты УДС вблизи
      - s_pch_x_severity: {s_pch: {dtp, deaths, injured}} — состояние проезжей части
      - factor_x_severity: {factor_item: {dtp, deaths, injured}} — факторы режима движения

      Этап 2 «Профиль ТС»:
      - vehicles_count_x_severity: {bucket: {dtp, deaths, injured}} — 1/2/3/4+ ТС
      - vehicle_brand_x_severity: {brand: {dtp, deaths, injured}} — марка ТС (marka_ts → m_ts)
      - vehicle_age_x_severity: {age_bucket: {dtp, deaths, injured}} — 0-3/4-7/8-12/13-20/20+/—
    """
    # Инициализация всех таблиц
    hour_x_severity: dict[str, dict] = {}
    weekday_x_severity: dict[str, dict] = {}
    experience_x_severity: dict[str, dict] = {}
    experience_x_violations: dict[str, Counter] = {}
    vehicle_type_x_severity: dict[str, dict] = {}
    road_value_x_severity: dict[str, dict] = {}
    weather_x_dtp_type: dict[str, Counter] = {}
    lighting_x_pedestrian_share: dict[str, dict] = {}
    belt_x_severity: dict[str, dict] = {}
    alcohol_x_weekday: dict[str, Counter] = {}
    alcohol_x_hour: dict[str, Counter] = {}
    gender_x_severity: dict[str, dict] = {}
    participant_category_x_severity: dict[str, dict] = {}
    dtp_type_x_severity: dict[str, dict] = {}
    road_value_x_dtp_type: dict[str, Counter] = {}
    weather_x_severity: dict[str, dict] = {}
    lighting_x_severity: dict[str, dict] = {}
    month_x_severity: dict[str, dict] = {}
    # Районы (district) и наименования дорог (dor) с категорией (dor_z)
    district_x_severity: dict[str, dict] = {}
    road_name_x_severity: dict[str, dict] = {}
    # Этап 1: новые кросс-таблицы
    # Вид ДТП × Район / Час / Категория дороги — Counter {key: Counter(dtp_type)}
    dtp_type_x_district: dict[str, Counter] = {}
    dtp_type_x_hour: dict[str, Counter] = {}
    dtp_type_x_road_value: dict[str, Counter] = {}
    # Опьянение × Район / Категория дороги — Counter {key: Counter("да"/"нет")}
    alcohol_x_district: dict[str, Counter] = {}
    alcohol_x_road_value: dict[str, Counter] = {}
    # Улица × тяжесть (топ-15) — берём из k_ul или street
    street_x_severity: dict[str, dict] = {}

    # Этап 1 «БДД-экспертиза» — все четыре поля из dor_usl.
    # Шаблон тот же, что у weather_x_severity: для каждого элемента списка
    # добавляем {dtp, deaths, injured} (одно ДТП может попасть в несколько
    # категорий, если в карточке указано несколько недостатков/факторов).
    ndu_x_severity: dict[str, dict] = {}
    objects_addr_x_severity: dict[str, dict] = {}
    s_pch_x_severity: dict[str, dict] = {}
    factor_x_severity: dict[str, dict] = {}

    # Этап 2 «Профиль ТС»
    # B1 — количество ТС в ДТП (k_ts): бакеты 1/2/3/4+.
    vehicles_count_x_severity: dict[str, dict] = {}
    # B2 — марка ТС (marka_ts, fallback m_ts): по ДТП, не по участникам.
    # Если в ДТП 2 ТС одной марки — считаем ДТП один раз для этой марки.
    vehicle_brand_x_severity: dict[str, dict] = {}
    # B3 — возраст ТС = год ДТП − g_v. Бакеты 0-3/4-7/8-12/13-20/20+/—.
    vehicle_age_x_severity: dict[str, dict] = {}

    def _add_severity(table: dict, key: str, deaths: int, injured: int, count: int = 1):
        if key not in table:
            table[key] = {"dtp": 0, "deaths": 0, "injured": 0}
        table[key]["dtp"] += count
        table[key]["deaths"] += deaths
        table[key]["injured"] += injured

    def _add_part_severity(table: dict, key: str, severity: str, count: int = 1):
        if key not in table:
            table[key] = {"participants": 0, "deaths": 0, "injured": 0, "unhurt": 0}
        table[key]["participants"] += count
        s_lower = severity.lower()
        if s_lower in ("погиб", "гибель"):
            table[key]["deaths"] += count
        elif s_lower in ("ранен", "ранение"):
            table[key]["injured"] += count
        else:
            table[key]["unhurt"] += count

    for card in cards:
        deaths = _safe_int(card.get("pog"))
        injured = _safe_int(card.get("ran"))
        dtp_type = str(card.get("dtpv", "")).strip()
        hour = _get_hour(str(card.get("time", "")))
        hour_interval = _classify_hour_3(hour)
        wd_num = _get_weekday(str(card.get("date_dtp", "")))
        wd_name = DAY_NAMES.get(wd_num, str(wd_num)) if wd_num is not None else "не указан"
        has_ped = _has_pedestrian_dtp(card)
        has_alc = _is_alcohol_dtp(card)
        _alc_key = "да" if has_alc else "нет"

        # Погода
        dor_usl = card.get("dor_usl", {}) or {}
        weather_list = dor_usl.get("spog", []) or []
        weather_str = "; ".join(str(w).strip() for w in weather_list if str(w).strip()) or "не указана"

        # Освещение
        lighting = str(dor_usl.get("osv", "")).strip() or "не указано"

        # Значение дороги
        road_value = str(card.get("dor_z", "")).strip() or "не указано"

        # Месяц
        date_str = str(card.get("date_dtp", "")).strip()
        month_str = "не указан"
        if date_str:
            try:
                from datetime import datetime
                month_num = datetime.strptime(date_str[:10], "%d.%m.%Y").month
                month_names_ru = {
                    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
                    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
                    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
                }
                month_str = month_names_ru.get(month_num, str(month_num))
            except (ValueError, IndexError):
                pass

        # 1. Час × тяжесть
        _add_severity(hour_x_severity, hour_interval, deaths, injured)

        # 2. День недели × тяжесть
        _add_severity(weekday_x_severity, wd_name, deaths, injured)

        # 13. Вид ДТП × тяжесть
        if dtp_type:
            _add_severity(dtp_type_x_severity, dtp_type, deaths, injured)

        # 6. Значение дороги × тяжесть
        _add_severity(road_value_x_severity, road_value, deaths, injured)

        # 15. Погода × тяжесть
        for w in weather_list:
            w_str = str(w).strip()
            if w_str:
                _add_severity(weather_x_severity, w_str, deaths, injured)

        # 16. Освещение × тяжесть
        _add_severity(lighting_x_severity, lighting, deaths, injured)

        # 17. Месяц × тяжесть
        _add_severity(month_x_severity, month_str, deaths, injured)

        # 18. Район × тяжесть
        # Берём поле district, при отсутствии — наименование населённого
        # пункта (np). Это позволяет отвечать на вопросы про наиболее
        # аварийные районы/города региона.
        np_name = str(card.get("np", "")).strip()
        district_name = str(card.get("district", "")).strip()
        if not district_name:
            district_name = np_name
        if district_name:
            _add_severity(district_x_severity, district_name, deaths, injured)

        # 19. Наименование дороги × тяжесть (с категорией)
        # Поле dor — наименование (например «М-4 Дон»), dor_z — значение
        # (категория). Сохраняем категорию в road_value поле bucket'а,
        # чтобы в промпте показывать «М-4 Дон (Федеральные)».
        road_name = str(card.get("dor", "")).strip()
        if road_name:
            road_sig_canonical = group_road_significance(
                str(card.get("dor_z", "")).strip()
            )
            bucket = road_name_x_severity.get(road_name)
            if bucket is None:
                bucket = {
                    "dtp": 0, "deaths": 0, "injured": 0,
                    "road_value": road_sig_canonical,
                }
                road_name_x_severity[road_name] = bucket
            bucket["dtp"] += 1
            bucket["deaths"] += deaths
            bucket["injured"] += injured

        # 20. Вид ДТП × Район (для адресных мер: где какой тип преобладает)
        if dtp_type and district_name:
            dtp_type_x_district.setdefault(district_name, Counter())[dtp_type] += 1

        # 21. Вид ДТП × Время суток (типичные часы для каждого вида ДТП)
        if dtp_type:
            dtp_type_x_hour.setdefault(hour_interval, Counter())[dtp_type] += 1

        # 22. Вид ДТП × Категория дороги (каноническая: Федеральные/Региональные/...)
        if dtp_type:
            road_sig_canonical_for_dtp = group_road_significance(
                str(card.get("dor_z", "")).strip()
            )
            dtp_type_x_road_value.setdefault(road_sig_canonical_for_dtp, Counter())[dtp_type] += 1

        # 23. Опьянение × Район (где чаще пьяные ДТП)
        if district_name:
            alcohol_x_district.setdefault(district_name, Counter())[_alc_key] += 1

        # 24. Опьянение × Категория дороги (на каких дорогах пьяные)
        road_sig_canonical_for_alc = group_road_significance(
            str(card.get("dor_z", "")).strip()
        )
        alcohol_x_road_value.setdefault(road_sig_canonical_for_alc, Counter())[_alc_key] += 1

        # 25. Улица × тяжесть (топ-15 улиц — для городского анализа очагов)
        # Берём k_ul (улица) или street, объединяем с np при наличии,
        # чтобы различать «ул. Ленина» в разных населённых пунктах.
        street_name = str(card.get("k_ul", "")).strip()
        if not street_name:
            street_name = str(card.get("street", "")).strip()
        if street_name and np_name:
            street_name = f"{street_name} ({np_name})"
        if street_name:
            _add_severity(street_x_severity, street_name, deaths, injured)

        # 26. Этап 1: Недостатки УДС × тяжесть (ndu — список строк)
        # Шаблон полностью повторяет weather_x_severity.
        ndu_list = dor_usl.get("ndu", []) or []
        if isinstance(ndu_list, list):
            for ndu_item in ndu_list:
                ndu_str = str(ndu_item).strip()
                if ndu_str:
                    _add_severity(ndu_x_severity, ndu_str, deaths, injured)
        else:
            ndu_str = str(ndu_list).strip()
            if ndu_str:
                _add_severity(ndu_x_severity, ndu_str, deaths, injured)

        # 27. Этап 1: Объекты УДС вблизи × тяжесть (obj_dtp — список строк)
        obj_dtp_list = dor_usl.get("obj_dtp", []) or []
        if isinstance(obj_dtp_list, list):
            for obj_item in obj_dtp_list:
                obj_str = str(obj_item).strip()
                if obj_str:
                    _add_severity(objects_addr_x_severity, obj_str, deaths, injured)
        else:
            obj_str = str(obj_dtp_list).strip()
            if obj_str:
                _add_severity(objects_addr_x_severity, obj_str, deaths, injured)

        # 28. Этап 1: Состояние проезжей части × тяжесть (s_pch — строка)
        s_pch_val = str(dor_usl.get("s_pch", "")).strip()
        if s_pch_val:
            _add_severity(s_pch_x_severity, s_pch_val, deaths, injured)

        # 29. Этап 1: Факторы режима движения × тяжесть (factor — список строк)
        factor_list = dor_usl.get("factor", []) or []
        if isinstance(factor_list, list):
            for f_item in factor_list:
                f_str = str(f_item).strip()
                if f_str:
                    _add_severity(factor_x_severity, f_str, deaths, injured)
        else:
            f_str = str(factor_list).strip()
            if f_str:
                _add_severity(factor_x_severity, f_str, deaths, injured)

        # 30. Этап 2: Количество ТС × тяжесть (k_ts → бакеты 1/2/3/4+)
        k_ts_val = _safe_int(card.get("k_ts"))
        if k_ts_val <= 0:
            # Если k_ts не указан — берём длину ts_info как fallback
            k_ts_val = len(card.get("ts_info", []) or [])
        if k_ts_val <= 0:
            k_ts_bucket = "не указано"
        elif k_ts_val == 1:
            k_ts_bucket = "1 ТС"
        elif k_ts_val == 2:
            k_ts_bucket = "2 ТС"
        elif k_ts_val == 3:
            k_ts_bucket = "3 ТС"
        else:
            k_ts_bucket = "4+ ТС"
        _add_severity(vehicles_count_x_severity, k_ts_bucket, deaths, injured)

        # 31-32. Этап 2: Марка и возраст ТС — собираем уникальные значения
        # по ts_info (одно ДТП учитывается один раз для каждой уникальной
        # марки/возрастной группы, представленной среди ТС этого ДТП).
        ts_list_for_profile = card.get("ts_info", []) or []

        # 31. Марка ТС × тяжесть
        # Приоритет: marka_ts (марка — производитель), fallback — m_ts (модель).
        unique_brands: set[str] = set()
        for ts in ts_list_for_profile:
            brand = str(ts.get("marka_ts", "")).strip()
            if not brand:
                brand = str(ts.get("m_ts", "")).strip()
            if brand:
                # Нормализуем: режем лишние пробелы и приводим к нижнему регистру
                # только для дедупликации (в таблице сохраняем оригинальный регистр).
                brand_norm = " ".join(brand.split())
                unique_brands.add(brand_norm)
        for brand in unique_brands:
            _add_severity(vehicle_brand_x_severity, brand, deaths, injured)

        # 32. Возраст ТС × тяжесть
        # Год ДТП берём из date_dtp (формат DD.MM.YYYY).
        dtp_year: int | None = None
        if date_str:
            try:
                from datetime import datetime
                dtp_year = datetime.strptime(date_str[:10], "%d.%m.%Y").year
            except (ValueError, IndexError):
                dtp_year = None

        unique_age_buckets: set[str] = set()
        for ts in ts_list_for_profile:
            g_v_str = str(ts.get("g_v", "")).strip()
            if not g_v_str or not dtp_year:
                continue
            try:
                g_v = int(float(g_v_str))
            except (ValueError, TypeError):
                continue
            if g_v <= 1900 or g_v > dtp_year + 1:
                # Явно некорректный год выпуска — пропускаем
                continue
            age = dtp_year - g_v
            if age < 0:
                age = 0
            if age <= 3:
                unique_age_buckets.add("0-3 года")
            elif age <= 7:
                unique_age_buckets.add("4-7 лет")
            elif age <= 12:
                unique_age_buckets.add("8-12 лет")
            elif age <= 20:
                unique_age_buckets.add("13-20 лет")
            else:
                unique_age_buckets.add("старше 20 лет")
        if not unique_age_buckets and ts_list_for_profile:
            # Были ТС, но ни одного валидного g_v — помечаем как «не указан»
            unique_age_buckets.add("не указан")
        for bucket in unique_age_buckets:
            _add_severity(vehicle_age_x_severity, bucket, deaths, injured)

        # 7. Погода × вид ДТП
        for w in weather_list:
            w_str = str(w).strip()
            if w_str and dtp_type:
                if w_str not in weather_x_dtp_type:
                    weather_x_dtp_type[w_str] = Counter()
                weather_x_dtp_type[w_str][dtp_type] += 1

        # 8. Освещение × доля пешеходов
        _light_key = lighting
        if _light_key not in lighting_x_pedestrian_share:
            lighting_x_pedestrian_share[_light_key] = {"dtp_with_ped": 0, "total_dtp": 0}
        lighting_x_pedestrian_share[_light_key]["total_dtp"] += 1
        if has_ped:
            lighting_x_pedestrian_share[_light_key]["dtp_with_ped"] += 1

        # 14. Значение дороги × вид ДТП
        if dtp_type:
            if road_value not in road_value_x_dtp_type:
                road_value_x_dtp_type[road_value] = Counter()
            road_value_x_dtp_type[road_value][dtp_type] += 1

        # 10. Опьянение × день недели
        # _alc_key уже вычислен выше (рядом с has_alc) — используется и в новых
        # таблицах 23-24, и здесь, и в 11.
        if _alc_key not in alcohol_x_weekday:
            alcohol_x_weekday[_alc_key] = Counter()
        if wd_num is not None:
            alcohol_x_weekday[_alc_key][wd_num] += 1

        # 11. Опьянение × час
        if _alc_key not in alcohol_x_hour:
            alcohol_x_hour[_alc_key] = Counter()
        alcohol_x_hour[_alc_key][hour_interval] += 1

        # Обработка участников
        all_parts = _get_all_participants(card)
        for uch in all_parts:
            kt = uch["kt_uch"].lower()
            s_t = uch["s_t"]
            pol = uch["pol"].strip() or "не указан"
            v_st = uch["v_st"]
            t_ts = uch["t_ts"].strip()
            belt = uch["safety_belt"].strip() or "не указан"
            exp_group = _classify_experience(v_st)
            violations = _get_violations(uch)

            # 3. Стаж × тяжесть
            if exp_group != "не указан" and kt == "водитель":
                _add_part_severity(experience_x_severity, exp_group, s_t)

            # 4. Стаж × нарушения
            if exp_group != "не указан" and kt == "водитель":
                if exp_group not in experience_x_violations:
                    experience_x_violations[exp_group] = Counter()
                for v in violations:
                    experience_x_violations[exp_group][v] += 1

            # 5. Тип ТС × тяжесть
            if t_ts:
                _add_part_severity(vehicle_type_x_severity, t_ts, s_t)

            # 9. Ремень × тяжесть
            if uch["vehicle"] is not None:  # только в ТС
                _add_part_severity(belt_x_severity, belt, s_t)

            # 12. Пол × тяжесть
            if pol != "не указан":
                _add_part_severity(gender_x_severity, pol, s_t)

            # Категория участника × тяжесть
            if kt:
                _add_part_severity(participant_category_x_severity, kt, s_t)

    return {
        "hour_x_severity": hour_x_severity,
        "weekday_x_severity": weekday_x_severity,
        "experience_x_severity": experience_x_severity,
        "experience_x_violations": experience_x_violations,
        "vehicle_type_x_severity": vehicle_type_x_severity,
        "road_value_x_severity": road_value_x_severity,
        "weather_x_dtp_type": weather_x_dtp_type,
        "lighting_x_pedestrian_share": lighting_x_pedestrian_share,
        "belt_x_severity": belt_x_severity,
        "alcohol_x_weekday": alcohol_x_weekday,
        "alcohol_x_hour": alcohol_x_hour,
        "gender_x_severity": gender_x_severity,
        "participant_category_x_severity": participant_category_x_severity,
        "dtp_type_x_severity": dtp_type_x_severity,
        "road_value_x_dtp_type": road_value_x_dtp_type,
        "weather_x_severity": weather_x_severity,
        "lighting_x_severity": lighting_x_severity,
        "month_x_severity": month_x_severity,
        "district_x_severity": district_x_severity,
        "road_name_x_severity": road_name_x_severity,
        # Этап 1: новые кросс-таблицы
        "dtp_type_x_district": dtp_type_x_district,
        "dtp_type_x_hour": dtp_type_x_hour,
        "dtp_type_x_road_value": dtp_type_x_road_value,
        "alcohol_x_district": alcohol_x_district,
        "alcohol_x_road_value": alcohol_x_road_value,
        "street_x_severity": street_x_severity,
        # Этап 1 «БДД-экспертиза»
        "ndu_x_severity": ndu_x_severity,
        "objects_addr_x_severity": objects_addr_x_severity,
        "s_pch_x_severity": s_pch_x_severity,
        "factor_x_severity": factor_x_severity,
        # Этап 2 «Профиль ТС»
        "vehicles_count_x_severity": vehicles_count_x_severity,
        "vehicle_brand_x_severity": vehicle_brand_x_severity,
        "vehicle_age_x_severity": vehicle_age_x_severity,
    }


# ============================================================
# Этап 2: Производные статистические метрики
# ============================================================

# Критические значения χ² при α=0.05 для df=1..20.
# Взято из стандартных таблиц (Pearson chi-square distribution).
# Используем хардкод, чтобы не тянуть scipy в продакшен.
_CHI2_CRITICAL_005 = {
    1: 3.841, 2: 5.991, 3: 7.815, 4: 9.488, 5: 11.070,
    6: 12.592, 7: 14.067, 8: 15.507, 9: 16.919, 10: 18.307,
    11: 19.675, 12: 21.026, 13: 22.362, 14: 23.685, 15: 24.996,
    16: 26.296, 17: 27.587, 18: 28.869, 19: 30.144, 20: 31.410,
}


def _mean_std(values: list[float]) -> tuple[float, float]:
    """Среднее и стандартное отклонение (population, не sample).

    Используем population std (деление на n, а не на n-1), так как
    кросс-таблица содержит данные по всем ДТП за период — это не выборка,
    а полная генеральная совокупность.
    """
    if not values:
        return 0.0, 0.0
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return mean, var ** 0.5


def _z_score(x: float, mean: float, std: float) -> float:
    """Z-score. При std=0 возвращает 0 (нет вариации → нет аномалии)."""
    if std == 0:
        return 0.0
    return (x - mean) / std


def _classify_z(z: float) -> str:
    """Классифицирует Z-score по уровням значимости."""
    az = abs(z)
    if az >= 2.0:
        return "значимо выше" if z > 0 else "значимо ниже"
    if az >= 1.5:
        return "выше среднего" if z > 0 else "ниже среднего"
    return "около среднего"


def _severity_rate(deaths: int, dtp: int) -> float:
    """Погибших на 100 ДТП. При dtp=0 возвращает 0.0."""
    if dtp <= 0:
        return 0.0
    return round(deaths / dtp * 100, 1)


def _build_severity_rates(
    table: dict[str, dict], slice_name: str,
) -> list[dict[str, Any]]:
    """Строит список {key, dtp, deaths, injured, deaths_per_100, injured_per_100}
    отсортированный по убыванию deaths_per_100.
    """
    rows = []
    for key, b in table.items():
        dtp = b.get("dtp", 0)
        deaths = b.get("deaths", 0)
        injured = b.get("injured", 0)
        rows.append({
            "key": key,
            "dtp": dtp,
            "deaths": deaths,
            "injured": injured,
            "deaths_per_100": _severity_rate(deaths, dtp),
            "injured_per_100": _severity_rate(injured, dtp),
        })
    rows.sort(key=lambda r: r["deaths_per_100"], reverse=True)
    return rows


def _build_z_anomalies(
    table: dict[str, dict], slice_name: str, min_dtp: int = 3,
) -> list[dict[str, Any]]:
    """Считает Z-score для каждого ключа таблицы по двум метрикам:
    числу ДТП и fatal rate (погибших на 100 ДТП).

    Фильтрует ключи с dtp < min_dtp — на маленькой выборке
    Z-score нестабилен и неинтерпретируем.

    Возвращает список словарей {key, dtp, deaths_per_100, z_dtp, z_fatality, label_dtp, label_fatality}
    отсортированный по убыванию |z_fatality| (наиболее аномальные по тяжести — сверху).
    """
    # Фильтруем по min_dtp
    keys_data = [(k, b) for k, b in table.items() if b.get("dtp", 0) >= min_dtp]
    if len(keys_data) < 2:
        # Если осталось <2 ключей — Z-score бессмысленен
        return []

    dtp_values = [float(b.get("dtp", 0)) for _, b in keys_data]
    fatality_values = [
        float(_severity_rate(b.get("deaths", 0), b.get("dtp", 0)))
        for _, b in keys_data
    ]

    mean_dtp, std_dtp = _mean_std(dtp_values)
    mean_fat, std_fat = _mean_std(fatality_values)

    rows = []
    for k, b in keys_data:
        dtp = b.get("dtp", 0)
        fat = _severity_rate(b.get("deaths", 0), dtp)
        z_dtp = _z_score(float(dtp), mean_dtp, std_dtp)
        z_fat = _z_score(float(fat), mean_fat, std_fat)
        rows.append({
            "key": k,
            "dtp": dtp,
            "deaths_per_100": fat,
            "z_dtp": round(z_dtp, 2),
            "z_fatality": round(z_fat, 2),
            "label_dtp": _classify_z(z_dtp),
            "label_fatality": _classify_z(z_fat),
        })
    rows.sort(key=lambda r: abs(r["z_fatality"]), reverse=True)
    return rows


def _chi_square_test(
    contingency: list[list[int]],
) -> dict[str, Any]:
    """χ²-тест независимости для матрицы наблюдаемых частот.

    Args:
        contingency: матрица [rows][cols] наблюдаемых частот.

    Returns:
        dict с chi2, df, expected, critical_005, significant.
    """
    rows = len(contingency)
    if rows < 2:
        return {"chi2": 0.0, "df": 0, "significant": False, "note": "мало строк"}
    cols = len(contingency[0])
    if cols < 2:
        return {"chi2": 0.0, "df": 0, "significant": False, "note": "мало колонок"}

    row_totals = [sum(r) for r in contingency]
    col_totals = [sum(contingency[r][c] for r in range(rows)) for c in range(cols)]
    grand_total = sum(row_totals)
    if grand_total == 0:
        return {"chi2": 0.0, "df": 0, "significant": False, "note": "пустая таблица"}

    # Ожидаемые частоты и χ²
    chi2 = 0.0
    for i in range(rows):
        for j in range(cols):
            expected = row_totals[i] * col_totals[j] / grand_total
            if expected > 0:
                chi2 += (contingency[i][j] - expected) ** 2 / expected

    df = (rows - 1) * (cols - 1)
    critical = _CHI2_CRITICAL_005.get(df)
    if critical is None:
        # Для df > 20 используем аппроксимацию: χ² ≈ df + 2*sqrt(2*df) при α=0.05
        critical = df + 2 * (2 * df) ** 0.5
    significant = chi2 > critical
    return {
        "chi2": round(chi2, 2),
        "df": df,
        "critical_005": round(critical, 2),
        "significant": significant,
        "note": "значимая связь" if significant else "связь не подтверждена",
    }


def calculate_statistical_metrics(cross: dict[str, Any]) -> dict[str, Any]:
    """Этап 2: производные статистические метрики на основе кросс-таблиц.

    Возвращает dict с тремя блоками:
      - severity_rates: {slice_name: [{key, dtp, deaths, injured, deaths_per_100, injured_per_100}]}
        — топ-5 самых "тяжёлых" разрезов по каждой таблице
      - z_score_anomalies: {slice_name: [{key, dtp, deaths_per_100, z_dtp, z_fatality, label_*}]}
        — аномалии по числу ДТП и по тяжести последствий
      - chi_square_tests: [{test_name, chi2, df, significant, note, top_cells}]
        — тесты независимости для пар факторов

    Все метрики вычисляются по текущему периоду (без сравнения с прошлым).
    """
    result: dict[str, Any] = {
        "severity_rates": {},
        "z_score_anomalies": {},
        "chi_square_tests": [],
    }

    # --- 1. Severity rates: погибших на 100 ДТП по разным срезам ---
    # Включаем новые таблицы Этапов 1-2, где бакеты достаточно укрупнены
    # (не более ~15-20 ключей), чтобы severity rates имели смысл.
    severity_slices = [
        ("Районы", "district_x_severity"),
        ("Дороги", "road_name_x_severity"),
        ("Время суток", "hour_x_severity"),
        ("Категория дороги", "road_value_x_severity"),
        ("Вид ДТП", "dtp_type_x_severity"),
        ("Месяц", "month_x_severity"),
        ("Освещение", "lighting_x_severity"),
        ("Улицы", "street_x_severity"),
        # Этап 1 «БДД-экспертиза»:
        ("Недостатки УДС", "ndu_x_severity"),
        ("Состояние покрытия", "s_pch_x_severity"),
        ("Факторы режима", "factor_x_severity"),
        # Этап 2 «Профиль ТС»:
        ("Количество ТС", "vehicles_count_x_severity"),
        ("Возраст ТС", "vehicle_age_x_severity"),
        # Марка ТС НЕ включаем — слишком много уникальных значений,
        # на каждой марке 1-5 ДТП, severity rate будет неинформативен.
    ]
    for slice_name, key in severity_slices:
        table = cross.get(key, {})
        if table:
            rates = _build_severity_rates(table, slice_name)
            # Берём топ-5 по тяжести + те, где >=3 ДТП
            top_rates = [r for r in rates if r["dtp"] >= 3][:5]
            if top_rates:
                result["severity_rates"][slice_name] = top_rates

    # --- 2. Z-score аномалии ---
    # Z-score имеет смысл только для укрупнённых бакетов — иначе на каждой
    # категории будет 1-2 ДТП и z будет огромным/неинформативным.
    anomaly_slices = [
        ("Районы", "district_x_severity"),
        ("Дороги", "road_name_x_severity"),
        ("Время суток", "hour_x_severity"),
        ("Категория дороги", "road_value_x_severity"),
        ("Вид ДТП", "dtp_type_x_severity"),
        ("Улицы", "street_x_severity"),
        # Этап 1 «БДД-экспертиза» — важнейший срез для аномалий:
        # если на «Яма на проезжей части» тяжесть аномально высокая — это
        # прямой сигнал для адресных мер.
        ("Недостатки УДС", "ndu_x_severity"),
        ("Состояние покрытия", "s_pch_x_severity"),
        ("Факторы режима", "factor_x_severity"),
        # Этап 2 «Профиль ТС» — бакеты 1/2/3/4+ и 0-3/.../20+:
        ("Количество ТС", "vehicles_count_x_severity"),
        ("Возраст ТС", "vehicle_age_x_severity"),
    ]
    for slice_name, key in anomaly_slices:
        table = cross.get(key, {})
        if table:
            anomalies = _build_z_anomalies(table, slice_name, min_dtp=3)
            # Берём топ-5 наиболее аномальных (по |z_fatality|)
            if anomalies:
                result["z_score_anomalies"][slice_name] = anomalies[:5]

    # --- 3. χ²-тесты независимости ---

    # 3a. Категория дороги × Вид ДТП
    if "dtp_type_x_road_value" in cross:
        rv_x_dt = cross["dtp_type_x_road_value"]
        if len(rv_x_dt) >= 2:
            # Соберём все виды ДТП встречающиеся хотя бы в 2 категориях
            all_dtp_types = set()
            for cnt in rv_x_dt.values():
                for t in cnt:
                    all_dtp_types.add(t)
            all_dtp_types = sorted(all_dtp_types)
            road_cats = sorted(rv_x_dt.keys())
            # Берём топ-5 самых частых видов ДТП чтобы не раздувать df
            total_per_type = Counter()
            for cnt in rv_x_dt.values():
                total_per_type.update(cnt)
            top_types = [t for t, _ in total_per_type.most_common(5)]
            contingency = [
                [rv_x_dt[rc].get(t, 0) for t in top_types]
                for rc in road_cats
            ]
            test = _chi_square_test(contingency)
            test["test_name"] = "Категория дороги × Вид ДТП"
            test["contingency_rows"] = road_cats
            test["contingency_cols"] = top_types
            result["chi_square_tests"].append(test)

    # 3b. Время суток × Опьянение
    if "alcohol_x_hour" in cross:
        axh = cross["alcohol_x_hour"]
        # axh: {"да": Counter(interval), "нет": Counter(interval)}
        alc_data = axh.get("да", Counter())
        no_alc_data = axh.get("нет", Counter())
        if alc_data and no_alc_data:
            intervals = sorted(set(list(alc_data.keys()) + list(no_alc_data.keys())))
            contingency = [
                [alc_data.get(iv, 0) for iv in intervals],
                [no_alc_data.get(iv, 0) for iv in intervals],
            ]
            test = _chi_square_test(contingency)
            test["test_name"] = "Время суток × Опьянение"
            test["contingency_rows"] = ["Опьянение", "Трезвые"]
            test["contingency_cols"] = intervals
            result["chi_square_tests"].append(test)

    # 3c. Категория дороги × Опьянение
    if "alcohol_x_road_value" in cross:
        axrv = cross["alcohol_x_road_value"]
        if len(axrv) >= 2:
            road_cats = sorted(axrv.keys())
            contingency = [
                [axrv[rc].get("да", 0), axrv[rc].get("нет", 0)]
                for rc in road_cats
            ]
            test = _chi_square_test(contingency)
            test["test_name"] = "Категория дороги × Опьянение"
            test["contingency_rows"] = road_cats
            test["contingency_cols"] = ["Опьянение", "Трезвые"]
            result["chi_square_tests"].append(test)

    # 3d. Освещение × Тяжесть (погиб/не погиб)
    if "lighting_x_severity" in cross:
        lxs = cross["lighting_x_severity"]
        if len(lxs) >= 2:
            lightings = sorted(lxs.keys())
            contingency = [
                # [погибло в ДТП с такой тяжестью, без погибших]
                # approximation: deaths>0 → есть погибшие, иначе нет
                [lxs[l].get("deaths", 0), lxs[l].get("dtp", 0) - lxs[l].get("deaths", 0)]
                for l in lightings
            ]
            test = _chi_square_test(contingency)
            test["test_name"] = "Освещение × Тяжесть (погибшие)"
            test["contingency_rows"] = lightings
            test["contingency_cols"] = ["С погибшими", "Без погибших"]
            result["chi_square_tests"].append(test)

    return result
