# GIBDD Stat Mini App

Telegram Mini App для выгрузки и анализа данных ДТП из открытых данных ГИБДД. Переиспользует существующие Python-модули `gibdd-bot` и решает главную проблему iOS-пользователей — **HTML-карты теперь открываются в нативном WebView Telegram, а не в Quick Look, который не выполняет JavaScript**.

## Архитектура

```
miniapp/
├── backend/                    # FastAPI backend
│   ├── main.py                 # Точка входа FastAPI
│   ├── config.py               # Pydantic-settings (читает .env)
│   ├── telegram_auth.py        # Проверка подписи initData (HMAC-SHA256)
│   ├── requirements.txt
│   ├── .env.example
│   ├── services/
│   │   └── gibdd_service.py    # Сервисный слой: lazy импорт модулей gibdd-bot
│   └── routers/
│       ├── regions.py          # GET /api/regions — список регионов
│       ├── parse.py            # POST /api/parse — NLP-парсинг запроса
│       ├── dtp.py              # CRUD задач выгрузки + скачивание файлов
│       └── point.py            # POST /api/point — статистика по геоточке
├── frontend/                   # Vite + React + TypeScript + Tailwind
│   ├── src/
│   │   ├── App.tsx             # Главный layout
│   │   ├── main.tsx            # Entry point (инициализирует Telegram SDK)
│   │   ├── lib/
│   │   │   ├── telegram.ts     # Обёртка над Telegram WebApp SDK
│   │   │   ├── api.ts          # API-клиент с авто-добавлением initData
│   │   │   └── utils.ts        # Утилиты (cn, formatSize, statusLabel)
│   │   ├── hooks/
│   │   │   └── useTaskPolling.ts  # Polling статуса задачи (react-query)
│   │   └── components/
│   │       ├── RequestForm.tsx       # Форма ввода запроса
│   │       ├── ProgressIndicator.tsx # Прогресс-бар
│   │       ├── ResultsPanel.tsx      # Табы: карта / аналитика / файлы
│   │       ├── MapFrame.tsx          # iframe с HTML-картой
│   │       └── HistoryList.tsx       # История запросов
│   ├── index.html              # Подключает telegram-web-app.js
│   ├── vite.config.ts          # Proxy /api → backend в dev
│   ├── tailwind.config.js      # Поддержка Telegram color variables
│   └── package.json
├── deploy/
│   └── bot_miniapp_patch.py    # Патч для существующего bot.py
├── Dockerfile                  # Multi-stage: build frontend + Python runtime
├── docker-compose.yml
├── nginx.conf                  # TLS + reverse proxy для production
└── README.md
```

## Ключевые особенности

### Решение проблемы iOS Quick Look
README исходного `gibdd-bot` прямо упоминает проблему: HTML-файлы из Telegram на iPhone открываются в Quick Look, который не выполняет JavaScript — карта отображается пустой. Mini App открывает HTML в нативном WebView Telegram, который полностью выполняет JS. **Решение из коробки, без установки HTML Viewer.**

### Переиспользование 100% бизнес-логики
Backend использует **lazy импорт** существующих модулей (`api_client`, `gibdd_parser`, `analytics`, `concentration_points`, `camera_matcher`, `excel_generator`, `report_generator`, `llm_analyzer`). Ни одна строка бизнес-логики не дублируется.

### Безопасная аутентификация
- Проверка подписи Telegram `initData` через HMAC-SHA256 (constant-time сравнение).
- Поддержка whitelist через `ALLOWED_USER_IDS` (как в исходном боте).
- TTL проверки — 24 часа (защита от replay-атак).
- `initData` передаётся в заголовке `X-Tg-Init-Data` — невидим в URL/logs.

### Тема Telegram
Frontend автоматически применяет цветовую схему Telegram (`themeParams` → CSS-переменные `--tg-color-*`). Поддержка light/dark mode из коробки.

### Haptic feedback
Все клики/ошибки/успехи сопровождаются нативной вибрацией на мобильных устройствах.

## Установка и запуск

### Вариант 1: Локальная разработка (без Docker)

```bash
# 1. Скопируйте miniapp/ в корень существующего проекта gibdd-bot
cd gibdd-bot
git clone <miniapp-repo> miniapp  # или просто скопируйте папку

# 2. Настройте backend
cd miniapp/backend
cp .env.example .env
# Отредактируйте .env: впишите TELEGRAM_BOT_TOKEN
pip install -r requirements.txt

# 3. Настройте frontend
cd ../frontend
cp .env.example .env
npm install

# 4. Запустите backend (терминал 1)
cd ../backend
uvicorn main:app --reload --port 8000

# 5. Запустите frontend (терминал 2)
cd ../frontend
npm run dev
# → http://localhost:5173
```

### Вариант 2: Docker (production-like)

```bash
# 1. Заполните .env
cd miniapp/backend
cp .env.example .env
nano .env  # TELEGRAM_BOT_TOKEN, MINIAPP_URL

# 2. Соберите и запустите
cd ..
docker compose up -d --build

# 3. Проверьте
curl http://localhost:8000/health
# → {"status":"ok","service":"gibdd-miniapp-backend",...}
```

### Вариант 3: Production на VPS (Timeweb Cloud)

```bash
# 1. На сервере (Ubuntu 22.04+)
sudo apt update && sudo apt install -y docker.io docker-compose nginx certbot python3-certbot-nginx
git clone <your-repo> /opt/gibdd-bot
cd /opt/gibdd-bot/miniapp

# 2. Заполните .env
cp backend/.env.example backend/.env
nano backend/.env

# 3. Запустите backend
docker compose up -d --build

# 4. Настройте Nginx
sudo cp nginx.conf /etc/nginx/sites-available/gibdd-miniapp.conf
sudo ln -s /etc/nginx/sites-available/gibdd-miniapp.conf /etc/nginx/sites-enabled/
sudo nano /etc/nginx/sites-available/gibdd-miniapp.conf
# Замените yourdomain.ru на ваш домен

# 5. Получите TLS сертификат
sudo certbot --nginx -d yourdomain.ru

# 6. Перезапустите Nginx
sudo nginx -t && sudo systemctl reload nginx
```

## Привязка к Telegram-боту

### Шаг 1. Задайте Menu Button через @BotFather

1. Откройте `@BotFather` в Telegram.
2. `/mybots` → выберите вашего бота → **Bot Settings** → **Menu Button** → **Configure menu button**.
3. Укажите URL: `https://yourdomain.ru` и текст кнопки: `📊 Аналитика ДТП`.

### Шаг 2. Добавьте кнопку в существующий `bot.py`

Скопируйте функции из `deploy/bot_miniapp_patch.py` в ваш `bot.py` и используйте в обработчике `/start`:

```python
from deploy.bot_miniapp_patch import start_handler_with_miniapp

# В Application handlers:
application.add_handler(CommandHandler("start", start_handler_with_miniapp))
```

### Шаг 3. Настройте Mini App URL

В `.env` backend:
```
MINIAPP_URL=https://yourdomain.ru
CORS_ORIGINS=https://yourdomain.ru,https://web.telegram.org
```

## API Endpoints

| Метод | Путь | Описание |
|---|---|---|
| GET | `/health` | Health-check |
| GET | `/api/regions` | Список регионов с кодами |
| GET | `/api/regions/search?q=` | Поиск регионов (autocomplete) |
| POST | `/api/parse` | Парсинг естественного языка → `{region_code, period}` |
| POST | `/api/dtp/tasks` | Создать задачу выгрузки, вернуть `task_id` |
| GET | `/api/dtp/tasks` | Список задач пользователя |
| GET | `/api/dtp/tasks/{id}` | Статус задачи (для polling) |
| GET | `/api/dtp/tasks/{id}/files` | Список готовых файлов |
| GET | `/api/dtp/tasks/{id}/map` | HTML-карта (для iframe) |
| GET | `/api/dtp/tasks/{id}/download/{file_type}` | Скачать Excel/HTML |
| POST | `/api/point` | Статистика ДТП в радиусе от точки |

Документация Swagger UI: `http://localhost:8000/docs`

## Переменные окружения

| Переменная | Обязательно | Описание |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | Токен от @BotFather |
| `ALLOWED_USER_IDS` | Нет | Telegram user IDs через запятую |
| `CORS_ORIGINS` | ✅ | Origins для CORS (URL Mini App) |
| `LLM_API_KEY` | Нет | Для AI-анализа (ZhipuAI) |
| `TARGET_API_TIMEOUT` | Нет | Таймаут запросов к stat.gibdd.ru (сек) |
| `GIBDD_PROJECT_PATH` | ✅ | Путь к корню gibdd-bot (для импорта модулей) |
| `TASKS_DIR` | Нет | Временная директория для файлов задач |

## Переход на production-архитектуру (для масштабирования)

Текущая MVP-версия хранит задачи **in-memory** и выполняет их в event loop FastAPI. Для боевой нагрузки рекомендуется:

1. **Celery + Redis** для асинхронных задач:
   ```python
   # В services/gibdd_service.py заменить asyncio.create_task на:
   from celery import Celery
   app = Celery('gibdd', broker='redis://redis:6379/0')
   @app.task
   def execute_task(task_id: str): ...
   ```

2. **PostgreSQL + PostGIS** для персистентного хранения:
   - Таблица `tasks` (вместо `_tasks: dict`)
   - Таблица `dtp_cards` (с партиционированием по регионам)
   - PostGIS-индекс для geo-запросов (замена Shapely in-memory)

3. **S3-совместимое хранилище** для файлов (Yandex Object Storage):
   - Заменить `_task_dir()` на загрузку в S3
   - Сгенерированные pre-signed URLs для скачивания

4. **JWT-сессии** (если нужно выходить за пределы Telegram-аудитории):
   - Обмен `initData` на JWT при первом запросе
   - Последующие запросы — с JWT в `Authorization: Bearer`

## Требования 152-ФЗ

⚠️ Mini App обрабатывает ПДн (данные участников ДТП). Для соответствия 152-ФЗ:

1. **Хостинг в РФ**: Timeweb Cloud / Selectel / Beget (все в реестре Минцифры).
2. **TLS обязателен** (Let's Encrypt — бесплатно).
3. **Политика обработки ПДн** + **Согласие** при первом открытии Mini App.
4. **Уведомление Роскомнадзора** об обработке ПДн (через Госуслуги).
5. **Журнал аудита доступа** к ПДн (логировать все запросы `user_id → region_code, period`).
6. **Шифрование БД при rest** (LUKS для диска VPS).

См. подробности в основной аналитической записке (раздел 8).

## Лицензия

MIT (наследует от исходного проекта `gibdd-bot`).
