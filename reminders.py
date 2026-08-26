"""Периодическая задача проверки и отправки напоминаний по заметкам."""

import logging
from datetime import datetime, timezone
from typing import List

from telegram.ext import CallbackContext

from db import get_db_connection
from parsing import format_created

logger = logging.getLogger(__name__)


def check_due_notes_job(context: CallbackContext) -> None:
    """
    Периодическая задача: ищет заметки, для которых пора прислать напоминание.
    Использует поле due_at (UTC) и флаг reminded.
    """
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
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
