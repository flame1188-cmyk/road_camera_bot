# GIBDD Stat — Telegram-бот + Mini App

Telegram-бот и веб-Mini App для выгрузки и анализа данных ДТП из открытых данных ГИБДД ([stat.gibdd.ru](http://stat.gibdd.ru)).

Проект объединяет:
- **Telegram-бот** (`bot.py` + пакет `bot/`) — команды, inline-кнопки, выгрузка файлов в чат, нативные HTML-карты в attachment.
- **Mini App** (`miniapp/`) — FastAPI backend + React/TypeScript frontend, открывается в нативном WebView Telegram (решает проблему iOS Quick Look, не выполняющего JS).
- **Единая точка входа** (`main.py`) — поднимает FastAPI и Telegram-бота (webhook) в одном процессе, раздаёт Mini App на `/app/`.

Бот запрашивает данные через Open Data API или (при недоступности API) напрямую с сайта, парсит карточки ДТП и возвращает стилизованные Excel-файлы и интерактивные HTML-карты. Поддерживает естественный язык ввода, аналитику с сравнением периодов, расчёт очагов концентрации ДТП, сопоставление с камерами фотовидеофиксации, прогноз по НП БДД и AI-анализ данных через ZhipuAI GLM.

---

## Возможности

### Основной функционал

- **3 способа ввода запроса:** inline-кнопки (`/dtp`), естественный язык («Вологодская область за 2025 год»), строгий формат (`2.2024 1119`)
- **Два Excel-файла:** карточки ДТП (1 строка = 1 ДТП) и участники ДТП (1 строка = 1 участник)
- **Аналитика:** сравнение текущего периода с АППГ, распределение по дням недели, часам, видам ДТП, 25 кросс-таблиц корреляций, статистические метрики (severity rates, Z-score, χ²)
- **Очаги концентрации ДТП:** автоматический расчёт мест концентрации аварийности с новой методологией v2 (пикетаж + соседи + слияния)
- **Камеры фотовидеофиксации:** загрузка реестра камер через Excel-файл, кэширование по регионам, автоматическое сопоставление с очагами ДТП (по пикетажу и геопозиции)
- **Статистика по точке:** отправка геолокации для получения сводки ДТП в заданном радиусе
- **AI-анализ (ZhipuAI GLM-4.7-Flash):** генерация аналитического резюме, ответы на вопросы по данным, поиск новостей из открытых источников (Google News RSS + DuckDuckGo) для контекста
- **НП БДД (Национальный проект «Безопасные качественные дороги»):** история погибших, прогноз с сезонными коэффициентами, коридор прогноза, KPI-статус (ok/warning/danger), frozen-годы
- **Web-fallback:** при ошибке API (5xx, ConnectionError) автоматически переключается на экспорт через сайт stat.gibdd.ru (POST генерация + GET скачивание XML)
- **4-уровневый fallback справочника регионов:** API → файловый кэш → встроенный хардкод (82 региона) → пустой список
- **Трёхуровневый кэш данных ДТП:** L1 in-memory LRU → L2 PostgreSQL (cards/clusters/excel) → L3 файловый кэш. Экономия до 18-28 сек на повторных запросах того же региона+периода
- **Персистентность задач в PostgreSQL:** задачи выгрузки, кластеры и аудит-лог доступа к ПДн (152-ФЗ) хранятся в БД, переживают рестарт
- **Ограничение доступа:** optional whitelist по Telegram user ID

### Очаги концентрации ДТП (методология v2)

Полная переработка методологии сопоставления очагов между периодами. Старая методология (центр очага + радиус 500м/2км + совпадение дороги) заменена на более точную:

**Алгоритм сопоставления:**
- **Повторный очаг:** та же дорога + пересечение диапазонов пикетажа (`dtp_pk_min/max`) ИЛИ ДТП в радиусе 100м (для безпикетажных, типично НП)
- **Подстатус для повторного:** `growing` / `shrinking` / `stable` / `merged` (по изменению кол-ва ДТП)
- **Слияние:** 2+ прошлогодних очага пересекаются с одним текущим → `repeated_merged`
- **Новый (есть ближайший в АППГ):** не пересеклись, но в радиусе 1000м (вне НП) / 250м (в НП) есть прошлый очаг. Сохраняется список до 3 ближайших
- **Новый:** нет ни повтора, ни соседа
- **Исчезнувший:** прошлый, у которого нет повторного в текущем (сосед не спасает от lost)

**Расчёт очагов:**
1. **Населённые пункты** — 3 прохода: перекрёстки (радиус 50 м), дороги с пикетажем (окно 200 м), общие точки (радиус 100 м + проверка пикетажа). Порог: 3+ ДТП одного вида или 5+ любых видов
2. **Вне НП (автодороги)** — группировка по названию дороги, скользящее окно 1 км. Порог: 3+ ДТП одного вида или 5+ любых видов
3. **Определение границ НП/вне НП** через OSM Overpass API с реальными полигонами (Shapely). Оптимизации: in-memory LRU-кэш полигонов, адаптивный bbox, параллельные запросы к зеркалам, дисковый кэш с TTL, STRtree для классификации (вместо unary_union)

**Excel-отчёт по очагам (4 листа):**
- Очаги текущего периода
- Динамика (с исчезнувшими, повторными, новыми, новыми с соседом)
- Детализация всех ДТП в очагах
- Предочаги (потенциальные очаги следующего периода)

### Интерактивные HTML-карты

Бот и Mini App генерируют самодостаточные HTML-файлы с картами на базе Leaflet. Все библиотеки встраиваются инлайн — файлы работают без интернет-соединения (кроме подгрузки тайлов OpenStreetMap).

**Типы карт:**
- **Карта ДТП** — все ДТП региона с попапами, фильтрами (вид, тяжесть, дата) и опциональным слоем камер
- **Карта очагов** — очаги и предочаги ДТП с зонами (convex hull), динамикой (7 цветов по статусу), камерами, пунктирными линиями связи для «новых с соседом»
- **Карта точки** — ДТП и камеры в радиусе от заданной геоточки

**Возможности карт:**
- **Popup с расширенной информацией** — дата, адрес, тяжести, транспортные средства, погодные условия, дорожные условия, объекты УДС, нарушения ПДД и сопутствующие нарушения
- **Кластеризация маркеров** (Leaflet.markercluster) — для карт ДТП и точки кластеризуются и ДТП, и камеры; для карты очагов только камеры (ДТП немного)
- **Spiderfy** — при максимальном зуме наложенные маркеры «раскрываются» в спираль/линию для доступа к каждой точке
- **maxZoom: 19** — детальное приближение до уровня отдельных зданий
- **Линейка** — собственная реализация без внешних зависимостей: кнопка-переключатель 📏 в углу карты, клик добавляет точки отрезка, двойной клик завершает измерение
- **Фильтры на карте ДТП** — по виду ДТП, тяжести, диапазону дат, моделям камер (множественный выбор)
- **Управление слоями** — возможность скрывать/показывать слои ДТП и камер

### Mini App

Telegram Mini App, открывающийся в нативном WebView Telegram. Решает главную проблему iOS-пользователей — HTML-карты открываются в WebView, выполняющем JavaScript, а не в Quick Look.

**Вкладки Mini App:**
1. **Запрос** — структурированная форма (регион + период) или текстовый ввод
2. **Аналитика** — 25 кросс-таблиц, статистические метрики, ECharts-визуализации
3. **Очаги** — расчёт очагов концентрации, карта (iframe), KPI-сводка, динамика vs АППГ, Top-10 по тяжести, предочаги, Excel-выгрузка
4. **Точка** — статистика ДТП в радиусе от геоточки + карта
5. **ИИ-анализ** — генерация аналитического резюме (15-90 сек), Q&A с историей
6. **НП БДД** — история, прогноз, коридор, KPI-статус, управление frozen-годами

**Технические особенности:**
- **Long polling** (25 сек) для статуса длительных операций (очаги, LLM-резюме) — устраняет 30+ коротких запросов
- **Локальный флаг `starting`** для мгновенного показа прогресс-бара после клика, не дожидаясь первого long-poll ответа
- **Elasped-time тикер** для LLM-анализа с предупреждениями при > 90 сек и рекомендацией отмены при > 240 сек
- **Сброс кэша react-query** при retry после ошибки — иначе кнопка «Повторить» возвращает старую ошибку
- **Telegram theme** — автоматическое применение цветовой схемы (light/dark) через CSS-переменные
- **Haptic feedback** — нативная вибрация на клики/ошибки/успехи
- **Fullscreen mode** — кнопка раскрытия на десктопе

### Совместимость с мобильными устройствами (iOS)

На iPhone HTML-файлы из Telegram по умолчанию открываются в Quick Look, который не выполняет JavaScript — в результате карта отображается пустой. Для полноценной работы:

- **Рекомендуется:** открыть Mini App через кнопку-меню бота (нативный WebView, JS выполняется полностью)
- **Альтернатива 1:** установить приложение [HTML Viewer](https://apps.apple.com/app/html-viewer) (бесплатно) и открывать файлы через него
- **Альтернатива 2:** в Telegram долгое нажатие на файл → «Поделиться» → «Сохранить в Файлы» → открыть в приложении «Файлы» (файл откроется в Safari)

---

## Архитектура

### Единый процесс на bothost

```
                    ┌─────────────────────────────────┐
                    │   bothost.ru (TLS-терминация)    │
                    │   bot1234.bothost.tech           │
                    └────────────┬────────────────────┘
                                 │  HTTPS
                    ┌────────────▼────────────────────┐
                    │   main.py (FastAPI + uvicorn)    │
                    │   один процесс, PORT=$PORT       │
                    ├─────────────────────────────────┤
                    │  /bot/webhook  → Telegram bot    │
                    │  /api/*        → Mini App API    │
                    │  /app/*        → React static    │
                    │  /health       → healthcheck     │
                    └─────────────────────────────────┘
```

**Единый процесс**: `main.py` поднимает FastAPI и в lifespan инициализирует Telegram-бота в webhook-режиме. Mini App frontend (собранный React) раздаётся как статика из `miniapp/frontend/dist`.

### Канал данных: API → Fallback → Кэш

```
Запрос пользователя
       │
       ▼
┌──────────────┐    5xx / ConnectionError
│  GIBDD API   │──────────────────────┐
│ (opendataapi)│                      │
└──────┬───────┘                      ▼
       │                    ┌──────────────────┐
       │                    │  Сайт stat.gibdd │
       │                    │  POST → GET → XML│
       │                    └────────┬─────────┘
       │                             │
       ▼                             ▼
┌─────────────────────────────────────────┐
│         Единый формат карточек ДТП       │
│   (совместимый для API и web-fallback)  │
└─────────────────┬───────────────────────┘
                  │
        ┌─────────┼─────────┐
        ▼         ▼         ▼
   dtp_cards  dtp_uch   analytics
     .xlsx      .xlsx     .xlsx
```

### Трёхуровневый кэш данных ДТП (L1 + L2 + L3)

После загрузки и обработки данные кэшируются на трёх уровнях. На повторных запросах того же региона+периода генерация полностью пропускается:

```
Запрос (reg_code, dat_hash)
       │
       ▼
┌─────────────────────────────────────────────────────┐
│ L1: In-memory LRU (data_cache.py, 100 записей)       │
│    cards + prev_cards in-process, мгновенный HIT     │
└─────────────┬───────────────────────────────────────┘
              │ miss
              ▼
┌─────────────────────────────────────────────────────┐
│ L2: PostgreSQL (модуль miniapp/backend/db/)          │
│    • cards_cache    — JSONB с карточками ДТП          │
│    • clusters_cache — JSONB с raw_clusters + metrics  │
│    • excel_cache    — BYTEA с готовыми xlsx-файлами   │
│    TTL=86400s (24ч), фоновая очистка каждые 2 часа    │
└─────────────┬───────────────────────────────────────┘
              │ miss
              ▼
┌─────────────────────────────────────────────────────┐
│ L3: Файловый кэш (regions_cache, cameras, osm_cache)  │
└─────────────┬───────────────────────────────────────┘
              │ miss
              ▼
        Полная выгрузка из API/сайта ГИБДД
```

**Ключи кэша:**
- `cards_cache`, `excel_cache`: `(reg_code, dat_hash)` — хэш списка dat (месяцев)
- `clusters_cache`: `(reg_code, current_dat_hash, prev_dat_hash)` — зависит от пары периодов

**Экономия на повторных запросах** (подтверждено в проде):

| Stage | Что закэшировано | Экономия на HIT |
|-------|------------------|-----------------|
| Stage 3 | Карточки ДТП (cards_cache) | ~3-5 сек |
| Stage 4 | Кластеры + raw_clusters (clusters_cache) | ~8-15 сек (DBSCAN) |
| Stage 5 | Excel Файл 1 + Файл 2 (excel_cache) | ~7-8 сек |

Совокупная экономия: **~18-28 сек** на повторном запросе. Кэш особенно эффективен, когда несколько сотрудников ГИБДД выгружают один регион за тот же период.

### Загрузка справочника регионов

```
1. Модульный кэш (_regions_cache в памяти)
   ↓ (не загружен)
2. API ГИБДД (fetch_regions) [можно отключить через REGIONS_API_ENABLED=0]
   ↓ (успех → сохранение в data/regions_cache.json)
   ↓ (ошибка: 5xx, таймаут)
3. Файловый кэш (data/regions_cache.json)
   ↓ (файл отсутствует)
4. Встроенный справочник (regions_builtin.py, 82 региона)
```

### Сопоставление с камерами

`camera_matcher.py` реализует три стратегии:
1. Линейный очаг с пикетажем → камера на том же участке дороги (по пикетажу)
2. Линейный очаг без камеры на участке → гео-поиск от крайних точек (1 км НП / 500 м вне НП)
3. Очаг без пикетажа (НП) → гео-поиск 200 м (закрытый) и 500 м (ближайшие)

### Архитектура Telegram-бота (пакет `bot/`)

После Phase 3-2 рефакторинга (август 2026) монолитный `bot.py` (4138 строк)
разбит на модульный пакет `bot/` с thin shim `bot.py` для обратной совместимости.
Принцип: 100% pure refactoring — никакая логика не изменена, только перемещена.

```
bot.py (13 строк)                ← thin shim: from bot.app import main; main()
└── bot/
    ├── _state.py      (214)     ← shared state: imports, logger, globals, constants
    ├── infra.py       (178)     ← утилиты TG API: _tg_retry, _safe_edit, _send_long_message
    ├── access.py      (187)     ← доступ + загрузка регионов: is_user_allowed, _fetch_cards_for_period
    ├── keyboards.py   (109)     ← inline-клавиатуры: build_region_keyboard, build_period_keyboard
    ├── analysis.py   (1335)     ← конвейер аналитики и очагов (самый большой модуль)
    ├── output.py      (258)     ← HTML-карты: _generate_and_send_dtp_map, _send_analytics_html
    ├── point_stats.py (422)     ← статистика по геоточке: _start_point_stats, _process_point_stats
    ├── qa.py          (150)     ← Q&A с LLM: _handle_analytics_question
    ├── app.py         (204)     ← точка входа: main, _build_app, error_handler
    └── handlers/
        ├── commands.py   (391)  ← /start /help /dtp /regions /miniapp /precache
        ├── callbacks.py  (512)  ← on_callback_query (488 строк, перенесён as-is)
        └── messages.py   (365)  ← handle_message + _handle_document
```

**Граф зависимостей** (без циклов):
```
app → handlers/* → analysis, point_stats, qa, output
                 → keyboards, access, infra
analysis → access, infra, keyboards, output
output → infra
point_stats → access, infra
qa → infra
infra / access / keyboards → _state
```

Все глобальные переменные (`_api_down`, `_user_locks`, `_precache_lock`, `logger`,
константы `TG_MSG_LIMIT`, `MONTH_*`, etc.) объявлены в `bot/_state.py` и
импортируются через `from bot._state import *`. Это гарантирует, что состояние
едино во всех модулях (проверено smoke-тестом `test_shared_state_is_single_instance`).

**Тестирование**: 19 smoke-тестов в `tests/smoke/test_bot_package.py` проверяют
импорт всех 14 модулей, thin shim, публичный API, единственность shared state,
отсутствие циклических импортов и структуру директории. PTB-зависимые тесты
корректно skip'аются, если `python-telegram-bot` не установлен.

### LLM-контекст для AI-анализа

Промпт для GLM формируется из нескольких секций:

```
┌─────────────────────────────────────────┐
│  Системный промпт (роль аналитика ГИБДД) │
└─────────────────┬───────────────────────┘
                  │
   ┌──────────────┼──────────────┐
   ▼              ▼              ▼
 Comparison   Clusters      Cross-tables
 (метрики     (разделены     (25 таблиц
  АППГ vs      на категории:  корреляций +
  текущий)     повторные/     статистика:
               новые/         severity rates,
               исчезнувшие)   Z-score, χ²)
                  │
                  ▼
         Q&A history (последние 10 пар)
```

**Разделение очагов по категориям для LLM** (метод `format_clusters_for_prompt`):
- **ПОВТОРНЫЕ** — текущие очаги, которые были и в АППГ. Показываем динамику (было X → стало Y)
- **НОВЫЕ** — текущие очаги, которых не было в АППГ. Для `new_with_neighbor` указываем ближайший АППГ-очаг
- **ИСЧЕЗНУВШИЕ** — очаги прошлого периода, которых больше нет

В Top-10 по тяжести для UI передаются только текущие очаги (исключая `is_lost` и `is_prev_matched`).

#### Sprint 5 — Streaming SSE

Резюме и Q&A стримятся через Server-Sent Events:
- POST `/api/dtp/tasks/{task_id}/llm/summary/stream` — токены резюме по мере поступления
- POST `/api/dtp/tasks/{task_id}/llm/ask/stream` — токены ответа на вопрос
- Backend: `stream_llm_summary()` / `ask_llm_question_stream()` в `services/llm_ops.py`
- Frontend: `EventSource`-подобный fetch-stream в `LLMAnalysisView.tsx`
- При `finish_reason=length` — WARNING в логах с подсказкой поднять `LLM_MAX_TOKENS`
- При 429 (rate limit) — до 3 ретраев с экспоненциальной задержкой (1с → 2с → 4с)

#### Sprint 6 — Персистентные LLM-сессии

После рестарта приложения резюме и история Q&A больше не теряются:

```
┌─────────────────────────────────────────────────────────┐
│  llm_sessions (PostgreSQL)                              │
│  ─────────────────────────────                          │
│  task_id           VARCHAR(32)  PRIMARY KEY             │
│  user_id           BIGINT                               │
│  summary_text      TEXT          ← финальный текст      │
│  summary_provider  VARCHAR(16)                          │
│  summary_generated_at TIMESTAMPTZ                       │
│  qa_history        JSONB         ← массив {question,    │
│                                       answer, provider, │
│                                       timestamp}        │
│                       trim до 10 последних              │
│  updated_at        TIMESTAMPTZ   ← auto-trigger         │
└─────────────────────────────────────────────────────────┘
```

- `save_llm_session(task_id, ...)` — upsert после стрима резюме (fire-and-forget)
- `append_qa_entry(task_id, question, answer, provider)` — atomic jsonb-обновление после каждого Q&A с trim до 10
- `load_llm_session(task_id)` — fast path при `get_task_async()`: восстанавливает `llm_summary_state` и `llm_qa_history`, если in-memory LRU их уже вытеснил
- Логи восстановления: `Sprint 6: restored LLM summary...` / `Sprint 6: restored Q&A history... (N entries)`
- UI: кнопки «⧉ Копировать» (финальный ответ + partial во время стрима + резюме) и «↻ Повторить» (новый стрим с тем же вопросом)
- `CopyButton` с fallback на `execCommand` для не-secure context (Telegram WebView на iOS)

---

## Быстрый старт

### 1. Клонирование и установка

```bash
git clone https://github.com/<your-username>/gibdd-stat-bot.git
cd gibdd-stat-bot

# Создайте виртуальное окружение
python -m venv venv

# Активируйте его
# Linux/Mac:
source venv/bin/activate
# Windows:
venv\Scripts\activate

# Установите зависимости
pip install -r requirements.txt
```

Либо на Windows:
```bash
install.bat
```

### 2. Настройка

```bash
cp .env.example .env
```

Заполните в `.env`:

| Переменная | Описание | Обязательно |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Токен бота от [@BotFather](https://t.me/BotFather) | Да |
| `BOTHOST_DOMAIN` | Домен bothost (для webhook) | Для production |
| `PORT` | Порт FastAPI (bothost передаёт через `$PORT`) | Для production |
| `CORS_ORIGINS` | Origins для CORS (URL Mini App + web.telegram.org) | Для production |
| `ALLOWED_USER_IDS` | ID пользователей через запятую (пустое = доступ всем) | Нет |
| `DATABASE_URL` | Connection string PostgreSQL (`postgresql://user:pass@host:port/db`). Если пусто — in-memory fallback | Для кэшей L2 |
| `DB_POOL_MIN` / `DB_POOL_MAX` | Размеры пула соединений (по умолчанию `1` / `5`) | Нет |
| `DB_CONNECT_TIMEOUT` | Таймаут подключения к БД в секундах (по умолчанию `10`) | Нет |
| `CARDS_CACHE_TTL_SECONDS` | TTL кэша карточек ДТП в PostgreSQL (по умолчанию `86400` = 24ч) | Нет |
| `CLUSTERS_CACHE_TTL_SECONDS` | TTL кэша кластеров в PostgreSQL (по умолчанию `86400` = 24ч) | Нет |
| `EXCEL_CACHE_TTL_SECONDS` | TTL кэша Excel-файлов в PostgreSQL (по умолчанию `86400` = 24ч) | Нет |
| `LLM_API_KEY` | API-ключ [ZhipuAI](https://open.bigmodel.cn) для AI-анализа | Нет |
| `LLM_MODEL` | Модель GLM (по умолчанию `glm-4.7-flash`) | Нет |
| `ENABLE_NEWS_SEARCH` | Поиск новостей для контекста LLM (`true`/`false`, по умолчанию `true`) | Нет |
| `LLM_PAID_API_KEY` | API-ключ платного провайдера (AItunnel, OpenRouter) для расширенного AI-анализа | Нет |
| `LLM_PAID_API_URL` | URL платного провайдера без `/chat/completions` (по умолчанию `https://api.aitunnel.ru/v1`) | Нет |
| `LLM_PAID_MODEL` | Модель платного LLM (по умолчанию `deepseek-v4-flash`) | Нет |
| `LLM_MAX_TOKENS` | Лимит длины ответа LLM в токенах (по умолчанию `16384`). При `finish_reason=length` в логах WARNING — поднимите значение | Нет |
| `ADMIN_TELEGRAM_IDS` | ID администраторов через запятую — для системных алертов (cache TTL, мониторинг) | Нет |
| `MAX_CONCURRENT_TASKS` | Лимит одновременных задач выгрузки (по умолчанию `5`, `asyncio.Semaphore`) | Нет |
| `RATE_LIMIT_PER_MINUTE` | Лимит запросов API на пользователя в минуту (slowapi, по умолчанию `60`) | Нет |
| `MAX_INMEMORY_TASKS` | Размер LRU-кэша задач в памяти (по умолчанию `50`) | Нет |
| `LOG_FORMAT` | Формат логов: `text` (по умолчанию) или `json` (структурированные логи для ELK/Loki) | Нет |
| `REGIONS_API_ENABLED` | Запрос справочника регионов через API ГИБДД (`1`/`0`, по умолчанию `0` — сразу файловый кэш) | Нет |
| `TARGET_API_TIMEOUT` | Таймаут запросов к API ГИБДД в секундах (по умолчанию `120`) | Нет |
| `LOG_LEVEL` | Уровень логирования (по умолчанию `INFO`) | Нет |
| `CAMERA_DATA_DIR` | Путь к директории с кэшем камер и регионов (по умолчанию `data`) | Нет |

### 3. Запуск

**Production (bothost):**
```bash
python main.py
```
FastAPI поднимается на `$PORT`, Telegram-бот работает в webhook-режиме на `/bot/webhook`, Mini App доступен на `/app/`.

**Локальная разработка:**
```bash
# Терминал 1 — backend + bot (hot reload)
PORT=8080 BOTHOST_DOMAIN=localhost TELEGRAM_BOT_TOKEN=<token> python main.py

# Терминал 2 — frontend (Vite dev server с hot reload)
cd miniapp/frontend
npm run dev
# → http://localhost:5173, проксирует /api → localhost:8080
```

**Только Telegram-бот (polling, без Mini App):**
```bash
python bot.py
```

Либо на Windows: `run_bot.bat` или `start_bot.bat`.

### 4. Установка webhook (после первого деплоя)

```bash
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?url=https://<BOTHOST_DOMAIN>/bot/webhook"
```

### 5. Настройка Mini App в BotFather

1. Откройте @BotFather → `/newapp` (или `/setmenubutton`)
2. Укажите бота, название «ДТП Статистика»
3. URL: `https://<BOTHOST_DOMAIN>/app/`
4. Теперь у бота появится кнопка-меню, открывающая Mini App

---

## Команды бота

| Команда | Описание |
|---|---|
| `/start` | Приветственное сообщение с описанием |
| `/help` | Справка по форматам запросов и командам |
| `/dtp` | Начать выгрузку через inline-кнопки (регион → период) |
| `/regions` | Показать список всех регионов с кодами |
| `/miniapp` | Прислать inline-кнопку для открытия Mini App |

### Форматы запросов

| Формат | Пример | Описание |
|---|---|---|
| Естественный язык | `Вологодская область за 2025 год` | Автопарсинг региона и периода |
| Естественный язык | `Алтайский край за I квартал 2025` | Кварталы, полугодия, месяцы |
| Строгий формат | `2.2024 1119` | `месяц.год код_региона` |
| С coordinate-прикреплением | Отправка геолокации | Статистика ДТП в радиусе от точки |
| С Excel-файлом | Прикрепить `.xls` / `.xlsx` | Загрузка реестра камер региона |

---

## Структура проекта

```
gibdd-bot/
├── main.py                 ← Единая точка входа: FastAPI + bot (webhook)
├── bot.py                  ← Thin shim (13 строк): from bot.app import main; main()
├── bot/                    ← Пакет Telegram-бота (Phase 3-2 рефакторинг)
│   ├── __init__.py         ←   документация пакета
│   ├── _state.py           ←   shared state (imports, logger, globals, constants)
│   ├── infra.py            ←   утилиты TG API (_tg_retry, _safe_edit, _send_long_message)
│   ├── access.py           ←   доступ + загрузка регионов (_fetch_cards_for_period)
│   ├── keyboards.py        ←   inline-клавиатуры (region, period)
│   ├── analysis.py         ←   конвейер аналитики и очагов (~1300 строк)
│   ├── output.py           ←   HTML-карты (_generate_and_send_dtp_map)
│   ├── point_stats.py      ←   статистика по геоточке
│   ├── qa.py               ←   Q&A с LLM (_handle_analytics_question)
│   ├── app.py              ←   точка входа (main, _build_app, error_handler)
│   └── handlers/           ←   обработчики PTB
│       ├── commands.py     ←     /start /help /dtp /regions /miniapp /precache
│       ├── callbacks.py    ←     on_callback_query (диспетчер кнопок)
│       └── messages.py     ←     handle_message + _handle_document
├── api_client.py           ← HTTP-клиент для Open Data API stat.gibdd.ru
│                              (connection pooling, ретраи с backoff, throttle)
├── web_fallback.py         ← Fallback-выгрузка через сайт (POST→GET→XML parse)
├── gibdd_parser.py         ← Парсинг карточек ДТП → структура для Excel
├── excel_generator.py      ← Генерация стилизованных .xlsx файлов
├── user_request_parser.py  ← NLP-парсер: естественный язык → регион + период
├── config.py               ← Конфигурация из .env / переменных окружения
├── analytics.py            ← Аналитика: метрики, сравнение периодов, 25 кросс-таблиц,
│                              статистические метрики (severity, Z-score, χ²)
├── report_generator.py     ← Генератор HTML-отчётов: карты (Leaflet),
│                              аналитика (ECharts), очаги, точка с радиусом.
│                              Кластеризация, spiderfy, линейка, попапы.
│                              Библиотеки встраиваются инлайн (кэш в data/report_libs/)
├── concentration_points.py ← Расчёт очагов концентрации ДТП
│                              (НП + вне НП, OSM Overpass, Shapely, методология v2)
├── camera_loader.py        ← Парсинг и поиск камер фотовидеофиксации
├── camera_matcher.py       ← Сопоставление камер с очагами ДТП
├── camera_cache.py         ← Файловый кэш камер по регионам
├── point_statistics.py     ← Локальная статистика по географической точке
├── llm_analyzer.py         ← Интеграция с ZhipuAI GLM (резюме, Q&A, промпты)
├── news_fetcher.py         ← Поиск новостей (Google News RSS + DuckDuckGo)
├── regions_cache.py        ← Файловый кэш справочника регионов
├── regions_builtin.py      ← Встроенный справочник 82 регионов (fallback)
├── regions_builtin.json    ← Тот же справочник в JSON
├── parse_regions.py        ← Утилита парсинга регионов с сайта stat.gibdd.ru
├── np_bdd/                 ← Национальный проект «Безопасные качественные дороги»
│   ├── scripts/            ← forecast.py, embedded_data.py, gibdd_adapter.py, ...
│   ├── datasets/           ← history, plans, vehicles, seasonal (по регионам)
│   └── schemas/            ← JSON Schema для валидации
├── miniapp/                ← Telegram Mini App
│   ├── backend/            ← FastAPI sub-app (монтируется на /api)
│   │   ├── main.py         ← Точка входа FastAPI (Prometheus, slowapi, middleware)
│   │   ├── config.py       ← Pydantic-settings
│   │   ├── telegram_auth.py← Проверка подписи initData (HMAC-SHA256)
│   │   ├── middleware/     ← request_id, CORS, логирование
│   │   ├── routers/        ← regions, parse, dtp, point, cameras, analyze, np_bdd, llm
│   │   │   └── analyze.py  ←   агрегирующий router с prefix="/dtp",
│   │   │                       включает в себя clusters, point, llm
│   │   ├── services/
│   │   │   ├── gibdd_service.py  ← Мост к модулям gibdd-bot (Task, pipeline, recovery)
│   │   │   ├── llm_ops.py        ← LLM-операции: summary/Q&A, SSE-стрим, кэш, retry 429
│   │   │   ├── task_registry.py  ← In-memory LRU _tasks + восстановление LLM-сессий
│   │   │   ├── pipeline.py       ← Анализатор конвейера (cards→analytics→clusters)
│   │   │   ├── analytics_ops.py  ← Аналитические операции (comparison, cross-tables)
│   │   │   ├── clusters_ops.py   ← Расчёт очагов (concentration_points v2)
│   │   │   ├── point_stats_ops.py← Статистика по геоточке
│   │   │   ├── query_ops.py      ← Парсинг запросов пользователя
│   │   │   ├── cleanup.py        ← Background cleanup (cards/clusters/excel cache TTL)
│   │   │   └── np_bdd_service.py ← Сервис НП БДД
│   │   └── db/             ← Слой PostgreSQL (Этап 2-6)
│   │       ├── connection.py     ← Async-пул (psycopg), init_pool/close_pool/health_check
│   │       ├── schema.sql        ← CREATE TABLE IF NOT EXISTS: tasks, access_log,
│   │       │                       dtp_cards_cache, clusters_cache, excel_cache,
│   │       │                       llm_cache, llm_sessions
│   │       ├── repository.py     ← TaskRepository: save/load/list/delete, log_access,
│   │       │                       save_llm_session/append_qa_entry/load_llm_session
│   │       ├── cards_cache.py    ← L2-кэш карточек ДТП (Этап 3)
│   │       ├── clusters_cache.py ← L2-кэш кластеров + raw_clusters (Этап 4)
│   │       ├── excel_cache.py    ← L2-кэш Excel-файлов (BYTEA, Этап 5)
│   │       └── llm_cache.py      ← L2-кэш LLM-резюме (Sprint 2, по cache_key SHA-256)
│   └── frontend/           ← Vite + React + TypeScript + Tailwind
│       ├── src/
│       │   ├── App.tsx     ← Главный layout с табами
│       │   ├── lib/        ← telegram.ts, api.ts, utils.ts
│       │   ├── hooks/      ← useTaskPolling.ts, useAnalysisPolling.ts
│       │   └── components/ ← ClustersView, LLMAnalysisView, AnalyticsView,
│       │                     NpBddView, PointStatsView, StructuredForm, ...
│       └── dist/           ← Собранная ститика (после npm run build)
├── tests/                  ← Полный набор тестов (464 теста, 77% coverage)
│   ├── unit/               ← Wave 1-2: чистые функции + LLM/service mocks
│   ├── integration/        ← Wave 3: end-to-end pipeline + stubs
│   ├── smoke/              ← Wave 4 + Phase 3-2: импорт, app init, структура пакета
│   └── golden/             ← Wave 4: эталонные выходы (11 файлов)
├── data/                   ← Рабочая директория (кэш камер, регионов, OSM)
├── Dockerfile              ← Multi-stage: build frontend + Python runtime
├── requirements.txt        ← Зависимости Python
├── .env.example            ← Шаблон конфигурации
├── install.bat             ← Установка зависимостей (Windows)
├── start_bot.bat           ← Запуск бота (Windows)
└── run_bot.bat             ← Запуск бота с автоустановкой (Windows)
```

---

## API Endpoints

### Mini App (FastAPI на `/api`)

| Метод | Путь | Описание |
|---|---|---|
| GET | `/health` | Health-check (статус бота, версия, БД) |
| GET | `/health/db` | Детальная диагностика PostgreSQL (пул, latency, schema) |
| GET | `/health/db/cards` | Статистика cards_cache: записи, hits/misses, размер |
| GET | `/health/db/clusters` | Статистика clusters_cache: записи, hits/misses, размер |
| GET | `/health/db/excel` | Статистика excel_cache: записи, hits/misses, размер |
| GET | `/api/regions` | Список регионов с кодами |
| GET | `/api/regions/search?q=` | Поиск регионов (autocomplete) |
| POST | `/api/parse` | Парсинг естественного языка → `{region_code, period}` |
| POST | `/api/dtp/tasks` | Создать задачу выгрузки, вернуть `task_id` |
| GET | `/api/dtp/tasks` | Список задач пользователя |
| GET | `/api/dtp/tasks/{id}` | Статус задачи (для polling) |
| GET | `/api/dtp/tasks/{id}/files` | Список готовых файлов |
| GET | `/api/dtp/tasks/{id}/map` | HTML-карта ДТП (для iframe) |
| GET | `/api/dtp/tasks/{id}/download/{file_type}` | Скачать Excel/HTML |
| POST | `/api/dtp/tasks/{id}/clusters` | Запустить расчёт очагов |
| GET | `/api/dtp/tasks/{id}/clusters?wait=N` | Статус очагов (long polling) |
| GET | `/api/dtp/tasks/{id}/clusters/map` | HTML-карта очагов |
| GET | `/api/dtp/tasks/{id}/clusters/excel` | Скачать Excel по очагам (4 листа) |
| POST | `/api/dtp/tasks/{id}/point` | Статистика ДТП в радиусе от точки |
| GET | `/api/dtp/tasks/{id}/point/map` | HTML-карта точки |
| GET | `/api/dtp/tasks/{id}/point/excel` | Excel по точке (2 листа) |
| GET | `/api/dtp/tasks/{id}/llm/providers` | Доступные LLM-провайдеры (free/paid) |
| POST | `/api/dtp/tasks/{id}/llm/summary` | Запустить генерацию резюме (async) |
| GET | `/api/dtp/tasks/{id}/llm/summary?wait=N` | Статус резюме (long polling, legacy) |
| POST | `/api/dtp/tasks/{id}/llm/summary/stream` | **SSE-стрим** резюме (токены по мере поступления) |
| POST | `/api/dtp/tasks/{id}/llm/ask` | Задать вопрос нейросети (async) |
| POST | `/api/dtp/tasks/{id}/llm/ask/stream` | **SSE-стрим** ответа на вопрос |
| GET | `/api/dtp/tasks/{id}/llm/qa-history` | История Q&A (in-memory + восстановление из БД) |
| GET | `/api/cameras` | Список регионов с камерами |
| POST | `/api/cameras/{reg_code}` | Загрузить реестр камер региона |
| DELETE | `/api/cameras/{reg_code}` | Удалить реестр камер |
| GET | `/api/np-bdd/regions` | Регионы НП БДД |
| GET | `/api/np-bdd/data` | Данные НП БДД (история, прогноз, KPI) |
| GET/PATCH | `/api/np-bdd/settings` | Настройки (plan_line_mode, forecast_method) |
| POST | `/api/np-bdd/freeze` | Заморозить год |
| POST | `/api/np-bdd/unfreeze` | Разморозить год |
| GET | `/api/np-bdd/frozen` | Список замороженных лет |

### Telegram-бот

| Эндпоинт | Метод | Назначение |
|---|---|---|
| `/bot/webhook` | POST | Webhook Telegram (приём updates) |

Документация Swagger UI: `https://<BOTHOST_DOMAIN>/docs` или `http://localhost:8080/docs`

---

## Деплой

Подробная инструкция по деплою на bothost.ru — в [`README_DEPLOY_BOTHOST.md`](README_DEPLOY_BOTHOST.md).

### Краткая сводка

| Платформа | Способ | Главный файл |
|---|---|---|
| bothost.ru (рекомендуется) | Dockerfile | `main.py` |
| Timeweb Cloud / Selectel | docker-compose | `main.py` |
| Локальная разработка | uvicorn + Vite | `main.py` + `npm run dev` |
| Только Telegram-бот | polling | `bot.py` |

### Проверка работоспособности

| Endpoint | Что проверяет | Ожидаемый ответ |
|----------|---------------|-----------------|
| `https://<DOMAIN>/health` | Сервер жив, БД готова | `{"status":"ok","database":{"ready":true}}` |
| `https://<DOMAIN>/health/db` | Диагностика PostgreSQL | `pool`, `latency_ms`, `tables` |
| `https://<DOMAIN>/health/db/excel` | Статистика excel_cache | `entries`, `hits`, `misses` |
| `https://<DOMAIN>/api/regions` | Авторизация | 401 (нужен initData) |
| `https://<DOMAIN>/app/` | Frontend | HTML страница |
| `https://<DOMAIN>/docs` | Swagger UI | Документация API |
| Telegram `/start` | Бот отвечает | Сообщение приветствия |
| Telegram `/miniapp` | Кнопка Mini App | Inline-кнопка «Открыть» |

---

## Зависимости

| Пакет | Версия | Назначение |
|---|---|---|
| `python-telegram-bot` | 21.7 | Telegram Bot API |
| `fastapi` | 0.115+ | Web-фреймворк для Mini App |
| `uvicorn` | 0.30+ | ASGI-сервер |
| `httpx` | 0.27.0 | Асинхронный HTTP-клиент |
| `openpyxl` | 3.1.5 | Генерация Excel-файлов |
| `xlrd` | 2.0.1 | Чтение .xls файлов (реестр камер) |
| `python-dotenv` | 1.0.1 | Загрузка .env |
| `shapely` | 2.0.6 | Геометрические операции (полигоны НП) |
| `pydantic-settings` | 2.x | Конфигурация Mini App |
| `psycopg[binary,pool]` | 3.2+ | Async-драйвер PostgreSQL + пул соединений (Этап 2-5) |
| `pytz` | 2024.x | Таймзоны для НП БДД |

Опционально (для AI-анализа):
| Пакет | Назначение |
|---|---|
| `zhipuai` | SDK для ZhipuAI GLM-4.7-Flash |

Frontend:
| Пакет | Назначение |
|---|---|
| `react` 18 + `react-dom` | UI-фреймворк |
| `typescript` 5 | Типизация |
| `vite` 5 | Сборщик + dev-сервер |
| `tailwindcss` 3 | Утилитарный CSS |
| `@tanstack/react-query` 5 | Серверный стейт (polling, кэш) |

---

## Разработка

### Обновление встроенного справочника регионов

```bash
python parse_regions.py
```

Скрипт загружает страницу stat.gibdd.ru, извлекает `var regions` и `var regId2MiasId` из HTML, конвертирует в API-формат (`"11" + miasId`) и перегенерирует `regions_builtin.py` и `regions_builtin.json`.

### Smoke-тесты

| Скрипт | Что тестирует |
|---|---|
| `scripts/smoke_test_new_match_clusters.py` | Методология v2 сопоставления очагов (15 тестов) |
| `scripts/smoke_test_bdd_vehicle.py` | Аналитика по профилю ТС (БДД) |
| `scripts/smoke_test_stage1_cross_tables.py` | Кросс-таблицы (Этап 1) |
| `scripts/smoke_test_stage2_stats.py` | Статистические метрики (Этап 2) |
| `scripts/smoke_test_current_month.py` | Прогноз НП БДД по текущему месяцу |

### Структура директории `data/`

Создаётся автоматически при первом запуске:

```
data/
├── regions_cache.json          # Кэш справочника регионов
├── report_libs/                # Кэш CDN-библиотек для HTML-карт
│   ├── leaflet.css             # Leaflet (загружается с unpkg.com при первом запуске)
│   ├── leaflet.js
│   ├── MarkerCluster.css       # MarkerCluster + Default + JS
│   └── echarts.min.js          # ECharts (для аналитических отчётов)
├── osm_cache/                  # Кэш границ НП из OSM (по регионам)
│   ├── region_1119.json
│   ├── settlements_*.json
│   └── ...
├── cameras_1119.xls            # Кэш реестра камер региона 1119
├── cameras_1101.xls            # Кэш реестра камер региона 1101
└── ...
```

### Сборка frontend для деплоя

```bash
cd miniapp/frontend
npm install
npm run build
# Результат: miniapp/frontend/dist/
```

---

## Требования 152-ФЗ

⚠️ Mini App обрабатывает ПДн (данные участников ДТП). Для соответствия 152-ФЗ:

1. **Хостинг в РФ**: bothost.ru / Timeweb Cloud / Selectel / Beget (все в реестре Минцифры)
2. **TLS обязателен** (Let's Encrypt — бесплатно, на bothost включён автоматически)
3. **Политика обработки ПДн** + **Согласие** при первом открытии Mini App
4. **Уведомление Роскомнадзора** об обработке ПДн (через Госуслуги)
5. **Журнал аудита доступа** к ПДн (логировать все запросы `user_id → region_code, period`)
6. **Шифрование БД при rest** (LUKS для диска VPS, на bothost — на стороне хостера)

---

## Устранение неполадок

### Бот не отвечает после деплоя

1. Проверьте `/health` — `telegram_bot` должен быть `"running"`
2. Если `"stopped"` — проверьте логи на `InvalidToken` или проблемы с webhook
3. Проверьте webhook:
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
   ```
4. Если `last_error_message` указывает на 404/503 — проверьте `BOTHOST_DOMAIN` и что `/bot/webhook` доступен

### Mini App открывается, но API возвращает 401

`X-Tg-Init-Data` не передаётся. Возможные причины:
1. Frontend собран со старым `VITE_API_BASE` — пересоберите
2. Telegram SDK не загрузился — проверьте `<script src="https://telegram.org/js/telegram-web-app.js">` в `index.html`
3. Mini App открыт в обычном браузере (не через Telegram) — `initData` пустой

### CORS ошибки в консоли браузера

Добавьте ваш домен в `CORS_ORIGINS`:
```
CORS_ORIGINS=https://bot1234.bothost.tech,https://web.telegram.org,https://a.telegram.org
```

### LLM: ошибка `TypeError: ... unexpected keyword argument 'max_retries'`

Серверная версия `llm_analyzer.py` устарела. Обновите файл на сервере из репозитория — `get_ai_summary` и `get_ai_answer` теперь принимают `max_retries: int = 3`.

### LLM: 429 (rate limit) после retry

Кнопка «Повторить» теперь сбрасывает кэш react-query (`queryClient.removeQueries`) и показывает прогресс-бар мгновенно. После успешного retry результат возвращается в MiniApp через long polling автоматически.

### Frontend не обновляется после деплоя

Vite добавляет хэш к именам файлов (`index-AbCd1234.js`). Если старый `index.html` закеширован — он будет ссылаться на несуществующий файл. Решение: убедитесь, что bothost не кэширует `/app/` агрессивно, или добавьте version-busting.

### PostgreSQL: кэш не срабатывает (всегда MISS)

1. Проверьте `DATABASE_URL` в `.env` — без него приложение работает в in-memory режиме
2. Откройте `/health/db` — `ready: true` и `pool` не `null`
3. Проверьте логи на `cards_cache: TTL=86400s` — должно появляться при первом запросе
4. Проверьте, что в логах есть `PUT` и `HIT` записи, а не только `MISS`
5. При нехватке места в БД старые записи могут не очищаться — проверьте `/health/db/excel` на размер `entries`

### PostgreSQL: ConnectionError / pool timeout

1. Проверьте `DB_POOL_MAX` (по умолчанию 5) — при высоком RPS увеличьте
2. Проверьте `DB_CONNECT_TIMEOUT` (по умолчанию 10с)
3. На bothost.ru PostgreSQL иногда уходит на обслуживание — приложение автоматически переключится на in-memory fallback, но кэш будет менее эффективным

---

## Журнал изменений

### Sprint 6 — Персистентные LLM-сессии + UX-правки (`2026-08-10`)

**6.0 — LLM-сессии в PostgreSQL + Q&A UX-кнопки** (`sprint6-llm-sessions-and-qa-buttons.zip`)
- Таблица `llm_sessions` (task_id PK, user_id, summary_text, qa_history JSONB, updated_at)
- `save_llm_session(task_id, summary, provider)` — upsert после стрима резюме (fire-and-forget)
- `append_qa_entry(task_id, question, answer, provider)` — atomic jsonb-обновление после Q&A с trim до 10
- `load_llm_session(task_id)` — fast path в `get_task_async()`: восстанавливает `llm_summary_state` и `llm_qa_history` после рестарта или LRU eviction
- `_try_restore_llm_session(task)` в `task_registry.py` — логирует `Sprint 6: restored LLM summary...` / `Sprint 6: restored Q&A history... (N entries)`
- UI: кнопки «⧉ Копировать» (финальный ответ + partial во время стрима + резюме) и «↻ Повторить» (новый стрим с тем же вопросом)
- `CopyButton` с fallback на `document.execCommand('copy')` для не-secure context (Telegram WebView на iOS)
- SUGGESTED_QUESTIONS расширены с 6 до 12 (добавлены БДД-экспертиза + профиль ТС)
- Файлы: `db/repository.py` (+250 строк: 3 новые функции), `db/schema.sql` (+50 строк: таблица llm_sessions), `services/task_registry.py` (+80 строк), `routers/llm.py` (+40 строк), `LLMAnalysisView.tsx` (+200 строк)

**6.hotfix1 — SQL: `operator does not exist: jsonb || json`** (`sprint6-llm-sessions-and-qa-buttons-hotfix1.zip`)
- В `append_qa_entry` использовался `Json()` (без `b`) — PostgreSQL неявно кастит к `json`, а не `jsonb`, и оператор `||` для `(jsonb, json)` не существует
- **Fix:** `Json() → Jsonb()` + `::jsonb` каст на существующий `qa_history` перед `||`
- Дополнительно: `save_llm_session` тоже defensively использует `Jsonb()` для `qa_history`
- Интеграционный тест `scripts/test_append_qa_fix.py` против боевой БД — 4 шага проходят без ошибок
- Логи после деплоя подтверждают: `Sprint 6: appended Q&A to session task=... (answer XXXX chars)` без WARNING

**6.hotfix2 — Финальная стабилизация** (`sprint6-llm-sessions-and-qa-buttons-hotfix2.zip`)
- Проверена вся цепочка: генерация резюме → 3 Q&A → рестарт → восстановление → новый Q&A с контекстом
- Подтверждено пользователем: «При открытии предыдущей задачи и отправки вопроса, в части контекста разговора, LLM пересказывает весь контекст, с учетом всех сообщений из предыдущей сессии»
- История Q&A корректно передаётся в LLM как `history_for_llm` (user+assistant на каждый Q&A, последние 10 пар = 20 сообщений)
- Системный промпт объясняет модели, что история — это контекст, а новый вопрос — отдельное обращение

---

### Sprint 5 — Streaming SSE + retry при 429 (`2026-08-10`)

**5.0 — Финализация streaming** (`sprint5-finalize-streaming.zip`)
- Polling fallback (`?wait=25`) для summary/Q&A полностью удалён
- Резюме — единственный источник правды: `streamingSummary` + `finalSummary` в React state
- При монтировании: one-shot GET `/llm/summary` (без wait) для cache-hit
- После `onDone` стрима: `finalSummary = streamingSummary`
- Q&A: `onDone` использует `streamingQA.answer`, не дёргает `qa-history`
- Markdown-рендер: bold/italic/headings/lists/code через `MarkdownText.tsx`

**5.1 — Fix: пустые ответы LLM** (`sprint5-1-empty-response-fix.zip`)
- При `finish_reason=length` и пустом `content` LLM возвращал пустой ответ без ошибки
- Добавлен guard: если `content` пустой после стрима — fall back на non-streaming запрос
- Логирование `prompt_tokens` / `completion_tokens` / `total_tokens` / `finish_reason` для диагностики

**5.2 — Retry при 429 (rate limit)** (`sprint5-429-retry-fix.zip`)
- При HTTP 429 от ZhipuAI — до 3 ретраев с экспоненциальной задержкой (1с → 2с → 4с)
- Заголовок `Retry-After` уважается, если присутствует
- На 3-й неудаче — пользовательский fallback-промпт без расширенного контекста (только comparison)
- Логи: `LLM 429 retry 1/3 after 1.0s` / `LLM 429 retry 2/3 after 2.0s` / `LLM 429 exhausted retries`

---

### Sprint 4 — Streaming LLM через SSE (`2026-08-08`)

**4.0 — Streaming SSE для резюме и Q&A** (`sprint4-streaming-llm-sse.zip`)
- `stream_llm_summary()` / `ask_llm_question_stream()` в `services/llm_ops.py` — генераторы SSE-событий
- POST `/api/dtp/tasks/{task_id}/llm/summary/stream` и `/api/dtp/tasks/{task_id}/llm/ask/stream`
- SSE-формат: `data: {"type": "token", "content": "..."}\n\n` + `data: {"type": "done", "answer": "..."}\n\n`
- Frontend: `fetch` + `ReadableStream` + `TextDecoder` для парсинга SSE (вместо `EventSource`, т.к. нужен POST)
- Прогресс-бар и elapsed-time тикер во время стрима

**4.1 — Fix: SSE separator + proxy buffering** (`sprint4-fix-sse-separator.zip` + `sprint4-streaming-fix-proxy-buffering.zip`)
- Nginx на bothost буферизовал SSE-ответы — стрим не шёл до полного завершения LLM
- Fix: заголовки `X-Accel-Buffering: no`, `Cache-Control: no-cache`, `Connection: keep-alive`
- Fix: `\n\n` разделитель между SSE-событиями (раньше был `\n` — браузер не парсил)
- `nginx.conf` обновлён: `proxy_buffering off` для `/api/dtp/tasks/*/llm/*/stream`
- Включён `gzip off` для SSE-эндпоинтов (иначе токены склеивались)

---

### Sprint 1-3 — Mini App backend рефакторинг (`2026-08-07`)

**3.0 — Роутеры разделены** (`sprint3-routers-split.zip`)
- `routers/analyze.py` — агрегирующий router с `prefix="/dtp"`, включает в себя `clusters`, `point`, `llm`
- `routers/llm.py` вынесен отдельно (раньше был внутри `analyze.py`)
- `routers/_common.py` — общие зависимости (`_require_done_task`, `_require_user_task`)

**3.1-3.2 — Recovery cards/clusters после рестарта** (`sprint3.1-cards-recovery-fix.zip` + `sprint3.2-clusters-recovery-fix.zip`)
- После рестарта `task.cards` / `task.raw_clusters` (heavy fields, не в БД) терялись
- `_ensure_cards_loaded(task)` — восстановление из `cards_cache` (PostgreSQL, TTL 24ч)
- `_ensure_raw_clusters_loaded(task)` — восстановление из `clusters_cache.raw_clusters`
- При cache miss: понятное сообщение «Создайте новую выгрузку для этого региона и периода»

**2.0 — LLM semaphore + cache** (`sprint2-llm-semaphore-cache.zip`)
- `asyncio.Semaphore(MAX_CONCURRENT_LLM=3)` — не больше 3 одновременных LLM-запросов
- `llm_cache` таблица: `cache_key = SHA-256(reg_code | dat_hash | provider | prompt_hash | llm_version)`
- Cache hit: <100 мс вместо ~53 сек
- При 3+ одновременных запросах на free-тарифе — 429 Too Many Requests, теперь их не будет

**1.0 — Разделение gibdd_service.py** (`sprint1-gibdd-service-split.zip`)
- Монолитный `gibdd_service.py` (~1800 строк) разбит на 8 модулей в `services/`:
  `pipeline.py`, `analytics_ops.py`, `clusters_ops.py`, `point_stats_ops.py`,
  `query_ops.py`, `cleanup.py`, `models.py`, `_imports.py`
- `gibdd_service.py` остаётся тонким фасадом для обратной совместимости

---

### Phase 3 (завершена)

**3.2 — Рефакторинг bot.py → модульный пакет bot/** (`2026-08-06`)
- Монолитный `bot.py` (4138 строк, 180 KB) разбит на 14-модульный пакет `bot/`
- Принцип: 100% pure refactoring — никакая логика не изменена, только перемещена
- `bot.py` сохранён как thin shim (13 строк): `from bot.app import main; main()`
- Структура: `_state.py` (shared state) + `infra.py` + `access.py` + `keyboards.py` +
  `analysis.py` (1335 строк, самый большой) + `output.py` + `point_stats.py` +
  `qa.py` + `app.py` + `handlers/{commands,callbacks,messages}.py`
- Все глобальные переменные в `bot/_state.py`, импортируются через `from bot._state import *`
- Граф зависимостей без циклов: `app → handlers/* → analysis, point_stats, qa, output → ...
  → keyboards, access, infra → _state`
- Добавлено 19 smoke-тестов в `tests/smoke/test_bot_package.py`: импорт всех 14 модулей,
  thin shim работает, публичный API доступен, shared state единственный, нет циклов,
  структура директории соответствует плану. PTB-зависимые тесты skip'аются если
  `python-telegram-bot` не установлен.
- Скрипт-экстрактор `scripts/extract_bot.py` сохранён для воспроизводимости
- Тестов всего: 464 (438 Phase 3-1 + 19 Phase 3-2 + 7 skip), покрытие 77.04%
- Проверено на Linux (Python 3.12.13) и Windows (Python 3.11.9) — 0 failed
- Файлы: `bot.py` (4138 → 13 строк), новый пакет `bot/` (14 файлов, ~4400 строк),
  `tests/smoke/test_bot_package.py` (268 строк), `scripts/extract_bot.py` (470 строк)

**3.1 — Оптимизация analytics-фазы** (`2026-08-06`)
- Профилирование `analytics.py` через `scripts/profile_analytics.py` на синтетике 500/2000/5000 ДТП
- Главная находка: CPU-расчёт быстрый (~100 ms на 2629 ДТП), основное время «analytics 36%» — это сеть + clusters
- `calculate_cross_tables` пересчитывался при каждом LLM-запросе и Q&A — теперь in-memory кэш в `Task` (8 полей с инвалидацией по `id(cards)`)
- `ensure_comparison`: `asyncio.gather(calculate_metrics(current), ensure_prev_cards())` — CPU работает пока идёт сеть
- Timing-логирование: каждая операция пишет ms (`calculate_metrics — 2629 ДТП, 9.1 ms` и т.д.)
- Cache hit при повторных Q&A: ~0 ms вместо ~38 ms
- Файл: `miniapp/backend/services/gibdd_service.py` (+98 строк)

**3.0 — LLM max_tokens + транкация-детект** (`2026-08-06`)
- Лимит `max_tokens` поднят с `8192` → `16384`, вынесен в env `LLM_MAX_TOKENS`
- Логирование `prompt_tokens`, `completion_tokens`, `total_tokens`, `finish_reason`
- WARNING при `finish_reason=length` с подсказкой «поднимите LLM_MAX_TOKENS в .env»
- Применяется одинаково к бесплатному (GLM-4.7-flash) и платному (deepseek-v4-flash) провайдерам
- Файлы: `config.py` (+17 строк), `llm_analyzer.py` (+18/-1 строк)

### Phase 2 — observability + устойчивость

**2.8 — Fix: восстановление cards после рестарта** (`2026-08-06`)
- После рестарта контейнера `task.cards` (heavy field, не персистится в БД) терялся
- Добавлен `_ensure_cards_loaded(task)` — восстанавливает из `cards_cache` (PostgreSQL, TTL 24ч)
- Интегрирован в 4 функции: `ensure_comparison`, `compute_point_stats`, `start_clusters_calculation`, `generate_point_stats_map_html`
- При cache miss (старая задача): понятное сообщение «Создайте новую выгрузку для этого региона и периода»
- Файл: `miniapp/backend/services/gibdd_service.py` (+105 строк)

**2.7 — Fix: observability метрики** (`2026-08-05`)
- Background `_metrics_updater_loop()` каждые 30 сек обновляет Prometheus gauges
- До этого `gibdd_process_rss_bytes=0` и `gibdd_db_pool_size` были пустые — обновлялись только при запросе к `/health/detailed`
- Корректная формула: `active = pool_size - pool_available`, `idle = pool_available`
- Файл: `miniapp/backend/main.py` (+60 строк)

**2.1–2.6 — Observability + тюнинг**
- `2.1` Prometheus-метрики: `gibdd_tasks_total`, `gibdd_task_duration_seconds`, `gibdd_db_pool_size`, `gibdd_process_rss_bytes`, `gibdd_cards_cache_hits_total`
- `2.2` `/health/detailed` эндпоинт с pool stats, cache stats, version
- `2.3` slowapi rate limiting (60 req/min на пользователя)
- `2.4` `MAX_CONCURRENT_TASKS=5` через `asyncio.Semaphore`
- `2.5` LRU `_tasks` (maxlen=50) с eviction тяжёлых полей в БД
- `2.6` request_id middleware — каждый лог содержит `request_id` для трассировки
- `2.7` JSON-логи опционально через `LOG_FORMAT=json` (для ELK/Loki)

### Phase 1 — Mini App + PostgreSQL кэши

**1.5 — Excel-кэш в PostgreSQL** (`Stage 5`)
- Готовые Excel-файлы (Файл 1 ДТП + Файл 2 участники) кэшируются в БД (TTL 24ч)
- Экономия ~5-8 сек на повторных запросах того же региона+периода

**1.4 — Clusters-кэш в PostgreSQL** (`Stage 4`)
- Результат расчёта очагов (payload + raw_clusters + raw_preclusters) кэшируется в БД (TTL 6ч)
- Второй пользователь с тем же регионом+периодом — мгновенный cache hit вместо 15-30 сек расчёта

**1.3 — TTL-мониторинг кэшей** (`Stage 3`)
- `cleanup_old_cards()`, `cleanup_old_clusters()`, `cleanup_old_excel()` — фоновые задачи
- Метрика `gibdd_cards_cache_entries` для наблюдения за размером

**1.2 — PostgreSQL миграция** (`Stage 1-2`)
- `psycopg_pool.AsyncConnectionPool` (min=2, max=30)
- Таблицы: `dtp_cards_cache`, `dtp_clusters_cache`, `dtp_excel_cache`, `tasks`, `audit_log`
- Fallback: при недоступности БД — in-memory режим (функциональность сохраняется)

**1.1 — Mini App интеграция** (`v0.2-v0.6`)
- FastAPI backend + React/TypeScript frontend, единый процесс с Telegram-ботом
- 6 вкладок: Запрос / Аналитика / Очаги / Точка / ИИ-анализ / НП БДД
- Long polling (25 сек) для статуса длительных операций
- Telegram theme (light/dark), haptic feedback, fullscreen mode

### Phase 0 — Базовый функционал

- Telegram-бот с командами `/start`, `/dtp`, `/regions`, `/help`
- Выгрузка карточек ДТП и участников через GIBDD API + web fallback
- Excel-генерация (2 файла: ДТП + участники)
- HTML-карты (Leaflet, инлайн-библиотеки, работа офлайн)
- AI-анализ через ZhipuAI GLM-4.7-Flash
- 4-уровневый fallback справочника регионов
- Web-fallback при ошибке API (5xx, ConnectionError)

---

## Лицензия

MIT
