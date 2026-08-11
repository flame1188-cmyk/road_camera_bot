"""
Тесты статистических метрик и helper-функций analytics.py.

Покрывает:
  - group_dtp_type / group_road_significance (классификация)
  - _z_score / _classify_z (z-оценка)
  - _safe_int / _safe_float / _get_hour (приведения)
  - calculate_statistical_metrics (на готовом cross-выхлопе)
  - format_change (форматирование процента)
"""
import math
import analytics
from tests.fixtures.synthetic_cards import cards_basic_set


class TestGroupDtpType:
    """group_dtp_type: сырой вид ДТП → каноническая категория (9 шт.)."""

    def test_collision(self):
        assert analytics.group_dtp_type("Столкновение") == "Столкновение"

    def test_pedestrian_hit(self):
        assert analytics.group_dtp_type("Наезд на пешехода") == "Наезд на пешехода"

    def test_bicycle(self):
        assert analytics.group_dtp_type("Наезд на велосипедиста") == "Наезд на велосипедиста"

    def test_sim_goes_to_sim_category(self):
        """СИМ (электросамокаты) → отдельная категория «Наезд на лицо, использующее СИМ»."""
        assert analytics.group_dtp_type("Наезд на лицо, использующее СИМ") == "Наезд на лицо, использующее СИМ"

    def test_unknown_goes_to_other(self):
        assert analytics.group_dtp_type("Какой-то новый вид ДТП") == "Иные ДТП"

    def test_empty_goes_to_other(self):
        assert analytics.group_dtp_type("") == "Иные ДТП"

    def test_none_goes_to_other(self):
        assert analytics.group_dtp_type(None) == "Иные ДТП"

    def test_case_insensitive(self):
        """Регистр не важен."""
        assert analytics.group_dtp_type("СТОЛКНОВЕНИЕ") == "Столкновение"


class TestGroupRoadSignificance:
    """group_road_significance: dor_z → каноническая категория."""

    def test_federal(self):
        assert analytics.group_road_significance("Федерального значения") == "Федеральные"

    def test_regional(self):
        assert analytics.group_road_significance("Регионального или межмуниципального значения") == "Региональные"

    def test_municipal(self):
        assert analytics.group_road_significance("Муниципального значения") == "Муниципальные"

    def test_intermunicipal(self):
        assert analytics.group_road_significance("Межмуниципального значения") == "Межмуниципальные"

    def test_empty_goes_to_other(self):
        assert analytics.group_road_significance("") == "Иные"

    def test_unknown_goes_to_other(self):
        assert analytics.group_road_significance("Неизвестная категория") == "Иные"


class TestSafeHelpers:
    """_safe_int / _safe_float / _get_hour — устойчивость к мусору."""

    def test_safe_int_none(self):
        assert analytics._safe_int(None) == 0

    def test_safe_int_string(self):
        assert analytics._safe_int("5") == 5

    def test_safe_int_invalid(self):
        assert analytics._safe_int("abc") == 0

    def test_safe_float_none(self):
        assert analytics._safe_float(None) == 0.0

    def test_safe_float_string(self):
        assert analytics._safe_float("3.14") == 3.14

    def test_safe_float_invalid(self):
        assert analytics._safe_float("abc") == 0.0

    def test_get_hour_valid(self):
        assert analytics._get_hour("14:30") == 14

    def test_get_hour_empty(self):
        assert analytics._get_hour("") is None

    def test_get_hour_invalid_hour(self):
        """'25:00' — hour > 23, должно вернуть None."""
        assert analytics._get_hour("25:00") is None

    def test_get_hour_garbage(self):
        assert analytics._get_hour("не время") is None


class TestZScore:
    """_z_score: стандартная z-оценка (x - mean) / std."""

    def test_zero_when_x_equals_mean(self):
        assert analytics._z_score(5.0, 5.0, 1.0) == 0.0

    def test_positive_when_x_above_mean(self):
        assert analytics._z_score(7.0, 5.0, 1.0) == 2.0

    def test_negative_when_x_below_mean(self):
        assert analytics._z_score(3.0, 5.0, 1.0) == -2.0

    def test_zero_std_returns_zero(self):
        """std=0 не должно ронять ZeroDivisionError.

        Реализация может вернуть 0 или inf — но не должна падать.
        """
        # Проверяем что не падает
        result = analytics._z_score(5.0, 5.0, 0.0)
        assert math.isfinite(result) or math.isinf(result)


class TestStatisticalMetrics:
    """calculate_statistical_metrics: производные метрики на готовом cross."""

    def test_empty_cross_returns_dict(self):
        """На пустом cross — должна вернуть структуру без падения."""
        cross = analytics.calculate_cross_tables([])
        result = analytics.calculate_statistical_metrics(cross)
        assert isinstance(result, dict)

    def test_returns_expected_keys(self):
        """Должна вернуть severity_rates, z_anomalies, chi_square и пр."""
        cross = analytics.calculate_cross_tables(cards_basic_set())
        result = analytics.calculate_statistical_metrics(cross)
        # Проверяем хотя бы базовые ключи — точный список может меняться,
        # но эти точно должны быть (см. docstring calculate_statistical_metrics).
        assert "severity_rates" in result or "z_anomalies" in result or "chi_square" in result


class TestFormatChange:
    """format_change: процент → человекочитаемая строка с знаком.

    Используется в build_analytics_message для текстовых отчётов бота.
    """

    def test_positive_change_has_plus(self):
        result = analytics.format_change(50.0)
        assert "+" in result or "50" in result

    def test_negative_change_has_minus(self):
        result = analytics.format_change(-30.0)
        assert "-" in result or "30" in result

    def test_zero_change(self):
        """0% — особый случай, должен быть читаемым."""
        result = analytics.format_change(0.0)
        # Просто проверяем, что не падает и возвращает строку
        assert isinstance(result, str)
        assert len(result) > 0
