"""
Тесты compare_metrics: процент изменения между периодами.

Критичные edge-cases:
  - old=0, new=0  → change=0.0 (не NaN, не +∞)
  - old=0, new>0  → change=100.0 (не +∞, не ZeroDivisionError)
  - old>0, new=0  → change=-100.0
  - deaths_per_100: разница, а не процент (это abs_change)
  - by_weekday / by_hour / etc. передаются как {current, previous}
"""
import math
import analytics


def _make_metrics(total=0, deaths=0, injured=0, alcohol=0, pedestrians=0,
                  deaths_per_100=0.0, injured_per_100=0.0):
    """Минимальный словарь метрик, валидный для compare_metrics.

    Все недостающие поля calculate_metrics обычно добавляет — но для
    тестирования compare_metrics изолированно нужны только те ключи,
    к которым функция обращается.
    """
    return {
        "total": total, "deaths": deaths, "injured": injured,
        "alcohol": alcohol, "pedestrians": pedestrians,
        "deaths_per_100": deaths_per_100, "injured_per_100": injured_per_100,
        "by_weekday": {}, "by_hour": {}, "by_type": {},
        "by_type_grouped": {}, "by_weather": {}, "by_road": {},
        "by_month": {}, "by_weekday_severity": {}, "by_hour_severity": {},
        "by_type_grouped_severity": {}, "by_weather_severity": {},
        "by_road_significance": {},
    }


class TestCompareMetricsEdgeCases:
    """Граничные случаи, на которых чаще всего ломаются аналитики."""

    def test_zero_to_zero_change_is_zero(self):
        """old=0, new=0 → change=0.0, не NaN и не ZeroDivisionError."""
        result = analytics.compare_metrics(
            _make_metrics(total=0), _make_metrics(total=0),
        )
        assert result["total"]["change"] == 0.0
        assert math.isfinite(result["total"]["change"])

    def test_zero_to_nonzero_is_100_percent(self):
        """old=0, new=5 → +100.0% (не +∞, не ZeroDivisionError).

        Это сознательное решение:_pct_change возвращает 100.0,
        а не float('inf'), чтобы downstream код не падал на format.
        """
        result = analytics.compare_metrics(
            _make_metrics(total=5), _make_metrics(total=0),
        )
        assert result["total"]["change"] == 100.0
        assert math.isfinite(result["total"]["change"])

    def test_nonzero_to_zero_is_minus_100(self):
        """old=5, new=0 → -100.0%."""
        result = analytics.compare_metrics(
            _make_metrics(total=0), _make_metrics(total=5),
        )
        assert result["total"]["change"] == -100.0

    def test_doubling_is_plus_100(self):
        """old=5, new=10 → +100.0%."""
        result = analytics.compare_metrics(
            _make_metrics(total=10), _make_metrics(total=5),
        )
        assert result["total"]["change"] == 100.0

    def test_halving_is_minus_50(self):
        """old=10, new=5 → -50.0%."""
        result = analytics.compare_metrics(
            _make_metrics(total=5), _make_metrics(total=10),
        )
        assert result["total"]["change"] == -50.0

    def test_abs_change_is_correct(self):
        """abs_change = current - previous, без знака процента."""
        result = analytics.compare_metrics(
            _make_metrics(total=15), _make_metrics(total=10),
        )
        assert result["total"]["abs_change"] == 5

    def test_deaths_per_100_is_difference_not_pct(self):
        """deaths_per_100.change = current - previous (абсолют, не %).

        Это часто путают — но в коде так: для per_100 это разница,
        а для total/deaths/injured — процент.
        """
        result = analytics.compare_metrics(
            _make_metrics(deaths_per_100=5.5), _make_metrics(deaths_per_100=3.0),
        )
        assert result["deaths_per_100"]["change"] == 2.5
        assert result["deaths_per_100"]["abs_change"] == 2.5


class TestCompareMetricsStructure:
    """Проверка, что compare_metrics возвращает все ожидаемые ключи."""

    def test_all_metric_keys_present(self):
        """Все 7 метрик должны быть в результате."""
        result = analytics.compare_metrics(
            _make_metrics(), _make_metrics(),
        )
        expected_keys = {
            "total", "deaths", "injured", "alcohol", "pedestrians",
            "deaths_per_100", "injured_per_100",
        }
        assert expected_keys.issubset(result.keys())

    def test_each_metric_has_current_previous_change_abs_change(self):
        """Каждая метрика имеет 4 поля: current, previous, change, abs_change."""
        result = analytics.compare_metrics(
            _make_metrics(total=10, deaths=2),
            _make_metrics(total=8, deaths=1),
        )
        for key in ("total", "deaths", "injured", "alcohol", "pedestrians",
                    "deaths_per_100", "injured_per_100"):
            assert "current" in result[key], f"{key} missing 'current'"
            assert "previous" in result[key], f"{key} missing 'previous'"
            assert "change" in result[key], f"{key} missing 'change'"
            assert "abs_change" in result[key], f"{key} missing 'abs_change'"

    def test_distributions_passed_through(self):
        """by_weekday/by_hour/etc. передаются как {current, previous}."""
        cur = _make_metrics()
        cur["by_weekday"] = {0: 5, 1: 3}
        prev = _make_metrics()
        prev["by_weekday"] = {0: 2}
        result = analytics.compare_metrics(cur, prev)
        assert result["by_weekday"]["current"] == {0: 5, 1: 3}
        assert result["by_weekday"]["previous"] == {0: 2}
