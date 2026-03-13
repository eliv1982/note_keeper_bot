# Развёртывание бота на Linux-сервере

Инструкция по запуску Telegram-бота на VPS или выделенном сервере под Linux (Ubuntu/Debian).

## 1. Подготовка сервера

Убедитесь, что установлен Python 3.9 или выше:

```bash
python3 --version
```

При необходимости установите Python и venv:

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install python3 python3-venv python3-pip -y
```

## 2. Загрузка проекта на сервер

Варианты:

- **Git** (если проект в репозитории):
  ```bash
  git clone <url_репозитория> bot_notes
  cd bot_notes
  ```

- **SCP/SFTP**: скопируйте папку с `bot.py`, `requirements.txt`, `.env.example` на сервер в каталог, например `/home/ubuntu/bot_notes`.

Перейдите в каталог проекта:

```bash
cd /home/ubuntu/bot_notes   # или ваш путь
```

## 3. Виртуальное окружение и зависимости

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 4. Настройка токена

Создайте файл `.env` из примера и укажите токен:

```bash
cp .env.example .env
nano .env
```

Вставьте свой токен:

```
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHI...
```

Сохраните (Ctrl+O, Enter, Ctrl+X в nano). Убедитесь, что права на `.env` ограничены:

```bash
chmod 600 .env
```

**Альтернатива без .env:** экспортируйте переменную в текущей сессии или в systemd (см. п. 6):

```bash
export TELEGRAM_BOT_TOKEN="ваш_токен"
```

## 5. Проверочный запуск

```bash
source venv/bin/activate
python bot.py
```

В логах должно появиться «Бот запущен». Проверьте бота в Telegram. Остановка — Ctrl+C.

## 6. Запуск через systemd (постоянная работа)

Чтобы бот работал после выхода из SSH и перезагрузки сервера:

1. Создайте unit-файл:

   ```bash
   sudo nano /etc/systemd/system/telegram-notes-bot.service
   ```

2. Вставьте (подставьте свой путь и пользователя):

   ```ini
   [Unit]
   Description=Telegram Notes Bot
   After=network.target

   [Service]
   Type=simple
   User=ubuntu
   Group=ubuntu
   WorkingDirectory=/home/ubuntu/bot_notes
   EnvironmentFile=/home/ubuntu/bot_notes/.env
   ExecStart=/home/ubuntu/bot_notes/venv/bin/python bot.py
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   ```

   Если не используете `.env`, замените строку `EnvironmentFile=...` на:

   ```ini
   Environment=TELEGRAM_BOT_TOKEN=ваш_токен_здесь
   ```

3. Включите и запустите сервис:

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable telegram-notes-bot
   sudo systemctl start telegram-notes-bot
   sudo systemctl status telegram-notes-bot
   ```

4. Полезные команды:

   ```bash
   sudo systemctl stop telegram-notes-bot    # остановить
   sudo systemctl restart telegram-notes-bot # перезапустить
   journalctl -u telegram-notes-bot -f      # логи в реальном времени
   ```

## 7. База данных

Файл `notes_bot.db` создаётся в каталоге с `bot.py` при первом запуске. Для бэкапов достаточно копировать этот файл (остановив бота или в момент простоя):

```bash
cp /home/ubuntu/bot_notes/notes_bot.db /path/to/backup/notes_bot_$(date +%Y%m%d).db
```

## 8. Обновление бота

```bash
cd /home/ubuntu/bot_notes
source venv/bin/activate
git pull   # если используете git
pip install -r requirements.txt --upgrade
sudo systemctl restart telegram-notes-bot
```

## 9. Деплой через Docker и Docker Hub

Этот вариант удобен, если на сервере уже установлен Docker и вы хотите обновлять бота через Docker Hub.

### 9.1. Подготовка локально (ваш компьютер)

1. В корне проекта уже есть `Dockerfile` и `.dockerignore`.

2. Авторизуйтесь в Docker Hub:

```bash
docker login
```

3. Соберите образ и затегайте его своим именем:

```bash
docker build -t <dockerhub_user>/notes-bot:latest .
```

4. Запушьте образ:

```bash
docker push <dockerhub_user>/notes-bot:latest
```

### 9.2. Подготовка сервера (один раз)

1. На сервере создайте каталог для данных бота:

```bash
sudo mkdir -p /opt/notes-bot
sudo chown $(whoami):$(whoami) /opt/notes-bot
```

В этом каталоге будут храниться:

- `notes_bot.db` — база SQLite;
- каталог `output/` с экспортом `notes.csv`;
- файл `.env` с токеном.

2. Создайте `.env` с токеном бота (на сервере, в `/opt/notes-bot`):

```bash
cd /opt/notes-bot
nano .env
```

Содержимое:

```bash
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHI...
```

Сохраните файл и ограничьте права:

```bash
chmod 600 .env
```

> В контейнере бот читает токен через `python-dotenv` из файла `.env` в рабочем каталоге `/app`.

### 9.3. Первый запуск контейнера на сервере

1. На сервере подтяните образ:

```bash
docker pull <dockerhub_user>/notes-bot:latest
```

2. Запустите контейнер:

```bash
docker run -d \
  --name notes-bot \
  --restart unless-stopped \
  -v /opt/notes-bot:/app \
  <dockerhub_user>/notes-bot:latest
```

Пояснения:

- `--restart unless-stopped` — контейнер автоматически перезапустится после перезагрузки сервера или падения Docker;
- `-v /opt/notes-bot:/app` — внутрь контейнера монтируется каталог `/opt/notes-bot` как рабочая директория `/app`:
  - там лежит `.env` с токеном;
  - там будут храниться `notes_bot.db` и `output/` между перезапусками.

Логи контейнера:

```bash
docker logs -f notes-bot
```

Остановка и удаление контейнера:

```bash
docker stop notes-bot
docker rm notes-bot
```

## 10. Обновление бота через Docker Hub

Когда вы меняете код локально, процесс обновления такой:

### 10.1. На локальной машине

1. Обновите код (`git commit` и т.п.).
2. Соберите новый образ:

```bash
docker build -t <dockerhub_user>/notes-bot:latest .
```

3. Запушьте его:

```bash
docker push <dockerhub_user>/notes-bot:latest
```

### 10.2. На сервере

1. Подтяните свежий образ:

```bash
docker pull <dockerhub_user>/notes-bot:latest
```

2. Перезапустите контейнер с теми же параметрами (данные и `.env` уже лежат в `/opt/notes-bot`):

```bash
docker stop notes-bot
docker rm notes-bot

docker run -d \
  --name notes-bot \
  --restart unless-stopped \
  -v /opt/notes-bot:/app \
  <dockerhub_user>/notes-bot:latest
```

База `notes_bot.db` и файл `.env` не затрагиваются — они находятся на хосте.

## Возможные проблемы

- **Бот не отвечает** — проверьте:
  - что контейнер запущен: `docker ps`;
  - логи контейнера: `docker logs -n 100 notes-bot`;
  - токен в `/opt/notes-bot/.env`.
- **Нет сети у контейнера** — проверьте доступ сервера в интернет (ping, curl до `https://api.telegram.org`).
- **Permission denied / права на файлы** — убедитесь, что пользователь Docker имеет права на `/opt/notes-bot` и `.env` доступен для чтения внутри контейнера (можно проверить `docker exec -it notes-bot ls -la`).
- **ModuleNotFoundError при системном запуске (старый вариант без Docker)** — убедитесь, что в `ExecStart` указан Python из `venv`: `/path/to/bot_notes/venv/bin/python`.

