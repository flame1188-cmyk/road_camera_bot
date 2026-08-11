# Деплой на bothost.ru — пошаговая инструкция

## Что изменилось в проекте

К существующему коду `gibdd-bot` добавлено:

```
gibdd-bot/
├── main.py                    # ← НОВАЯ единая точка входа (FastAPI + webhook)
├── Dockerfile                 # ← НОВЫЙ для bothost
├── Procfile                   # ← НОВЫЙ (опционально)
├── requirements.txt           # ← ОБНОВЛЁН (добавлены fastapi, uvicorn, pydantic-settings)
├── env.example                # ← ОБНОВЛЁН (добавлены BOTHOST_DOMAIN, PORT, CORS)
├── .gitignore                 # ← ОБНОВЛЁН
├── bot.py                     # БЕЗ ИЗМЕНЕНИЙ (использует _build_app, который уже был)
├── ... (существующие модули)
└── miniapp/                   # ← НОВЫЙ каталог Mini App
    ├── backend/
    │   ├── main.py            # FastAPI sub-app (монтируется в /api)
    │   ├── config.py          # Настройки (читает .env из корня)
    │   ├── telegram_auth.py   # Проверка initData (HMAC-SHA256)
    │   ├── services/
    │   │   └── gibdd_service.py  # Сервисный слой (lazy импорт bot, gibdd_parser, ...)
    │   └── routers/
    │       ├── regions.py     # GET /api/regions
    │       ├── parse.py       # POST /api/parse
    │       ├── dtp.py         # CRUD задач /api/dtp/tasks/*
    │       └── point.py       # POST /api/point
    └── frontend/              # React + Vite + Tailwind
        └── src/...
```

## Ключевая архитектура

```
                    Пользователь в Telegram
                          │
            ┌─────────────┴──────────────┐
            │                            │
      Сообщения боту              Клик "📊 Аналитика"
            │                            │
            ▼                            ▼
   https://bot1234.bothost.tech/bot/webhook    https://bot1234.bothost.tech/app/
            │                            │
            ▼                            ▼
    ┌──────────────────────────────────────────────┐
    │  Один процесс main.py на bothost            │
    │  (FastAPI + Telegram webhook)               │
    │                                              │
    │  ┌────────────────────────────────────────┐ │
    │  │ Telegram Application (webhook mode)   │ │
    │  │   ↑ переиспользует bot._build_app()   │ │
    │  │   ↑ все handler'ы из bot.py           │ │
    │  └────────────────────────────────────────┘ │
    │  ┌────────────────────────────────────────┐ │
    │  │ FastAPI                                │ │
    │  │   /api/* — Mini App endpoints          │ │
    │  │   /app/ — собранный React frontend     │ │
    │  └────────────────────────────────────────┘ │
    │  ┌────────────────────────────────────────┐ │
    │  │ Существующие модули gibdd-bot          │ │
    │  │ (api_client, gibdd_parser, analytics,  │ │
    │  │  concentration_points, report_gen)     │ │
    │  └────────────────────────────────────────┘ │
    └──────────────────────────────────────────────┘
```

## Подготовка к деплою

### Шаг 1. Соберите frontend локально

Frontend нужно собрать один раз — собранные файлы попадут в Docker-образ.

```bash
cd miniapp/frontend
cp .env.example .env  # оставить пустым (CORS решается через backend)
npm install
npm run build
# Появится директория miniapp/frontend/dist/
```

Если планируете деплой через Git (bothost сам соберёт Docker) — этот шаг не нужен, Dockerfile соберёт фронтенд автоматически.

### Шаг 2. Заполните .env

```bash
cp env.example .env
nano .env
```

Обязательные поля:
```ini
TELEGRAM_BOT_TOKEN=123456:ABC-DEF_your_token_from_BotFather
BOTHOST_DOMAIN=bot1234.bothost.tech   # домен, который выдаст bothost
CORS_ORIGINS=https://bot1234.bothost.tech,https://web.telegram.org,https://a.telegram.org
```

Опциональные (если нужен AI):
```ini
LLM_API_KEY=ваш_ключ_zhipuai
LLM_MODEL=glm-4.7-flash
```

### Шаг 3. Протестируйте локально

```bash
# Установите зависимости
pip install -r requirements.txt

# Запустите
PORT=8080 python main.py

# В другом терминале проверьте
curl http://localhost:8080/health
# → {"status":"ok","service":"gibdd-bot-miniapp",...}

curl http://localhost:8080/
# → {"name":"GIBDD Stat Bot + Mini App","docs":"/docs",...}
```

Mini App UI: http://localhost:8080/app/
API документация: http://localhost:8080/docs

## Деплой на bothost.ru

### Шаг 4. Создайте проект на bothost

1. Зарегистрируйтесь на https://bothost.ru
2. Создайте новый проект, выберите **Python**
3. **Включите опцию «Использовать домен»** (это выдаст `bot1234.bothost.tech`)
4. Запишите выданный домен

### Шаг 5. Загрузите код

**Вариант A — через Git (рекомендуется):**

1. Загрузите код в GitHub/GitLab репозиторий:
   ```bash
   cd gibdd-bot
   git init
   git add .
   git commit -m "Add Mini App + bothost integration"
   git remote add origin https://github.com/yourname/gibdd-bot.git
   git push -u origin main
   ```

2. В панели bothost подключите репозиторий и укажите ветку `main`.

3. Укажите:
   - **Главный файл:** `main.py`
   - **Dockerfile:** `Dockerfile` (если bothost использует Docker-сборку)
   - bothost автоматически соберёт образ и запустит.

**Вариант B — через веб-интерфейс:**

Загрузите ZIP-архив всего проекта через веб-форму bothost.

### Шаг 6. Настройте переменные окружения в bothost

В панели bothost → раздел «Env переменные» добавьте все переменные из вашего `.env`:

```
TELEGRAM_BOT_TOKEN=...
BOTHOST_DOMAIN=bot1234.bothost.tech
CORS_ORIGINS=https://bot1234.bothost.tech,https://web.telegram.org,https://a.telegram.org
CAMERA_DATA_DIR=/app/data
LOG_LEVEL=INFO
LLM_API_KEY=... (опционально)
LLM_MODEL=glm-4.7-flash
ENABLE_NEWS_SEARCH=true
TARGET_API_TIMEOUT=120
ALLOWED_USER_IDS=
PORT=8080
```

### Шаг 7. Запустите и проверьте

1. Нажмите «Deploy» / «Restart» в панели bothost.
2. Откройте логи в панели bothost — должны увидеть:
   ```
   === GIBDD Bot + Mini App запускается на порту 8080 ===
   [INFO] miniapp.backend.services.gibdd_service: Mini App: загружено 82 регионов
   [INFO] main: Telegram webhook установлен: https://bot1234.bothost.tech/bot/webhook
   ```
3. Проверьте health:
   ```bash
   curl https://bot1234.bothost.tech/health
   # → {"status":"ok","service":"gibdd-bot-miniapp","telegram_bot":"running",...}
   ```

### Шаг 8. Установите webhook в Telegram

Если в логах видно, что webhook уже установлен (строка «Telegram webhook установлен») — пропустите этот шаг.

Иначе вручную:

```bash
curl "https://api.telegram.org/bot<ВАШ_ТОКЕН>/setWebhook?url=https://bot1234.bothost.tech/bot/webhook"
# → {"ok":true,"result":true,"description":"Webhook was set"}
```

Проверьте:
```bash
curl "https://api.telegram.org/bot<ВАШ_ТОКЕН>/getWebhookInfo"
# → {"ok":true,"result":{"url":"https://bot1234.bothost.tech/bot/webhook",...}}
```

### Шаг 9. Настройте Menu Button в @BotFather

1. Откройте `@BotFather` в Telegram.
2. `/mybots` → выберите вашего бота → **Bot Settings** → **Menu Button** → **Configure menu button**.
3. Укажите:
   - URL: `https://bot1234.bothost.tech/app/`
   - Text: `📊 Аналитика ДТП`

### Шаг 10. Тестирование

1. Откройте бота в Telegram.
2. Нажмите `/start` — бот должен ответить как обычно.
3. Нажмите кнопку Menu (слева от поля ввода) → откроется Mini App.
4. Введите запрос «Вологодская область за 2025 год» → наблюдайте прогресс.
5. После завершения откройте вкладку «Карта» — должна отобразиться интерактивная Leaflet-карта (без проблемы iOS Quick Look!).
6. Скачайте Excel-файлы через вкладку «Файлы».

## Что делать, если что-то не работает

### Бот не отвечает на сообщения

1. Проверьте логи bothost — нет ли ошибок при запуске.
2. Проверьте webhook:
   ```bash
   curl "https://api.telegram.org/bot<ТОКЕН>/getWebhookInfo"
   ```
   Если `last_error_message` не пустой — Telegram не достучался до вашего сервера.

### Mini App показывает белый экран

1. Откройте `https://bot1234.bothost.tech/app/` в браузере — должна загрузиться страница.
2. Если 404 — frontend не собран. На bothost с Docker-сборкой это должно произойти автоматически. Если нет — соберите локально и запушьте `miniapp/frontend/dist/`.
3. Откройте DevTools (F12) — проверьте ошибки в Console.

### API возвращает 401 Unauthorized

- Не передаётся `initData` от Telegram. Убедитесь, что Mini App открывается **из Telegram** (через кнопку Menu), а не просто по ссылке в браузере.
- При разработке можно отключить проверку — закомментируйте зависимость `get_current_user` в роутерах.

### CORS ошибки в консоли браузера

- Добавьте ваш bothost-домен в `CORS_ORIGINS`:
  ```
  CORS_ORIGINS=https://bot1234.bothost.tech,https://web.telegram.org,https://a.telegram.org
  ```
- Перезапустите проект в bothost.

### Выгрузка ГИБДД зависает

- API ГИБДД может быть недоступен. Проверьте:
  ```bash
  curl "https://api.gibdd.ru/opendataapi/v1/dictionary/rows?code=1"
  ```
- Если API недоступен — бот автоматически переключится на web-fallback (это займёт больше времени, 1-3 минуты).

### In-memory задачи теряются при перезапуске

Это особенность текущей MVP-версии. Для production нужно:
- Подключить Redis (bothost может предложить как доп. сервис)
- Или PostgreSQL (внешний, например Beget или Selectel Managed PG)

См. roadmap в основном README.

## Обновление кода

### Через Git

Просто сделайте `git push` в основную ветку — bothost автоматически пересоберёт и перезапустит проект.

### Вручную

В панели bothost нажмите «Redeploy» или загрузите новый ZIP.

## Финансовые затраты

| Ресурс | Стоимость |
|---|---|
| Bothost Basic (1 GB RAM, 5 GB SSD) | 99 ₽/мес |
| Домен .ru (опционально) | 200-300 ₽/год |
| ZhipuAI GLM (если включён AI) | 0-500 ₽/мес |
| **Итого минимум:** | **~99 ₽/мес** |

## Что нужно уточнить у поддержки bothost ДО деплоя

1. **Где физически дата-центр?** (для 152-ФЗ — должен быть РФ)
2. **Можно ли подключить свой домен** вместо `bot1234.bothost.tech`?
3. **Поддерживается ли внешний Redis/PostgreSQL**?
4. **Какой таймаут HTTP-запросов** на стороне bothost? (важно для долгих выгрузок)
5. **Лимиты на размер диска** для кэша камер в `data/`?

## Roadmap для масштабирования

Когда упрётесь в ограничения bothost:

1. **In-memory → Redis**: хранение задач в Redis, несколько воркеров.
2. **Celery**: асинхронные задачи выносятся в отдельный worker.
3. **PostgreSQL + PostGIS**: персистентное хранение, geo-индексы.
4. **S3-хранилище**: Yandex Object Storage для больших Excel/HTML-файлов.
5. **CDN**: для раздачи статики (если будет много пользователей).

Подробности — в основном анализе миграции.
