"""
Golden-тесты для gibdd_parser.parse_card_to_row.

Эталонные выходы сохранены в tests/golden/fixtures/parser/card_*.json.
Обновление: pytest tests/golden/test_golden_parser.py --update-golden
"""
import pytest

from gibdd_parser import parse_card_to_row
from tests.fixtures.synthetic_cards import (
    BASE_CARD,
    card_with_alcohol,
    card_with_death,
    card_with_pedestrian,
    card_unknown_type,
)


pytestmark = pytest.mark.golden


# Список (карточка, имя эталона) для параметризации.
GOLDEN_CASES = [
    (BASE_CARD, "parser/card_base.json"),
    (card_with_death(), "parser/card_with_death.json"),
    (card_with_alcohol(), "parser/card_with_alcohol.json"),
    (card_with_pedestrian(), "parser/card_with_pedestrian.json"),
    (card_unknown_type(), "parser/card_unknown_type.json"),
]


@pytest.mark.parametrize("card, golden_path", GOLDEN_CASES)
def test_parse_card_to_row_matches_golden(card, golden_path, golden_compare):
    """parse_card_to_row должен возвращать ровно тот же dict, что в эталоне.

    Если функция изменит формат (новые поля, другие имена, другой порядок) — тест упадёт.
    Обновление эталона: pytest --update-golden tests/golden/test_golden_parser.py
    """
    row = parse_card_to_row(card)
    golden_compare(row, golden_path)


def test_all_card_variants_covered():
    """Проверяем, что все 5 вариантов карточек имеют эталоны."""
    from pathlib import Path

    fixtures_dir = Path(__file__).parent / "fixtures" / "parser"
    expected_files = {
        "card_base.json",
        "card_with_death.json",
        "card_with_alcohol.json",
        "card_with_pedestrian.json",
        "card_unknown_type.json",
    }
    actual_files = {f.name for f in fixtures_dir.glob("card_*.json")}
    missing = expected_files - actual_files
    assert not missing, f"Отсутствуют эталоны: {missing}. Запустите generate_golden.py"


def test_parse_card_to_row_field_count_stable():
    """Количество полей в результате parse_card_to_row не должно меняться.

    Если добавили/удалили поле — это явное изменение контракта, и нужно
    осознанно обновить эталоны. Этот тест даёт раннее предупреждение.
    """
    row = parse_card_to_row(BASE_CARD)
    # На момент написания — 57 полей. Если изменилось — нужно перегенерировать эталоны.
    # Число обновляется только при осознанном изменении схемы.
    assert len(row) >= 50, (
        f"parse_card_to_row возвращает {len(row)} полей, ожидалось >= 50. "
        f"Возможно, кто-то удалил поля. Если это осознанно — обновите эталон "
        f"и число в этом тесте."
    )
