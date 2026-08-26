"""Тесты для парсинга даты/времени напоминаний. Все примеры детерминированы
(не зависят от текущего момента), чтобы не было мерцающих тестов у полуночи."""

import bot


def test_explicit_date_and_time():
    due_utc, note = bot.parse_due_message("15.03.2026 13:00 Тренировка")
    assert due_utc == "2026-03-15 10:00:00"  # 13:00 МСК -> 10:00 UTC
    assert note == "Тренировка"


def test_regression_zavtra_v_time_with_colon():
    """Регрессия: раньше в заметке оставался хвостовой предлог "в" (баг в text[i-2:i] == " в")."""
    due_utc, note = bot.parse_due_message("Тренировка завтра в 10:00")
    assert note == "Тренировка"
    assert due_utc is not None
    assert due_utc.endswith("07:00:00")  # 10:00 МСК -> 07:00 UTC, независимо от даты


def test_zavtra_v_time_without_colon():
    due_utc, note = bot.parse_due_message("Купить молоко завтра в 10")
    assert note == "Купить молоко"
    assert due_utc is not None
    assert due_utc.endswith("07:00:00")


def test_segodnya_with_colon_time():
    due_utc, note = bot.parse_due_message("Позвонить сегодня 18:30")
    assert note == "Позвонить"
    assert due_utc is not None
    assert due_utc.endswith("15:30:00")  # 18:30 МСК -> 15:30 UTC


def test_no_date_or_time_returns_none():
    due_utc, note = bot.parse_due_message("Просто текст без даты")
    assert due_utc is None
    assert note is None


def test_invalid_hour_returns_none():
    due_utc, note = bot.parse_due_message("Текст в 25:00 завтра")
    assert due_utc is None
    assert note is None


def test_empty_text_after_stripping_returns_none():
    due_utc, note = bot.parse_due_message("завтра 10:00")
    assert due_utc is None
    assert note is None


def test_parse_due_datetime_to_utc():
    assert bot.parse_due_datetime_to_utc("15.03.2026", "13:00") == "2026-03-15 10:00:00"


def test_parse_due_datetime_to_utc_invalid():
    assert bot.parse_due_datetime_to_utc("31.02.2026", "13:00") is None


def test_format_created():
    assert bot.format_created("2026-03-15 10:00:00") == "15-03-2026 13:00"
