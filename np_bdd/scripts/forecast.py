"""
forecast.py — сезонная корректировка и расчёт runtime-метрик текущего года.

ЗАДАЧИ
======

1. Загрузить сезонные коэффициенты из data/seasonal_coefficients.json.
   Если файла нет — создать с дефолтными значениями (равномерное распределение
   1/12 на каждый месяц) и предупредить пользователя.

2. Дать две функции:
   - forecast_full_year_deaths(deaths_ytd, current_month) → int
       Прогноз погибших на конец года по сезонной корректировке.
   - build_monthly_cumulative_tr(deaths_by_month_actual,
                                  deaths_forecast_full_year,
                                  vehicles_year, plan_tr_year,
                                  plan_line_mode) → dict
       Формирует данные для графика 2 (кумулятивный Тр по месяцам):
       - фактическая часть (сплошная линия)
       - прогнозная часть (пунктир)
       - линия плана (линейный рост ИЛИ горизонтальная — по toggle)

3. Дать функцию runtime_calc(region_code) → dict, которая собирает всё
   вместе для отдачи в UI: история + текущий год + прогноз + план + KPI.

ИСПОЛЬЗОВАНИЕ
=============

    from forecast import runtime_calc
    payload = runtime_calc("67")
    # payload готов к сериализации и отправке в UI бота

АДМИНИСТРАТИВНЫЙ ИНТЕРФЕЙС
==========================

    python np_bdd/scripts/forecast.py --recalc-seasonal
        Пересчитать seasonal_coefficients.json по имеющейся истории.

    python np_bdd/scripts/forecast.py --region 67
        Напечатать runtime_calc для региона 67 (для отладки).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

# --- Относительные пути (модуль самодостаточен, не зависит от хардкода) ----
# scripts/ → родитель = np_bdd/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_HIST_DIR = PROJECT_ROOT / "datasets" / "history"
DATA_PLANS_DIR = PROJECT_ROOT / "datasets" / "plans"
DATA_VEHI_DIR = PROJECT_ROOT / "datasets" / "vehicles"
DATA_FREEZE_DIR = PROJECT_ROOT / "datasets" / "freeze"
# Per-region сезонные коэффициенты лежат в datasets/seasonal/{region_code}.json.
# Глобальный профиль (фолбэк) — в datasets/seasonal/global.json.
# Legacy-файл datasets/seasonal_coefficients.json читается, если seasonal/global.json нет.
SEASONAL_DIR = PROJECT_ROOT / "datasets" / "seasonal"
SEASONAL_GLOBAL_FILE = SEASONAL_DIR / "global.json"
SEASONAL_LEGACY_FILE = PROJECT_ROOT / "datasets" / "seasonal_coefficients.json"

# Минимальное число регион-лет с deaths_by_month для построения per-region
# сезонного профиля. Если истории меньше — используем глобальный профиль.
MIN_SAMPLES_FOR_PER_REGION = 2

TODAY = date.today()


# --- Метод прогноза --------------------------------------------------------
#
# forecast_method = "central_only"  — текущий метод: deaths_ytd / avg(cum_share).
#                                     Одна линия прогноза (без коридора).
# forecast_method = "corridor"      — центр = текущий метод, плюс две границы
#                                     (optimistic / pessimistic), построенные
#                                     через min/max кумулятивных долей по
#                                     историческим годам региона.
#
# Для "corridor" нужен per-year кумулятивный профиль (см. compute_per_year_cum_shares).
# Если per-year истории нет (только global/uniform) — коридор не строится,
# возвращаются None, центр = avg.
ForecastMethod = Literal["central_only", "corridor"]
DEFAULT_FORECAST_METHOD: ForecastMethod = "corridor"


# --- Сезонные коэффициенты (per-region с фолбэком на global) ------------------


DEFAULT_MONTHLY_SHARE = {str(m): round(1 / 12, 4) for m in range(1, 13)}


def _default_uniform_payload(reason: str) -> dict[str, Any]:
    """Возвращает дефолтный uniform-профиль (1/12 на каждый месяц)."""
    cumulative = {}
    running = 0.0
    for m in range(1, 13):
        running += DEFAULT_MONTHLY_SHARE[str(m)]
        cumulative[str(m)] = round(running, 4)
    return {
        "updated_at": TODAY.isoformat(),
        "method": f"default uniform 1/12 ({reason})",
        "monthly_share": DEFAULT_MONTHLY_SHARE,
        "cumulative_share": cumulative,
        "region_code": None,  # признак global/uniform
        "samples_used": 0,
    }


def load_seasonal_coefficients(region_code: str | None = None) -> dict[str, Any]:
    """
    Загружает сезонные коэффициенты с приоритетом:

    1. datasets/seasonal/{region_code}.json (per-region профиль).
    2. datasets/seasonal/global.json (глобальный профиль).
    3. datasets/seasonal_coefficients.json (legacy, для обратной совместимости).
    4. Дефолтный uniform 1/12 (если ничего нет).

    Args:
        region_code: код региона (напр. "1106"). Если None или не найден —
            фолбэк на global.

    Returns:
        dict с ключами:
            - region_code: str | None (код региона или None для global/uniform)
            - method: str (описание метода)
            - monthly_share: {"1": 0.0691, ...}
            - cumulative_share: {"1": 0.0691, "2": 0.1366, ...}
            - samples_used: int (сколько регион-лет усреднено)
            - source: "per-region" | "global" | "legacy" | "uniform"
    """
    # 1. Per-region.
    if region_code is not None:
        per_region_file = SEASONAL_DIR / f"{region_code}.json"
        if per_region_file.exists():
            try:
                data = json.loads(per_region_file.read_text(encoding="utf-8"))
                # Проверим, что per-region основан на достаточной истории.
                if data.get("samples_used", 0) >= MIN_SAMPLES_FOR_PER_REGION:
                    data.setdefault("source", "per-region")
                    return data
                # Иначе per-region есть, но истории мало — логируем и фолбэк.
                print(f"[forecast] Per-region сезонный профиль для {region_code} "
                      f"основан на {data.get('samples_used', 0)} регион-лет "
                      f"(< {MIN_SAMPLES_FOR_PER_REGION}). Фолбэк на global.",
                      file=sys.stderr)
            except (json.JSONDecodeError, KeyError) as exc:
                print(f"[forecast] Ошибка чтения {per_region_file}: {exc}. "
                      f"Фолбэк на global.", file=sys.stderr)

    # 2. Global (новый путь).
    if SEASONAL_GLOBAL_FILE.exists():
        try:
            data = json.loads(SEASONAL_GLOBAL_FILE.read_text(encoding="utf-8"))
            data.setdefault("source", "global")
            return data
        except (json.JSONDecodeError, KeyError) as exc:
            print(f"[forecast] Ошибка чтения {SEASONAL_GLOBAL_FILE}: {exc}.",
                  file=sys.stderr)

    # 3. Legacy (старый путь, для обратной совместимости).
    if SEASONAL_LEGACY_FILE.exists():
        try:
            data = json.loads(SEASONAL_LEGACY_FILE.read_text(encoding="utf-8"))
            data.setdefault("source", "legacy")
            return data
        except (json.JSONDecodeError, KeyError) as exc:
            print(f"[forecast] Ошибка чтения {SEASONAL_LEGACY_FILE}: {exc}.",
                  file=sys.stderr)

    # 4. Дефолтный uniform.
    payload = _default_uniform_payload("no seasonal files found")
    payload["source"] = "uniform"
    print(f"[forecast] ВНИМАНИЕ: сезонные файлы не найдены. "
          f"Использую uniform 1/12. Запустите --recalc-seasonal.",
          file=sys.stderr)
    return payload


# --- Прогноз ---------------------------------------------------------------


def forecast_full_year_deaths(deaths_ytd: int, current_month: int,
                              seasonal: dict[str, Any] | None = None,
                              region_code: str | None = None) -> int:
    """
    Прогноз погибших на конец года по сезонной корректировке.

    Формула: deaths_ytd / cumulative_share[current_month]

    Особые случаи:
    - current_month == 0  — данных с ГИБДД ещё нет, возвращаем 0.
    - current_month == 12 — год закончился, прогноз = факт.
    - deaths_ytd == 0     — нет погибших YTD, прогноз = 0 (без деления 0/0).

    Args:
        seasonal: предзагруженный профиль (приоритет). Если None —
            загружается через load_seasonal_coefficients(region_code).
        region_code: код региона, для которого считается прогноз.
            Используется для выбора per-region сезонного профиля,
            если seasonal не передан.
    """
    if current_month < 0 or current_month > 12:
        raise ValueError(f"current_month must be 0..12, got {current_month}")
    if current_month == 0 or current_month == 12 or deaths_ytd == 0:
        return int(deaths_ytd)
    if seasonal is None:
        seasonal = load_seasonal_coefficients(region_code)
    cum_share = float(seasonal["cumulative_share"][str(current_month)])
    if cum_share <= 0:
        return int(deaths_ytd)
    return int(round(deaths_ytd / cum_share))


# --- Per-year кумулятивные доли (для коридора прогноза) --------------------


def compute_per_year_cum_shares(
    region_code: str,
) -> dict[str, dict[str, float]]:
    """
    Для каждого исторического года региона считает кумулятивную долю
    по месяцам: cum_share_Y[m] = sum(deaths[1..m]) / sum(deaths[1..12]).

    Используется для построения коридора прогноза:
    - optimistic_forecast = deaths_ytd / max(cum_share_Y[current_month])
    - pessimistic_forecast = deaths_ytd / min(cum_share_Y[current_month])

    Returns:
        {"2023": {"1": 0.125, "2": 0.250, ..., "12": 1.0},
         "2024": {...}, "2025": {...}}

        Пустой словарь, если:
        - файла history/{region_code}.json нет
        - ни одного года с deaths_by_month и total > 0
    """
    hist_file = DATA_HIST_DIR / f"{region_code}.json"
    if not hist_file.exists():
        return {}

    try:
        hist = json.loads(hist_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[forecast] Ошибка чтения {hist_file}: {exc}", file=sys.stderr)
        return {}

    result: dict[str, dict[str, float]] = {}
    for year_str, rec in hist.get("years", {}).items():
        dbm = rec.get("deaths_by_month")
        if not dbm:
            continue
        # Сумма по 12 месяцам (берём именно sum(dbm), а не rec["deaths"],
        # потому что rec["deaths"] может быть замороженным/внешним значением,
        # а нам нужно распределение именно из deaths_by_month).
        dbm_full = {str(m): int(dbm.get(str(m), 0)) for m in range(1, 13)}
        total = sum(dbm_full.values())
        if total <= 0:
            continue
        running = 0
        cum = {}
        for m in range(1, 13):
            running += dbm_full[str(m)]
            cum[str(m)] = round(running / total, 6)
        result[year_str] = cum
    return result


def forecast_with_corridor(
    deaths_ytd: int,
    current_month: int,
    region_code: str | None = None,
    seasonal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Возвращает прогноз с коридором неопределённости.

    Логика:
        central     = deaths_ytd / avg(cum_share_Y[current_month])
                       (== текущий метод, avg = среднее по историческим годам)
        optimistic  = deaths_ytd / max(cum_share_Y[current_month])
                       (минимальный прогноз: в этом году самая «передняя»
                       сезонность — много ДТП накопилось рано, значит остаток
                       года будет относительно лёгким)
        pessimistic = deaths_ytd / min(cum_share_Y[current_month])
                       (максимальный прогноз: самая «задняя» сезонность —
                       пик ещё впереди)

    Если per-year истории нет (region_code=None или файл пустой) —
    optimistic/pessimistic = None, возвращается только central.

    Returns:
        {
            "central": int,
            "optimistic": int | None,
            "pessimistic": int | None,
            "per_year_cum_at_current": {"2023": 0.510, "2024": 0.541, ...} | {},
            "years_used": list[str],
            "available": bool,  # True, если коридор посчитан
        }
    """
    if current_month < 0 or current_month > 12:
        raise ValueError(f"current_month must be 0..12, got {current_month}")
    if current_month == 0 or current_month == 12 or deaths_ytd == 0:
        # Года ещё нет или уже закончился — коридор не имеет смысла.
        return {
            "central": int(deaths_ytd),
            "optimistic": None,
            "pessimistic": None,
            "per_year_cum_at_current": {},
            "years_used": [],
            "available": False,
        }

    if seasonal is None:
        seasonal = load_seasonal_coefficients(region_code)

    # Central = текущий метод (avg cum_share).
    avg_cum = float(seasonal["cumulative_share"][str(current_month)])
    if avg_cum <= 0:
        central = int(deaths_ytd)
    else:
        central = int(round(deaths_ytd / avg_cum))

    # Per-year cum_share — только если есть region_code.
    per_year: dict[str, dict[str, float]] = (
        compute_per_year_cum_shares(region_code) if region_code else {}
    )
    if not per_year:
        return {
            "central": central,
            "optimistic": None,
            "pessimistic": None,
            "per_year_cum_at_current": {},
            "years_used": [],
            "available": False,
        }

    # Берём cum_share[current_month] для каждого года.
    cum_at_current = {
        year: float(cum[str(current_month)])
        for year, cum in per_year.items()
        if str(current_month) in cum and float(cum[str(current_month)]) > 0
    }
    if not cum_at_current:
        # Все годы вернули 0 в этом месяце — не из чего строить коридор.
        return {
            "central": central,
            "optimistic": None,
            "pessimistic": None,
            "per_year_cum_at_current": {},
            "years_used": [],
            "available": False,
        }

    max_cum = max(cum_at_current.values())  # самая «передняя» сезонность
    min_cum = min(cum_at_current.values())  # самая «задняя» сезонность

    optimistic = int(round(deaths_ytd / max_cum))  # минимальный прогноз
    pessimistic = int(round(deaths_ytd / min_cum))  # максимальный прогноз

    return {
        "central": central,
        "optimistic": optimistic,
        "pessimistic": pessimistic,
        "per_year_cum_at_current": cum_at_current,
        "years_used": sorted(cum_at_current.keys()),
        "available": True,
    }


# --- Кумулятивный Тр по месяцам (для графика 2) ----------------------------


def build_monthly_cumulative_tr(
    deaths_by_month_actual: dict[str, int],
    deaths_forecast_full_year: int,
    vehicles_year: int,
    plan_tr_year: float,
    plan_line_mode: Literal["linear", "horizontal"] = "linear",
    current_month: int | None = None,
    region_code: str | None = None,
    corridor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Формирует структуру для графика 2.

    Возвращает:
    {
      "months": [1..12],
      "tr_actual_cumulative": {"1": 0.31, "2": 0.62, ...},   # сплошная
      "tr_forecast_cumulative": {"7": ..., "8": ..., ...},    # пунктир (центр)
      "tr_optimistic_cumulative": {"7": ..., "8": ..., ...},  # нижняя граница
      "tr_pessimistic_cumulative": {"7": ..., "8": ..., ...}, # верхняя граница
      "plan_cumulative": {"1": plan/12, "2": 2*plan/12, ...}  # линейный
                    OR {"1": plan, "2": plan, ...}            # горизонтальный
      "current_month": 6,
      "plan_line_mode": "linear",
      "forecast_method": "central_only" | "corridor",
      "corridor_available": bool,
      "seasonal_source": "per-region" | "global" | "legacy" | "uniform"
    }

    Если current_month не задан — берётся текущий календарный месяц.

    Args:
        region_code: код региона — для выбора per-region сезонного профиля.
        corridor: опционально, результат forecast_with_corridor(). Если передан
            и corridor["available"] == True — строятся линии optimistic/
            pessimistic для прогнозных месяцев. Если None или not available —
            поля tr_optimistic_cumulative / tr_pessimistic_cumulative = {}.
    """
    if current_month is None:
        current_month = TODAY.month
    if vehicles_year <= 0:
        raise ValueError("vehicles_year must be > 0")

    # Доля прогноза на оставшиеся месяцы (per-region, если region_code задан).
    seasonal = load_seasonal_coefficients(region_code)
    monthly_share = seasonal["monthly_share"]

    # Фактические месяцы: считаем кумулятивный Тр нарастающим итогом.
    tr_actual_cum: dict[str, float] = {}
    deaths_actual_cum: dict[str, int] = {}
    deaths_cum = 0
    for m in range(1, current_month + 1):
        deaths_cum += int(deaths_by_month_actual.get(str(m), 0))
        deaths_actual_cum[str(m)] = deaths_cum
        tr_actual_cum[str(m)] = round((deaths_cum * 10000) / vehicles_year, 3)

    # Прогнозные месяцы: если forecast_full_year задан, распределить
    # остаток по seasonal_share оставшихся месяцев.
    tr_forecast_cum: dict[str, float] = {}
    deaths_forecast_cum: dict[str, int] = {}
    if current_month < 12 and deaths_forecast_full_year > 0:
        # Сколько погибших "уже в факте".
        deaths_actual_total = sum(
            int(deaths_by_month_actual.get(str(m), 0))
            for m in range(1, current_month + 1)
        )
        deaths_remaining = max(0, deaths_forecast_full_year - deaths_actual_total)

        # Доля оставшихся месяцев в общем годовом распределении.
        remaining_share_total = sum(
            float(monthly_share[str(m)])
            for m in range(current_month + 1, 13)
        )
        if remaining_share_total <= 0:
            # Непредвиденная ситуация (все доли в прошедших месяцах).
            # Фолбэк: равномерно по оставшимся месяцам.
            per_month_remaining = deaths_remaining / max(1, (12 - current_month))
            remaining_breakdown = {
                str(m): per_month_remaining
                for m in range(current_month + 1, 13)
            }
        else:
            remaining_breakdown = {
                str(m): deaths_remaining * float(monthly_share[str(m)]) / remaining_share_total
                for m in range(current_month + 1, 13)
            }

        # Кумулятивно.
        running_deaths = deaths_actual_total
        for m in range(current_month + 1, 13):
            running_deaths += remaining_breakdown[str(m)]
            deaths_forecast_cum[str(m)] = int(round(running_deaths))
            tr_forecast_cum[str(m)] = round((running_deaths * 10000) / vehicles_year, 3)

    # --- Коридор (optimistic / pessimistic) --------------------------------
    # Метод B: для каждого сценария (optimistic/pessimistic) берётся ОДИН
    # исторический год, чья seasonal-форма даёт экстремальный прогноз, и
    # его форма применяется ко всему остатку года.
    #
    #   year_optimistic  = год с MAX cum_share[current_month]  → самая
    #                       «передняя» сезонность → меньший прогноз.
    #   year_pessimistic = год с MIN cum_share[current_month]  → самая
    #                       «задняя» сезонность → больший прогноз.
    #
    # Траектория для сценария S (с годом Y_S):
    #   cum_deaths_at_m = deaths_ytd + (year_total_S - deaths_ytd) *
    #                     (cum_share_Y_S[m] - cum_share_Y_S[current_month]) /
    #                     (1 - cum_share_Y_S[current_month])
    #
    # где year_total_S = округлённый deaths_forecast_S (из corridor dict),
    # already an int. Это гарантирует, что cum_deaths_at_12 = year_total_S,
    # и tr_optimistic_cum[12] == tr_forecast_optimistic (согласованность с KPI).
    tr_optimistic_cum: dict[str, float] = {}
    tr_pessimistic_cum: dict[str, float] = {}
    deaths_optimistic_cum: dict[str, int] = {}
    deaths_pessimistic_cum: dict[str, int] = {}
    corridor_available = bool(corridor and corridor.get("available"))
    if (corridor_available
            and current_month < 12
            and 0 < current_month <= 12
            and region_code
            and corridor.get("optimistic") is not None
            and corridor.get("pessimistic") is not None):
        deaths_actual_total = sum(
            int(deaths_by_month_actual.get(str(m), 0))
            for m in range(1, current_month + 1)
        )
        if deaths_actual_total > 0:
            per_year = compute_per_year_cum_shares(region_code)
            # cum_share[current_month] для каждого года — из corridor dict.
            cum_at_current: dict[str, float] = corridor.get(
                "per_year_cum_at_current", {}
            )
            # Найти год с max/min cum_at_current.
            if cum_at_current:
                year_optimistic = max(cum_at_current, key=cum_at_current.get)
                year_pessimistic = min(cum_at_current, key=cum_at_current.get)
                cum_opt = per_year.get(year_optimistic, {})
                cum_pess = per_year.get(year_pessimistic, {})

                deaths_total_opt = int(corridor["optimistic"])
                deaths_total_pess = int(corridor["pessimistic"])
                deaths_remaining_opt = max(0, deaths_total_opt - deaths_actual_total)
                deaths_remaining_pess = max(0, deaths_total_pess - deaths_actual_total)

                cum_opt_curr = float(cum_opt.get(str(current_month), 0))
                cum_pess_curr = float(cum_pess.get(str(current_month), 0))
                denom_opt = 1.0 - cum_opt_curr  # = cum_share[12] - cum_share[current_month]
                denom_pess = 1.0 - cum_pess_curr

                for m in range(current_month + 1, 13):
                    cum_opt_m = float(cum_opt.get(str(m), 0))
                    cum_pess_m = float(cum_pess.get(str(m), 0))
                    # Прирост от current_month к m, в долях от остатка года.
                    if denom_opt > 0:
                        frac_opt = (cum_opt_m - cum_opt_curr) / denom_opt
                    else:
                        frac_opt = 0.0
                    if denom_pess > 0:
                        frac_pess = (cum_pess_m - cum_pess_curr) / denom_pess
                    else:
                        frac_pess = 0.0
                    cum_deaths_opt = deaths_actual_total + deaths_remaining_opt * frac_opt
                    cum_deaths_pess = deaths_actual_total + deaths_remaining_pess * frac_pess
                    deaths_optimistic_cum[str(m)] = int(round(cum_deaths_opt))
                    deaths_pessimistic_cum[str(m)] = int(round(cum_deaths_pess))
                    tr_optimistic_cum[str(m)] = round(
                        (cum_deaths_opt * 10000) / vehicles_year, 3
                    )
                    tr_pessimistic_cum[str(m)] = round(
                        (cum_deaths_pess * 10000) / vehicles_year, 3
                    )

    # План: линейный рост или горизонтальная линия.
    plan_cum: dict[str, float] = {}
    for m in range(1, 13):
        if plan_line_mode == "linear":
            plan_cum[str(m)] = round(plan_tr_year * m / 12, 3)
        else:  # horizontal
            plan_cum[str(m)] = round(plan_tr_year, 3)

    return {
        "months": list(range(1, 13)),
        "tr_actual_cumulative": tr_actual_cum,
        "tr_forecast_cumulative": tr_forecast_cum,
        "tr_optimistic_cumulative": tr_optimistic_cum,
        "tr_pessimistic_cumulative": tr_pessimistic_cum,
        "deaths_actual_cumulative": deaths_actual_cum,
        "deaths_forecast_cumulative": deaths_forecast_cum,
        "deaths_optimistic_cumulative": deaths_optimistic_cum,
        "deaths_pessimistic_cumulative": deaths_pessimistic_cum,
        "plan_cumulative": plan_cum,
        "current_month": current_month,
        "plan_line_mode": plan_line_mode,
        "forecast_method": "corridor" if corridor_available else "central_only",
        "corridor_available": corridor_available,
        "seasonal_source": seasonal.get("source", "unknown"),
        "seasonal_region_code": seasonal.get("region_code"),
        "seasonal_samples_used": seasonal.get("samples_used", 0),
    }


# --- Runtime-сборка для региона -------------------------------------------


def load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def get_year_data(region_code: str, year: int) -> dict[str, Any] | None:
    """
    Возвращает данные за указанный год с приоритетом:
    freeze > history > None (текущий год считается отдельно).

    Для замороженного года — структура с пометкой frozen=True.
    Для исторического — из history.
    """
    freeze = load_json_if_exists(DATA_FREEZE_DIR / f"{region_code}.json")
    if freeze and str(year) in freeze.get("frozen_years", {}):
        rec = freeze["frozen_years"][str(year)]
        return {
            "deaths": rec["deaths"],
            "vehicles": rec["vehicles"],
            "tr": rec["tr"],
            "frozen": True,
            "frozen_at": rec.get("frozen_at"),
            "source": "freeze",
        }

    history = load_json_if_exists(DATA_HIST_DIR / f"{region_code}.json")
    if history and str(year) in history.get("years", {}):
        rec = history["years"][str(year)]
        return {
            "deaths": rec["deaths"],
            "vehicles": rec["vehicles"],
            "tr": rec["tr"],
            "frozen": False,
            "source": "history",
        }
    return None


def fetch_actual_deaths_from_web(region_code: str, year: int) -> dict[str, int]:
    """
    Получает фактические погибшие по месяцам из карточек ДТП через gibdd-bot.

    Делегирует работу в gibdd_adapter.fetch_deaths_by_month_sync, который:
    1. Маппит Excel-код региона → ГИБДД-API-код (напр. 1106 → 1167 для Севастополя).
    2. Загружает карточки через bot._fetch_cards_for_period (API + web_fallback + кэш).
    3. Агрегирует карточки в {месяц: погибших}.

    ВНИМАНИЕ: функция синхронная, использует asyncio.run() — НЕ вызывать
    из асинхронного контекста (бота). Для бота используйте async-версию
    fetch_actual_deaths_from_web_async().

    При ошибке возвращает пустой словарь (логирует в stderr).
    """
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    try:
        from gibdd_adapter import fetch_deaths_by_month_sync
    except ImportError as exc:
        print(f"[forecast] НЕ удалось импортировать gibdd_adapter: {exc}",
              file=_sys.stderr)
        return {}

    try:
        deaths_by_month, errors = fetch_deaths_by_month_sync(region_code, year)
    except RuntimeError as exc:
        # Скорее всего, вызвано из event loop — нужен async-вариант.
        print(f"[forecast] RuntimeError в fetch_actual_deaths_from_web: {exc}",
              file=_sys.stderr)
        return {}
    except Exception as exc:  # noqa: BLE001
        print(f"[forecast] Ошибка при получении данных с ГИБДД: {exc}",
              file=_sys.stderr)
        return {}

    if errors:
        print(f"[forecast] Получены ошибки от gibdd_adapter "
              f"({len(errors)} шт.): первые = {errors[:2]}",
              file=_sys.stderr)

    # НЕ добиваем искусственно нулями будущие месяцы — это привело бы к тому,
    # что current_month в _build_runtime_payload считался бы по TODAY.month,
    # а не по фактически доступным данным ГИБДД.
    # Заполняем только «дырки» между 1 и max(месяц с данными), чтобы
    # месяцы с нулём ДТП в середине года корректно отображались как 0.
    return _fill_monthly_gaps(deaths_by_month)


async def fetch_actual_deaths_from_web_async(region_code: str, year: int) -> dict[str, int]:
    """
    Асинхронная версия fetch_actual_deaths_from_web для использования в боте.

    См. документацию синхронной версии.
    """
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from gibdd_adapter import fetch_deaths_by_month

    try:
        deaths_by_month, errors = await fetch_deaths_by_month(region_code, year)
    except Exception as exc:  # noqa: BLE001
        print(f"[forecast] Ошибка при получении данных с ГИБДД (async): {exc}",
              file=_sys.stderr)
        return {}

    if errors:
        print(f"[forecast] Получены ошибки от gibdd_adapter "
              f"({len(errors)} шт.): первые = {errors[:2]}",
              file=_sys.stderr)

    # См. комментарий в sync-версии: не добавляем будущие месяцы с нулями,
    # текущий период определяется по факту имеющихся данных.
    return _fill_monthly_gaps(deaths_by_month)


def _fill_monthly_gaps(deaths_by_month: dict[str, int]) -> dict[str, int]:
    """
    Заполняет «дырки» между январём и максимальным месяцем с данными.

    Если GIBDD вернул карты для месяцев {1, 2, 4, 5}, но не для 3 —
    функция добавит "3": 0. Это нужно, чтобы месяц без ДТП корректно
    отображался как 0, а не пропадал с графика.

    ВАЖНО: будущие месяцы (после max месяца с данными) НЕ добавляются —
    именно это позволяет downstream-коду корректно определить current_month
    как max месяц с фактическими данными, а не TODAY.month.
    """
    if not deaths_by_month:
        return {}
    months_present = sorted(int(m) for m in deaths_by_month.keys())
    max_month = months_present[-1]
    result = dict(deaths_by_month)
    for m in range(1, max_month + 1):
        result.setdefault(str(m), 0)
    return result


def _build_runtime_payload(
    region_code: str,
    deaths_by_month_actual: dict[str, int],
    plan_line_mode: Literal["linear", "horizontal"] = "linear",
    forecast_method: ForecastMethod = DEFAULT_FORECAST_METHOD,
) -> dict[str, Any]:
    """
    Сборка итогового payload для UI по уже полученным deaths_by_month_actual.

    Эта функция — общий код для sync- и async-версий runtime_calc.

    ВАЖНО: current_month определяется не по TODAY.month (календарный месяц),
    а по max месяцу, для которого GIBDD вернул данные. Это корректно,
    потому что сайт ГИБДД может отставать на 1-2 месяца: если сегодня
    1 августа, а данные есть только по июнь — current_month = 6, а не 8.

    Args:
        forecast_method: "central_only" — одна линия прогноза (текущий метод);
            "corridor" — центр + optimistic/pessimistic через min/max
            per-year cum_share. Если per-year истории нет — коридор
            молча отключается (corridor_available=False).
    """
    current_year = TODAY.year
    # current_month = max месяц с фактическими данными (не TODAY.month!).
    if deaths_by_month_actual:
        current_month = max(int(m) for m in deaths_by_month_actual.keys())
    else:
        current_month = 0

    # --- История (замороженная или из кэша) ---
    history: dict[str, dict[str, Any]] = {}
    for y in range(2023, current_year):
        rec = get_year_data(region_code, y)
        if rec:
            history[str(y)] = rec

    # --- Текущий год (runtime) ---
    vehicles = load_json_if_exists(DATA_VEHI_DIR / f"{region_code}.json") or {}
    plans = load_json_if_exists(DATA_PLANS_DIR / f"{region_code}.json") or {}

    vehicles_year = vehicles.get("vehicles_by_year", {}).get(str(current_year))
    plan_tr_year = plans.get("plan_tr", {}).get(str(current_year))
    if vehicles_year is None:
        raise RuntimeError(f"Нет Ктс за {current_year} для региона {region_code}")
    if plan_tr_year is None:
        raise RuntimeError(f"Нет плана за {current_year} для региона {region_code}")

    deaths_ytd = sum(deaths_by_month_actual.values())

    # --- Прогноз: central_only или corridor -------------------------------
    # corridor_result всегда считаем (дёшево), используем по необходимости.
    corridor_result = forecast_with_corridor(
        deaths_ytd, current_month, region_code=region_code,
    )

    if forecast_method == "corridor" and corridor_result["available"]:
        # Центр = central (он же текущий метод).
        deaths_forecast_full = corridor_result["central"]
        deaths_forecast_optimistic = corridor_result["optimistic"]
        deaths_forecast_pessimistic = corridor_result["pessimistic"]
        corridor_applied = True
    else:
        # central_only или коридор недоступен — используем текущий метод.
        # deaths_forecast_full = corridor_result["central"] тоже работает
        # (central там считается той же формулой), но для гарантии
        # детерминированности вызовем каноническую функцию.
        deaths_forecast_full = forecast_full_year_deaths(
            deaths_ytd, current_month, region_code=region_code,
        )
        deaths_forecast_optimistic = None
        deaths_forecast_pessimistic = None
        corridor_applied = False

    tr_actual_ytd = round((deaths_ytd * 10000) / vehicles_year, 3) if deaths_ytd else 0.0
    tr_forecast_full = round((deaths_forecast_full * 10000) / vehicles_year, 3)
    tr_forecast_optimistic = (
        round((deaths_forecast_optimistic * 10000) / vehicles_year, 3)
        if deaths_forecast_optimistic is not None else None
    )
    tr_forecast_pessimistic = (
        round((deaths_forecast_pessimistic * 10000) / vehicles_year, 3)
        if deaths_forecast_pessimistic is not None else None
    )

    monthly_chart = build_monthly_cumulative_tr(
        deaths_by_month_actual=deaths_by_month_actual,
        deaths_forecast_full_year=deaths_forecast_full,
        vehicles_year=vehicles_year,
        plan_tr_year=plan_tr_year,
        plan_line_mode=plan_line_mode,
        current_month=current_month,
        region_code=region_code,
        corridor=corridor_result if corridor_applied else None,
    )

    # --- Серия плана 2023..2030 ---
    plan_series = plans.get("plan_tr", {})

    # --- KPI ---
    deviation_pct = (
        round((tr_forecast_full - plan_tr_year) / plan_tr_year * 100, 1)
        if plan_tr_year > 0 else 0.0
    )
    if deviation_pct <= -5:
        status = "ok"
    elif deviation_pct <= 5:
        status = "warning"
    else:
        status = "danger"

    region_name = (vehicles.get("region_name")
                   or plans.get("region_name")
                   or f"Регион {region_code}")

    return {
        "region": {"code": region_code, "name": region_name},
        "history": history,
        "current_year": {
            "year": current_year,
            "months_actual": list(range(1, current_month + 1)),
            "months_forecast": list(range(current_month + 1, 13)),
            "deaths_by_month_actual": deaths_by_month_actual,
            "deaths_ytd": deaths_ytd,
            "deaths_forecast_full_year": deaths_forecast_full,
            "deaths_forecast_optimistic": deaths_forecast_optimistic,
            "deaths_forecast_pessimistic": deaths_forecast_pessimistic,
            "tr_actual_ytd": tr_actual_ytd,
            "tr_forecast_full_year": tr_forecast_full,
            "tr_forecast_optimistic": tr_forecast_optimistic,
            "tr_forecast_pessimistic": tr_forecast_pessimistic,
            "tr_plan": plan_tr_year,
            "monthly_chart": monthly_chart,
        },
        "plan_series": plan_series,
        "kpi": {
            "tr_actual_ytd": tr_actual_ytd,
            "tr_forecast_full_year": tr_forecast_full,
            "tr_forecast_optimistic": tr_forecast_optimistic,
            "tr_forecast_pessimistic": tr_forecast_pessimistic,
            "tr_plan": plan_tr_year,
            "deviation_pct": deviation_pct,
            "status": status,
        },
        "forecast_method": "corridor" if corridor_applied else "central_only",
        "corridor_available": corridor_result["available"],
        "corridor_years_used": corridor_result["years_used"],
        "seasonal": {
            "source": monthly_chart.get("seasonal_source", "unknown"),
            "region_code": monthly_chart.get("seasonal_region_code"),
            "samples_used": monthly_chart.get("seasonal_samples_used", 0),
        },
        "calculated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def runtime_calc(region_code: str,
                 plan_line_mode: Literal["linear", "horizontal"] = "linear",
                 forecast_method: ForecastMethod = DEFAULT_FORECAST_METHOD,
                 ) -> dict[str, Any]:
    """
    Синхронная версия runtime_calc для CLI и тестов.

    ВНИМАНИЕ: использует asyncio.run() через fetch_actual_deaths_from_web.
    НЕ вызывайте из асинхронного контекста (бота) — используйте
    `await runtime_calc_async(...)`.
    """
    deaths_by_month_actual = fetch_actual_deaths_from_web(region_code, TODAY.year)
    return _build_runtime_payload(
        region_code, deaths_by_month_actual, plan_line_mode, forecast_method,
    )


async def runtime_calc_async(region_code: str,
                             plan_line_mode: Literal["linear", "horizontal"] = "linear",
                             forecast_method: ForecastMethod = DEFAULT_FORECAST_METHOD,
                             ) -> dict[str, Any]:
    """
    Асинхронная версия runtime_calc для использования в боте.
    """
    deaths_by_month_actual = await fetch_actual_deaths_from_web_async(
        region_code, TODAY.year
    )
    return _build_runtime_payload(
        region_code, deaths_by_month_actual, plan_line_mode, forecast_method,
    )


# --- Административная команда: пересчёт сезонных коэффициентов ------------


def _compute_monthly_share_from_samples(
    samples: list[tuple[dict[str, int], int]],
) -> tuple[dict[str, float], dict[str, float]]:
    """
    По списку (dbm_full, total) считает (monthly_share, cumulative_share).

    Для каждого сэмпла доля месяца = dbm[m] / total. Усредняем по сэмплам,
    затем нормализуем к сумме = 1.0.
    """
    if not samples:
        uniform = {str(m): round(1 / 12, 4) for m in range(1, 13)}
        cum = {}
        run = 0.0
        for m in range(1, 13):
            run += uniform[str(m)]
            cum[str(m)] = round(run, 4)
        return uniform, cum

    monthly_sum = {str(m): 0.0 for m in range(1, 13)}
    for dbm, total in samples:
        for m in range(1, 13):
            monthly_sum[str(m)] += dbm[str(m)] / total

    monthly_share = {
        str(m): round(monthly_sum[str(m)] / len(samples), 4)
        for m in range(1, 13)
    }

    # Нормализация.
    total_share = sum(monthly_share.values())
    if total_share > 0:
        monthly_share = {
            m: round(v / total_share, 4)
            for m, v in monthly_share.items()
        }

    cumulative = {}
    running = 0.0
    for m in range(1, 13):
        running += monthly_share[str(m)]
        cumulative[str(m)] = round(running, 4)

    return monthly_share, cumulative


def _print_seasonal_profile(monthly_share: dict[str, float],
                            cumulative: dict[str, float]) -> None:
    """Печатает ASCII-гистограмму для визуального контроля."""
    for m in range(1, 13):
        ms = monthly_share[str(m)]
        cs = cumulative[str(m)]
        bar = "█" * int(ms * 200)
        print(f"  м{m:2d}: {ms:.4f} (cum={cs:.4f}) {bar}")


def recalc_seasonal_coefficients() -> dict[str, Any]:
    """
    Пересчитывает сезонные коэффициенты по имеющейся истории.

    Создаёт ДВА набора файлов:

    1. datasets/seasonal/global.json — глобальный профиль, среднее по всем
       регионам и годам (используется как фолбэк для регионов с малой историей).

    2. datasets/seasonal/{region_code}.json — per-region профиль, среднее
       по годам этого региона. Создаётся только если есть >=
       MIN_SAMPLES_FOR_PER_REGION лет с deaths_by_month.

    Метод для каждого сэмпла (регион-год):
        доля месяца m = deaths_by_month[m] / deaths_total
    Усреднение по сэмплам с последующей нормализацией суммы долей к 1.0.

    Returns:
        dict с ключами:
            - global: payload глобального профиля
            - per_region: {region_code: payload} для каждого региона с историей
            - regions_per_region: int (сколько регионов получили per-region)
            - regions_fallback: int (сколько регионов используют global)
    """
    print("[forecast] recalc_seasonal_coefficients: расчёт по истории...")
    SEASONAL_DIR.mkdir(parents=True, exist_ok=True)

    # Собираем сэмплы по регионам: {region_code: [(dbm_full, total), ...]}.
    samples_by_region: dict[str, list[tuple[dict[str, int], int]]] = {}
    all_samples: list[tuple[dict[str, int], int]] = []

    for hist_file in DATA_HIST_DIR.glob("*.json"):
        region_code = hist_file.stem
        with hist_file.open("r", encoding="utf-8") as fh:
            hist = json.load(fh)
        region_samples: list[tuple[dict[str, int], int]] = []
        for year_str, rec in hist.get("years", {}).items():
            dbm = rec.get("deaths_by_month")
            total = rec.get("deaths", 0)
            if not dbm or total <= 0:
                continue
            dbm_full = {str(m): int(dbm.get(str(m), 0)) for m in range(1, 13)}
            sum_check = sum(dbm_full.values())
            region_samples.append((dbm_full, sum_check))
            all_samples.append((dbm_full, sum_check))
        if region_samples:
            samples_by_region[region_code] = region_samples

    if not all_samples:
        print("[forecast] В history нет записей с deaths_by_month. "
              "Создаю default uniform.")
        uniform_payload = _default_uniform_payload("no history available")
        uniform_payload["source"] = "uniform"
        SEASONAL_GLOBAL_FILE.write_text(
            json.dumps(uniform_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {"global": uniform_payload, "per_region": {},
                "regions_per_region": 0, "regions_fallback": 0}

    print(f"[forecast] Всего сэмплов: {len(all_samples)} регион-лет "
          f"по {len(samples_by_region)} регионам.")

    # --- 1. Глобальный профиль ---
    global_share, global_cum = _compute_monthly_share_from_samples(all_samples)
    global_payload = {
        "updated_at": TODAY.isoformat(),
        "method": f"среднее по {len(all_samples)} регион-годам (global)",
        "monthly_share": global_share,
        "cumulative_share": global_cum,
        "region_code": None,
        "samples_used": len(all_samples),
    }
    SEASONAL_GLOBAL_FILE.write_text(
        json.dumps(global_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[forecast] {SEASONAL_GLOBAL_FILE} сохранён (global, "
          f"{len(all_samples)} сэмплов).")
    print("[forecast] Глобальный профиль:")
    _print_seasonal_profile(global_share, global_cum)

    # --- 2. Per-region профили ---
    per_region_payloads: dict[str, dict[str, Any]] = {}
    regions_per_region_count = 0
    regions_fallback_count = 0

    print(f"\n[forecast] Per-region профили (порог: "
          f"{MIN_SAMPLES_FOR_PER_REGION} сэмпла):")
    for region_code, samples in sorted(samples_by_region.items()):
        if len(samples) >= MIN_SAMPLES_FOR_PER_REGION:
            monthly_share, cumulative = _compute_monthly_share_from_samples(samples)
            payload = {
                "updated_at": TODAY.isoformat(),
                "method": f"среднее по {len(samples)} годам для региона {region_code}",
                "monthly_share": monthly_share,
                "cumulative_share": cumulative,
                "region_code": region_code,
                "samples_used": len(samples),
            }
            out_file = SEASONAL_DIR / f"{region_code}.json"
            out_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            per_region_payloads[region_code] = payload
            regions_per_region_count += 1
            print(f"  ✓ {region_code}: {len(samples)} лет — per-region сохранён.")
        else:
            regions_fallback_count += 1
            print(f"  → {region_code}: {len(samples)} лет (< {MIN_SAMPLES_FOR_PER_REGION}) "
                  f"— фолбэк на global.")

    # --- 3. Сводка ---
    print(f"\n[forecast] СВОДКА:")
    print(f"  Глобальный профиль: {SEASONAL_GLOBAL_FILE.name} ({len(all_samples)} сэмплов).")
    print(f"  Per-region профили: {regions_per_region_count} регионов.")
    print(f"  Фолбэк на global: {regions_fallback_count} регионов.")

    # --- 4. Покажем сравнение per-region vs global для диагностики ---
    if per_region_payloads:
        print(f"\n[forecast] Сравнение per-region профилей (cumulative_share[6]):")
        print(f"  global: {global_cum['6']:.4f}")
        for region_code, payload in sorted(per_region_payloads.items()):
            cum6 = payload["cumulative_share"]["6"]
            diff_pct = (cum6 - float(global_cum["6"])) / float(global_cum["6"]) * 100
            print(f"  {region_code}: {cum6:.4f} ({diff_pct:+.1f}% к global)")

    return {
        "global": global_payload,
        "per_region": per_region_payloads,
        "regions_per_region": regions_per_region_count,
        "regions_fallback": regions_fallback_count,
    }


# --- CLI -------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recalc-seasonal", action="store_true",
                        help="Пересчитать сезонные коэффициенты: global + per-region")
    parser.add_argument("--region", type=str,
                        help="Напечатать runtime_calc для региона")
    parser.add_argument("--plan-line", choices=["linear", "horizontal"],
                        default="linear",
                        help="Режим линии плана на графике 2")
    parser.add_argument("--forecast-method", choices=["central_only", "corridor"],
                        default=DEFAULT_FORECAST_METHOD,
                        help="Метод прогноза: central_only (одна линия) или "
                             "corridor (центр + optimistic/pessimistic)")
    args = parser.parse_args(argv[1:])

    if args.recalc_seasonal:
        recalc_seasonal_coefficients()
        return 0

    if args.region:
        payload = runtime_calc(
            args.region,
            plan_line_mode=args.plan_line,
            forecast_method=args.forecast_method,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
