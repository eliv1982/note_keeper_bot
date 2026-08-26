"""Слой доступа к данным: SQLite-схема, CRUD категорий/заметок (с проверкой user_id) и CSV-экспорт."""

import csv
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Tuple

from parsing import format_created

logger = logging.getLogger(__name__)

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


def update_note(
    category_id: int,
    note_id: int,
    user_id: int,
    new_text: str,
    due_at_utc: Optional[str] = None,
    remind_at_utc: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Обновить текст заметки и/или напоминание. Проверка прав через категорию.
    Если due_at_utc и remind_at_utc оба None — убрать напоминание (бессрочная заметка).
    Возвращает (True, None) при успехе или (False, сообщение_об_ошибке).
    """
    note = get_note_by_id_and_user(category_id, note_id, user_id)
    if not note:
        return False, "Заметка не найдена или у вас нет к ней доступа."
    new_text = new_text.strip()
    if not new_text:
        return False, "Текст заметки не может быть пустым."
    try:
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE notes SET text = ?, due_at = ?, remind_at = ?, reminded = 0 "
                "WHERE id = ? AND category_id = ?",
                (new_text, due_at_utc, remind_at_utc, note_id, category_id),
            )
        export_notes_to_csv()
        return True, None
    except sqlite3.Error as e:
        logger.exception("Ошибка при обновлении заметки: %s", e)
        return False, "Не удалось обновить заметку. Попробуйте позже."


def delete_note(
    category_id: int, note_id: int, user_id: int
) -> Tuple[bool, Optional[str]]:
    """
    Удалить заметку. Проверка прав через категорию.
    Если заметка уже удалена (например повторный callback), возвращает (True, None) — идемпотентность.
    Возвращает (True, None) при успехе или (False, сообщение_об_ошибке).
    """
    note = get_note_by_id_and_user(category_id, note_id, user_id)
    if not note:
        return True, None  # уже удалена — считаем успехом, чтобы не слать лишнее сообщение об ошибке
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
