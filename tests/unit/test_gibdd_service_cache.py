"""
Тест на инвалидацию кэша _get_cross_tables в gibdd_service.py.

Это критичный тест для Phase 3.1: если логика инвалидации по id(cards)
сломается — кэш вернёт чужие данные, и LLM будет смотреть на неправильные
числа. Это тот класс багов, который не ловится на одном запросе, но
гарантированно роняет аналитику при частых Q&A.

Тест изолирован от БД и сети: использует легковесный stub Task,
повторяющий нужные поля из gibdd_service.Task.
"""
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from dataclasses import dataclass, field

import pytest

# Корень проекта gibdd-bot (для импорта analytics и tests.fixtures)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# miniapp/ в path — чтобы работал `from backend.services import gibdd_service`
# (routers используют relative imports, поэтому `backend` должен быть пакетом)
MINIAPP_ROOT = PROJECT_ROOT / "miniapp"
if str(MINIAPP_ROOT) not in sys.path:
    sys.path.insert(0, str(MINIAPP_ROOT))

import analytics
from tests.fixtures.synthetic_cards import cards_basic_set


# ============================================================
# Легковесный stub Task
# ============================================================
# gibdd_service.Task — полноценная dataclass с ~30 полями, многие из
# которых требуют AsyncConnectionPool и other heavy deps. Здесь —
# минимальный stub, повторяющий только те поля, которые реально
# читает _get_cross_tables: id, cards, cross_tables, cross_tables_cards_id,
# prev_cards, prev_cross_tables, prev_cross_tables_cards_id.
#
# Если в gibdd_service.Task добавятся новые кэш-поля — добавить их сюда
# тоже, иначе тест перестанет покрывать реальную логику.

@dataclass
class StubTask:
    id: str = "test-task"
    cards: Optional[list] = None
    cross_tables: Optional[Dict[str, Any]] = None
    cross_tables_cards_id: Optional[int] = None
    prev_cards: Optional[list] = None
    prev_cross_tables: Optional[Dict[str, Any]] = None
    prev_cross_tables_cards_id: Optional[int] = None


# ============================================================
# Импортируем сам _get_cross_tables
# ============================================================
# gibdd_service.py импортирует кучу тяжёлых зависимостей (FastAPI,
# psycopg pool, и т.д.) — если импорт падает, тест пропускается
# с понятным сообщением, а не роняет весь suite.

try:
    from backend.services.gibdd_service import _get_cross_tables, Task
    GIBDD_SERVICE_AVAILABLE = True
except Exception as e:  # pragma: no cover — environment without deps
    GIBDD_SERVICE_AVAILABLE = False
    GIBDD_SERVICE_IMPORT_ERROR = str(e)


# Если gibdd_service импортируется — проверим, что наш stub совместим
# с реальной сигнатурой _get_cross_tables.
pytestmark = pytest.mark.skipif(
    not GIBDD_SERVICE_AVAILABLE,
    reason=f"gibdd_service not importable: {GIBDD_SERVICE_IMPORT_ERROR if not GIBDD_SERVICE_AVAILABLE else ''}",
)


# ============================================================
# Тесты
# ============================================================

class TestGetCrossTablesCacheHit:
    """Cache hit: при повторном вызове с тем же task.cards."""

    def test_first_call_returns_dict_and_sets_cache(self):
        """Первый вызов — cache miss, считается и сохраняется."""
        task = StubTask(cards=cards_basic_set())
        result = _get_cross_tables(task, prev=False)
        assert result is not None
        assert isinstance(result, dict)
        assert task.cross_tables is not None, "Cache не записан"
        assert task.cross_tables_cards_id == id(task.cards)

    def test_second_call_returns_same_object(self):
        """Второй вызов — cache hit, возвращается тот же объект (is)."""
        task = StubTask(cards=cards_basic_set())
        first = _get_cross_tables(task, prev=False)
        second = _get_cross_tables(task, prev=False)
        assert first is second, "Cache hit должен вернуть тот же объект"

    def test_prev_flag_uses_prev_fields(self):
        """prev=True — кэш в prev_cross_tables / prev_cross_tables_cards_id."""
        task = StubTask(prev_cards=cards_basic_set())
        result = _get_cross_tables(task, prev=True)
        assert result is not None
        assert task.prev_cross_tables is not None
        assert task.prev_cross_tables_cards_id == id(task.prev_cards)


class TestGetCrossTablesCacheInvalidation:
    """Cache invalidation: смена task.cards → новый id → пересчёт."""

    def test_empty_cards_returns_none(self):
        """Пустой cards → None, без вызова calculate_cross_tables."""
        task = StubTask(cards=[])
        assert _get_cross_tables(task, prev=False) is None
        assert task.cross_tables is None

    def test_none_cards_returns_none(self):
        task = StubTask(cards=None)
        assert _get_cross_tables(task, prev=False) is None

    def test_changing_cards_invalidates_cache(self):
        """Главный тест Phase 3.1: новый объект cards → пересчёт.

        Сценарий:
          1. Загружаем cards_v1, считаем cross_tables_v1.
          2. Перезагружаем cards (новый объект списка, id отличается).
          3. Вызываем _get_cross_tables снова — должно пересчитать,
             а не вернуть старый cross_tables_v1.
        """
        cards_v1 = cards_basic_set()
        task = StubTask(cards=cards_v1)
        result_v1 = _get_cross_tables(task, prev=False)
        assert task.cross_tables_cards_id == id(cards_v1)

        # Делаем копию с изменённым числом погибших в первой карточке
        cards_v2 = [dict(c) for c in cards_basic_set()]
        cards_v2[0] = dict(cards_v2[0])
        cards_v2[0]["pog"] = "5"  # было "0"
        task.cards = cards_v2  # новый объект → новый id

        result_v2 = _get_cross_tables(task, prev=False)
        assert task.cross_tables_cards_id == id(cards_v2), "Cache id не обновился"
        assert result_v2 is not result_v1, "Cache не инвалидировался — вернулся старый dict"
        # Число погибших в Столкновении должно было вырасти:
        # в v1: 0 (BASE_CARD) + 1 (card_with_death) = 1
        # в v2: 5 (BASE_CARD с pog=5) + 1 (card_with_death) = 6
        assert result_v1["dtp_type_x_severity"]["Столкновение"]["deaths"] == 1
        assert result_v2["dtp_type_x_severity"]["Столкновение"]["deaths"] == 6

    def test_same_content_different_object_triggers_recalc(self):
        """Разный объект списка, но то же содержимое → всё равно пересчёт.

        Это сознательное поведение: id(cards) ≠ hash(cards), мы не
        сравниваем содержимое, только идентичность объекта. Это
        означает «cards мог быть модифицирован in-place» → safer
        пересчитать.
        """
        cards_v1 = cards_basic_set()
        task = StubTask(cards=cards_v1)
        result_v1 = _get_cross_tables(task, prev=False)

        # Новый объект, то же содержимое
        cards_v2 = list(cards_basic_set())
        assert cards_v2 == cards_v1, "Содержимое должно совпадать"
        assert id(cards_v2) != id(cards_v1), "id должен различаться"
        task.cards = cards_v2

        result_v2 = _get_cross_tables(task, prev=False)
        assert result_v2 is not result_v1, "Cache должен инвалидироваться по id, не по содержимому"
        # Но содержимое cross_tables должно совпадать (одни и те же данные)
        assert (result_v2["dtp_type_x_severity"]["Столкновение"]["dtp"]
                == result_v1["dtp_type_x_severity"]["Столкновение"]["dtp"])


class TestGetCrossTablesIsolation:
    """Изоляция: кэш одного task не влияет на другой."""

    def test_two_tasks_have_independent_caches(self):
        """Два разных task с разными cards — каждый имеет свой кэш."""
        cards_a = cards_basic_set()
        cards_b = [dict(c) for c in cards_basic_set()]
        cards_b[0] = dict(cards_b[0])
        cards_b[0]["pog"] = "9"

        task_a = StubTask(id="A", cards=cards_a)
        task_b = StubTask(id="B", cards=cards_b)

        result_a = _get_cross_tables(task_a, prev=False)
        result_b = _get_cross_tables(task_b, prev=False)

        assert task_a.cross_tables_cards_id == id(cards_a)
        assert task_b.cross_tables_cards_id == id(cards_b)
        assert (result_a["dtp_type_x_severity"]["Столкновение"]["deaths"]
                != result_b["dtp_type_x_severity"]["Столкновение"]["deaths"])
