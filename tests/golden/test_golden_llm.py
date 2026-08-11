"""
Golden-тесты для llm_analyzer.format_metrics_for_prompt.

Эталонный текст сохранён в tests/golden/fixtures/llm/metrics_prompt_may_vs_april.txt.
Обновление: pytest tests/golden/test_golden_llm.py --update-golden

Этот тест особенно важен: LLM prompt — это контракт с моделью. Любое изменение
(пробел, перевод строки, формат числа) может повлиять на качество ответа модели.
"""
import pytest

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


pytestmark = pytest.mark.golden


def _build_comparison():
    """Строит comparison dict, идентичный используемому в generate_golden.py."""
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
    return compare_metrics(current_metrics, previous_metrics)


def test_format_metrics_for_prompt_matches_golden(golden_text_compare):
    """format_metrics_for_prompt должен выдавать ровно тот же текст, что в эталоне.

    Любое изменение формата (число, разделитель, перевод строки) — тест упадёт.
    Обновление: pytest --update-golden tests/golden/test_golden_llm.py
    """
    comparison = _build_comparison()
    prompt = format_metrics_for_prompt(
        comparison,
        reg_name="Вологодская область",
        current_label="Май 2025",
        prev_label="Апрель 2025",
    )
    golden_text_compare(prompt, "llm/metrics_prompt_may_vs_april.txt")


def test_prompt_contains_required_sections():
    """Prompt должен содержать все обязательные секции.

    Это «smoke-level» проверка формата — менее строгая, чем golden.
    Если кто-то добавил/удалил секцию — золотой тест упадёт первым,
    но этот тест помогает понять, что именно пропало.
    """
    comparison = _build_comparison()
    prompt = format_metrics_for_prompt(
        comparison,
        reg_name="Тестовый регион",
        current_label="Май 2025",
        prev_label="Апрель 2025",
    )

    required_sections = [
        "Регион: Тестовый регион",
        "Текущий период: Май 2025",
        "Предыдущий период: Апрель 2025",
        "ОСНОВНЫЕ ПОКАЗАТЕЛИ:",
        "РАСПРЕДЕЛЕНИЕ ПО ДНЯМ НЕДЕЛИ:",
        "РАСПРЕДЕЛЕНИЕ ПО ЧАСАМ СУТОК",
        "РАСПРЕДЕЛЕНИЕ ПО ВИДАМ ДТП:",
    ]

    for section in required_sections:
        assert section in prompt, (
            f"Секция '{section}' отсутствует в prompt. "
            f"Возможно, формат изменился — обновите golden-эталон."
        )


def test_prompt_starts_with_region():
    """Prompt должен начинаться с 'Регион: ...' — это контракт с LLM."""
    comparison = _build_comparison()
    prompt = format_metrics_for_prompt(
        comparison,
        reg_name="X",
        current_label="Y",
        prev_label="Z",
    )
    assert prompt.startswith("Регион: X")


def test_prompt_includes_change_indicators():
    """Prompt должен показывать изменения (%, +, -) для метрик с предыдущим периодом."""
    comparison = _build_comparison()
    prompt = format_metrics_for_prompt(
        comparison,
        reg_name="X",
        current_label="Y",
        prev_label="Z",
    )

    # Должен быть хотя бы один знак % (изменение)
    assert "%" in prompt, "В prompt нет ни одного изменения (%)"
    # Должны быть числа в скобках "(было N, ...)"
    assert "было" in prompt, "В prompt нет сравнения с предыдущим периодом"
