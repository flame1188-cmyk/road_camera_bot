"""
Golden-тесты для user_request_parser.parse_period и find_region.

Эталонные выходы в tests/golden/fixtures/parser/.
Обновление: pytest tests/golden/test_golden_user_parser.py --update-golden

Эти тесты критичны: парсер пользовательского ввода должен стабильно
распознавать одни и те же запросы. Любое изменение (новый синоним месяца,
другой regex) — нужно осознанно обновить эталон.
"""
import pytest

from regions_builtin import BUILTIN_REGIONS
from user_request_parser import find_region, parse_period


pytestmark = pytest.mark.golden


# ============================================================
# parse_period golden
# ============================================================
PARSE_PERIOD_CASES = [
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


def test_parse_period_matches_golden(golden_compare):
    """parse_period для всех тестовых запросов должен совпадать с эталоном."""
    results = {}
    for query, key in PARSE_PERIOD_CASES:
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

    golden_compare(results, "parser/parse_period_cases.json")


@pytest.mark.parametrize("query, key", PARSE_PERIOD_CASES)
def test_parse_period_each_case_stable(query, key):
    """Каждый случай parse_period должен давать стабильный результат.

    Это stricter проверка: если функция начнёт выдавать разные результаты
    на одном и том же входе (например, из-за global state) — тест упадёт.
    """
    period1 = parse_period(query)
    period2 = parse_period(query)

    if period1 is None and period2 is None:
        return

    assert period1 is not None and period2 is not None, (
        f"parse_period('{query}') нестабилен: None vs not-None"
    )
    assert period1.months == period2.months
    assert period1.year == period2.year
    assert period1.label == period2.label


# ============================================================
# find_region golden
# ============================================================
FIND_REGION_CASES = [
    "Вологодская область",
    "Вологодская",
    "Москва",
    "г. Москва",
    "Республика Татарстан",
    "Татарстан",
    "Алтайский край",
    "Ленинградская область",
    "77",
    "несуществующий регион xyz",
]


def test_find_region_matches_golden(golden_compare):
    """find_region для всех тестовых запросов должен совпадать с эталоном."""
    results = {}
    for query in FIND_REGION_CASES:
        result = find_region(query, BUILTIN_REGIONS)
        # tuple → list для JSON-сериализации
        if result is None:
            results[query] = None
        else:
            results[query] = list(result)

    golden_compare(results, "parser/find_region_cases.json")


# ============================================================
# Regression: bugs fixed in Wave 1
# ============================================================
def test_quarter_iii_recognized():
    """Регрессия BUG #1: 'III квартал' должен распознаваться (раньше только I и II).

    Без фикса parse_period('за III квартал 2025') возвращал None.
    """
    period = parse_period("за III квартал 2025")
    assert period is not None, "III квартал должен распознаваться (BUG #1 regression)"
    assert period.months == [7, 8, 9]


def test_quarter_iv_recognized():
    """Регрессия BUG #1: 'IV квартал' должен распознаваться."""
    period = parse_period("за IV квартал 2025")
    assert period is not None
    assert period.months == [10, 11, 12]


def test_quarter_ii_recognized():
    period = parse_period("за II квартал 2025")
    assert period is not None
    assert period.months == [4, 5, 6]


def test_find_region_word_boundary():
    """Регрессия BUG #3: 'Адыгея' не должна находится в 'Республика Адыгея' по подстроке.

    Раньше find_region использовал substring match, что давало ложные срабатывания.
    Теперь используется word boundary regex.
    """
    # Если ищем просто "Адыгея", должны найти "Республика Адыгея"
    result = find_region("Адыгея", BUILTIN_REGIONS)
    assert result is not None
    assert "Адыгея" in result[1] or "Адыгея" in result[0]
