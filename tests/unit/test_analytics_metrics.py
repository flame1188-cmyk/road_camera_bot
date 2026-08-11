"""
Тесты calculate_metrics: основные счётчики.

Покрывает:
  - total / deaths / injured / alcohol / pedestrians
  - deaths_per_100, injured_per_100
  - by_weekday (валидный и невалидный date_dtp)
  - by_hour (валидный и пустой time)
  - by_type / by_type_grouped (включая «Иные ДТП»)
  - пустой список карточек
"""
import analytics
from tests.fixtures.synthetic_cards import (
    BASE_CARD,
    cards_basic_set,
    card_with_alcohol,
    card_with_death,
    card_with_pedestrian,
    card_unknown_type,
    card_empty_time,
    card_invalid_date,
)


class TestCalculateMetricsBasic:
    """Базовые счётчики — должна корректно суммировать по карточкам."""

    def test_empty_cards_returns_zeros(self):
        """Пустой список → все нули, без ZeroDivisionError."""
        m = analytics.calculate_metrics([])
        assert m["total"] == 0
        assert m["deaths"] == 0
        assert m["injured"] == 0
        assert m["alcohol"] == 0
        assert m["pedestrians"] == 0
        assert m["deaths_per_100"] == 0
        assert m["injured_per_100"] == 0
        assert m["by_weekday"] == {}
        assert m["by_hour"] == {}
        assert m["by_type"] == {}

    def test_total_count(self):
        """len(cards) == total."""
        cards = cards_basic_set()
        m = analytics.calculate_metrics(cards)
        assert m["total"] == 5

    def test_deaths_count(self):
        """Считает pog по всем карточкам."""
        cards = [card_with_death(), card_with_death()]
        m = analytics.calculate_metrics(cards)
        assert m["deaths"] == 2

    def test_injured_count(self):
        """Считает ran по всем карточкам."""
        cards = cards_basic_set()
        m = analytics.calculate_metrics(cards)
        # BASE_CARD (1) + death (0) + alcohol (1, наследует ran=1)
        # + pedestrian (2) + unknown_type (1, наследует ran=1) = 5
        assert m["injured"] == 5

    def test_alcohol_detection(self):
        """ДТП с alco != '0' считается как alcohol."""
        cards = [card_with_alcohol()]
        m = analytics.calculate_metrics(cards)
        assert m["alcohol"] == 1

    def test_alcohol_zero_not_counted(self):
        """alco == '0' не считается как нетрезвый."""
        cards = [BASE_CARD]
        m = analytics.calculate_metrics(cards)
        assert m["alcohol"] == 0

    def test_pedestrian_detection(self):
        """ДТП с пешеходом в uch_info считается."""
        cards = [card_with_pedestrian()]
        m = analytics.calculate_metrics(cards)
        assert m["pedestrians"] == 1

    def test_pedestrian_by_dtpv_fallback(self):
        """Если uch_info пустой, но dtpv содержит «пешеход» — fallback."""
        card = {
            **BASE_CARD,
            "uch_info": [],
            "dtpv": "Наезд на пешехода",
        }
        m = analytics.calculate_metrics([card])
        assert m["pedestrians"] == 1


class TestCalculateMetricsPer100:
    """deaths_per_100 / injured_per_100 — пропорции на 100 ДТП."""

    def test_per_100_calculation(self):
        """10 ДТП, 5 погибших → 50.0 на 100."""
        cards = [{**BASE_CARD, "pog": "1"} for _ in range(10)]
        # 9 копий с pog=1 + BASE_CARD с pog=0 → 9 погибших на 10 ДТП
        # Но мы сделали pog=1 во всех 10, так что 10/10*100 = 100.0
        # Actually: 10 cards × pog=1 → 10 deaths, /10 × 100 = 100.0
        m = analytics.calculate_metrics(cards)
        assert m["deaths_per_100"] == 100.0

    def test_per_100_rounded_to_one_decimal(self):
        """3 ДТП, 1 погибший → 33.3 (округление до 1 знака)."""
        cards = [
            {**BASE_CARD, "pog": "1"},
            {**BASE_CARD, "pog": "0"},
            {**BASE_CARD, "pog": "0"},
        ]
        m = analytics.calculate_metrics(cards)
        assert m["deaths_per_100"] == 33.3


class TestCalculateMetricsByWeekday:
    """by_weekday: разбор date_dtp → weekday (0=Пн, 6=Вс)."""

    def test_valid_date_parsed(self):
        """15.05.2025 — четверг (weekday=3)."""
        m = analytics.calculate_metrics([BASE_CARD])
        assert m["by_weekday"].get(3) == 1  # Thursday

    def test_invalid_date_skipped(self):
        """Битая дата не должна учитываться в by_weekday."""
        cards = [card_invalid_date()]
        m = analytics.calculate_metrics(cards)
        assert m["by_weekday"] == {}


class TestCalculateMetricsByHour:
    """by_hour: разбор time 'HH:MM' → hour."""

    def test_valid_time_parsed(self):
        """14:30 → hour 14."""
        m = analytics.calculate_metrics([BASE_CARD])
        assert m["by_hour"].get(14) == 1

    def test_empty_time_skipped(self):
        """Пустое time не должно учитываться в by_hour."""
        cards = [card_empty_time()]
        m = analytics.calculate_metrics(cards)
        assert m["by_hour"] == {}

    def test_invalid_hour_returns_none(self):
        """time='25:99' → не должно ломаться, просто пропустить."""
        # _get_hour возвращает None для hour > 23
        card = {**BASE_CARD, "time": "25:99"}
        m = analytics.calculate_metrics([card])
        assert m["by_hour"] == {}


class TestCalculateMetricsByType:
    """by_type и by_type_grouped: вид ДТП raw + каноническая категория."""

    def test_raw_type_preserved(self):
        """by_type содержит ровно тот dtpv, что был в карточке."""
        m = analytics.calculate_metrics([BASE_CARD])
        assert m["by_type"].get("Столкновение") == 1

    def test_grouped_to_canonical(self):
        """'Столкновение' → категория 'Столкновение'."""
        m = analytics.calculate_metrics([BASE_CARD])
        assert m["by_type_grouped"].get("Столкновение") == 1

    def test_unknown_type_goes_to_other(self):
        """Неизвестный вид ДТП → 'Иные ДТП'."""
        m = analytics.calculate_metrics([card_unknown_type()])
        assert m["by_type_grouped"].get("Иные ДТП") == 1

    def test_empty_dtpv_skipped_in_raw_but_counted_in_grouped(self):
        """Пустой dtpv не попадает в by_type, но в grouped → 'Иные ДТП'."""
        card = {**BASE_CARD, "dtpv": ""}
        m = analytics.calculate_metrics([card])
        assert m["by_type"] == {}
        assert m["by_type_grouped"].get("Иные ДТП") == 1
