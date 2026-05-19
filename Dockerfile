FROM python:3.12-slim

WORKDIR /app

# Системные зависимости + Chromium (через apt-get, системные пакеты)
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    gnupg2 \
    unzip \
    libxi6 \
    libgtk-3-0 \
    libx11-xcb1 \
    libdrm2 \
    libxss1 \
    libasound2 \
    xvfb \
    chromium \
    fonts-liberation \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Проверяем что chromium установлен
RUN which chromium

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-B", "bot.py"]
