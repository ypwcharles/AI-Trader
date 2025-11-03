FROM python:3.12-slim

# Faster, quieter Python, consistent timezone
ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# Install Python deps
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Default command: run the trading-hour scheduler
CMD ["python", "-u", "scripts/scheduler.py"]

