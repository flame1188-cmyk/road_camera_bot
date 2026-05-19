FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .

# --user: ставит пакеты в ~/.local (работает без root)
RUN pip install --user --no-cache-dir -r requirements.txt

# Добавляем ~/.local/bin в PATH (там лежит playwright CLI)
ENV PATH="/root/.local/bin:${PATH}"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

# Системные зависимости для Chromium
RUN playwright install-deps chromium

COPY . .
RUN find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Chromium скачивается при старте контейнера
CMD ["sh", "-c", "playwright install chromium && exec python -B bot.py"]
