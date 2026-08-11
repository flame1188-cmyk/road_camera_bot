# ============================================================
# Multi-stage Dockerfile для Mini App:
#   1. build-frontend — собирает React/Vite в /app/dist
#   2. runtime — Python + собранный frontend
# ============================================================

# --- Stage 1: Сборка frontend ---
FROM node:20-alpine AS build-frontend
WORKDIR /build

# Кэшируем установку зависимостей
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

# Копируем исходники и собираем
COPY frontend/ ./
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

# Устанавливаем Python-зависимости backend
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Копируем backend-код
COPY backend/ ./backend/

# Копируем собранный frontend (монтируется в /app на /app URL)
COPY --from=build-frontend /build/dist ./frontend/dist

# Копируем существующие модули gibdd-bot (для импорта сервисным слоем)
# ВАЖНО: предполагается, что Dockerfile лежит в <gibdd-bot>/miniapp/
# Если структура другая — скорректируйте COPY в docker-compose.yml
COPY .. /gibdd-bot/

ENV GIBDD_PROJECT_PATH=/gibdd-bot
ENV PYTHONPATH=/app
ENV TASKS_DIR=/tmp/gibdd_tasks

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

# Запуск через gunicorn с uvicorn worker'ами
CMD ["sh", "-c", "gunicorn backend.main:app \
    --workers ${APP_WORKERS:-2} \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:${APP_PORT:-8000} \
    --timeout 300 \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile -"]
