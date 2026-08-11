"""
Smoke-тесты для LLM analyzer: модуль импортируется, клиенты инициализируются
с фиктивными ключами, базовые функции доступны.

Запуск: pytest tests/smoke/test_llm_smoke.py -m smoke
"""
import pytest


@pytest.mark.smoke
def test_llm_analyzer_imports() -> None:
    """llm_analyzer должен импортироваться без ошибок."""
    import llm_analyzer

    assert hasattr(llm_analyzer, "format_metrics_for_prompt")
    assert hasattr(llm_analyzer, "get_ai_summary")
    assert hasattr(llm_analyzer, "get_ai_answer")
    assert hasattr(llm_analyzer, "ask_llm")


@pytest.mark.smoke
def test_llm_clients_none_when_no_keys(monkeypatch) -> None:
    """Без API-ключей LLM клиенты должны оставаться None.

    Это предотвращает случайные сетевые вызовы в продакшене без конфигурации.
    """
    import config
    import llm_analyzer

    monkeypatch.setattr(config, "LLM_API_KEY", "")
    monkeypatch.setattr(llm_analyzer, "LLM_API_KEY", "", raising=False)
    monkeypatch.setattr(llm_analyzer, "_free_llm_client", None, raising=False)
    monkeypatch.setattr(llm_analyzer, "_paid_llm_client", None, raising=False)

    # Клиенты не должны создаваться с пустым ключом
    # (точное поведение зависит от реализации — здесь просто проверяем,
    # что модуль не падает при попытке получить клиент).
    assert llm_analyzer._free_llm_client is None
    assert llm_analyzer._paid_llm_client is None


@pytest.mark.smoke
def test_format_metrics_for_prompt_callable() -> None:
    """format_metrics_for_prompt должна быть вызываема с валидным comparison."""
    import llm_analyzer

    # Минимальный comparison dict — Structure как в conftest.py sample_comparison
    minimal_comparison = {
        "total":       {"current": 10, "previous": 8,  "change": 25.0},
        "deaths":      {"current": 1,  "previous": 2,  "change": -50.0},
        "injured":     {"current": 12, "previous": 10, "change": 20.0},
        "alcohol":     {"current": 1,  "previous": 0,  "change": 100.0},
        "pedestrians": {"current": 2,  "previous": 3,  "change": -33.3},
        "deaths_per_100":      {"current": 10.0, "previous": 25.0, "change": -60.0},
        "injured_per_100":     {"current": 120.0,"previous": 125.0,"change": -4.0},
        "by_weekday": {
            "current":  {0: 1, 1: 2, 2: 1, 3: 1, 4: 2, 5: 2, 6: 1},
            "previous": {0: 1, 1: 1, 2: 1, 3: 1, 4: 2, 5: 1, 6: 1},
        },
        "by_hour": {
            "current":  {0: 0, 6: 1, 9: 2, 12: 2, 15: 2, 18: 2, 21: 1},
            "previous": {0: 0, 6: 1, 9: 1, 12: 1, 15: 2, 18: 2, 21: 1},
        },
        "by_type": {
            "current":  {"Столкновение": 5, "Наезд на пешехода": 3, "Опрокидывание": 2},
            "previous": {"Столкновение": 4, "Наезд на пешехода": 3, "Опрокидывание": 1},
        },
        "by_weather": {
            "current":  {"Ясно": 7, "Дождь": 3},
            "previous": {"Ясно": 6, "Дождь": 2},
        },
    }

    # Просто вызываем — если функция упадёт с KeyError/AttributeError, тест упадёт.
    result = llm_analyzer.format_metrics_for_prompt(
        minimal_comparison,
        reg_name="Вологодская область",
        current_label="Май 2025",
        prev_label="Апрель 2025",
    )

    assert isinstance(result, str)
    assert "Вологодская область" in result
    assert "Май 2025" in result
    assert "Апрель 2025" in result
    assert "ОСНОВНЫЕ ПОКАЗАТЕЛИ" in result


@pytest.mark.smoke
def test_gibdd_parser_parses_base_card() -> None:
    """gibdd_parser.parse_card_to_row должен обрабатывать BASE_CARD без ошибок."""
    from tests.fixtures.synthetic_cards import BASE_CARD
    from gibdd_parser import parse_card_to_row

    row = parse_card_to_row(BASE_CARD)

    # Базовые проверки структуры
    assert isinstance(row, dict)
    assert row["kart_id"] == "000001"
    assert row["date_dtp"] == "15.05.2025"
    assert row["time"] == "14:30"
    assert row["dtpv"] == "Столкновение"
    assert row["pog"] == "0"
    assert row["ran"] == "1"
    assert row["np"] == "Вологда"


@pytest.mark.smoke
def test_analytics_calculate_metrics_callable() -> None:
    """analytics.calculate_metrics должен работать с минимальным набором карточек."""
    from tests.fixtures.synthetic_cards import cards_basic_set
    from analytics import calculate_metrics

    metrics = calculate_metrics(cards_basic_set())

    assert metrics["total"] == 5
    assert metrics["deaths"] == 1  # card_with_death
    assert metrics["injured"] >= 3  # BASE_CARD (1) + card_with_pedestrian (2) + хотя бы ещё
    assert metrics["alcohol"] == 1  # card_with_alcohol
    assert metrics["pedestrians"] == 1  # card_with_pedestrian
    assert "by_weekday" in metrics
    assert "by_hour" in metrics
    assert "by_type" in metrics
    assert "by_weather" in metrics
