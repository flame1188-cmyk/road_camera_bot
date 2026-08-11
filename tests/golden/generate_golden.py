"""
Генератор эталонов для golden-тестов.

Запуск: python tests/golden/generate_golden.py

Скрипт вызывает реальные функции (parse_card_to_row, calculate_metrics,
calculate_cross_tables, format_metrics_for_prompt, find_region, parse_period)
на заранее зафиксированных входах и сохраняет результаты как эталонные JSON/txt.

Повторный запуск перезаписывает эталоны. Использовать при осознанном изменении
формата вывода (после ревью).

Внимание: эталоны зависят от:
- BASE_CARD в tests/fixtures/synthetic_cards.py
- hardcoded queries в этом файле
- versions installed (Python, pydantic, etc.)

Если BASE_CARD меняется — нужно перегенерировать эталоны.
"""
import asyncio
import json
import sys
from pathlib import Path

# Добавляем корень проекта в sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

# Добавляем miniapp/ в sys.path (как в conftest.py)
MINIAPP_ROOT = PROJECT_ROOT / "miniapp"
sys.path.insert(0, str(MINIAPP_ROOT))

# Папка для эталонов
FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)


def _save_json(data, relative_path: str) -> None:
    """Сохраняет data как JSON с сортировкой ключей."""
    path = FIXTURES_DIR / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    path.write_text(serialized + "\n", encoding="utf-8")
    print(f"  ✓ {relative_path} ({len(serialized)} bytes)")


def _save_text(data: str, relative_path: str) -> None:
    """Сохраняет строку как текстовый файл."""
    path = FIXTURES_DIR / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")
    print(f"  ✓ {relative_path} ({len(data)} bytes)")


# ============================================================
# 1. gibdd_parser.parse_card_to_row → JSON
# ============================================================
def generate_parser_golden() -> None:
    """Эталон для parse_card_to_row на всех типах карточек."""
    from gibdd_parser import parse_card_to_row
    from tests.fixtures.synthetic_cards import (
        BASE_CARD,
        card_with_alcohol,
        card_with_death,
        card_with_pedestrian,
        card_unknown_type,
    )

    print("Generating parser golden...")

    # Каждая карточка → отдельный эталон
    cards = {
        "base": BASE_CARD,
        "with_death": card_with_death(),
        "with_alcohol": card_with_alcohol(),
        "with_pedestrian": card_with_pedestrian(),
        "unknown_type": card_unknown_type(),
    }

    for name, card in cards.items():
        row = parse_card_to_row(card)
        _save_json(row, f"parser/card_{name}.json")


# ============================================================
# 2. analytics.calculate_metrics → JSON
# ============================================================
def generate_analytics_golden() -> None:
    """Эталон для calculate_metrics на cards_basic_set()."""
    from analytics import calculate_metrics, calculate_cross_tables
    from tests.fixtures.synthetic_cards import cards_basic_set

    print("Generating analytics golden...")

    cards = cards_basic_set()

    metrics = calculate_metrics(cards)
    _save_json(metrics, "analytics/metrics_basic_set.json")

    cross_tables = calculate_cross_tables(cards)
    _save_json(cross_tables, "analytics/cross_tables_basic_set.json")


# ============================================================
# 3. analytics.compare_metrics → JSON
# ============================================================
def generate_compare_golden() -> None:
    """Эталон для compare_metrics: сравнение 2 выборок карточек."""
    from analytics import calculate_metrics, compare_metrics
    from tests.fixtures.synthetic_cards import (
        BASE_CARD,
        card_with_alcohol,
        card_with_death,
        card_with_pedestrian,
        card_unknown_type,
        make_card,
    )

    print("Generating compare golden...")

    # 2 набора карточек: "current" (май 2025) vs "previous" (апрель 2025)
    # Используем те же карточки, но с разными pog/ran для реалистичного diff.
    current_cards = [
        BASE_CARD,
        card_with_death(),
        card_with_alcohol(),
        card_with_pedestrian(),
        card_unknown_type(),
    ]
    previous_cards = [
        make_card(kart_id="p001", pog="0", ran="2"),
        make_card(kart_id="p002", pog="2", ran="0"),  # больше погибших
        make_card(kart_id="p003"),  # без алкоголя
        make_card(kart_id="p004", dtpv="Столкновение"),
        make_card(kart_id="p005", dtpv="Опрокидывание"),
    ]

    current_metrics = calculate_metrics(current_cards)
    previous_metrics = calculate_metrics(previous_cards)

    comparison = compare_metrics(current_metrics, previous_metrics)
    _save_json(comparison, "analytics/comparison_may_vs_april.json")


# ============================================================
# 4. llm_analyzer.format_metrics_for_prompt → text
# ============================================================
def generate_llm_prompt_golden() -> None:
    """Эталон для format_metrics_for_prompt на конкретном comparison."""
    from analytics import calculate_metrics, compare_metrics
    from llm_analyzer import format_metrics_for_prompt
    from tests.fixtures.synthetic_cards import (
        BASE_CARD,
        card_with_alcohol,
        card_with_death,
        card_with_pedestrian,
        card_unknown_type,
        make_card,
    )

    print("Generating LLM prompt golden...")

    current_cards = [
        BASE_CARD,
        card_with_death(),
        card_with_alcohol(),
        card_with_pedestrian(),
        card_unknown_type(),
    ]
    previous_cards = [
        make_card(kart_id="p001", pog="0", ran="2"),
        make_card(kart_id="p002", pog="2", ran="0"),
        make_card(kart_id="p003"),
        make_card(kart_id="p004", dtpv="Столкновение"),
        make_card(kart_id="p005", dtpv="Опрокидывание"),
    ]

    current_metrics = calculate_metrics(current_cards)
    previous_metrics = calculate_metrics(previous_cards)
    comparison = compare_metrics(current_metrics, previous_metrics)

    prompt = format_metrics_for_prompt(
        comparison,
        reg_name="Вологодская область",
        current_label="Май 2025",
        prev_label="Апрель 2025",
    )
    _save_text(prompt, "llm/metrics_prompt_may_vs_april.txt")


# ============================================================
# 5. user_request_parser.parse_period → JSON
# ============================================================
def generate_parse_period_golden() -> None:
    """Эталон для parse_period на разных текстовых запросах."""
    from user_request_parser import parse_period

    print("Generating parse_period golden...")

    # (query, description)
    test_cases = [
        ("за 2025 год", "year_only"),
        ("за 3 месяца 2025", "3_months"),
        ("март 2025", "specific_month"),
        ("за I квартал 2025", "q1_roman"),
        ("за II квартал 2025", "q2_roman"),
        ("за III квартал 2025", "q3_roman"),
        ("за IV квартал 2025", "q4_roman"),
        ("за полугодие 2025", "half_year"),
        ("за первое полугодие 2026", "first_half_year_named"),
        ("2.2024", "month_year_short"),
    ]

    results = {}
    for query, key in test_cases:
        period = parse_period(query)
        if period is None:
            results[key] = {"input": query, "result": None}
        else:
            results[key] = {
                "input": query,
                "result": {
                    "months": period.months,
                    "year": period.year,
                    "label": period.label,
                    "dat_list": period.get_dat_list(),
                },
            }

    _save_json(results, "parser/parse_period_cases.json")


# ============================================================
# 6. user_request_parser.find_region → JSON
# ============================================================
def generate_find_region_golden() -> None:
    """Эталон для find_region на разных запросах регионов."""
    from regions_builtin import BUILTIN_REGIONS
    from user_request_parser import find_region

    print("Generating find_region golden...")

    test_cases = [
        "Вологодская область",
        "Вологодская",
        "Москва",
        "г. Москва",
        "Республика Татарстан",
        "Татарстан",
        "Алтайский край",
        "Ленинградская область",
        "77",  # код региона
        "несуществующий регион xyz",
    ]

    results = {}
    for query in test_cases:
        result = find_region(query, BUILTIN_REGIONS)
        results[query] = result  # tuple или None

    _save_json(results, "parser/find_region_cases.json")


# ============================================================
# 7. analytics.group_dtp_type → JSON (вспомогательная функция)
# ============================================================
def generate_grouping_golden() -> None:
    """Эталон для group_dtp_type — какой raw тип в какую группу попадает."""
    from analytics import group_dtp_type, group_road_significance

    print("Generating grouping golden...")

    dtp_types = [
        "Столкновение",
        "Наезд на пешехода",
        "Наезд на велосипедиста",
        "Наезд на животное",
        "Опрокидывание",
        "Падение пассажира",
        "Иной вид ДТП",
        "",
        "Съезд с дороги",
    ]

    dtp_results = {t: group_dtp_type(t) for t in dtp_types}
    _save_json(dtp_results, "analytics/group_dtp_type.json")

    road_types = [
        "Федерального значения",
        "Регионального значения",
        "Муниципального значения",
        "Иной категории",
        "",
    ]
    road_results = {t: group_road_significance(t) for t in road_types}
    _save_json(road_results, "analytics/group_road_significance.json")


# ============================================================
# Main
# ============================================================
def main() -> None:
    print("=" * 60)
    print("Generating golden files...")
    print(f"  Output: {FIXTURES_DIR}")
    print("=" * 60)

    generate_parser_golden()
    generate_analytics_golden()
    generate_compare_golden()
    generate_llm_prompt_golden()
    generate_parse_period_golden()
    generate_find_region_golden()
    generate_grouping_golden()

    print("=" * 60)
    print("Done. Etalons saved to:", FIXTURES_DIR)
    print("=" * 60)


if __name__ == "__main__":
    main()
