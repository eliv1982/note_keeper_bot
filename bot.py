#!/usr/bin/env python3
"""
Telegram-бот для управления заметками и категориями.
Стек: Python 3.9+, python-telegram-bot 13.x (синхронный), SQLite3.
"""

import logging
import os
import sys
import warnings
import csv
from datetime import datetime, timedelta

# Убираем предупреждения ptb 13.x и APScheduler (консоль и PowerShell не ругаются)
warnings.filterwarnings("ignore", message=".*upstream urllib3.*", category=UserWarning)
warnings.filterwarnings("ignore", message=".*pkg_resources is deprecated.*", category=UserWarning)

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple

from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    CallbackContext,
    Filters,
    CallbackQueryHandler,
)
from telegram.error import TimedOut

# --- Логирование ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- Метаданные бота ---
BOT_VERSION = "1.0.0 (2026-03-13)"


def build_start_text(first_name: Optional[str]) -> str:
    """Собрать текст приветствия и списка команд."""
    username = first_name or "пользователь"
    return (
        f"Привет, {username}!\n\n"
        "Я бот для хранения заметок по категориям. Выберите команду — я подскажу, что делать дальше.\n\n"
        "Команды:\n"
        "/newcategory — создать категорию\n"
        "/categories — список ваших категорий\n"
        "/add — добавить заметку в категорию\n"
        "/adddue — добавить заметку с напоминанием\n"
        "/get — показать заметки выбранной категории\n"
        "/delnote — удалить заметку\n"
        "/delcat — удалить категорию\n"
        "/cancel — отменить текущее действие\n"
        "/help — краткая справка по командам\n"
        "/version — версия бота\n"
    )

# --- Конфигурация БД ---
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "notes_bot.db"
OUTPUT_DIR = BASE_DIR / "output"
NOTES_EXPORT_PATH = OUTPUT_DIR / "notes.csv"


@contextmanager
def get_db_connection():
    """Контекстный менеджер для подключения к SQLite. Гарантирует закрытие соединения."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Создание таблиц при первом запуске."""
    try:
        OUTPUT_DIR.mkdir(exist_ok=True)
        with get_db_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    UNIQUE(user_id, name)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    due_at TIMESTAMP NULL,
                    remind_at TIMESTAMP NULL,
                    reminded INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (category_id) REFERENCES categories(id)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_categories_user ON categories(user_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_notes_category ON notes(category_id)"
            )
            # Альтеры на случай существующей таблицы без новых колонок (SQLite не поддерживает IF NOT EXISTS для столбцов)
            try:
                conn.execute("ALTER TABLE notes ADD COLUMN due_at TIMESTAMP NULL")
            except sqlite3.OperationalError:
                # Колонка уже существует
                pass
            try:
                conn.execute("ALTER TABLE notes ADD COLUMN remind_at TIMESTAMP NULL")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute(
                    "ALTER TABLE notes ADD COLUMN reminded INTEGER NOT NULL DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass
    except sqlite3.Error as e:
        logger.exception("Ошибка инициализации БД: %s", e)
        raise


# --- Функции работы с БД (с проверкой user_id) ---

def get_category_by_id_and_user(category_id: int, user_id: int) -> Optional[sqlite3.Row]:
    """Получить категорию по id и user_id. Защита от IDOR."""
    try:
        with get_db_connection() as conn:
            cur = conn.execute(
                "SELECT id, user_id, name FROM categories WHERE id = ? AND user_id = ?",
                (category_id, user_id),
            )
            return cur.fetchone()
    except sqlite3.Error as e:
        logger.exception("Ошибка при получении категории: %s", e)
        return None


def get_categories_by_user(user_id: int) -> List[Tuple[int, str]]:
    """Список категорий пользователя: [(id, name), ...]."""
    try:
        with get_db_connection() as conn:
            cur = conn.execute(
                "SELECT id, name FROM categories WHERE user_id = ? ORDER BY id",
                (user_id,),
            )
            return [(row["id"], row["name"]) for row in cur.fetchall()]
    except sqlite3.Error as e:
        logger.exception("Ошибка при получении списка категорий: %s", e)
        return []


def create_category(user_id: int, name: str) -> Tuple[Optional[int], Optional[str]]:
    """
    Создать категорию. Возвращает (id, None) при успехе или (None, сообщение_об_ошибке).
    """
    name = name.strip()
    if not name:
        return None, "Название категории не может быть пустым."
    try:
        with get_db_connection() as conn:
            cur = conn.execute(
                "INSERT INTO categories (user_id, name) VALUES (?, ?)",
                (user_id, name),
            )
            return cur.lastrowid, None
    except sqlite3.IntegrityError:
        return None, "Категория с таким названием уже существует."
    except sqlite3.Error as e:
        logger.exception("Ошибка при создании категории: %s", e)
        return None, "Не удалось создать категорию. Попробуйте позже."


def add_note(
    category_id: int,
    user_id: int,
    text: str,
    due_at_utc: Optional[str] = None,
    remind_at_utc: Optional[str] = None,
) -> Tuple[Optional[int], Optional[str]]:
    """
    Добавить заметку в категорию. Проверка прав по user_id через категорию.
    Возвращает (id_заметки, None) или (None, сообщение_об_ошибке).
    """
    cat = get_category_by_id_and_user(category_id, user_id)
    if not cat:
        return None, "Категория не найдена или у вас нет к ней доступа."
    text = text.strip()
    if not text:
        return None, "Текст заметки не может быть пустым."
    try:
        with get_db_connection() as conn:
            cur = conn.execute(
                "INSERT INTO notes (category_id, text, due_at, remind_at, reminded) "
                "VALUES (?, ?, ?, ?, 0)",
                (category_id, text, due_at_utc, remind_at_utc),
            )
            note_id = cur.lastrowid
        export_notes_to_csv()
        return note_id, None
    except sqlite3.Error as e:
        logger.exception("Ошибка при добавлении заметки: %s", e)
        return None, "Не удалось добавить заметку. Попробуйте позже."


def get_notes_by_category_and_user(
    category_id: int, user_id: int
) -> Tuple[Optional[List[Tuple[int, str, str]]], Optional[str]]:
    """
    Список заметок категории для пользователя. Проверка прав через категорию.
    Возвращает ([(id, text, created_iso)], None) или (None, сообщение_об_ошибке).
    """
    cat = get_category_by_id_and_user(category_id, user_id)
    if not cat:
        return None, "Категория не найдена или у вас нет к ней доступа."
    try:
        with get_db_connection() as conn:
            cur = conn.execute(
                "SELECT id, text, created FROM notes WHERE category_id = ? ORDER BY id",
                (category_id,),
            )
            rows = [(r["id"], r["text"], r["created"]) for r in cur.fetchall()]
            return rows, None
    except sqlite3.Error as e:
        logger.exception("Ошибка при получении заметок: %s", e)
        return None, "Не удалось загрузить заметки. Попробуйте позже."


def get_note_by_id_and_user(
    category_id: int, note_id: int, user_id: int
) -> Optional[sqlite3.Row]:
    """Получить заметку по id в категории с проверкой прав (категория принадлежит user_id)."""
    cat = get_category_by_id_and_user(category_id, user_id)
    if not cat:
        return None
    try:
        with get_db_connection() as conn:
            cur = conn.execute(
                "SELECT id, category_id, text, created FROM notes WHERE id = ? AND category_id = ?",
                (note_id, category_id),
            )
            return cur.fetchone()
    except sqlite3.Error as e:
        logger.exception("Ошибка при получении заметки: %s", e)
        return None


def delete_note(
    category_id: int, note_id: int, user_id: int
) -> Tuple[bool, Optional[str]]:
    """
    Удалить заметку. Проверка прав через категорию.
    Возвращает (True, None) при успехе или (False, сообщение_об_ошибке).
    """
    note = get_note_by_id_and_user(category_id, note_id, user_id)
    if not note:
        return False, "Заметка не найдена или у вас нет к ней доступа."
    try:
        with get_db_connection() as conn:
            conn.execute(
                "DELETE FROM notes WHERE id = ? AND category_id = ?",
                (note_id, category_id),
            )
        export_notes_to_csv()
        return True, None
    except sqlite3.Error as e:
        logger.exception("Ошибка при удалении заметки: %s", e)
        return False, "Не удалось удалить заметку. Попробуйте позже."


def delete_category(user_id: int, category_id: int) -> Tuple[bool, Optional[str]]:
    """
    Удалить категорию пользователя и все её заметки.
    Возвращает (True, None) при успехе или (False, сообщение_об_ошибке).
    """
    cat = get_category_by_id_and_user(category_id, user_id)
    if not cat:
        return False, "Категория не найдена или у вас нет к ней доступа."
    try:
        with get_db_connection() as conn:
            conn.execute("DELETE FROM notes WHERE category_id = ?", (category_id,))
            conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        export_notes_to_csv()
        return True, None
    except sqlite3.Error as e:
        logger.exception("Ошибка при удалении категории: %s", e)
        return False, "Не удалось удалить категорию. Попробуйте позже."


def export_notes_to_csv() -> None:
    """
    Экспорт всех заметок в CSV-файл с UTF-8 (поддержка кириллицы).
    Формат: id_заметки,user_id,id_категории,название_категории,текст,создано_мск.
    """
    try:
        OUTPUT_DIR.mkdir(exist_ok=True)
        with get_db_connection() as conn:
            cur = conn.execute(
                """
                SELECT n.id,
                       c.user_id,
                       c.id AS category_id,
                       c.name AS category_name,
                       n.text,
                       n.created,
                       n.due_at,
                       n.remind_at,
                       n.reminded
                FROM notes n
                JOIN categories c ON n.category_id = c.id
                ORDER BY c.user_id, c.id, n.id
                """
            )
            rows = cur.fetchall()

        with NOTES_EXPORT_PATH.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(
                [
                    "id_заметки",
                    "user_id",
                    "id_категории",
                    "название_категории",
                    "текст",
                    "создано_мск",
                    "срок_мск",
                    "напомнить_в_мск",
                    "напоминание_отправлено",
                ]
            )
            for r in rows:
                created_msk = format_created(r["created"])
                due_msk = format_created(r["due_at"]) if r["due_at"] else ""
                remind_msk = format_created(r["remind_at"]) if r["remind_at"] else ""
                writer.writerow(
                    [
                        r["id"],
                        r["user_id"],
                        r["category_id"],
                        r["category_name"],
                        r["text"],
                        created_msk,
                        due_msk,
                        remind_msk,
                        r["reminded"],
                    ]
                )
    except Exception as e:  # noqa: BLE001
        # Экспорт не критичен для работы бота
        logger.exception("Ошибка при экспорте заметок в CSV: %s", e)


def format_created(created: str) -> str:
    """Преобразовать timestamp (UTC) в московское время ДД-ММ-ГГГГ ЧЧ:ММ."""
    try:
        # SQLite CURRENT_TIMESTAMP даёт 'YYYY-MM-DD HH:MM:SS' в UTC.
        s = created.strip().replace("T", " ").replace("Z", "")
        dt = datetime.fromisoformat(s)
        dt_msk = dt + timedelta(hours=3)
        return dt_msk.strftime("%d-%m-%Y %H:%M")
    except (ValueError, TypeError):
        return created


def parse_due_datetime_to_utc(date_str: str, time_str: str) -> Optional[str]:
    """
    Преобразовать строку даты и времени из формата ДД.ММ.ГГГГ ЧЧ:ММ (московское время)
    в строку UTC для хранения в БД.
    """
    try:
        dt_local = datetime.strptime(
            f"{date_str.strip()} {time_str.strip()}", "%d.%m.%Y %H:%M"
        )
        dt_utc = dt_local - timedelta(hours=3)
        return dt_utc.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def format_categories_list(cats: List[Tuple[int, str]]) -> str:
    """Форматирует список категорий для вывода пользователю с локальной нумерацией 1..N."""
    if not cats:
        return "У вас пока нет категорий."
    return "Ваши категории:\n" + "\n".join(
        f"{idx}. {name}" for idx, (_, name) in enumerate(cats, start=1)
    )


def build_categories_keyboard(
    cats: List[Tuple[int, str]], action: str
) -> InlineKeyboardMarkup:
    """
    Построить инлайн-клавиатуру для списка категорий.
    action определяет, что делать при выборе:
    - 'get'  -> показать заметки категории
    - 'delcat' -> удалить категорию
    """
    buttons: List[List[InlineKeyboardButton]] = []
    for idx, (cid, name) in enumerate(cats, start=1):
        text = f"{idx}. {name}"
        callback_data = f"{action}:{cid}"
        buttons.append([InlineKeyboardButton(text, callback_data=callback_data)])
    return InlineKeyboardMarkup(buttons)


def build_notes_keyboard(
    rows: List[Tuple[int, str, str]], category_id: int, action: str
) -> InlineKeyboardMarkup:
    """
    Инлайн-клавиатура для списка заметок в категории.
    action: 'delnote' -> удалить заметку.
    """
    buttons: List[List[InlineKeyboardButton]] = []
    for idx, (nid, note_text, created) in enumerate(rows, start=1):
        short = note_text[:40] + ("…" if len(note_text) > 40 else "")
        text = f"[{idx}] {short}"
        callback_data = f"{action}:{category_id}:{nid}"
        buttons.append([InlineKeyboardButton(text, callback_data=callback_data)])
    return InlineKeyboardMarkup(buttons)


# --- Состояния диалога (пошаговый ввод) ---
STATE_NEWCATEGORY = "newcategory_name"
STATE_GET_CATEGORY = "get_category"
STATE_ADD_INPUT = "add_input"
STATE_ADD_TEXT = "add_text"
STATE_ADD_DUE_INPUT = "add_due_input"
STATE_DELNOTE_CATEGORY = "delnote_category"
STATE_DELNOTE_NOTE = "delnote_note"
STATE_DELCAT = "delcat_category"

# Состояние храним в модульном словаре (context.user_data в ptb 13.x может не сохраняться между обновлениями)
# Структура по user_id:
# {
#   "state": <STATE_*>,
#   "category_id": int | None,
#   "note_id_map": {порядковый_номер_в_категории: реальный_id_заметки}  # для удаления по локальной нумерации
# }
USER_STATES: Dict[int, Dict[str, Any]] = {}


def clear_state(context: CallbackContext, user_id: Optional[int] = None) -> None:
    """Сбросить состояние пользователя."""
    if user_id is not None:
        _clear_user_state(user_id)
    if context.user_data:
        context.user_data.pop("state", None)
        context.user_data.pop("category_id", None)


def _get_state(user_id: int) -> Optional[str]:
    return USER_STATES.get(user_id, {}).get("state")


def _set_state(user_id: int, state: str, category_id: Optional[int] = None) -> None:
    USER_STATES[user_id] = {"state": state}
    if category_id is not None:
        USER_STATES[user_id]["category_id"] = category_id


def _clear_user_state(user_id: int) -> None:
    USER_STATES.pop(user_id, None)


def _parse_category_or_note_number(raw: str) -> Optional[int]:
    """Извлекает число из строки, допускает точку после числа (например '2.' -> 2)."""
    if not raw:
        return None
    s = raw.strip().rstrip(".")
    if not s.isdigit():
        return None
    return int(s)


# --- Обработчики команд ---

def cmd_start(update: Update, context: CallbackContext) -> None:
    """Команда /start."""
    clear_state(context, update.effective_user.id)
    user = update.effective_user
    text = build_start_text(user.first_name)
    update.message.reply_text(text)


def cmd_help(update: Update, context: CallbackContext) -> None:
    """Команда /help — краткая справка по командам."""
    clear_state(context, update.effective_user.id)
    user = update.effective_user
    text = (
        "Краткая справка по командам:\n\n"
        "/newcategory — создать категорию\n"
        "/categories — список ваших категорий\n"
        "/add — добавить заметку в категорию\n"
        "/adddue — добавить заметку с напоминанием\n"
        "/get — показать заметки выбранной категории\n"
        "/delnote — удалить заметку\n"
        "/delcat — удалить категорию\n"
        "/cancel — отменить текущее действие\n\n"
        "Если запутались, всегда можно набрать /start — там расширенное приветствие.\n"
    )
    update.message.reply_text(text)


def cmd_version(update: Update, context: CallbackContext) -> None:
    """Команда /version — информация о версии бота."""
    clear_state(context, update.effective_user.id)
    update.message.reply_text(f"Версия бота: {BOT_VERSION}")


def _show_notes_for_category(
    chat_id: int, user_id: int, category_id: int, context: CallbackContext
) -> None:
    """Показать заметки категории с локальной нумерацией."""
    rows, err = get_notes_by_category_and_user(category_id, user_id)
    bot = context.bot
    if err:
        bot.send_message(chat_id=chat_id, text=err)
        return
    if not rows:
        bot.send_message(chat_id=chat_id, text="В этой категории пока нет заметок.")
        return
    lines = [
        f"[{idx}] {format_created(created)}\n{note_text}"
        for idx, (nid, note_text, created) in enumerate(rows, start=1)
    ]
    bot.send_message(chat_id=chat_id, text="\n\n—\n\n".join(lines))


def cb_get_category(update: Update, context: CallbackContext) -> None:
    """Обработка выбора категории через кнопку для просмотра заметок."""
    query = update.callback_query
    assert query is not None  # для type checker
    query.answer()
    user_id = query.from_user.id
    chat_id = query.message.chat.id
    data = query.data or ""
    try:
        _, raw_id = data.split(":", 1)
        category_id = int(raw_id)
    except (ValueError, IndexError):
        query.edit_message_text("Не удалось распознать выбранную категорию.")
        return
    clear_state(context, user_id)
    _clear_user_state(user_id)
    _show_notes_for_category(chat_id, user_id, category_id, context)


def cb_delnote_category(update: Update, context: CallbackContext) -> None:
    """Выбор категории для удаления заметки (первый шаг через кнопку)."""
    query = update.callback_query
    assert query is not None
    query.answer()
    user_id = query.from_user.id
    chat_id = query.message.chat.id
    data = query.data or ""
    try:
        _, raw_id = data.split(":", 1)
        category_id = int(raw_id)
    except (ValueError, IndexError):
        query.edit_message_text("Не удалось распознать выбранную категорию для удаления заметки.")
        return

    rows, err = get_notes_by_category_and_user(category_id, user_id)
    if err:
        context.bot.send_message(chat_id=chat_id, text=err)
        return
    if not rows:
        context.bot.send_message(chat_id=chat_id, text="В этой категории нет заметок.")
        return

    lines = [
        f"[{idx}] {format_created(created)} — {note_text[:50]}{'…' if len(note_text) > 50 else ''}"
        for idx, (nid, note_text, created) in enumerate(rows, start=1)
    ]
    context.bot.send_message(
        chat_id=chat_id,
        text="Заметки в этой категории:\n"
        + "\n".join(lines)
        + "\n\nВыберите заметку для удаления кнопкой:",
        reply_markup=build_notes_keyboard(rows, category_id, action="delnote"),
    )


def cb_delcat_category(update: Update, context: CallbackContext) -> None:
    """Обработка выбора категории через кнопку для удаления категории."""
    query = update.callback_query
    assert query is not None
    query.answer()
    user_id = query.from_user.id
    chat_id = query.message.chat.id
    data = query.data or ""
    try:
        _, raw_id = data.split(":", 1)
        category_id = int(raw_id)
    except (ValueError, IndexError):
        query.edit_message_text("Не удалось распознать выбранную категорию для удаления.")
        return
    clear_state(context, user_id)
    _clear_user_state(user_id)
    ok, err = delete_category(user_id, category_id)
    if err:
        context.bot.send_message(chat_id=chat_id, text=err)
        return
    context.bot.send_message(chat_id=chat_id, text="Категория и все её заметки удалены.")


def cb_add_category(update: Update, context: CallbackContext) -> None:
    """Выбор категории для добавления заметки через кнопку."""
    query = update.callback_query
    assert query is not None
    query.answer()
    user_id = query.from_user.id
    data = query.data or ""
    try:
        _, raw_id = data.split(":", 1)
        category_id = int(raw_id)
    except (ValueError, IndexError):
        query.edit_message_text("Не удалось распознать выбранную категорию для добавления заметки.")
        return
    # Запоминаем, что дальше ждём только текст заметки для этой категории
    _set_state(user_id, STATE_ADD_TEXT, category_id=category_id)
    query.edit_message_text(
        "Категория выбрана. Теперь отправьте текст заметки одним сообщением.\n"
        "Если передумали, используйте /cancel."
    )


def cmd_cancel(update: Update, context: CallbackContext) -> None:
    """Команда /cancel — сброс состояния."""
    clear_state(context, update.effective_user.id)
    update.message.reply_text("Действие отменено. Используйте команды из /start.")


def cmd_newcategory(update: Update, context: CallbackContext) -> None:
    """Команда /newcategory — запрос названия категории."""
    user_id = update.effective_user.id
    clear_state(context, user_id)
    _set_state(user_id, STATE_NEWCATEGORY)
    update.message.reply_text("Введите название новой категории:")


def cmd_categories(update: Update, context: CallbackContext) -> None:
    """Команда /categories — только список категорий."""
    user_id = update.effective_user.id
    clear_state(context, user_id)
    cats = get_categories_by_user(user_id)
    update.message.reply_text(format_categories_list(cats))


def cmd_add(update: Update, context: CallbackContext) -> None:
    """Команда /add — запрос номера категории и текста заметки."""
    user_id = update.effective_user.id
    clear_state(context, user_id)
    cats = get_categories_by_user(user_id)
    if not cats:
        update.message.reply_text("Сначала создайте категорию: /newcategory")
        return
    _set_state(user_id, STATE_ADD_INPUT)
    update.message.reply_text(
        format_categories_list(cats) + "\n\n"
        "Выберите категорию кнопкой ниже или введите номер по списку и текст заметки "
        "(например: 2 Купить молоко):",
        reply_markup=build_categories_keyboard(cats, action="add"),
    )


def cmd_adddue(update: Update, context: CallbackContext) -> None:
    """Команда /adddue — добавить заметку с напоминанием (дата и время)."""
    user_id = update.effective_user.id
    clear_state(context, user_id)
    cats = get_categories_by_user(user_id)
    if not cats:
        update.message.reply_text("У вас пока нет категорий. Сначала создайте категорию: /newcategory")
        return
    _set_state(user_id, STATE_ADD_DUE_INPUT)
    update.message.reply_text(
        format_categories_list(cats) + "\n\n"
        "Введите номер категории, дату, время и текст заметки в формате:\n"
        "2 20.03.2026 19:00 Купить подарок маме\n\n"
        "Дата и время указываются по московскому времени.",
    )


def cmd_get(update: Update, context: CallbackContext) -> None:
    """Команда /get — запрос номера категории для просмотра заметок."""
    user_id = update.effective_user.id
    clear_state(context, user_id)
    cats = get_categories_by_user(user_id)
    if not cats:
        update.message.reply_text("У вас пока нет категорий. Создайте: /newcategory")
        return
    _set_state(user_id, STATE_GET_CATEGORY)
    update.message.reply_text(
        format_categories_list(cats) + "\n\n"
        "Введите номер категории по списку или нажмите кнопку, "
        "чтобы увидеть её заметки:",
        reply_markup=build_categories_keyboard(cats, action="get"),
    )


def cmd_delnote(update: Update, context: CallbackContext) -> None:
    """Команда /delnote — пошагово: категория, затем номер заметки."""
    user_id = update.effective_user.id
    clear_state(context, user_id)
    cats = get_categories_by_user(user_id)
    if not cats:
        update.message.reply_text("У вас пока нет категорий. Создайте: /newcategory")
        return
    _set_state(user_id, STATE_DELNOTE_CATEGORY)
    update.message.reply_text(
        format_categories_list(cats) + "\n\n"
        "Введите номер категории по списку, из которой нужно удалить заметку, "
        "или выберите категорию кнопкой:",
        reply_markup=build_categories_keyboard(cats, action="delnote_cat"),
    )


def cmd_delcat(update: Update, context: CallbackContext) -> None:
    """Команда /delcat — выбрать категорию для удаления."""
    user_id = update.effective_user.id
    clear_state(context, user_id)
    cats = get_categories_by_user(user_id)
    if not cats:
        update.message.reply_text("У вас пока нет категорий.")
        return
    _set_state(user_id, STATE_DELCAT)
    update.message.reply_text(
        format_categories_list(cats) + "\n\n"
        "Введите номер категории по списку или нажмите кнопку, "
        "чтобы удалить категорию:",
        reply_markup=build_categories_keyboard(cats, action="delcat"),
    )


def handle_text(update: Update, context: CallbackContext) -> None:
    """Обработка текстового ввода в зависимости от состояния."""
    user_id = update.effective_user.id
    state = _get_state(user_id)
    text = (update.message.text or "").strip()

    if not state:
        update.message.reply_text("Используйте команду из меню (например /start).")
        return

    if state == STATE_NEWCATEGORY:
        _clear_user_state(user_id)
        clear_state(context, user_id)
        if not text:
            update.message.reply_text("Название не может быть пустым. Попробуйте снова или /cancel.")
            return
        _, err = create_category(user_id, text)
        if err:
            update.message.reply_text(err)
            return
        update.message.reply_text("Категория создана.")

    elif state == STATE_GET_CATEGORY:
        _clear_user_state(user_id)
        clear_state(context, user_id)
        idx = _parse_category_or_note_number(text)
        if idx is None:
            update.message.reply_text("Введите номер категории по списку (1, 2, 3...) или воспользуйтесь кнопками.")
            return
        cats = get_categories_by_user(user_id)
        if not cats or idx < 1 or idx > len(cats):
            update.message.reply_text("Категории с таким номером нет в списке. Посмотрите список ещё раз: /categories")
            return
        category_id = cats[idx - 1][0]
        rows, err = get_notes_by_category_and_user(category_id, user_id)
        if err:
            update.message.reply_text(err)
            return
        if not rows:
            update.message.reply_text("В этой категории пока нет заметок.")
            return
        # Локальная нумерация заметок в категории с 1
        lines = [
            f"[{idx}] {format_created(created)}\n{note_text}"
            for idx, (nid, note_text, created) in enumerate(rows, start=1)
        ]
        update.message.reply_text("\n\n—\n\n".join(lines))

    elif state == STATE_ADD_INPUT:
        parts = text.split(None, 1)
        if len(parts) < 2:
            update.message.reply_text("Введите номер категории и текст заметки (например: 2 Купить молоко или 2. Купить молоко).")
            return
        raw_id, note_text = parts[0], parts[1]
        idx = _parse_category_or_note_number(raw_id)
        if idx is None:
            update.message.reply_text("Номер категории должен быть числом по списку (например 1, 2, 3).")
            return
        cats = get_categories_by_user(user_id)
        if not cats or idx < 1 or idx > len(cats):
            update.message.reply_text("Категории с таким номером нет в списке. Посмотрите список ещё раз: /categories")
            return
        category_id = cats[idx - 1][0]
        _clear_user_state(user_id)
        clear_state(context, user_id)
        _, err = add_note(category_id, user_id, note_text)
        if err:
            update.message.reply_text(err)
            return
        update.message.reply_text("Заметка добавлена.")

    elif state == STATE_ADD_TEXT:
        # Текст заметки для категории, выбранной кнопкой
        user_state = USER_STATES.get(user_id, {})
        category_id = user_state.get("category_id")
        if category_id is None:
            _clear_user_state(user_id)
            clear_state(context, user_id)
            update.message.reply_text("Категория не найдена. Начните заново: /add")
            return
        note_text = text
        if not note_text:
            update.message.reply_text("Текст заметки не может быть пустым. Введите текст или /cancel.")
            return
        _, err = add_note(category_id, user_id, note_text)
        _clear_user_state(user_id)
        clear_state(context, user_id)
        if err:
            update.message.reply_text(err)
            return
        update.message.reply_text("Заметка добавлена.")

    elif state == STATE_ADD_DUE_INPUT:
        # Ожидаем: <номер_категории> <ДД.ММ.ГГГГ> <ЧЧ:ММ> <текст>
        parts = text.split(None, 3)
        if len(parts) < 4:
            update.message.reply_text(
                "Введите номер категории, дату, время и текст заметки в формате:\n"
                "2 20.03.2026 19:00 Купить подарок маме"
            )
            return
        raw_idx, date_str, time_str, note_text = parts
        idx = _parse_category_or_note_number(raw_idx)
        if idx is None:
            update.message.reply_text("Номер категории должен быть числом по списку (например 1, 2, 3).")
            return
        cats = get_categories_by_user(user_id)
        if not cats or idx < 1 or idx > len(cats):
            update.message.reply_text("Категории с таким номером нет в списке. Посмотрите список ещё раз: /categories")
            return
        due_utc = parse_due_datetime_to_utc(date_str, time_str)
        if due_utc is None:
            update.message.reply_text(
                "Не удалось разобрать дату и время. Используйте формат ДД.ММ.ГГГГ ЧЧ:ММ,\n"
                "например: 20.03.2026 19:00"
            )
            return
        # Напоминание за 1 час до срока (в московском времени), пересчитанное в UTC
        try:
            due_dt_utc = datetime.fromisoformat(due_utc)
            remind_dt_utc = due_dt_utc - timedelta(hours=1)
            remind_utc = remind_dt_utc.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            remind_utc = None
        category_id = cats[idx - 1][0]
        _clear_user_state(user_id)
        clear_state(context, user_id)
        _, err = add_note(
            category_id,
            user_id,
            note_text,
            due_at_utc=due_utc,
            remind_at_utc=remind_utc,
        )
        if err:
            update.message.reply_text(err)
            return
        update.message.reply_text("Заметка с напоминанием добавлена.")

    elif state == STATE_DELCAT:
        idx = _parse_category_or_note_number(text)
        if idx is None:
            update.message.reply_text("Введите номер категории по списку (1, 2, 3...).")
            return
        cats = get_categories_by_user(user_id)
        if not cats or idx < 1 or idx > len(cats):
            update.message.reply_text("Категории с таким номером нет в списке. Посмотрите список ещё раз: /categories")
            return
        category_id = cats[idx - 1][0]
        ok, err = delete_category(user_id, category_id)
        _clear_user_state(user_id)
        clear_state(context, user_id)
        if err:
            update.message.reply_text(err)
            return
        update.message.reply_text("Категория и все её заметки удалены.")

    elif state == STATE_DELNOTE_CATEGORY:
        idx = _parse_category_or_note_number(text)
        if idx is None:
            update.message.reply_text("Введите номер категории по списку (1, 2, 3...).")
            return
        cats = get_categories_by_user(user_id)
        if not cats or idx < 1 or idx > len(cats):
            update.message.reply_text("Категории с таким номером нет в списке. Посмотрите список ещё раз: /categories")
            return
        category_id = cats[idx - 1][0]
        cat = get_category_by_id_and_user(category_id, user_id)
        if not cat:
            update.message.reply_text("Категория не найдена или нет доступа. Введите номер из списка выше.")
            return
        rows, err = get_notes_by_category_and_user(category_id, user_id)
        if err:
            _clear_user_state(user_id)
            clear_state(context, user_id)
            update.message.reply_text(err)
            return
        if not rows:
            _clear_user_state(user_id)
            clear_state(context, user_id)
            update.message.reply_text("В этой категории нет заметок.")
            return
        # Сохраняем локальную нумерацию заметок в категории:
        # 1 -> real_id_заметки, 2 -> ...
        _set_state(user_id, STATE_DELNOTE_NOTE, category_id=category_id)
        USER_STATES[user_id]["note_id_map"] = {
            idx: nid for idx, (nid, note_text, created) in enumerate(rows, start=1)
        }
        lines = [
            f"[{idx}] {format_created(created)} — {note_text[:50]}{'…' if len(note_text) > 50 else ''}"
            for idx, (nid, note_text, created) in enumerate(rows, start=1)
        ]
        update.message.reply_text(
            "Заметки в этой категории:\n" + "\n".join(lines) + "\n\nВведите номер заметки для удаления:"
        )

    elif state == STATE_DELNOTE_NOTE:
        note_local_number = _parse_category_or_note_number(text)
        if note_local_number is None:
            update.message.reply_text("Введите номер заметки числом (можно с точкой).")
            return
        # Достаём сохранённую категорию и карту локальных номеров заметок
        user_state = USER_STATES.get(user_id, {})
        category_id = user_state.get("category_id")
        note_id_map = user_state.get("note_id_map") or {}
        real_note_id = note_id_map.get(note_local_number)
        _clear_user_state(user_id)
        clear_state(context, user_id)
        if category_id is None:
            update.message.reply_text("Сессия сброшена. Начните заново: /delnote")
            return
        if real_note_id is None:
            update.message.reply_text("Заметка с таким номером не найдена в текущем списке. Попробуйте снова: /delnote")
            return
        ok, err = delete_note(category_id, real_note_id, user_id)
        if err:
            update.message.reply_text(err)
            return
        update.message.reply_text("Заметка удалена.")


def error_handler(update: Optional[Update], context: CallbackContext) -> None:
    """Глобальный обработчик ошибок."""
    # Временные сетевые таймауты Telegram — нормальная ситуация, просто логируем покороче.
    if isinstance(context.error, TimedOut):
        logger.warning("Сетевой таймаут при обработке update %s: %s", update, context.error)
        return
    logger.exception("Исключение при обработке update %s: %s", update, context.error)


def check_due_notes_job(context: CallbackContext) -> None:
    """
    Периодическая задача: ищет заметки, для которых пора прислать напоминание.
    Использует поле due_at (UTC) и флаг reminded.
    """
    now_utc = datetime.utcnow()
    now_utc_str = now_utc.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db_connection() as conn:
            cur = conn.execute(
                """
                SELECT n.id,
                       n.category_id,
                       n.text,
                       n.due_at,
                       n.remind_at,
                       c.user_id,
                       c.name AS category_name
                FROM notes n
                JOIN categories c ON n.category_id = c.id
                WHERE n.remind_at IS NOT NULL
                  AND n.reminded = 0
                  AND n.remind_at <= ?
                """,
                (now_utc_str,),
            )
            rows = cur.fetchall()
            note_ids_to_mark: List[int] = [r["id"] for r in rows]
            for r in rows:
                user_id = r["user_id"]
                category_name = r["category_name"]
                text = r["text"]
                due_msk = format_created(r["due_at"]) if r["due_at"] else "не указан"
                message = (
                    f"Напоминание по категории «{category_name}».\n"
                    f"Срок: {due_msk}\n\n"
                    f"{text}"
                )
                context.bot.send_message(chat_id=user_id, text=message)

            if note_ids_to_mark:
                conn.executemany(
                    "UPDATE notes SET reminded = 1 WHERE id = ?",
                    [(nid,) for nid in note_ids_to_mark],
                )
    except Exception as e:  # noqa: BLE001
        logger.exception("Ошибка при выполнении задачи напоминаний: %s", e)


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
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

    # Обработчики инлайн-кнопок по категориям
    dp.add_handler(CallbackQueryHandler(cb_get_category, pattern=r"^get:\d+$"))
    dp.add_handler(CallbackQueryHandler(cb_delcat_category, pattern=r"^delcat:\d+$"))

    dp.add_error_handler(error_handler)

    logger.info("Бот запущен, версия %s", BOT_VERSION)
    # Периодическая задача проверки напоминаний
    job_queue = updater.job_queue
    job_queue.run_repeating(check_due_notes_job, interval=60, first=10)

    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
