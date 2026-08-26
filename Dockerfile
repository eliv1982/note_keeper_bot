FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Moscow

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Непривилегированный пользователь для запуска бота.
# /app принадлежит ему, чтобы notes_bot.db и output/ можно было создавать и писать в рантайме.
RUN useradd --create-home --uid 1000 appuser \
 && mkdir -p /app/output \
 && chown -R appuser:appuser /app
USER appuser

# Телеграм-токен передаётся через переменную окружения TELEGRAM_BOT_TOKEN

CMD ["python", "bot.py"]

