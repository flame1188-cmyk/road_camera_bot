"""
freeze_year.py — ручная заморозка/разморозка года для региона.

КОНЦЕПЦИЯ
========

В начале следующего года ГИБДД корректирует карточки ДТП за прошедший год
(запаздывающие данные, уточнения). Поэтому «история» за только что
завершившийся год в течение 2-3 месяцев нового года может колебаться.

Заморозка решает эту проблему: администратор один раз выполняет
команду `/admin/freeze_year <region> <year>` после того, как данные
за год финализированы, и после этого год всегда берётся из
`data/freeze/{region_code}.json` — без пересчёта.

Приоритет данных при расчёте (см. forecast.get_year_data):
  freeze > history > runtime (только для текущего года)

ИСПОЛЬЗОВАНИЕ
=============

    # Заморозить 2025 год для региона 67 (г. Севастополь)
    python np_bdd/scripts/freeze_year.py freeze 67 2025 \\
        --note "Финальные данные после корректировок ГИБДД"

    # Разморозить (удалить запись о заморозке за 2025)
    python np_bdd/scripts/freeze_year.py unfreeze 67 2025

    # Показать все замороженные года для региона
    python np_bdd/scripts/freeze_year.py list 67
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from jsonschema import validate

# --- Относительные пути ---------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # np_bdd/
DATA_HIST_DIR = PROJECT_ROOT / "datasets" / "history"
DATA_VEHI_DIR = PROJECT_ROOT / "datasets" / "vehicles"
DATA_FREEZE_DIR = PROJECT_ROOT / "datasets" / "freeze"
SCHEMAS_DIR = PROJECT_ROOT / "schemas"


def load_schema() -> dict[str, Any]:
    with (SCHEMAS_DIR / "freeze.schema.json").open("r", encoding="utf-8") as fh:
        return json.load(fh)


SCHEMA_FREEZE = load_schema()


def load_freeze_file(region_code: str) -> dict[str, Any]:
    """Загружает freeze-файл региона или создаёт пустой."""
    path = DATA_FREEZE_DIR / f"{region_code}.json"
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    return {"region_code": region_code, "frozen_years": {}}


def save_freeze_file(payload: dict[str, Any]) -> None:
    validate(instance=payload, schema=SCHEMA_FREEZE)
    path = DATA_FREEZE_DIR / f"{payload['region_code']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def get_year_data_for_freeze(region_code: str, year: int) -> dict[str, Any]:
    """
    Берёт данные за год ИЗ history + vehicles, рассчитывает Тр и
    месячную разбивку (если есть).

    Заморозка создаёт снапшот: deaths, vehicles, tr, frozen_at, frozen_by,
    source_deaths_breakdown (если доступно), note.
    """
    hist_path = DATA_HIST_DIR / f"{region_code}.json"
    veh_path = DATA_VEHI_DIR / f"{region_code}.json"
    if not hist_path.exists():
        raise FileNotFoundError(f"history-файл не найден: {hist_path}")
    if not veh_path.exists():
        raise FileNotFoundError(f"vehicles-файл не найден: {veh_path}")

    with hist_path.open("r", encoding="utf-8") as fh:
        history = json.load(fh)
    with veh_path.open("r", encoding="utf-8") as fh:
        vehicles = json.load(fh)

    year_str = str(year)
    if year_str not in history.get("years", {}):
        raise KeyError(f"в history нет данных за {year}")

    rec = history["years"][year_str]
    vehicles_year = vehicles.get("vehicles_by_year", {}).get(year_str)
    if vehicles_year is None:
        raise KeyError(f"в vehicles нет Ктс за {year}")

    return {
        "deaths": rec["deaths"],
        "vehicles": vehicles_year,
        "tr": round((rec["deaths"] * 10000) / vehicles_year, 3),
        "source_deaths_breakdown": rec.get("deaths_by_month"),
    }


def cmd_freeze(region_code: str, year: int, note: str | None,
               frozen_by: str = "admin") -> int:
    payload = load_freeze_file(region_code)
    year_str = str(year)

    try:
        snapshot = get_year_data_for_freeze(region_code, year)
    except (FileNotFoundError, KeyError) as exc:
        print(f"[freeze] ОШИБКА: {exc}", file=sys.stderr)
        return 1

    frozen_record = {
        "deaths": snapshot["deaths"],
        "vehicles": snapshot["vehicles"],
        "tr": snapshot["tr"],
        "frozen_at": date.today().isoformat(),
        "frozen_by": frozen_by,
    }
    if snapshot.get("source_deaths_breakdown"):
        frozen_record["source_deaths_breakdown"] = snapshot["source_deaths_breakdown"]
    if note:
        frozen_record["note"] = note

    payload["frozen_years"][year_str] = frozen_record
    save_freeze_file(payload)

    print(f"[freeze] Регион {region_code}, год {year} заморожен.")
    print(f"         Тр = {frozen_record['tr']}, "
          f"погибших = {frozen_record['deaths']}, "
          f"Ктс = {frozen_record['vehicles']}")
    print(f"         Файл: {DATA_FREEZE_DIR / f'{region_code}.json'}")
    return 0


def cmd_unfreeze(region_code: str, year: int) -> int:
    payload = load_freeze_file(region_code)
    year_str = str(year)
    if year_str not in payload["frozen_years"]:
        print(f"[freeze] Регион {region_code}, год {year} НЕ был заморожен.")
        return 1
    del payload["frozen_years"][year_str]
    save_freeze_file(payload)
    print(f"[freeze] Регион {region_code}, год {year} разморожен.")
    return 0


def cmd_list(region_code: str) -> int:
    payload = load_freeze_file(region_code)
    frozen = payload.get("frozen_years", {})
    if not frozen:
        print(f"[freeze] Регион {region_code}: замороженных лет нет.")
        return 0
    print(f"[freeze] Регион {region_code}: заморожено {len(frozen)} лет(а):")
    for year_str, rec in sorted(frozen.items()):
        print(f"  {year_str}: Тр = {rec['tr']}, "
              f"погибших = {rec['deaths']}, "
              f"заморожен {rec.get('frozen_at', '?')} "
              f"пользователем {rec.get('frozen_by', '?')}"
              + (f" ({rec.get('note')})" if rec.get("note") else ""))
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Управление заморозкой лет НП БДД")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_freeze = sub.add_parser("freeze", help="Заморозить год")
    p_freeze.add_argument("region_code")
    p_freeze.add_argument("year", type=int)
    p_freeze.add_argument("--note", type=str, default=None)
    p_freeze.add_argument("--by", type=str, default="admin")

    p_unfreeze = sub.add_parser("unfreeze", help="Разморозить год")
    p_unfreeze.add_argument("region_code")
    p_unfreeze.add_argument("year", type=int)

    p_list = sub.add_parser("list", help="Показать замороженные года")
    p_list.add_argument("region_code")

    args = parser.parse_args(argv[1:])

    if args.cmd == "freeze":
        return cmd_freeze(args.region_code, args.year, args.note, args.by)
    if args.cmd == "unfreeze":
        return cmd_unfreeze(args.region_code, args.year)
    if args.cmd == "list":
        return cmd_list(args.region_code)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
