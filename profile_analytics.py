"""
Профайлер analytics-фазы.

Генерирует синтетические cards разного размера (500/2000/5000 ДТП)
и замеряет время каждой операции:
  - calculate_metrics
  - calculate_cross_tables (текущий период)
  - calculate_cross_tables (прошлый период)
  - calculate_statistical_metrics
  - compare_metrics
  - format_cross_tables_for_prompt

Результаты — таблица по операциям и размерам данных.
"""
import sys
import os
import time
import random
import json
from pathlib import Path

# Добавляем корень gibdd-bot в sys.path
ROOT = Path("/home/z/my-project/gibdd-bot")
sys.path.insert(0, str(ROOT))

# Подменяем конфиг, чтобы не требовать TELEGRAM_BOT_TOKEN
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy_for_profiling")

from analytics import (
    calculate_metrics,
    calculate_cross_tables,
    calculate_statistical_metrics,
    compare_metrics,
)


# -----------------------------
# Генератор синтетических cards
# -----------------------------
DTP_TYPES = ["Наезд на пешехода", "Столкновение", "Опрокидывание",
             "Наезд на препятствие", "Наезд на велосипедиста"]
WEATHER = ["Ясно", "Дождь", "Снег", "Туман", "Пасмурно"]
LIGHTING = ["Светлое время суток", "В темное время суток, освещение включено",
            "В темное время суток, освещение не включено"]
ROAD_STATES = ["Сухое", "Мокрое", "Заснеженное", "Гололедица"]
VEHICLE_TYPES = ["Легковой", "Грузовой", "Автобус", "Мотоцикл"]
GENDERS = ["Мужской", "Женский"]
CATEGORIES = ["Водитель", "Пассажир", "Пешеход"]
VIOLATIONS = ["Превышение скорости", "Нарушение проезда перекрестка",
              "Выезд на встречную", "Непропуск пешехода"]
DISTRICTS = ["Центральный", "Северный", "Южный", "Восточный", "Западный"]
ROADS = ["М-7", "Р-241", "Восточный обход", "ул. Ленина", "пр. Мира"]
ALCOHOL = ["да", "нет"]


def make_card(idx: int) -> dict:
    """Сгенерировать одну синтетическую карточку ДТП."""
    n_participants = random.randint(2, 6)
    participants = []
    for _ in range(n_participants):
        participants.append({
            "k_uch": random.choice(CATEGORIES),
            "pol": random.choice(GENDERS),
            "stazh": str(random.randint(0, 30)),
            "ts_tp": random.choice(VEHICLE_TYPES),
            "s_pch": random.choice(["Ремень пристегнут", "Не пристегнут", "—"]),
            "npr": random.choice(VIOLATIONS),
            "marka_ts": f"Brand-{random.randint(1, 20)}",
            "god_vyp": str(random.randint(2000, 2024)),
            "pyt": random.choice(["трезв", "пьян"]),
        })
    return {
        "id": idx,
        "dat": f"{random.randint(1,28):02d}.{random.randint(1,12):02d}.2025",
        "time": f"{random.randint(0,23):02d}:{random.randint(0,59):02d}",
        "dtp_type": random.choice(DTP_TYPES),
        "pog": random.randint(0, 3),
        "ran": random.randint(0, 8),
        "k_ts": str(random.randint(1, 5)),
        "k_ul": f"ул. {random.choice(['Ленина', 'Мира', 'Советская', 'Гагарина'])}",
        "np": f"Город-{random.randint(1, 5)}",
        "street": f"ул. {random.choice(['Пушкина', 'Кирова', 'Октябрьская'])}",
        "dor": random.choice(ROADS),
        "dor_z": str(random.randint(1, 6)),
        "weather": random.choice(WEATHER),
        "lighting": random.choice(LIGHTING),
        "s_pch": random.choice(ROAD_STATES),
        "factor": [random.choice(["Скользкое покрытие", "Дефекты покрытия", "—"])],
        "ndu": [random.choice(["Отсутствие освещения", "Неисправный светофор", "—"])],
        "obj_dtp": [random.choice(["Пешеходный переход", "Остановка", "—"])],
        "district": random.choice(DISTRICTS),
        "alcohol": random.choice(ALCOHOL),
        "uchastniki": participants,
        "ts_info": [{"marka_ts": p["marka_ts"], "god_vyp": p["god_vyp"],
                     "ts_tp": p["ts_tp"]} for p in participants],
    }


def make_cards(n: int) -> list[dict]:
    random.seed(42)  # детерминированно для повторяемости
    return [make_card(i) for i in range(n)]


def timed(label: str, fn, *args, **kwargs):
    """Замерить время вызова fn(*args) и вернуть (result, elapsed_ms)."""
    t0 = time.perf_counter()
    res = fn(*args, **kwargs)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return res, elapsed_ms


def format_ms(ms: float) -> str:
    if ms < 1000:
        return f"{ms:7.1f} ms"
    return f"{ms/1000:6.2f}  s"


def profile_size(n_cards: int) -> dict:
    """Профилировать analytics-фазу на синтетике размера n_cards."""
    print(f"\n{'='*60}\nРазмер выборки: {n_cards} ДТП\n{'='*60}")

    current_cards = make_cards(n_cards)
    prev_cards = make_cards(n_cards)

    results = {}

    # 1. calculate_metrics (current)
    current_metrics, t1 = timed("metrics", calculate_metrics, current_cards)
    results["calculate_metrics (current)"] = t1
    print(f"  calculate_metrics (current)     : {format_ms(t1)}")

    # 2. calculate_metrics (prev) — параллельно сейчас не идёт
    prev_metrics, t2 = timed("metrics", calculate_metrics, prev_cards)
    results["calculate_metrics (prev)"] = t2
    print(f"  calculate_metrics (prev)        : {format_ms(t2)}")

    # 3. compare_metrics
    _, t3 = timed("compare", compare_metrics, current_metrics, prev_metrics)
    results["compare_metrics"] = t3
    print(f"  compare_metrics                 : {format_ms(t3)}")

    # 4. calculate_cross_tables (current)
    current_cross, t4 = timed("cross_tables", calculate_cross_tables, current_cards)
    results["calculate_cross_tables (current)"] = t4
    print(f"  calculate_cross_tables (current): {format_ms(t4)}")

    # 5. calculate_cross_tables (prev)
    prev_cross, t5 = timed("cross_tables", calculate_cross_tables, prev_cards)
    results["calculate_cross_tables (prev)"] = t5
    print(f"  calculate_cross_tables (prev)   : {format_ms(t5)}")

    # 6. calculate_statistical_metrics
    _, t6 = timed("stats", calculate_statistical_metrics, current_cross)
    results["calculate_statistical_metrics"] = t6
    print(f"  calculate_statistical_metrics   : {format_ms(t6)}")

    # Итоги
    total_analytics = t1 + t2 + t3 + t4 + t5 + t6
    print(f"  {'-'*40}")
    print(f"  TOTAL analytics                 : {format_ms(total_analytics)}")
    print(f"  Из них cross_tables (2×)        : {format_ms(t4 + t5)} "
          f"({(t4+t5)/total_analytics*100:.0f}%)")
    print(f"  Из них metrics (2×)             : {format_ms(t1 + t2)} "
          f"({(t1+t2)/total_analytics*100:.0f}%)")

    return results


def main():
    print("Профайлер analytics-фазы (Phase 3.1)")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Корень gibdd-bot: {ROOT}")

    all_results = {}
    for n in [500, 2000, 5000]:
        all_results[n] = profile_size(n)

    # Сводная таблица
    print(f"\n\n{'='*70}\nСВОДНАЯ ТАБЛИЦА (ms)\n{'='*70}")
    headers = ["Операция", "500 ДТП", "2000 ДТП", "5000 ДТП"]
    print(f"  {headers[0]:<40} {headers[1]:>12} {headers[2]:>12} {headers[3]:>12}")
    print(f"  {'-'*40} {'-'*12} {'-'*12} {'-'*12}")
    for op in all_results[500].keys():
        row = f"  {op:<40} "
        for n in [500, 2000, 5000]:
            row += f"{all_results[n][op]:>10.1f}ms "
        print(row)

    # Прогноз на реальный регион (2629 ДТП, как в логах)
    print(f"\nПрогноз для региона 1160 (2629 ДТП, фактические логи):")
    # Линейная экстраполяция от 2000 ДТП
    scale = 2629 / 2000
    for op, t in all_results[2000].items():
        est = t * scale
        print(f"  {op:<40} ~{format_ms(est)}")

    print(f"\nВЫВОД: какая операция занимает >50% времени?")
    total_2000 = sum(all_results[2000].values())
    for op, t in sorted(all_results[2000].items(), key=lambda x: -x[1]):
        pct = t / total_2000 * 100
        if pct > 15:
            print(f"  🔴 {op:<40} {pct:>5.1f}% ({format_ms(t)})")
        else:
            print(f"  ▢  {op:<40} {pct:>5.1f}% ({format_ms(t)})")


if __name__ == "__main__":
    main()
