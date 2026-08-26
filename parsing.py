"""Разбор даты/времени напоминаний и форматирование меток времени (МСК)."""

import re
from datetime import datetime, timedelta, time, timezone
from typing import Optional, Tuple

# Московское время для расчёта "сегодня"
MSK_UTC_OFFSET = timedelta(hours=3)


def _next_weekday(start: datetime, weekday: int) -> datetime:
    """Ближайший день недели (0=пн, 4=пт). Возвращает datetime в тот день с тем же временем, что start."""
    d = start.date()
    current = d.weekday()
    if current == weekday:
        return start
    days_ahead = (weekday - current) % 7
    if days_ahead == 0:
        days_ahead = 7
    next_d = d + timedelta(days=days_ahead)
    return datetime.combine(next_d, start.time())


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


def parse_due_message(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Извлечь из строки дату/время (МСК) и текст заметки.
    Поддерживает:
    - ДД.ММ.ГГГГ и ЧЧ:ММ в любом месте строки (остальное — текст заметки);
    - сегодня/завтра/послезавтра + время;
    - в пятницу + время (ближайшая пятница).
    Возвращает (due_utc_str, note_text) или (None, None) при ошибке.
    """
    text = text.strip()
    if not text:
        return None, None

    # 1) Ищем явную дату ДД.ММ.ГГГГ и время ЧЧ:ММ или Ч:ММ
    date_match = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", text)
    time_match = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if date_match and time_match:
        date_str = f"{date_match.group(1)}.{date_match.group(2)}.{date_match.group(3)}"
        time_str = f"{time_match.group(1)}:{time_match.group(2)}"
        due_utc = parse_due_datetime_to_utc(date_str, time_str)
        if due_utc is None:
            return None, None
        # Убираем найденные дату и время из строки (с конца, чтобы индексы не сбивались)
        note = text
        for m in sorted([date_match, time_match], key=lambda x: x.start(), reverse=True):
            note = note[: m.start()] + " " + note[m.end() :]
        note = " ".join(note.split()).strip()
        if not note:
            return None, None
        return due_utc, note

    # 2) Относительные даты: сегодня, завтра, послезавтра, в пятницу + время ЧЧ:ММ или "в 10"
    now_msk = datetime.now(timezone.utc).replace(tzinfo=None) + MSK_UTC_OFFSET
    time_match = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if time_match:
        try:
            hour, minute = int(time_match.group(1)), int(time_match.group(2))
            if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                return None, None
        except ValueError:
            return None, None
    else:
        # "в 10" или "в 9" — час без минут (10:00, 9:00)
        time_match = re.search(r"\bв\s+(\d{1,2})\b", text, re.IGNORECASE)
        if not time_match:
            return None, None
        try:
            hour = int(time_match.group(1))
            if hour < 0 or hour > 23:
                return None, None
            minute = 0
        except ValueError:
            return None, None

    target_date = None
    lower = text.lower()
    user_time = time(hour, minute, 0, 0)
    if re.search(r"\bсегодня\b", lower):
        target_date = datetime.combine(now_msk.date(), user_time)
        if target_date <= now_msk:
            target_date += timedelta(days=1)
    elif re.search(r"\bзавтра\b", lower):
        d = (now_msk + timedelta(days=1)).date()
        target_date = datetime.combine(d, user_time)
    elif re.search(r"\bпослезавтра\b", lower):
        d = (now_msk + timedelta(days=2)).date()
        target_date = datetime.combine(d, user_time)
    elif re.search(r"\bв\s+пятницу\b|\bпятницу\b", lower):
        next_fri = _next_weekday(now_msk, 4)
        target_date = datetime.combine(next_fri.date(), user_time)
        if target_date <= now_msk:
            target_date += timedelta(days=7)
    else:
        return None, None

    if target_date is None:
        return None, None
    # target_date в МСК, переводим в UTC
    due_utc_dt = target_date - MSK_UTC_OFFSET
    due_utc = due_utc_dt.strftime("%Y-%m-%d %H:%M:%S")
    note = text
    # Собираем интервалы для удаления: ключевые слова даты и фрагмент времени (ЧЧ:ММ или "в 10")
    keyword_matches = list(re.finditer(r"\b(сегодня|завтра|послезавтра|в\s+пятницу|пятницу)\b", text, re.IGNORECASE))
    spans = [(m.start(), m.end()) for m in keyword_matches] + [(time_match.start(), time_match.end())]
    # Если перед временем ЧЧ:ММ стоит "в " (например "завтра в 10:00"), убрать предлог из заметки
    i = time_match.start()
    if i >= 2 and text[i - 2 : i] == "в ":
        spans.append((i - 2, i))
    for start, end in sorted(spans, key=lambda x: x[0], reverse=True):
        note = note[:start] + " " + note[end:]
    note = " ".join(note.split()).strip()
    if not note:
        return None, None
    return due_utc, note
