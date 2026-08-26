import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402


def pytest_configure():
    # Подстраховка: тесты не должны видеть/трогать боевую БД проекта.
    assert bot.DB_PATH.name == "notes_bot.db"


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Подменяет пути БД/экспорта на временные, чтобы тесты не трогали боевую notes_bot.db."""
    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "test_notes.db")
    monkeypatch.setattr(bot, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(bot, "NOTES_EXPORT_PATH", tmp_path / "output" / "notes.csv")
    bot.init_db()
    return bot
