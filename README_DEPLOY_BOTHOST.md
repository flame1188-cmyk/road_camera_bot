# Деплой GIBDD Bot + Mini App на bothost.ru

Этот документ описывает деплой единого приложения (Telegram-бот + Mini App)
на хостинг [bothost.ru](https://bothost.ru).

## Архитектура

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

**Единый процесс**: `main.py` поднимает FastAPI и в lifespan инициализирует
Telegram-бота в webhook-режиме. Mini App frontend (собранный React) раздаётся
как статика из `miniapp/frontend/dist`.

## Что изменилось по сравнению с pure-Telegram-ботом

| До | После |
|----|-------|
| `python bot.py` (polling) | `python main.py` (FastAPI + webhook) |
| 1 процесс: только бот | 1 процесс: FastAPI + бот + ститика |
| Нет веб-интерфейса | Mini App на `/app/` |
| Webhook не нужен | Webhook обязателен на `/bot/webhook` |
| `bot._build_app()` — внутренний | `bot._build_app()` — используется `main.py` |

Структура проекта:

```
gibdd-bot/
├── main.py                 ← Единая точка входа для bothost
├── bot.py                  ← Существующий бот + команда /miniapp
├── config.py               ← Существующий конфиг
├── requirements.txt        ← Объединённые зависимости
├── Dockerfile              ← Multi-stage: frontend build + python main.py
├── env.example             ← Шаблон .env с bothost-переменными
├── miniapp/
│   ├── __init__.py         ← Пакет
│   ├── backend/
│   │   ├── main.py         ← FastAPI sub-app (монтируется на /api)
│   │   ├── config.py       ← Settings (pydantic-settings)
│   │   ├── telegram_auth.py← Проверка initData (HMAC-SHA256)
│   │   ├── routers/        ← /regions, /parse, /dtp, /point
│   │   └── services/
│   │       └── gibdd_service.py ← Мост к существующим модулям gibdd-bot
│   └── frontend/           ← Vite + React + TS + Tailwind
│       └── dist/           ← Собранная ститика (после npm run build)
└── ... (существующие модули gibdd-bot)
```

## Подготовка к деплою

### 1. Получите домен на bothost

После регистрации бота на bothost вы получите домен вида
`bot1234.bothost.tech`. Запишите его.

### 2. Подготовьте переменные окружения

Скопируйте `env.example` в `.env` и заполните:

```bash
cp env.example .env
```

Обязательные переменные:

| Переменная | Пример | Описание |
|------------|--------|----------|
| `TELEGRAM_BOT_TOKEN` | `123456:ABC-DEF...` | Токен от @BotFather |
| `BOTHOST_DOMAIN` | `bot1234.bothost.tech` | Домен от bothost |
| `PORT` | `8080` | Порт (bothost обычно передаёт через `$PORT`) |
| `CORS_ORIGINS` | `https://bot1234.bothost.tech,https://web.telegram.org` | CORS |

Опциональные:

| Переменная | Описание |
|------------|----------|
| `LLM_API_KEY` | Ключ ZhipuAI для AI-анализа |
| `ALLOWED_USER_IDS` | Список Telegram ID через запятую (пусто = всем) |

### 3. Соберите frontend (если не используете Docker)

Если bothost собирает Dockerfile автоматически — этот шаг выполняется
внутри контейнера. Если деплоите как Python-процесс:

```bash
cd miniapp/frontend
npm install
npm run build
# Результат: miniapp/frontend/dist/
```

## Деплой на bothost.ru

### Вариант A: Через Dockerfile (рекомендуется)

1. Загрузите репозиторий на bothost (через git или архивом).
2. В настройках проекта укажите **Dockerfile** как источник.
3. В переменных окружения bothost задайте `TELEGRAM_BOT_TOKEN`,
   `BOTHOST_DOMAIN`, `CORS_ORIGINS`.
4. bothost автоматически:
   - Соберёт frontend (Stage 1: `node:20-alpine`)
   - Установит Python-зависимости (Stage 2: `python:3.11-slim`)
   - Запустит `python main.py` на `$PORT`

### Вариант B: Через главный файл main.py

Если bothost не поддерживает Dockerfile:

1. Укажите `main.py` как главный файл в настройках bothost.
2. Убедитесь, что `requirements.txt` указан как файл зависимостей.
3. **Соберите frontend локально** и загрузите `miniapp/frontend/dist/`
   вместе с проектом.
4. bothost запустит `python main.py`.

## После первого деплоя: установка webhook

После успешного запуска (проверьте `/health` в браузере) установите
webhook для Telegram **один раз**:

```bash
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?url=https://<BOTHOST_DOMAIN>/bot/webhook"
```

Проверка:

```bash
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getWebhookInfo"
```

В ответе `webhook_url` должен быть `https://<BOTHOST_DOMAIN>/bot/webhook`,
а `last_error_message` — пустым.

## Настройка Mini App в BotFather

Чтобы кнопка Mini App отображалась в меню бота:

1. Откройте @BotFather → `/newapp` (или `/setmenubutton`).
2. Укажите бота, название "ДТП Статистика".
3. URL: `https://<BOTHOST_DOMAIN>/app/`.
4. Теперь у бота появится кнопка-меню (слева от поля ввода), открывающая
   Mini App.

Альтернативно — команда `/miniapp` в чате с ботом присылает inline-кнопку
для открытия Mini App.

## Проверка работоспособности

| Endpoint | Что проверяет | Ожидаемый ответ |
|----------|---------------|-----------------|
| `https://<DOMAIN>/health` | Сервер жив | `{"status":"ok",...}` |
| `https://<DOMAIN>/` | Корневой info | JSON с путями |
| `https://<DOMAIN>/api/miniapp/health` | Mini App API | `{"status":"ok",...}` |
| `https://<DOMAIN>/api/regions` | Авторизация | 401 (нужен initData) |
| `https://<DOMAIN>/app/` | Frontend | HTML страница |
| `https://<DOMAIN>/docs` | Swagger UI | Документация API |
| Telegram `/start` | Бот отвечает | Сообщение приветствия |
| Telegram `/miniapp` | Кнопка Mini App | Inline-кнопка "Открыть" |

## Локальная разработка

### Backend + Frontend (hot reload)

Терминал 1 — backend:

```bash
PORT=8080 BOTHOST_DOMAIN=localhost TELEGRAM_BOT_TOKEN=<token> python main.py
```

Терминал 2 — frontend (dev-сервер с hot reload):

```bash
cd miniapp/frontend
npm run dev
# Откроется http://localhost:5173, проксирует /api → localhost:8080
```

### Только backend (frontend уже собран)

```bash
cd miniapp/frontend && npm run build && cd ../..
PORT=8080 BOTHOST_DOMAIN=localhost TELEGRAM_BOT_TOKEN=<token> python main.py
# Откройте http://localhost:8080/app/
```

### Без Telegram-бота (только Mini App)

Можно запустить с пустым `TELEGRAM_BOT_TOKEN` — FastAPI поднимется,
Mini App будет работать, но авторизация через initData не пройдёт
(нужен реальный токен для проверки подписи).

## Устранение неполадок

### Бот не отвечает после деплоя

1. Проверьте `/health` — `telegram_bot` должен быть `"running"`.
2. Если `"stopped"` — смотрите логи bothost, обычно причина:
   - Невалидный `TELEGRAM_BOT_TOKEN`
   - Telegram API недоступен (проверьте `getWebhookInfo`)
3. Проверьте, что webhook установлен:
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
   ```
4. Если `last_error_message` указывает на 404/503 — проверьте, что
   `BOTHOST_DOMAIN` задан и `/bot/webhook` доступен.

### Mini App открывается, но API возвращает 401

Это значит, что `X-Tg-Init-Data` не передаётся. Возможные причины:

1. Frontend собран со старым `VITE_API_BASE` — пересоберите.
2. Telegram SDK не загрузился — проверьте, что в `index.html` есть
   `<script src="https://telegram.org/js/telegram-web-app.js"></script>`.
3. Mini App открыт в обычном браузере (не через Telegram) — в этом
   случае `initData` пустой, авторизация не пройдёт.

### CORS ошибки в консоли браузера

Добавьте ваш домен в `CORS_ORIGINS`:

```
CORS_ORIGINS=https://bot1234.bothost.tech,https://web.telegram.org,https://a.telegram.org
```

### Frontend не обновляется после деплоя

Vite добавляет хэш к именам файлов (`index-AbCd1234.js`). Если старый
`index.html` закеширован — он будет ссылаться на несуществующий файл.
Решение: убедитесь, что bothost не кэширует `/app/` агрессивно, или
добавьте version-busting.

### Ошибка `InvalidToken` в логах

Telegram отверг токен. Проверьте:

1. Токен скопирован полностью (включая двоеточие и часть после).
2. Токен не отозван в @BotFather (`/revoke` + новый токен).
3. Нет лишних пробелов/переводов строк в `.env`.

## Ограничения bothost.ru

- **1 процесс**: `main.py` запускает один uvicorn worker. Webhook требует
  единственного процесса (иначе Telegram будет слать updates случайному).
- **RAM ~2 ГБ**: gibdd-bot уже оптимизирован под это (см. комментарии в
  `bot.py` про tracemalloc). Mini App добавляет ~50 МБ.
- **Диск**: кэш регионов/камер хранится в `data/`. На bothost обычно
  `/data` — задайте `CAMERA_DATA_DIR=/data` если доступно.
- **Таймауты**: API ГИБДД может отвечать до 120 сек. `TARGET_API_TIMEOUT=120`
  уже настроен. bothost обычно даёт 300 сек на HTTP-запрос.
- **152-ФЗ**: bothost — российский хостинг, дата-центр в РФ. Данные
  пользователей не покидают юрисдикцию.

## Откат к polling-режиму

Если нужно вернуться к старому режиму (только Telegram-бот без Mini App):

```bash
# 1. Удалите webhook
curl "https://api.telegram.org/bot<TOKEN>/deleteWebhook"

# 2. Запустите старый main.py (переименован в bot.py)
python bot.py
```

`bot.py` полностью сохранил свою polling-логику и может работать
независимо от `main.py` и Mini App.
