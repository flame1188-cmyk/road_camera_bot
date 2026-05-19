FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-B", "bot.py"]
