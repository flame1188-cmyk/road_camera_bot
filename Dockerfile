FROM python:3.12-slim

WORKDIR /app

# Системные зависимости для headless Chromium (Playwright)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 libxkbcommon0 \
    libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libasound2t64 libpango-1.0-0 \
    libcairo2 libxshmfence1 libx11-xcb1 libxext6 \
    fonts-liberation fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Установка браузера Chromium для Playwright
RUN playwright install chromium

COPY . .
RUN find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-B", "bot.py"]
