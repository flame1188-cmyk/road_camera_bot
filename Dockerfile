FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Системные зависимости для Chromium (маленькие пакеты, быстро ставятся)
RUN playwright install-deps chromium

COPY . .
RUN find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

ENV PYTHONDONTWRITEBYTECODE=1
ENV PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

# Chromium скачивается при старте контейнера (Amvera блокирует большие скачки при сборке)
# playwright install chromium — идемпотентен: если уже скачан, пропускается за 1 секунду
CMD ["sh", "-c", "playwright install chromium && exec python -B bot.py"]
