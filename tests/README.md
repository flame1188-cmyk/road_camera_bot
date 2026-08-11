# GIBDD Stat Bot — Test Suite

Полный набор unit- и integration-тестов для проекта GIBDD-bot
(Telegram-бот + FastAPI mini-app для анализа ДТП).

**Текущее состояние:**

| Метрика                | Значение                                  |
|------------------------|-------------------------------------------|
| Всего тестов           | **464** (438 Phase 3-1 + 19 Phase 3-2 + 7 skip) |
| Все тесты проходят     | ✅ (`458 passed, 6 skipped in 29.53s` на Windows) |
| Общее покрытие кода    | **77.04 %** (порог в `pytest.ini` — 40 %) |
| Волн тестирования      | Wave 1 ✅ · Wave 2 ✅ · Wave 3 ✅ · Wave 4 ✅ |
| Фаз рефакторинга       | Phase 3-1 ✅ · Phase 3-2 ✅ (bot.py → модульный пакет) |
| Найдено и фиксировано багов | **4** (3 в `user_request_parser.py`, 1 в `llm_analyzer.py`) |
| Cross-platform         | ✅ Linux (Python 3.12.13) + Windows (Python 3.11.9) |

---

## Содержимое архива

```
tests/
├── README.md                          ← этот файл
├── conftest.py                        ← общие фикстуры (Wave 1 + Wave 2)
├── __init__.py
│
├── fixtures/
│   ├── __init__.py
│   └── synthetic_cards.py             ← синтетические карточки ДТП
│
├── unit/                              ← Wave 1 + Wave 2: unit-тесты
│   ├── __init__.py
│   ├── test_analytics_metrics.py      ← 19 тестов · calculate_metrics
│   ├── test_analytics_compare.py      ← 10 тестов · compare_metrics
│   ├── test_analytics_cross_tables.py ← 8 тестов  · calculate_cross_tables
│   ├── test_analytics_stats.py        ← 33 теста  · point_statistics
│   ├── test_gibdd_parser.py           ← 37 тестов · parse_card_to_row
│   ├── test_gibdd_service.py          ← 25 тестов · gibdd_service (mock HTTP) [Wave 2]
│   ├── test_gibdd_service_cache.py    ← 8 тестов  · кэш in-memory
│   ├── test_llm_analyzer_format.py    ← 50 тестов · format_metrics_for_prompt [Wave 2]
│   ├── test_llm_analyzer_ask.py       ← 25 тестов · ask_paid_llm / ask_free_llm (mock) [Wave 2]
│   ├── test_telegram_auth.py          ← 18 тестов · Telegram initData HMAC [Wave 2]
│   └── test_user_request_parser.py    ← 42 теста  · parse_period / find_region / etc.
│
└── integration/                       ← Wave 2 + Wave 3: integration-тесты
    ├── __init__.py
    ├── _gibdd_stubs.py                ← shared stubs для Wave 3 (bot/parser/analytics/excel/LLM) [Wave 3]
    ├── test_routes.py                 ← 20 тестов · FastAPI routes через TestClient [Wave 2]
    ├── test_analyze_flow.py           ← 23 теста · execute_task полный pipeline + ensure_comparison + point_stats + LLM summary [Wave 3]
    ├── test_task_lifecycle.py         ← 6 тестов  · end-to-end lifecycle через FastAPI TestClient [Wave 3]
    ├── test_error_paths.py            ← 15 тестов · edge cases: таймауты, падения модулей, cleanup_old_tasks [Wave 3]
    └── test_clusters_flow.py          ← 20 тестов · start_clusters_calculation + Excel/HTML генерация + _serialize_cluster [Wave 3]

smoke/                                 ← Wave 4: smoke-тесты (импорт, app init, liveness) + Phase 3-2 (структура пакета)
├── __init__.py
├── test_imports.py                   ← 43 теста · импорт 29 модулей + 7 опциональных (DB/slowapi skip)
├── test_app_init.py                  ← 8 тестов  · FastAPI app создаётся, роутеры, /health, /openapi.json
├── test_llm_smoke.py                 ← 5 тестов  · llm_analyzer callable, клиенты None без ключей
└── test_bot_package.py               ← 19 тестов · структура bot/ пакета после Phase 3-2 [Phase 3-2]

golden/                                ← Wave 4: golden-тесты (эталонные выходы)
├── __init__.py
├── conftest.py                       ← фикстуры golden_compare / golden_text_compare + --update-golden
├── generate_golden.py                ← скрипт перегенерации эталонов из реальных функций
├── test_golden_parser.py             ← 7 тестов  · parse_card_to_row → эталонный JSON
├── test_golden_analytics.py          ← 8 тестов  · calculate_metrics / cross_tables / compare_metrics
├── test_golden_llm.py                ← 4 теста   · format_metrics_for_prompt → эталонный .txt
├── test_golden_user_parser.py        ← 16 тестов · parse_period / find_region → эталоны
└── fixtures/                         ← 11 эталонных файлов (~14 KB)
    ├── parser/card_*.json            ← 5 карточек ДТП
    ├── parser/parse_period_cases.json
    ├── parser/find_region_cases.json
    ├── analytics/metrics_basic_set.json
    ├── analytics/cross_tables_basic_set.json
    ├── analytics/comparison_may_vs_april.json
    ├── analytics/group_dtp_type.json
    ├── analytics/group_road_significance.json
    └── llm/metrics_prompt_may_vs_april.txt
```

**Дополнительно нужно положить рядом с `tests/`:**

| Файл                 | Назначение                                                |
|----------------------|-----------------------------------------------------------|
| `pytest.ini`         | Конфигурация pytest, маркеры, порог покрытия              |
| `requirements-dev.txt` | Зависимости для тестирования (pytest, respx, freezegun) |

---

## Быстрый старт

### 1. Установка зависимостей

```bash
cd gibdd-bot
pip install -r requirements.txt        # основные зависимости проекта
pip install -r requirements-dev.txt    # зависимости для тестов
```

`requirements-dev.txt` ставит:

- `pytest >= 8.0`
- `pytest-asyncio >= 0.23` (режим `auto`)
- `pytest-cov >= 5.0`
- `respx >= 0.21` (mock для `httpx`)
- `freezegun >= 1.5` (mock для времени)
- `coverage >= 7.4`

### 2. Запуск всех тестов

```bash
pytest
```

Ожидаемый вывод (на Linux с установленным python-telegram-bot):

```
457 passed, 7 skipped, 1 warning in 6.65s

Required test coverage of 40% reached. Total coverage: 77.04%
Coverage HTML written to dir tests/_coverage_html
```

На Windows без `python-telegram-bot` — 458 passed, 6 skipped (PTB-зависимые
тесты корректно skip'аются, см. раздел «Опциональные зависимости»).

### 3. Запуск с деталями

```bash
pytest -v                              # подробный список тестов
pytest tests/unit/test_analytics_metrics.py   # один файл
pytest tests/unit/test_analytics_metrics.py::TestCalculateMetrics::test_total_accidents
                                       # один конкретный тест
```

### 4. HTML-отчёт покрытия

```bash
pytest                                 # запускает и генерит отчёт
# открыть в браузере:
xdg-open tests/_coverage_html/index.html
```

### 5. Фильтрация по маркерам

```bash
pytest -m "not slow"                   # без медленных тестов
pytest -m smoke                        # только smoke (1.6с)
pytest -m golden                       # только golden-тесты (0.2с)
pytest -m integration                  # только интеграционные
pytest tests/golden/ --update-golden   # перезаписать эталоны (осознанно!)
pytest tests/smoke/test_bot_package.py # только Phase 3-2 структурные тесты
```

### 6. Опциональные зависимости

Некоторые smoke-тесты корректно skip'аются, если опциональная зависимость
не установлена. Это нормально для dev-окружения и не является ошибкой:

| Зависимость          | Что пропускается                          | Как установить |
|----------------------|-------------------------------------------|----------------|
| `psycopg`            | DB-модули (`backend.db.*`, 6 тестов)       | `pip install psycopg[binary]` |
| `slowapi`            | rate_limit middleware (1 тест)             | `pip install slowapi` |
| `python-telegram-bot` | bot/* модули после Phase 3-2 (15 тестов) | `pip install python-telegram-bot>=20.0` |

В продакшн-окружении все три зависимости установлены — все 464 теста проходят.

---

## Структура тестирования — 4 волны

Тесты спроектированы послойно, от чистых функций к интеграционным сценариям.
Каждая волна независима и может запускаться отдельно.

### Wave 1 — Чистые функции (✅ завершена)

Тестирует модули без внешних зависимостей:

- `analytics.py` → `calculate_metrics`, `compare_metrics`, `calculate_cross_tables`
- `point_statistics.py` → агрегаты по точкам
- `gibdd_parser.py` → `parse_card_to_row`
- `user_request_parser.py` → `parse_period`, `find_region`, `parse_user_message`
- Кэш in-memory в `gibdd_service.py` (через `id(cards)`)

**Фикстуры:** `tests/fixtures/synthetic_cards.py` — `BASE_CARD` + 7 готовых вариантов
(`card_with_death`, `card_with_alcohol`, `card_with_pedestrian`, и т.д.)

**Найдено багов: 3** (см. раздел «Исправленные баги» ниже)

### Wave 2 — Моки для LLM и сервисов (✅ завершена)

Тестирует модули с внешними HTTP-зависимостями, используя моки:

- `llm_analyzer.py` → `format_metrics_for_prompt` (50 тестов на форматирование)
  и `ask_paid_llm` / `ask_free_llm` (25 тестов с моками `httpx` через `respx`)
- `miniapp/backend/telegram_auth.py` → проверка HMAC-подписи Telegram initData
  (18 тестов, включая corrupted hash, replay, expired auth_date)
- `miniapp/backend/services/gibdd_service.py` → endpoint `/analyze` с моком
  внешнего API ГИБДД (25 тестов)
- `miniapp/backend/routers/*` → 20 интеграционных тестов через `TestClient`
  FastAPI с переопределённой зависимостью `get_current_user`

**Ключевые фикстуры `conftest.py` (Wave 2):**

| Фикстура                 | Что делает                                                |
|--------------------------|-----------------------------------------------------------|
| `patch_llm_keys`         | Подменяет `LLM_API_KEY`, `LLM_PAID_API_KEY` на тестовые   |
| `reset_llm_clients`      | Сбрасывает глобальные `_free_llm_client` / `_paid_llm_client` |
| `disable_rate_limiter`   | Отключает `_MIN_LLM_INTERVAL` (иначе тесты ждут по 5 сек) |
| `telegram_init_data_factory` | Генерирует валидный initData с правильной HMAC-подписью |
| `test_bot_token`         | Фиксирует `TELEGRAM_BOT_TOKEN` для тестов                 |
| `fastapi_test_user`      | Возвращает `TelegramUser` для override-авторизации        |
| `fastapi_client`         | FastAPI `TestClient` с уже подменённой авторизацией       |
| `clear_in_memory_tasks`  | Чистит `_tasks` в `gibdd_service` до/после теста          |
| `sample_comparison`      | Минимальный `comparison dict` для тестов форматирования   |

### Wave 3 — End-to-end integration (✅ завершена)

Тестирует **полный пайплайн** `execute_task` от создания задачи до готовых
файлов, а также все длительные операции (clusters, point_stats, LLM summary,
Excel/HTML генерация). Внешние модули (bot, gibdd_parser, analytics,
excel_generator, report_generator, llm_analyzer, point_statistics,
concentration_points, camera_cache, camera_matcher) подменяются stub'ами
через `_gibdd_stubs.py`.

**Ключевой файл:** `tests/integration/_gibdd_stubs.py` — фабрика stub-модулей
с конфигурируемыми параметрами (cards, prev_cards, errors, raise, llm_answer,
has_cameras, config_overrides).

| Тест-файл                           | Тестов | Что покрывает |
|-------------------------------------|--------|---------------|
| `test_analyze_flow.py`              | 23     | `execute_task` happy path, переходы статусов, error paths (empty cards, bot exception, task not found), `ensure_prev_cards` (4 кейса), `ensure_comparison` (4 кейса), `compute_point_stats` (2 кейса), `start_llm_summary` (3 кейса), `ask_llm_question` (3 кейса), `get_llm_providers_status` (2 кейса) |
| `test_task_lifecycle.py`            | 6      | End-to-end через FastAPI TestClient: create → poll → done → GET files, structured/text/failed modes, LLM summary polling, cached summary, QA history через эндпоинты |
| `test_error_paths.py`               | 15     | Edge cases: excel_generator crash, report_generator crash (карта опциональна), analytics fallback, multi-month prev loading, cleanup_old_tasks с файлами и без, LLM summary с invalid provider, LLM exception, cached comparison, ask_llm history preserved |
| `test_clusters_flow.py`             | 20     | `start_clusters_calculation` happy/failed/cameras, `_serialize_cluster` (4 кейса), `generate_clusters_map_html` (4 кейса), `generate_clusters_excel`, `generate_point_stats_excel`, `generate_point_stats_map_html`, `_color_for_severity` |

### Wave 4 — Golden / Smoke (✅ завершена)

Финальная волна: регрессионная защита эталонных выходов и быстрые
проверки живости модулей. Не пытается поднять покрытие (это задача
Wave 1–3) — фиксирует **контракты**.

**Smoke-тесты** (`tests/smoke/`, маркер `@pytest.mark.smoke`):

| Файл                       | Тестов | Что проверяет |
|----------------------------|--------|---------------|
| `test_imports.py`          | 43     | Все 29 ключевых модулей импортируются без ошибок + 7 опциональных (DB/slowapi — skip если dep нет), отсутствие циклических импортов, импорт тестовых фикстур, существование `worklog.md` |
| `test_app_init.py`         | 8      | FastAPI app создаётся, все 6 роутеров зарегистрированы, `/miniapp/health` → 200, `/openapi.json` генерируется, CORSMiddleware (soft warning), `/docs` и `/redoc`, Settings загружаются, `gibdd_service._tasks` доступен |
| `test_llm_smoke.py`        | 5      | llm_analyzer импортируется, клиенты None без ключей, `format_metrics_for_prompt` вызывается, `parse_card_to_row(BASE_CARD)` работает, `calculate_metrics(cards_basic_set())` работает |
| `test_bot_package.py`      | 19     | [Phase 3-2] Структура пакета `bot/` после рефакторинга: 14 модулей импортируются, thin shim `bot.py` работает, публичный API доступен, shared state единственный, нет циклических импортов, структура директории соответствует плану. PTB-зависимые тесты skip'аются если `python-telegram-bot` не установлен. |

**Golden-тесты** (`tests/golden/`, маркер `@pytest.mark.golden`):

| Файл                       | Тестов | Что сравнивает с эталоном |
|----------------------------|--------|----------------------------|
| `test_golden_parser.py`    | 7      | `parse_card_to_row` для 5 типов карточек (BASE_CARD + 4 варианта), проверка наличия всех эталонов, стабильность количества полей (>=50) |
| `test_golden_analytics.py` | 8      | `calculate_metrics(cards_basic_set())`, `calculate_cross_tables(...)`, `compare_metrics(май vs апрель)`, `group_dtp_type` для 9 типов, `group_road_significance` для 5 категорий, инварианты формул |
| `test_golden_llm.py`       | 4      | `format_metrics_for_prompt` → эталонный `.txt`, наличие обязательных секций (7 разделов), prompt начинается с "Регион:", содержит знаки изменения |
| `test_golden_user_parser.py` | 16   | `parse_period` для 10 запросов (год, кварталы I/II/III/IV, полугодие, конкретный месяц, strict format), параметризованная стабильность, `find_region` для 10 запросов, регрессии BUG #1 и BUG #3 |

**Эталонные файлы** (`tests/golden/fixtures/`, 11 файлов):

- `parser/card_base.json` + 4 варианта (`with_death`, `with_alcohol`, `with_pedestrian`, `unknown_type`)
- `parser/parse_period_cases.json` — 10 запросов → ParsedPeriod
- `parser/find_region_cases.json` — 10 запросов → tuple/None
- `analytics/metrics_basic_set.json` — calculate_metrics на cards_basic_set
- `analytics/cross_tables_basic_set.json` — calculate_cross_tables на cards_basic_set
- `analytics/comparison_may_vs_april.json` — compare_metrics двух выборок
- `analytics/group_dtp_type.json` — маппинг raw типов в 9 категорий
- `analytics/group_road_significance.json` — маппинг категорий дорог
- `llm/metrics_prompt_may_vs_april.txt` — эталонный LLM prompt

**Обновление эталонов** (только при осознанном изменении контракта!):

```bash
# Перегенерировать все эталоны из реальных функций:
python tests/golden/generate_golden.py

# Или обновить через pytest (прочитает actual из теста и перезапишет):
pytest tests/golden/ --update-golden
```

После обновления — закоммитьте изменения в `tests/golden/fixtures/` с описанием,
почему изменился контракт.

---

## Покрытие по модулям

| Модуль                                  | Stmts | Miss | Cover | 
|-----------------------------------------|-------|------|-------|
| `analytics.py`                          | 943   | 421  | 55 %  |
| `gibdd_parser.py`                       | 249   | 2    | **99 %** |
| `llm_analyzer.py`                       | 791   | 108  | 86 %  |
| `miniapp/backend/services/gibdd_service.py` | 934 | 177  | **81 %** |
| `miniapp/backend/telegram_auth.py`      | 60    | 0    | **100 %** |
| `user_request_parser.py`                | 211   | 24   | 89 %  |
| **ИТОГО**                               | 3188  | 732  | **77.04 %** |

`gibdd_service.py` после Wave 3 вырос с 31 % до 81 % — все ключевые
функции (`execute_task`, `ensure_prev_cards`, `ensure_comparison`,
`compute_point_stats`, `start_clusters_calculation`, `start_llm_summary`,
`ask_llm_question`, `generate_clusters_*`, `generate_point_stats_*`,
`cleanup_old_tasks`) теперь покрыты.

`analytics.py` (55 %) — большая часть непокрытых строк это
`build_full_analytics` (сложная агрегирующая функция) и SQL-like
фильтры. Для неё добавлены golden-тесты на основные public-API
(`calculate_metrics`, `calculate_cross_tables`, `compare_metrics`,
`group_dtp_type`, `group_road_significance`).

---

## Исправленные баги

Все 4 бага найдены через тесты Wave 1–4.

### BUG #1 (Wave 1): Регулярное выражение для кварталов

**Было:** `r"(?:(i{1,2}v?|vi{0,3}|iv|v|ix|x{1,3})\s*(?:кв|квартал))"`

**Проблема:** `i{1,2}v?` матчит только I, II, IV — но не III квартал.
III квартал вообще не распознавался, запросы вида «III квартал 2024»
возвращали `None`.

**Стало:** `i{1,3}v?` — теперь матчит I, II, III, IV.

**Тест:** `test_parse_period_quarters` проверяет все 4 квартала.

### BUG #2: Пустая строка в `find_region`

**Было:** функция сразу начинала нормализацию и пыталась искать вхождения.

**Проблема:** При `text_lower = ""` (пустой ввод) функция проходила по всем
регионам и для каждого проверяла `if word in normalized:` — это работало,
потому что пустая строка содержится в любой. В итоге `find_region("")`
возвращал **первый регион из справочника**, а не `None`.

**Стало:** добавлен ранний возврат:
```python
if not text_lower:
    return None
```

**Тест:** `test_find_region_empty_string_returns_none`.

### BUG #3: Substring-матч регионов

**Было:** `if word in normalized:` — простой `in` без границ слов.

**Проблема:** Запрос «москва» находил не только «г. Москва», но и любой
регион, где в названии есть подстрока «москва» (например, «Московская
область» — это другое). Также «орел» матчит «Орёл», «Орловская область»,
и любой регион с «орел» внутри названия — выбирался первый попавшийся.

**Стало:** `if re.search(r'\b' + re.escape(word) + r'\b', normalized):`
— матч только по границам слов.

**Тест:** `test_find_region_does_not_match_substring` — проверяет, что
«москва» не возвращает «Московская область».

### BUG #4 (Wave 4): Non-deterministic sort в LLM prompt

**Где:** `llm_analyzer.py`, функция `format_metrics_for_prompt`, блоки
«По видам ДТП» и «По погодным условиям».

**Было:**
```python
all_types = sorted(
    set(list(cur_type.keys()) + list(prev_type.keys())),
    key=lambda x: cur_type.get(x, 0) + prev_type.get(x, 0),
    reverse=True,
)
```

**Проблема:** `sorted` с одним ключом (только суммарная частота) не
детерминирован, когда у нескольких элементов одинаковая сумма (например,
«Наезд на пешехода» 1+0 = 1 и «Опрокидывание» 0+1 = 1). Python `sorted`
стабилен, но порядок итерации `set(...)` зависит от `PYTHONHASHSEED` —
поэтому при равных суммах взаимный порядок менялся между запусками.

На практике это означало, что LLM получал один и тот же промпт с
разным порядком пунктов при разных запусках бота — что ухудшало
кэшируемость ответов и могло влиять на качество анализа.

**Стало:**
```python
all_types = sorted(
    set(list(cur_type.keys()) + list(prev_type.keys())),
    key=lambda x: (-(cur_type.get(x, 0) + prev_type.get(x, 0)), x),
)
```
Вторичный ключ `x` (алфавитный) гарантирует детерминированный порядок
при равных суммах.

**Тест:** `test_format_metrics_for_prompt_matches_golden` — golden-тест
на эталонный `.txt` падает, если порядок меняется. Проверено при
`PYTHONHASHSEED=0,1,42,12345` — стабильно.

---

## Конфигурация `pytest.ini`

```ini
[pytest]
asyncio_mode = auto                          # async-тесты запускаются без @pytest.mark.asyncio
testpaths = tests
strict_markers = true                        # неизвестный маркер = ошибка
addopts =
    -ra
    --strict-markers
    --cov=analytics
    --cov=user_request_parser
    --cov=gibdd_parser
    --cov=llm_analyzer
    --cov=backend.telegram_auth
    --cov=backend.services.gibdd_service
    --cov-report=term-missing
    --cov-report=html:tests/_coverage_html
    --cov-fail-under=40                      # CI упадёт, если покрытие < 40 %

markers =
    slow:        тесты дольше 1 секунды
    integration: требуют БД или внешние сервисы
    golden:      replay захваченных ответов LLM
    smoke:       быстрые проверки живости прод-эндпоинтов

filterwarnings =
    ignore::DeprecationWarning:pytest_asyncio.*
    ignore:coroutine '.*' was never awaited:RuntimeWarning
```

---

## Как добавить новый тест

### Unit-тест для чистой функции

1. Открой соответствующий файл в `tests/unit/test_<module>.py`
   (или создай новый, если модуль ещё не покрыт).
2. Используй `BASE_CARD` / `make_card(**overrides)` из
   `tests/fixtures/synthetic_cards.py` для данных.
3. Имя функции — `test_<что_проверяется>_<условие>`.
4. Не используй `time.sleep` — бери `freezegun.freeze_time`.

### Тест с моком HTTP (respx)

```python
import respx
import httpx

@respx.mock
async def test_my_endpoint():
    respx.post("https://api.example.com/v1").respond(
        json={"result": "ok"},
    )
    # ... вызов функции, которая делает httpx-запрос ...
```

### Тест FastAPI route

Используй фикстуру `fastapi_client` — она уже подменяет Telegram-авторизацию:

```python
def test_my_route(fastapi_client):
    response = fastapi_client.get("/api/v1/regions")
    assert response.status_code == 200
```

### Тест LLM-вызова

Используй `patch_llm_keys` + `reset_llm_clients` + `disable_rate_limiter`:

```python
@respx.mock
async def test_ask_paid_llm(patch_llm_keys, reset_llm_clients, disable_rate_limiter):
    respx.post("https://test.example.com/v1").respond(
        json={"choices": [{"message": {"content": "Анализ готов"}}]},
    )
    result = await llm_analyzer.ask_paid_llm("промпт")
    assert result == "Анализ готов"
```

### Тест gibdd_service pipeline (Wave 3)

Используй `install_stubs` из `_gibdd_stubs.py`:

```python
from tests.integration._gibdd_stubs import install_stubs, make_minimal_cards

@pytest.mark.asyncio
async def test_execute_task(monkeypatch, clear_in_memory_tasks, tmp_path):
    from backend.services import gibdd_service
    monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

    install_stubs(monkeypatch, cards=make_minimal_cards(3))

    task = gibdd_service.create_task(
        user_id=1, region_code="1101", region_name="Рег",
        period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
    )
    await gibdd_service.execute_task(task.id)

    assert task.status == gibdd_service.TaskStatus.DONE
    assert task.total_dtp == 3
```

Stub'ы конфигурируются: `cards`, `prev_cards`, `bot_errors`, `bot_raise`,
`llm_answer`, `has_cameras`, `config_overrides`, `record_bot_calls`.

---

## CI/CD интеграция

Минимальный GitHub Actions:

```yaml
name: tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: pytest --cov-fail-under=40
```

`pytest.ini` уже настроен так, что упадёт, если:
- любой тест упадёт;
- покрытие упадёт ниже 40 %;
- будет использован незарегистрированный маркер.

---

## Что дальше

**Все 4 волны тестирования и Phase 3-2 рефакторинг завершены.** Полная защищённость от регрессий:

| Волна   | Тестов | Покрытие | Что защищает |
|---------|--------|----------|--------------|
| Wave 1  | 157    | +60 %    | Чистые функции (parser, analytics, user_request_parser) |
| Wave 2  | +138   | +62 %    | LLM и сервисы с моками HTTP/config/FastAPI auth |
| Wave 3  | +84    | +71 %    | End-to-end интеграция (TestClient + stubs) |
| Wave 4  | +59    | 77 %     | Эталонные выходы + smoke проверки живости |
| Phase 3-2 | +19 | 77 %     | Структура пакета `bot/` после рефакторинга (smoke) |
| **Итого** | **464** (458 + 6 skip) | **77.04 %** | — |

### Завершённые фазы

- ✅ **Phase 3-1** (4 волны тестов) — 445 тестов, 77% coverage, 4 бага найдено и исправлено
- ✅ **Phase 3-2** (рефакторинг `bot.py`) — 4138 строк → 14-модульный пакет `bot/`.
  100% pure refactoring (никакая логика не изменена, только перемещена).
  19 smoke-тестов на структуру пакета + thin shim `bot.py` для обратной совместимости.
  `python bot.py` продолжает работать как раньше.

### Будущие работы

- **Phase 3-3** — тюнинг под 30+ concurrent users (нагрузочное тестирование)
- **Phase 3-4** (optional) — PostgreSQL cache для cross-user reuse
- Будущая работа: декомпозиция `on_callback_query` (488 строк) в dispatch-таблицу
- Будущая работа: разбиение `bot/analysis.py` (1335 строк) на `pipeline.py + clusters.py + menu.py`
