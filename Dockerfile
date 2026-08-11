# ============================================================
# Multi-stage Dockerfile для GIBDD Bot + Mini App
#
# Сборка:
#   docker build -t gibdd-bot-miniapp .
#
# Запуск (локально):
#   docker run -d --env-file .env -p 8080:8080 gibdd-bot-miniapp
#
# На bothost.ru: просто укажите этот Dockerfile как источник,
# bothost автоматически соберёт и запустит.
# ============================================================

# --- Stage 1: Сборка frontend ---
FROM node:20-alpine AS build-frontend
WORKDIR /build

# Кэшируем установку зависимостей
COPY miniapp/frontend/package.json miniapp/frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

# Копируем исходники и собираем
COPY miniapp/frontend/ ./
RUN npm run build


# --- Stage 2: Runtime ---
FROM python:3.11-slim AS runtime

# Системные зависимости для Shapely + httpx
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libgeos-dev \
    libxml2 \
    libxslt1.1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Устанавливаем Python-зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код проекта (gibdd-bot + miniapp)
COPY . .

# Копируем собранный frontend
COPY --from=build-frontend /build/dist ./miniapp/frontend/dist

# Создаём директорию для данных
# /app/data — persistent volume на Bothost (переживает redeploy).
# Внутри: osm_cache/ (предкэш границ НП), cameras/ (кэш камер).
RUN mkdir -p /app/data /app/data/osm_cache /app/data/cameras

# Переменные окружения по умолчанию.
# PORT не задаём здесь жёстко — bothost передаёт свой PORT через env (обычно 3000).
# Если запускаем локально без bothost — main.py использует 8080 по умолчанию.
ENV PYTHONPATH=/app
ENV CAMERA_DATA_DIR=/app/data

# Healthcheck: берём порт из $PORT, чтобы он работал и на bothost (3000), и локально (8080).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f "http://localhost:${PORT:-8080}/health" || exit 1

# Открываем оба порта: 3000 (bothost) и 8080 (локальный дефолт main.py).
# EXPOSE — это метаданные, bothost всё равно использует поле «Порт» из дашборда.
EXPOSE 3000 8080

# Запуск: один процесс main.py (FastAPI + Telegram webhook)
CMD ["python", "main.py"]
