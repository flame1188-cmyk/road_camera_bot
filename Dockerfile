FROM python:3.12-slim

WORKDIR /app

# Путь для кэша браузеров Playwright
ENV PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright: устанавливает Chromium + ВСЕ системные зависимости автоматически
RUN playwright install --with-deps chromium

COPY . .
RUN find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-B", "bot.py"]
