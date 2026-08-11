"""
Golden-тесты для analytics.calculate_metrics и calculate_cross_tables.

Эталонные выходы сохранены в tests/golden/fixtures/analytics/.
Обновление: pytest tests/golden/test_golden_analytics.py --update-golden
"""
import pytest

from analytics import calculate_cross_tables, calculate_metrics, compare_metrics
from tests.fixtures.synthetic_cards import (
    BASE_CARD,
    card_with_alcohol,
    card_with_death,
    card_with_pedestrian,
    card_unknown_type,
    cards_basic_set,
    make_card,
)


pytestmark = pytest.mark.golden


def test_calculate_metrics_basic_set_matches_golden(golden_compare):
    """calculate_metrics на cards_basic_set() должен совпадать с эталоном."""
    metrics = calculate_metrics(cards_basic_set())
    golden_compare(metrics, "analytics/metrics_basic_set.json")


def test_calculate_cross_tables_basic_set_matches_golden(golden_compare):
    """calculate_cross_tables на cards_basic_set() должен совпадать с эталоном."""
    cross_tables = calculate_cross_tables(cards_basic_set())
    golden_compare(cross_tables, "analytics/cross_tables_basic_set.json")


def test_compare_metrics_may_vs_april_matches_golden(golden_compare):
    """compare_metrics для двух выборок (май vs апрель) должен совпадать с эталоном."""
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

    golden_compare(comparison, "analytics/comparison_may_vs_april.json")


# ============================================================
# Grouping golden tests
# ============================================================
def test_group_dtp_type_matches_golden(golden_compare):
    """group_dtp_type для разных raw типов должен совпадать с эталоном."""
    from analytics import group_dtp_type

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
    results = {t: group_dtp_type(t) for t in dtp_types}
    golden_compare(results, "analytics/group_dtp_type.json")


def test_group_road_significance_matches_golden(golden_compare):
    """group_road_significance для разных типов дорог должен совпадать с эталоном."""
    from analytics import group_road_significance

    road_types = [
        "Федерального значения",
        "Регионального значения",
        "Муниципального значения",
        "Иной категории",
        "",
    ]
    results = {t: group_road_significance(t) for t in road_types}
    golden_compare(results, "analytics/group_road_significance.json")


# ============================================================
# Stability tests (not golden, but related — guarantee metric invariants)
# ============================================================
def test_metrics_total_equals_len_cards():
    """total всегда равен len(cards)."""
    cards = cards_basic_set()
    metrics = calculate_metrics(cards)
    assert metrics["total"] == len(cards)


def test_metrics_deaths_per_100_formula_stable():
    """deaths_per_100 = round(deaths / total * 100, 1)."""
    cards = cards_basic_set()
    metrics = calculate_metrics(cards)
    expected = round(metrics["deaths"] / metrics["total"] * 100, 1)
    assert metrics["deaths_per_100"] == expected


def test_metrics_injured_per_100_formula_stable():
    """injured_per_100 = round(injured / total * 100, 1)."""
    cards = cards_basic_set()
    metrics = calculate_metrics(cards)
    expected = round(metrics["injured"] / metrics["total"] * 100, 1)
    assert metrics["injured_per_100"] == expected
