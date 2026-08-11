"""
Dry-run проверка патча Phase 3.1.

Без запуска сервера — проверяем:
1. Файл парсится (AST валиден)
2. Импорт gibdd_service не падает
3. Поля Task существуют
4. Helper _get_cross_tables существует и работает с синтетическими cards
5. Кэш инвалидируется по id(cards) при замене списка
"""
import sys
import os
from pathlib import Path

ROOT = Path("/home/z/my-project/gibdd-bot")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "miniapp" / "backend"))

# Конфиг-заглушки, чтобы не требовать TELEGRAM_BOT_TOKEN
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy_for_dry_run")
os.environ.setdefault("LLM_API_KEY", "dummy")
os.environ.setdefault("LLM_PAID_API_KEY", "dummy")

# Заменяем gibdd_service.py на пропатченную версию
PATCHED = Path("/home/z/my-project/phase3-1-analytics-optimization/gibdd_service.py")
TARGET = ROOT / "miniapp" / "backend" / "services" / "gibdd_service.py"
BACKUP = ROOT / "miniapp" / "backend" / "services" / "gibdd_service.py.bak_phase31"

print("=" * 60)
print("Phase 3.1 dry-run проверка")
print("=" * 60)

# 1. AST-валидация
import ast
src = PATCHED.read_text()
ast.parse(src)
print(f"✓ AST валиден: {len(src.splitlines())} строк")

# 2. Backup оригинала и копирование пропатченного
import shutil
shutil.copy2(TARGET, BACKUP)
shutil.copy2(PATCHED, TARGET)
print(f"✓ Оригинал сохранён в {BACKUP.name}")
print(f"✓ Пропатченный файл скопирован в services/")

# 3. Импорт модуля
try:
    # force reimport
    for mod_name in list(sys.modules.keys()):
        if "gibdd_service" in mod_name or "services" in mod_name:
            del sys.modules[mod_name]

    from miniapp.backend.services.gibdd_service import Task, _get_cross_tables
    print(f"✓ Импорт Task и _get_cross_tables успешен")
except Exception as e:
    print(f"✗ Импорт провалился: {e}")
    import traceback
    traceback.print_exc()
    # Восстановление
    shutil.copy2(BACKUP, TARGET)
    sys.exit(1)

# 4. Проверка полей Task
task = Task(
    id="dry-run-test",
    user_id=12345,
    region_code="1160",
    region_name="Тестовый регион",
    period_label="Январь 2025",
    dat_list=["01.2025"],
    raw_query={},
)
expected_fields = [
    "cross_tables", "cross_tables_cards_id",
    "prev_cross_tables", "prev_cross_tables_cards_id",
    "current_metrics", "current_metrics_cards_id",
    "prev_metrics", "prev_metrics_cards_id",
]
for field_name in expected_fields:
    if not hasattr(task, field_name):
        print(f"✗ Поле {field_name} отсутствует в Task")
        shutil.copy2(BACKUP, TARGET)
        sys.exit(1)
    val = getattr(task, field_name)
    if val is not None:
        print(f"✗ Поле {field_name} должно быть None по умолчанию, got {val}")
        shutil.copy2(BACKUP, TARGET)
        sys.exit(1)
print(f"✓ Все {len(expected_fields)} полей кэша присутствуют и None по умолчанию")

# 5. Проверка helper _get_cross_tables с синтетическими cards
import random
def make_card(i):
    return {
        "id": i,
        "dat": f"{(i%28)+1:02d}.{(i%12)+1:02d}.2025",
        "time": f"{(i%24):02d}:{(i%60):02d}",
        "dtp_type": ["Столкновение", "Наезд на пешехода"][i % 2],
        "pog": i % 4,
        "ran": i % 10,
        "k_ts": str((i % 4) + 1),
        "weather": "Ясно",
        "lighting": "Светлое время суток",
        "s_pch": "Сухое",
        "district": "Центральный",
        "dor": "М-7",
        "dor_z": "1",
        "alcohol": "нет",
        "uchastniki": [{
            "k_uch": "Водитель",
            "pol": "Мужской",
            "stazh": "5",
            "ts_tp": "Легковой",
            "s_pch": "Ремень пристегнут",
            "npr": "—",
            "marka_ts": "Brand-1",
            "god_vyp": "2020",
            "pyt": "трезв",
        }],
    }

cards = [make_card(i) for i in range(200)]
task.cards = cards
task.total_dtp = len(cards)

# Первый вызов — cache miss, должен посчитать
result1 = _get_cross_tables(task, prev=False)
if result1 is None:
    print("✗ _get_cross_tables вернул None при непустых cards")
    shutil.copy2(BACKUP, TARGET)
    sys.exit(1)
if task.cross_tables is None:
    print("✗ task.cross_tables не сохранён в кэш после первого вызова")
    shutil.copy2(BACKUP, TARGET)
    sys.exit(1)
if task.cross_tables_cards_id != id(cards):
    print("✗ task.cross_tables_cards_id не соответствует id(cards)")
    shutil.copy2(BACKUP, TARGET)
    sys.exit(1)
print(f"✓ Первый вызов _get_cross_tables: посчитан, {len(result1)} таблиц, "
      f"сохранён в кэш, cards_id={task.cross_tables_cards_id}")

# Второй вызов — cache hit, должен вернуть тот же объект
result2 = _get_cross_tables(task, prev=False)
if result2 is not result1:
    print("✗ Cache miss при втором вызове — вернулся другой объект")
    shutil.copy2(BACKUP, TARGET)
    sys.exit(1)
print(f"✓ Второй вызов: cache hit (тот же объект в памяти)")

# Третий вызов с новыми cards — cache miss, должен пересчитать
new_cards = [make_card(i) for i in range(300)]  # другой размер → другой id
task.cards = new_cards
result3 = _get_cross_tables(task, prev=False)
if result3 is result1:
    print("✗ Cache hit при смене cards — инвалидация не сработала")
    shutil.copy2(BACKUP, TARGET)
    sys.exit(1)
if task.cross_tables_cards_id != id(new_cards):
    print("✗ После смены cards cards_id не обновился")
    shutil.copy2(BACKUP, TARGET)
    sys.exit(1)
print(f"✓ Третий вызов с новыми cards: инвалидация сработала, "
      f"новый cards_id={task.cross_tables_cards_id}")

# Проверка prev=True с пустыми prev_cards
task.prev_cards = []
result_prev_empty = _get_cross_tables(task, prev=True)
if result_prev_empty is not None:
    print("✗ _get_cross_tables(prev=True) должен вернуть None при пустых prev_cards")
    shutil.copy2(BACKUP, TARGET)
    sys.exit(1)
print(f"✓ _get_cross_tables(prev=True) с пустыми prev_cards: вернул None (корректно)")

# Проверка prev=True с непустыми prev_cards
task.prev_cards = new_cards[:100]
result_prev = _get_cross_tables(task, prev=True)
if result_prev is None:
    print("✗ _get_cross_tables(prev=True) вернул None при непустых prev_cards")
    shutil.copy2(BACKUP, TARGET)
    sys.exit(1)
if task.prev_cross_tables_cards_id != id(task.prev_cards):
    print("✗ prev_cross_tables_cards_id не соответствует id(prev_cards)")
    shutil.copy2(BACKUP, TARGET)
    sys.exit(1)
print(f"✓ _get_cross_tables(prev=True) с непустыми prev_cards: посчитан, "
      f"{len(result_prev)} таблиц, cards_id={task.prev_cross_tables_cards_id}")

# Восстановление оригинала
shutil.copy2(BACKUP, TARGET)
BACKUP.unlink()  # удалить бэкап
print(f"\n✓ Оригинал gibdd_service.py восстановлен, бэкап удалён")

print("\n" + "=" * 60)
print("ВСЕ ПРОВЕРКИ ПРОШЛИ УСПЕШНО")
print("=" * 60)
print("\nПатч готов к деплою:")
print(f"  Файл: /home/z/my-project/phase3-1-analytics-optimization/gibdd_service.py")
print(f"  Размер: {len(src.splitlines())} строк (оригинал: ~2293)")
print(f"  Дельта: +{len(src.splitlines()) - 2293} строк")
