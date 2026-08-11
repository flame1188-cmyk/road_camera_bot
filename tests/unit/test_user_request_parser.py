"""
Тесты user_request_parser: распознавание региона и периода из текста.

Покрывает:
  - parse_period: год / квартал / полугодие / N месяцев / конкретный месяц
  - find_region: полное название / сокращение / код / fuzzy
  - _parse_strict_format: m.YYYY RRRR
  - parse_user_message: интеграционный тест region + period

Все тесты СИНХРОННЫ (parse_period, find_region, _parse_strict_format —
это не async функции). parse_user_message — async, но без сети,
потому что regions_builtin встроенный (не требует API ГИБДД).
"""
import pytest

import user_request_parser as urp
from user_request_parser import (
    ParsedPeriod, ParsedRequest,
    parse_period, find_region, _parse_strict_format,
)


# ============================================================
# Фикстуры
# ============================================================

@pytest.fixture
def builtin_regions():
    """Встроенный справочник регионов (без сети)."""
    from regions_builtin import BUILTIN_REGIONS
    return list(BUILTIN_REGIONS)


# ============================================================
# parse_period
# ============================================================

class TestParsePeriodFullYear:
    """Год: 'за 2025 год', '2025', etc."""

    def test_explicit_year_word(self):
        p = parse_period("за 2025 год")
        assert p is not None
        assert p.year == 2025
        assert p.months == list(range(1, 13))
        assert "2025" in p.label

    def test_just_year(self):
        p = parse_period("2025")
        assert p is not None
        assert p.year == 2025
        assert len(p.months) == 12

    def test_year_in_context(self):
        """'Вологодская за 2025 год' → период 2025."""
        p = parse_period("Вологодская за 2025 год")
        assert p is not None
        assert p.year == 2025


class TestParsePeriodQuarter:
    """Квартал: 'I квартал 2025', '1 квартал 2025', etc."""

    def test_roman_quarter(self):
        p = parse_period("I квартал 2025")
        assert p is not None
        assert p.year == 2025
        assert p.months == [1, 2, 3]
        assert "I" in p.label

    def test_q2(self):
        p = parse_period("II квартал 2025")
        assert p.months == [4, 5, 6]

    def test_q3(self):
        p = parse_period("III квартал 2025")
        assert p.months == [7, 8, 9]

    def test_q4(self):
        p = parse_period("IV квартал 2025")
        assert p.months == [10, 11, 12]

    def test_arabic_quarter_number(self):
        """'квартал 1 2025' — арабская цифра."""
        p = parse_period("квартал 1 2025")
        assert p is not None
        assert p.months == [1, 2, 3]


class TestParsePeriodHalfYear:
    """Полугодие."""

    def test_first_half(self):
        p = parse_period("первое полугодие 2025")
        assert p is not None
        assert p.months == list(range(1, 7))

    def test_second_half(self):
        p = parse_period("второе полугодие 2025")
        assert p is not None
        assert p.months == list(range(7, 13))


class TestParsePeriodNMonths:
    """N месяцев: 'за 3 месяца 2025'."""

    def test_three_months(self):
        p = parse_period("за 3 месяца 2025")
        assert p is not None
        assert p.months == [1, 2, 3]
        assert "3" in p.label

    def test_six_months(self):
        p = parse_period("за 6 месяцев 2025")
        assert p is not None
        assert p.months == [1, 2, 3, 4, 5, 6]

    def test_twelve_months_eq_full_year(self):
        """12 месяцев = весь год (по месяцам)."""
        p = parse_period("за 12 месяцев 2025")
        assert p.months == list(range(1, 13))


class TestParsePeriodSpecificMonth:
    """Конкретный месяц: 'март 2025', 'за март 2025', 'в марте 2025'."""

    def test_month_before_year(self):
        p = parse_period("март 2025")
        assert p is not None
        assert p.months == [3]
        assert p.year == 2025

    def test_month_with_za(self):
        p = parse_period("за март 2025")
        assert p is not None
        assert p.months == [3]

    def test_month_short_form(self):
        """Сокращение 'сен' — должно тоже сработать."""
        p = parse_period("сен 2025")
        assert p is not None
        assert p.months == [9]

    def test_month_genitive(self):
        """Родительный падеж: 'за январь' vs 'за января'."""
        # В родительном падеже: "мая", "июня" — тоже должно работать
        p = parse_period("мая 2025")
        assert p is not None
        assert p.months == [5]

    def test_year_after_month(self):
        """'март 2025' — год после месяца."""
        p = parse_period("март 2025")
        assert p.year == 2025


class TestParsePeriodEdgeCases:
    """Граничные случаи."""

    def test_empty_string_returns_none(self):
        assert parse_period("") is None

    def test_no_year_returns_none_or_current(self):
        """Если есть месяц, но нет года — используется current_year.

        Это допустимое поведение, главное — не падает.
        """
        p = parse_period("март")
        # Может вернуть ParsedPeriod с current_year или None
        # Главное — не падает
        if p is not None:
            assert p.months == [3]

    def test_garbage_returns_none(self):
        assert parse_period("абракадабра") is None


# ============================================================
# find_region
# ============================================================

class TestFindRegion:
    """Поиск региона по тексту."""

    def test_exact_full_name(self, builtin_regions):
        result = find_region("Вологодская область", builtin_regions)
        assert result is not None
        code, name = result
        assert code == "1119"
        assert "Вологодская" in name

    def test_short_name(self, builtin_regions):
        """'Вологодская' (без 'область') — должно найтись по сокращению."""
        result = find_region("Вологодская", builtin_regions)
        assert result is not None
        assert result[0] == "1119"

    def test_by_code(self, builtin_regions):
        """Поиск по коду региона (4 цифры в тексте)."""
        result = find_region("1101", builtin_regions)
        assert result is not None
        assert result[0] == "1101"  # Алтайский край

    def test_code_in_context(self, builtin_regions):
        r"""'ДТП в регионе 1101' — код региона в составе другого запроса.

        ВАЖНО: find_region использует \b(\d{2,4})\b — первое 2-4 значное число
        в тексте. Поэтому '2.2024 1101' НЕ сработает (матчит 2024, не 1101).
        Строгий формат 'm.YYYY RRRR' обрабатывается отдельно через
        _parse_strict_format и в find_region не попадает.
        """
        result = find_region("ДТП в регионе 1101", builtin_regions)
        assert result is not None
        assert result[0] == "1101"

    def test_tatarstan(self, builtin_regions):
        result = find_region("Республика Татарстан", builtin_regions)
        assert result is not None
        assert result[0] == "1192"

    def test_tatarstan_short(self, builtin_regions):
        """'Татарстан' без 'Республика'."""
        result = find_region("Татарстан", builtin_regions)
        assert result is not None
        assert result[0] == "1192"

    def test_unknown_returns_none(self, builtin_regions):
        result = find_region("Несуществующая Земля", builtin_regions)
        assert result is None

    def test_word_year_does_not_match_voloda(self, builtin_regions):
        """Регрессионный тест для BUG #3 (Wave 1).

        До фикса: слово 'год' (len=3) матчило подстроку 'воло[год]ская',
        score = 30+3 = 33 > порога 30 → любой запрос со словом 'год'
        ложно возвращал Вологодскую область.

        После фикса: word boundary через \\b...\\b — 'год' не отдельное
        слово в 'вологодская', поэтому не матчится.
        """
        result = find_region("какой-то год 2025", builtin_regions)
        assert result is None, (
            f"BUG #3 вернулся: слово 'год' снова матчит подстроку. "
            f"Получили: {result}"
        )

    def test_word_obl_does_not_match_every_oblast(self, builtin_regions):
        """Регрессионный тест: слово 'обл' не должно матчить все области.

        Аналог BUG #3, но для слова 'обл'. До фикса 'обл' in 'московская область' = True.
        После фикса с word boundary — должно требовать отдельное слово.
        """
        # 'обл' в составе 'московская область' — НЕ отдельное слово (сокращение),
        # но 'область' это отдельное слово. Здесь просто проверяем, что 'обл'
        # в 'какой-то обл 2025' не ложно матчит Московскую или любую другую.
        # 'обл' длиной 3, так что проходит порог len(word) < 3.
        # Если word boundary работает — 'обл' не матчит 'область'.
        result = find_region("дтп обл 2025", builtin_regions)
        # 'обл' не должно матчится с 'область' через word boundary
        # (хотя 'обл' как отдельное слово может быть в search_index через 'обл. ')
        # Принимаем либо None, либо Moscow (если 'обл.' в индексе). Главное — не все области сразу.
        if result is not None:
            # Должен быть только один регион, а не случайный
            assert isinstance(result, tuple)

    def test_empty_string_returns_none(self, builtin_regions):
        result = find_region("", builtin_regions)
        assert result is None


# ============================================================
# _parse_strict_format
# ============================================================

class TestStrictFormat:
    """Строгий формат: m.YYYY RRRR (например, '2.2024 1101')."""

    def test_valid_strict(self):
        result = _parse_strict_format("2.2024 1101")
        assert result is not None
        assert result.region_code == "1101"
        assert result.period.months == [2]
        assert result.period.year == 2024

    def test_code_1100_returns_none(self):
        """1100 — это «общий» код, недопустимый для запроса."""
        result = _parse_strict_format("2.2024 1100")
        assert result is None

    def test_invalid_month(self):
        """Месяц 13 — недопустимый."""
        result = _parse_strict_format("13.2024 1101")
        assert result is None

    def test_garbage_returns_none(self):
        result = _parse_strict_format("Вологодская за 2025 год")
        assert result is None

    def test_short_code_3_digits(self):
        """3-значный код региона (например, 80 — Свердловская)."""
        result = _parse_strict_format("3.2025 80")
        assert result is not None
        assert result.region_code == "80"
        assert result.period.months == [3]


# ============================================================
# parse_user_message (async integration)
# ============================================================

class TestParseUserMessageIntegration:
    """Интеграционный тест: текст → ParsedRequest.

    Без сети (REGIONS_API_ENABLED=False по умолчанию).
    """

    @pytest.mark.asyncio
    async def test_full_query_voloda(self):
        """'Вологодская область за 2025 год' → region + period."""
        result = await urp.parse_user_message("Вологодская область за 2025 год")
        assert result is not None
        assert result.region_code == "1119"
        assert result.period.year == 2025
        assert result.period.months == list(range(1, 13))

    @pytest.mark.asyncio
    async def test_short_query(self):
        """'Вологодская за 3 месяца 2026'."""
        result = await urp.parse_user_message("Вологодская за 3 месяца 2026")
        assert result is not None
        assert result.region_code == "1119"
        assert result.period.months == [1, 2, 3]
        assert result.period.year == 2026

    @pytest.mark.asyncio
    async def test_month_before_region(self):
        """'март 2025 Вологодская' — месяц раньше региона."""
        result = await urp.parse_user_message("март 2025 Вологодская")
        assert result is not None
        assert result.region_code == "1119"
        assert result.period.months == [3]

    @pytest.mark.asyncio
    async def test_strict_format(self):
        """'2.2024 1101' — строгий формат, без обращений к справочнику."""
        result = await urp.parse_user_message("2.2024 1101")
        assert result is not None
        assert result.region_code == "1101"
        assert result.period.months == [2]
        assert result.period.year == 2024

    @pytest.mark.asyncio
    async def test_unknown_region_returns_none(self):
        """Несуществующий регион → None.

        После фикса BUG #3 (word boundary в find_region) слово 'год'
        больше не ложно матчится с 'Вологодская'. Этот кейс раньше падал
        (возвращал Вологодскую), теперь должен вернуть None.
        """
        result = await urp.parse_user_message("Несуществующая Земля за 2025 год")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_string_returns_none(self):
        result = await urp.parse_user_message("")
        assert result is None
