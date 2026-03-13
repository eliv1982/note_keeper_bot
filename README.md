# Telegram-бот для заметок и категорий

Бот для управления заметками по категориям в Telegram. Стек: Python 3.9+, python-telegram-bot 13.x (синхронный), SQLite3.

## Возможности

- Создание категорий и заметок
- Просмотр заметок по категориям с датой создания
- Удаление заметок
- Изоляция данных по пользователям (доступ только к своим категориям и заметкам)

## Требования

- Python 3.9+ (на 3.12 используются зафиксированные версии urllib3 и setuptools из `requirements.txt`)
- Токен бота от [@BotFather](https://t.me/BotFather)

## Установка

1. Клонируйте репозиторий или скопируйте файлы проекта.

2. Создайте виртуальное окружение и установите зависимости:

   ```bash
   python -m venv venv
   source venv/bin/activate   # Linux/macOS
   # или: venv\Scripts\activate   # Windows

   pip install -r requirements.txt
   ```

3. Настройте токен бота:

   ```bash
   cp .env.example .env
   ```

   Откройте `.env` и подставьте свой токен вместо `your_bot_token_here`:

   ```
   TELEGRAM_BOT_TOKEN=123456789:ABCdefGHI...
   ```

## Запуск

```bash
python bot.py
```

Подробная инструкция по развёртыванию на Linux-сервере — в [DEPLOY.md](DEPLOY.md).

## Запуск в Docker

### Локальный образ

```bash
docker build -t notes-bot .
docker run -d \
  --name notes-bot \
  --restart unless-stopped \
  --env-file .env \
  -v $(pwd)/notes_bot.db:/app/notes_bot.db \
  -v $(pwd)/output:/app/output \
  notes-bot
```

Где:

- `.env` — файл с `TELEGRAM_BOT_TOKEN=...` в корне проекта;
- `notes_bot.db` и `output/` — база и экспорт, которые сохраняются вне контейнера.

### Через Docker Hub

1. Соберите и запушьте образ:

   ```bash
   docker build -t <dockerhub_user>/notes-bot:latest .
   docker push <dockerhub_user>/notes-bot:latest
   ```

2. На сервере:

   ```bash
   docker pull <dockerhub_user>/notes-bot:latest
   mkdir -p /opt/notes-bot/output

   # создайте /opt/notes-bot/.env с TELEGRAM_BOT_TOKEN

   docker run -d \
     --name notes-bot \
     --restart unless-stopped \
     --env-file /opt/notes-bot/.env \
     -v /opt/notes-bot/notes_bot.db:/app/notes_bot.db \
     -v /opt/notes-bot/output:/app/output \
     <dockerhub_user>/notes-bot:latest
   ```

## Команды бота

| Команда | Описание |
|--------|----------|
| `/start` | Приветствие и полный список команд |
| `/help` | Краткая справка по основным командам |
| `/newcategory` | Создать новую категорию (название бот спросит сообщением) |
| `/categories` | Список ваших категорий с номерами (локальная нумерация) |
| `/add` | Добавить заметку в категорию (выбор категории по номеру или кнопкой) |
| `/adddue` | Добавить заметку с напоминанием (за 1 час до указанного срока) |
| `/get` | Показать заметки выбранной категории |
| `/delnote` | Удалить заметку (категория и заметка выбираются по номеру или кнопками) |
| `/delcat` | Удалить категорию целиком |
| `/cancel` | Отменить текущий шаг диалога |
| `/version` | Версия бота |

## Структура проекта

```
Bot_notes/
├── bot.py           # Код бота
├── requirements.txt
├── .env.example     # Пример переменных окружения
├── .env             # Ваши переменные (НЕ коммитить!)
├── README.md
├── DEPLOY.md        # Инструкция по деплою на сервер
├── notes_bot.db     # База SQLite (создаётся при первом запуске)
└── output/          # Экспорт заметок в CSV
```

## Безопасность

- Токен хранится в переменной окружения или в `.env` (файл `.env` в `.gitignore`).
- При любом обращении к заметкам и категориям проверяется соответствие `user_id` — пользователи видят только свои данные (защита от IDOR).
- Все запросы к БД параметризованы (защита от SQL-инъекций).

## Лицензия

Проект для учебных и личных целей.
