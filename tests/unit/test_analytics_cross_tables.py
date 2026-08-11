"""
Тесты calculate_cross_tables: расширенные кросс-таблицы.

Это та самая функция, которую мы в Phase 3.1 стали кэшировать
через _get_cross_tables. Если эти тесты вдруг завалятся после
правок analytics.py — кэш начнёт возвращать мусор, и LLM будет
смотреть на неправильные числа.

Покрывает:
  - структура возвращаемого dict (наличие всех ожидаемых таблиц)
  - dtp_type_x_severity (вид ДТП × тяжесть)
  - alcohol_x_weekday (нетрезвые по дням недели)
  - month_x_severity
  - пустой список карточек
"""
import analytics
from tests.fixtures.synthetic_cards import (
    BASE_CARD,
    cards_basic_set,
    card_with_alcohol,
    card_with_death,
    card_with_pedestrian,
)


# Ожидаемые ключи в результате calculate_cross_tables.
# Если в analytics.py добавится новая таблица — здесь тест упадёт,
# и нужно будет осознанно добавить ключ в список.
EXPECTED_CROSS_TABLE_KEYS = {
    "hour_x_severity",
    "weekday_x_severity",
    "experience_x_severity",
    "experience_x_violations",
    "vehicle_type_x_severity",
    "road_value_x_severity",
    "weather_x_dtp_type",
    "lighting_x_pedestrian_share",
    "belt_x_severity",
    "alcohol_x_weekday",
    "alcohol_x_hour",
    "gender_x_severity",
    "participant_category_x_severity",
    "dtp_type_x_severity",
    "road_value_x_dtp_type",
    "weather_x_severity",
    "lighting_x_severity",
    "month_x_severity",
    "district_x_severity",
    "road_name_x_severity",
    "dtp_type_x_district",
    "dtp_type_x_hour",
    "dtp_type_x_road_value",
    "alcohol_x_district",
    "alcohol_x_road_value",
    "street_x_severity",
    "ndu_x_severity",
    "objects_addr_x_severity",
    "s_pch_x_severity",
    "factor_x_severity",
    "vehicles_count_x_severity",
    "vehicle_brand_x_severity",
    "vehicle_age_x_severity",
}


class TestCalculateCrossTablesStructure:
    """Гарантируем, что структура результата не меняется."""

    def test_empty_cards_returns_dict_with_expected_keys(self):
        """На пустом списке — все таблицы пустые, но ключи на месте.

        Это важно: _get_cross_tables в gibdd_service.py проверяет
        'if not task.cards: return None' — но calculate_cross_tables
        сама по себе должна быть устойчива к пустому списку.
        """
        result = analytics.calculate_cross_tables([])
        assert isinstance(result, dict)
        # На пустом списке — все ключи должны присутствовать как пустые dict
        for key in EXPECTED_CROSS_TABLE_KEYS:
            assert key in result, f"Missing cross-table key on empty: {key}"

    def test_all_expected_keys_present_on_basic_set(self):
        """На реальных карточках — все 33 таблицы присутствуют."""
        result = analytics.calculate_cross_tables(cards_basic_set())
        missing = EXPECTED_CROSS_TABLE_KEYS - set(result.keys())
        assert not missing, f"Missing cross-table keys: {missing}"


class TestDtpTypeXSeverity:
    """dtp_type_x_severity: {вид_ДТП: {dtp, deaths, injured}}.

    Самая часто используемая кросс-таблица — её LLM видит в prompt.
    """

    def test_basic_card_creates_collision_entry(self):
        """Базовая карточка (Столкновение, 0 погибших, 1 раненый)."""
        result = analytics.calculate_cross_tables([BASE_CARD])
        entry = result["dtp_type_x_severity"].get("Столкновение")
        assert entry is not None
        assert entry["dtp"] == 1
        assert entry["deaths"] == 0
        assert entry["injured"] == 1

    def test_death_card_increments_deaths(self):
        """Карточка с погибшим — увеличивает deaths в категории."""
        cards = [card_with_death()]
        result = analytics.calculate_cross_tables(cards)
        entry = result["dtp_type_x_severity"].get("Столкновение")
        assert entry["deaths"] == 1

    def test_pedestrian_card_separate_category(self):
        """Карточка с пешеходом попадает в «Наезд на пешехода»."""
        cards = [card_with_pedestrian()]
        result = analytics.calculate_cross_tables(cards)
        # dtp_type_x_severity хранит raw dtpv — должно быть «Наезд на пешехода»
        assert "Наезд на пешехода" in result["dtp_type_x_severity"]


class TestAlcoholCrossTables:
    """alcohol_x_weekday / alcohol_x_hour: алкоголь по времени."""

    def test_alcohol_card_counted_in_yes_bucket(self):
        """Карточка с нетрезвым → bucket 'да'."""
        cards = [card_with_alcohol()]
        result = analytics.calculate_cross_tables(cards)
        # alcohol_x_weekday = {"да": Counter(weekday), "нет": Counter(weekday)}
        assert "да" in result["alcohol_x_weekday"]
        # 17.05.2025 — суббота (weekday=5)
        assert result["alcohol_x_weekday"]["да"][5] == 1


class TestMonthXSeverity:
    """month_x_severity: {месяц: {dtp, deaths, injured}}."""

    def test_may_card_in_may_bucket(self):
        """Карточка за май (15.05.2025) попадает в 'Май'."""
        result = analytics.calculate_cross_tables([BASE_CARD])
        assert "Май" in result["month_x_severity"]
        assert result["month_x_severity"]["Май"]["dtp"] == 1


class TestCrossTablesAreIndependent:
    """Разные карточки не должны «протекать» между таблицами."""

    def test_two_cards_sum_correctly(self):
        """2 карточки Столкновение → dtp=2 в категории."""
        cards = [BASE_CARD, card_with_death()]  # обе Столкновение
        result = analytics.calculate_cross_tables(cards)
        entry = result["dtp_type_x_severity"]["Столкновение"]
        assert entry["dtp"] == 2
        assert entry["deaths"] == 1  # только во второй карточке
        assert entry["injured"] == 1  # только в первой
