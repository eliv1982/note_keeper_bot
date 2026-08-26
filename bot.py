#!/usr/bin/env python3
"""
Telegram-бот для управления заметками и категориями.
Стек: Python 3.9+, python-telegram-bot 13.x (синхронный), SQLite3.

Этот модуль — composition root: сборка Updater/Dispatcher, регистрация
обработчиков (handlers.py) и периодической задачи напоминаний (reminders.py).
"""

import logging
import os
import sys
import warnings

# Убираем предупреждения ptb 13.x и APScheduler (консоль и PowerShell не ругаются)
warnings.filterwarnings("ignore", message=".*upstream urllib3.*", category=UserWarning)
warnings.filterwarnings("ignore", message=".*pkg_resources is deprecated.*", category=UserWarning)

from dotenv import load_dotenv

from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackQueryHandler,
)

from db import init_db
from handlers import (
    BOT_VERSION,
    cmd_start,
    cmd_help,
    cmd_version,
    cmd_cancel,
    cmd_newcategory,
    cmd_categories,
    cmd_add,
    cmd_adddue,
    cmd_get,
    cmd_delnote,
    cmd_delcat,
    cmd_change,
    handle_text,
    cb_get_category,
    cb_delcat_category,
    cb_add_category,
    cb_adddue_category,
    cb_delnote_category,
    cb_delnote_note,
    cb_change_category,
    cb_change_note,
    error_handler,
)
from reminders import check_due_notes_job

# --- Логирование ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def get_token() -> str:
    """Получить токен бота из переменной окружения TELEGRAM_BOT_TOKEN."""
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logger.error(
            "Не задан TELEGRAM_BOT_TOKEN. "
            "Установите переменную окружения или создайте файл .env (см. .env.example)."
        )
        sys.exit(1)
    return token


def main() -> None:
    token = get_token()
    init_db()
    updater = Updater(token=token, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", cmd_start))
    dp.add_handler(CommandHandler("help", cmd_help))
    dp.add_handler(CommandHandler("version", cmd_version))
    dp.add_handler(CommandHandler("cancel", cmd_cancel))
    dp.add_handler(CommandHandler("newcategory", cmd_newcategory))
    dp.add_handler(CommandHandler("categories", cmd_categories))
    dp.add_handler(CommandHandler("add", cmd_add))
    dp.add_handler(CommandHandler("adddue", cmd_adddue))
    dp.add_handler(CommandHandler("get", cmd_get))
    dp.add_handler(CommandHandler("delnote", cmd_delnote))
    dp.add_handler(CommandHandler("delcat", cmd_delcat))
    dp.add_handler(CommandHandler("change", cmd_change))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

    # Обработчики инлайн-кнопок по категориям и заметкам
    dp.add_handler(CallbackQueryHandler(cb_get_category, pattern=r"^get:\d+$"))
    dp.add_handler(CallbackQueryHandler(cb_delcat_category, pattern=r"^delcat:\d+$"))
    dp.add_handler(CallbackQueryHandler(cb_add_category, pattern=r"^add:\d+$"))
    dp.add_handler(CallbackQueryHandler(cb_adddue_category, pattern=r"^adddue:\d+$"))
    dp.add_handler(CallbackQueryHandler(cb_delnote_category, pattern=r"^delnote_cat:\d+$"))
    dp.add_handler(CallbackQueryHandler(cb_delnote_note, pattern=r"^delnote:\d+:\d+$"))
    dp.add_handler(CallbackQueryHandler(cb_change_category, pattern=r"^change_cat:\d+$"))
    dp.add_handler(CallbackQueryHandler(cb_change_note, pattern=r"^change_note:\d+:\d+$"))

    dp.add_error_handler(error_handler)

    logger.info("Бот запущен, версия %s", BOT_VERSION)
    # Периодическая задача проверки напоминаний
    job_queue = updater.job_queue
    job_queue.run_repeating(check_due_notes_job, interval=60, first=10)

    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
