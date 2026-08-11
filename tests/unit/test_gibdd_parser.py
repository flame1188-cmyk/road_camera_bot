"""
Тесты gibdd_parser: преобразование карточки ГИБДД → строка Excel.

Покрывает:
  - parse_card_to_row: простые поля
  - parse_card_to_row: вложенные dor_usl (s_pch, osv, sdor, spog)
  - parse_card_to_row: ts_info (марка, модель, цвет, год)
  - parse_card_to_row: uch_info (пешеходы, нетрезвые)
  - parse_card_to_row: пустые / None значения
  - build_file1_data: маппинг в человекочитаемые колонки
  - get_file1_column_names: порядок колонок
"""
import gibdd_parser
from gibdd_parser import (
    parse_card_to_row, build_file1_data, get_file1_column_names,
    _safe_str, _join, _decimal_to_dms,
)
from tests.fixtures.synthetic_cards import (
    BASE_CARD, cards_basic_set,
    card_with_pedestrian, card_with_alcohol,
)


class TestParseCardToRowSimpleFields:
    """Простые поля верхнего уровня (kart_id, date_dtp, pog, ran и т.д.)."""

    def test_kart_id_preserved(self):
        row = parse_card_to_row(BASE_CARD)
        assert row["kart_id"] == "000001"

    def test_date_dtp_preserved(self):
        row = parse_card_to_row(BASE_CARD)
        assert row["date_dtp"] == "15.05.2025"

    def test_pog_and_ran(self):
        row = parse_card_to_row(BASE_CARD)
        assert row["pog"] == "0"
        assert row["ran"] == "1"

    def test_dtpv(self):
        row = parse_card_to_row(BASE_CARD)
        assert row["dtpv"] == "Столкновение"

    def test_district_and_street(self):
        row = parse_card_to_row(BASE_CARD)
        assert row["district"] == "Центральный"
        assert row["street"] == "ул. Мира"

    def test_dor_and_dor_z(self):
        row = parse_card_to_row(BASE_CARD)
        assert row["dor"] == "Р-5"
        assert row["dor_z"] == "Федерального значения"


class TestParseCardToRowDorUsl:
    """Вложенный объект dor_usl: s_pch, osv, sdor, spog, etc."""

    def test_s_pch(self):
        row = parse_card_to_row(BASE_CARD)
        assert row["s_pch"] == "Сухое"

    def test_osv(self):
        row = parse_card_to_row(BASE_CARD)
        assert row["osv"] == "В светлое время суток"

    def test_spog_joined(self):
        """spog — список, должен быть склеен через '; '."""
        row = parse_card_to_row(BASE_CARD)
        assert row["_spog"] == "Ясно"

    def test_empty_dor_usl_lists(self):
        """sdor=[], obj_dtp=[], ndu=[] — должны дать пустые строки."""
        row = parse_card_to_row(BASE_CARD)
        assert row["_sdor"] == ""
        assert row["_obj_dtp"] == ""
        assert row["_ndu"] == ""

    def test_multiple_spog_joined(self):
        """Несколько погодных условий — склейка через '; '."""
        card = {**BASE_CARD, "dor_usl": {**BASE_CARD["dor_usl"], "spog": ["Дождь", "Туман"]}}
        row = parse_card_to_row(card)
        assert row["_spog"] == "Дождь; Туман"

    def test_missing_dor_usl(self):
        """Если dor_usl отсутствует — не должно упасть."""
        card = {**BASE_CARD, "dor_usl": None}
        row = parse_card_to_row(card)
        assert row["s_pch"] == ""
        assert row["osv"] == ""


class TestParseCardToRowTsInfo:
    """ts_info: транспортные средства."""

    def test_marka_and_model(self):
        row = parse_card_to_row(BASE_CARD)
        assert row["marka_ts"] == "LADA"
        assert row["m_ts"] == "Vesta"

    def test_color(self):
        row = parse_card_to_row(BASE_CARD)
        assert row["color"] == "Серебристый"

    def test_year(self):
        row = parse_card_to_row(BASE_CARD)
        assert row["g_v"] == "2022"

    def test_ts_type(self):
        row = parse_card_to_row(BASE_CARD)
        assert row["t_ts"] == "Легковой автомобиль"

    def test_multiple_ts_joined(self):
        """2 ТС — поля склеены через ';'."""
        ts1 = BASE_CARD["ts_info"][0]
        ts2 = dict(ts1)
        ts2["n_ts"] = "2"
        ts2["marka_ts"] = "TOYOTA"
        card = {**BASE_CARD, "ts_info": [ts1, ts2], "k_ts": "2"}
        row = parse_card_to_row(card)
        assert row["marka_ts"] == "LADA; TOYOTA"
        assert row["_n_ts"] == "1; 2"

    def test_empty_ts_info(self):
        """Без ТС — поля пустые, но не None."""
        card = {**BASE_CARD, "ts_info": []}
        row = parse_card_to_row(card)
        assert row["marka_ts"] == ""
        assert row["m_ts"] == ""
        assert row["_ts_info"] == ""


class TestParseCardToRowUchInfo:
    """uch_info: участники без ТС (пешеходы, велосипедисты)."""

    def test_pedestrian_added_to_kt_uch(self):
        """Пешеход добавляется в kt_uch."""
        row = parse_card_to_row(card_with_pedestrian())
        # В базовой карточке есть водитель, плюс пешеход из uch_info
        assert "Пешеход" in row["kt_uch"]
        assert "Водитель" in row["kt_uch"]

    def test_alco_from_uch_info(self):
        """Поле alco включает значения из uch_info."""
        row = parse_card_to_row(card_with_alcohol())
        # alco из ts_uch — "Установлено опьянение"
        assert "Установлено опьянение" in row["alco"]

    def test_uch_info_string_built(self):
        """_uch_info — человекочитаемая строка с участником."""
        row = parse_card_to_row(card_with_pedestrian())
        assert "_uch_info" in row
        assert "Uchastnik" in row["_uch_info"]


class TestParseCardToRowEdgeCases:
    """Граничные случаи."""

    def test_empty_card(self):
        """Пустой dict — не должен упасть."""
        row = parse_card_to_row({})
        assert row["kart_id"] == ""
        assert row["pog"] == ""
        assert row["date_dtp"] == ""

    def test_none_values(self):
        """None в полях — должно стать пустой строкой."""
        card = {**BASE_CARD, "pog": None, "ran": None}
        row = parse_card_to_row(card)
        assert row["pog"] == ""
        assert row["ran"] == ""


class TestBuildFile1Data:
    """build_file1_data: сборка строк с человекочитаемыми колонками."""

    def test_returns_list_of_dicts(self):
        rows = build_file1_data(cards_basic_set())
        assert isinstance(rows, list)
        assert len(rows) == 5
        assert all(isinstance(r, dict) for r in rows)

    def test_first_row_has_number(self):
        """Первый столбец — № (порядковый номер)."""
        rows = build_file1_data([BASE_CARD])
        assert rows[0]["№"] == "1"

    def test_column_names_match_expected(self):
        """Все ключи в строке должны быть из get_file1_column_names()."""
        rows = build_file1_data([BASE_CARD])
        expected_cols = set(get_file1_column_names())
        for row in rows:
            row_keys = set(row.keys())
            assert row_keys.issubset(expected_cols), f"Unknown keys: {row_keys - expected_cols}"

    def test_multiple_cards_numbered_sequentially(self):
        """2 карточки → №1 и №2."""
        rows = build_file1_data([BASE_CARD, {**BASE_CARD, "kart_id": "000002"}])
        assert rows[0]["№"] == "1"
        assert rows[1]["№"] == "2"


class TestGetFile1ColumnNames:
    """get_file1_column_names: порядок колонок."""

    def test_first_column_is_number(self):
        cols = get_file1_column_names()
        assert cols[0] == "№"

    def test_contains_key_columns(self):
        """Должны быть ключевые колонки."""
        cols = get_file1_column_names()
        assert "Номер ДТП" in cols
        assert "Дата ДТП" in cols
        assert "Число погибших в ДТП" in cols
        assert "Число раненых в ДТП" in cols


class TestHelpers:
    """_safe_str / _join / _decimal_to_dms."""

    def test_safe_str_none(self):
        assert _safe_str(None) == ""

    def test_safe_str_strips_whitespace(self):
        assert _safe_str("  hello  ") == "hello"

    def test_join_list(self):
        assert _join(["a", "b", "c"]) == "a; b; c"

    def test_join_empty_list(self):
        assert _join([]) == ""

    def test_join_none(self):
        assert _join(None) == ""

    def test_join_filters_empty_strings(self):
        """Пустые строки и None в списке — пропускаются."""
        assert _join(["a", "", None, "b"]) == "a; b"

    def test_decimal_to_dms_basic(self):
        """0° 0' 0'' → ('0', '0', '0.0')."""
        d, m, s = _decimal_to_dms("0.0")
        assert d == "0"
        assert m == "0"

    def test_decimal_to_dms_invalid(self):
        """Не-число → пустые строки."""
        d, m, s = _decimal_to_dms("abc")
        assert d == ""
        assert m == ""
        assert s == ""
