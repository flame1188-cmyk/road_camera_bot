"""
precalc_history.py — предрассчёт исторических данных за 2023-2025.

КОНЦЕПЦИЯ
=========

Для каждого региона из data/vehicles/ (или data/plans/) тянем карточки ДТП
за все 12 месяцев каждого года 2023, 2024, 2025 и сохраняем в
data/history/{region_code}.json структуру:

{
  "region_code": "1106",
  "region_name": "г. Севастополь",
  "years": {
    "2023": {
      "deaths": 18,
      "vehicles": 175542,
      "tr": 1.026,
      "deaths_by_month": {"1": 2, "2": 1, ..., "12": 1},
      "calculated_at": "2026-08-01"
    },
    "2024": {...},
    "2025": {...}
  }
}

Поле deaths_by_month — опциональное (расширенная схема), нужно для:
- пересчёта сезонных коэффициентов через --recalc-seasonal
- отображения на графике 2 в случае, если пользователь решит посмотреть
  историю по месяцам (функция на будущее)

ВАЖНО: источник Ктс — data/vehicles/{region_code}.json. Если для какого-то
года Ктс отсутствует — год пропускается с предупреждением.

ИСПОЛЬЗОВАНИЕ
=============

    # Предрассчитать историю для всех 10 регионов (по умолчанию)
    python np_bdd/scripts/precalc_history.py

    # Один регион
    python np_bdd/scripts/precalc_history.py --region 1106

    # Конкретные годы
    python np_bdd/scripts/precalc_history.py --years 2023 2024

    # Параллельно (по умолчанию 1 регион за раз, чтобы не DDOS-ить ГИБДД)
    python np_bdd/scripts/precalc_history.py --concurrency 2

    # Только проверить, что уже есть (без запросов к ГИБДД)
    python np_bdd/scripts/precalc_history.py --dry-run

ОГРАНИЧЕНИЯ
===========

- ГИБДД может отдавать данные с задержкой 2-3 месяца. За 2025 год данные
  могут быть неполными (особенно ноябрь-декабрь). Это нормально — после
  финализации пользователь заморозит год через /admin/freeze_year.
- Один год = 12 запросов (по месяцам). На 1 регион × 3 года = 36 запросов.
  На 10 регионов = 360 запросов. При ~3 сек на запрос — ~18 минут.
- Если для месяца нет данных (HTTP 403/5xx), он попадает в errors и
  deaths_by_month за этот месяц = 0. Год всё равно сохраняется.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, timezone
from datetime import datetime
from pathlib import Path
from typing import Any

# --- Относительные пути ---------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # np_bdd/
DATA_HIST_DIR = PROJECT_ROOT / "datasets" / "history"
DATA_VEHI_DIR = PROJECT_ROOT / "datasets" / "vehicles"
DATA_PLANS_DIR = PROJECT_ROOT / "datasets" / "plans"
SCHEMAS_DIR = PROJECT_ROOT / "schemas"

TODAY = date.today()
HISTORY_YEARS_DEFAULT = [2023, 2024, 2025]


# --- Схема -----------------------------------------------------------------


def load_schema() -> dict[str, Any]:
    """Загружает схему history. Если в схеме нет поля deaths_by_month —
    добавляет его как optional (patternProperties под ключами 1..12).
    """
    with (SCHEMAS_DIR / "history.schema.json").open("r", encoding="utf-8") as fh:
        schema = json.load(fh)
    # Гарантируем, что schema принимает deaths_by_month (опциональное).
    # Достаточно, что в year-объекте нет additionalProperties: false,
    # либо deaths_by_month явно разрешён. По нашей текущей схеме
    # additionalProperties: false — нужно добавить поле.
    year_props = schema["properties"]["years"]["patternProperties"]["^[0-9]{4}$"]["properties"]
    if "deaths_by_month" not in year_props:
        year_props["deaths_by_month"] = {
            "type": "object",
            "patternProperties": {
                "^([1-9]|1[0-2])$": {"type": "integer", "minimum": 0}
            },
            "additionalProperties": False,
        }
    return schema


SCHEMA_HISTORY = load_schema()


# --- Утилиты ---------------------------------------------------------------


def list_regions() -> list[str]:
    """Возвращает список кодов регионов, для которых есть vehicles JSON."""
    return sorted(p.stem for p in DATA_VEHI_DIR.glob("*.json"))


def load_vehicles(region_code: str) -> dict[str, Any] | None:
    path = DATA_VEHI_DIR / f"{region_code}.json"
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_history(region_code: str) -> dict[str, Any]:
    path = DATA_HIST_DIR / f"{region_code}.json"
    if not path.exists():
        return {"region_code": region_code, "region_name": "", "years": {}}
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_history(payload: dict[str, Any]) -> None:
    from jsonschema import validate
    validate(instance=payload, schema=SCHEMA_HISTORY)
    path = DATA_HIST_DIR / f"{payload['region_code']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def calc_tr(deaths: int, vehicles: int) -> float:
    if vehicles <= 0:
        return 0.0
    return round((deaths * 10000) / vehicles, 3)


# --- Предрассчёт одного года одного региона -------------------------------


async def precalc_year_for_region(
    region_code_excel: str,
    year: int,
    vehicles_by_year: dict[str, int],
) -> tuple[dict[str, Any] | None, list[str]]:
    """
    Тянет 12 месяцев карточек ДТП за указанный год, агрегирует,
    считает Тр.

    Возвращает (year_record, errors). year_record = None, если нет Ктс.
    """
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from gibdd_adapter import fetch_deaths_by_month

    vehicles_year = vehicles_by_year.get(str(year))
    if not vehicles_year:
        return None, [f"Нет Ктс за {year} для региона {region_code_excel}"]

    # Загружаем все 12 месяцев.
    deaths_by_month, errors = await fetch_deaths_by_month(
        region_code_excel=region_code_excel,
        year=year,
        current_month=12,
    )

    deaths_total = sum(deaths_by_month.values())
    # Добиваем нулями отсутствующие месяцы.
    for m in range(1, 13):
        deaths_by_month.setdefault(str(m), 0)

    return {
        "deaths": deaths_total,
        "vehicles": vehicles_year,
        "tr": calc_tr(deaths_total, vehicles_year),
        "deaths_by_month": deaths_by_month,
        "calculated_at": TODAY.isoformat(),
    }, errors


# --- Предрассчёт одного региона (все годы) --------------------------------


async def precalc_region(
    region_code_excel: str,
    years: list[int],
    force: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """
    Предрассчитывает историю для одного региона.

    Args:
        region_code_excel: код региона в формате Excel (напр. "1106").
        years: список лет для расчёта.
        force: если True — пересчитать даже уже имеющиеся годы.
            Если False — пропустить года, которые уже в history.

    Returns:
        (history_payload, all_errors)
    """
    vehicles = load_vehicles(region_code_excel)
    if not vehicles:
        msg = f"Нет vehicles JSON для региона {region_code_excel}"
        return {"region_code": region_code_excel, "region_name": "", "years": {}}, [msg]

    history = load_history(region_code_excel)
    history["region_name"] = vehicles.get("region_name", "")

    vehicles_by_year = vehicles.get("vehicles_by_year", {})
    all_errors: list[str] = []

    for year in years:
        year_str = str(year)
        if year_str in history["years"] and not force:
            print(f"  [{region_code_excel}] {year}: уже есть, пропускаю (--force для пересчёта)")
            continue

        print(f"  [{region_code_excel}] {year}: загрузка 12 месяцев с ГИБДД...")
        record, errors = await precalc_year_for_region(
            region_code_excel, year, vehicles_by_year,
        )
        all_errors.extend(errors)

        if record is None:
            print(f"  [{region_code_excel}] {year}: ПРОПУЩЕН ({errors[0] if errors else 'нет Ктс'})")
            continue

        history["years"][year_str] = record
        print(f"  [{region_code_excel}] {year}: deaths={record['deaths']}, "
              f"tr={record['tr']}, errors={len(errors)}")

    return history, all_errors


# --- Главная функция -------------------------------------------------------


async def run_precalc(
    region_codes: list[str],
    years: list[int],
    force: bool = False,
    dry_run: bool = False,
) -> int:
    """Возвращает exit code."""
    if dry_run:
        print("=== DRY RUN ===")
        print(f"Регионы ({len(region_codes)}): {region_codes}")
        print(f"Года: {years}")
        for rc in region_codes:
            history = load_history(rc)
            vehicles = load_vehicles(rc)
            existing_years = sorted(history.get("years", {}).keys())
            veh_years = sorted(vehicles.get("vehicles_by_year", {}).keys()) if vehicles else []
            print(f"  [{rc}] {vehicles.get('region_name', '?') if vehicles else 'НЕТ vehicles'}: "
                  f"history has {existing_years}, vehicles has {veh_years}")
        return 0

    total_errors = 0
    for rc in region_codes:
        print(f"\n=== Регион {rc} ===")
        history, errors = await precalc_region(rc, years, force=force)
        if errors:
            total_errors += len(errors)
            print(f"  Ошибки ({len(errors)}):")
            for e in errors[:5]:
                print(f"    - {e}")
            if len(errors) > 5:
                print(f"    ... и ещё {len(errors) - 5}")
        # Сохраняем в любом случае (даже с ошибками — частичные данные лучше, чем ничего).
        if history["years"]:
            try:
                save_history(history)
                print(f"  Сохранено: {DATA_HIST_DIR / f'{rc}.json'}")
            except Exception as exc:  # noqa: BLE001
                print(f"  ОШИБКА сохранения: {exc}")
                total_errors += 1
        else:
            print(f"  Нет данных для сохранения")

    print(f"\n=== ИТОГ: регионов={len(region_codes)}, ошибок={total_errors} ===")
    return 0 if total_errors == 0 else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Предрассчёт истории 2023-2025 для НП БДД")
    parser.add_argument("--region", type=str, default=None,
                        help="Код региона (по умолчанию все из data/vehicles/)")
    parser.add_argument("--years", type=int, nargs="+", default=HISTORY_YEARS_DEFAULT,
                        help=f"Года для расчёта (по умолчанию {HISTORY_YEARS_DEFAULT})")
    parser.add_argument("--force", action="store_true",
                        help="Пересчитать даже уже имеющиеся года")
    parser.add_argument("--dry-run", action="store_true",
                        help="Только показать, что будет сделано")
    args = parser.parse_args(argv[1:])

    if args.region:
        region_codes = [args.region]
    else:
        region_codes = list_regions()

    if not region_codes:
        print("Нет регионов для расчёта. Положите vehicles JSON в data/vehicles/.")
        return 1

    return asyncio.run(run_precalc(
        region_codes=region_codes,
        years=args.years,
        force=args.force,
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
