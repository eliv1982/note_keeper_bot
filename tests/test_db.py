"""Тесты слоя БД: базовые CRUD-операции, изоляция по user_id (защита от IDOR)
и идемпотентность удаления заметки."""

USER_A = 111
USER_B = 222


def _make_category_with_note(db, user_id=USER_A, cat_name="Дом", text="Купить молоко"):
    cat_id, err = db.create_category(user_id, cat_name)
    assert err is None
    note_id, err = db.add_note(cat_id, user_id, text)
    assert err is None
    return cat_id, note_id


def test_create_category_and_add_note(isolated_db):
    db = isolated_db
    cat_id, note_id = _make_category_with_note(db)
    rows, err = db.get_notes_by_category_and_user(cat_id, USER_A)
    assert err is None
    assert [r[0] for r in rows] == [note_id]
    assert rows[0][1] == "Купить молоко"


def test_duplicate_category_name_rejected(isolated_db):
    db = isolated_db
    db.create_category(USER_A, "Дом")
    cat_id, err = db.create_category(USER_A, "Дом")
    assert cat_id is None
    assert "уже существует" in err


def test_same_category_name_allowed_for_different_users(isolated_db):
    db = isolated_db
    id_a, err_a = db.create_category(USER_A, "Дом")
    id_b, err_b = db.create_category(USER_B, "Дом")
    assert err_a is None and err_b is None
    assert id_a != id_b


def test_update_note(isolated_db):
    db = isolated_db
    cat_id, note_id = _make_category_with_note(db)
    ok, err = db.update_note(cat_id, note_id, USER_A, "Купить хлеб")
    assert ok is True and err is None
    rows, _ = db.get_notes_by_category_and_user(cat_id, USER_A)
    assert rows[0][1] == "Купить хлеб"


def test_delete_category_removes_its_notes(isolated_db):
    db = isolated_db
    cat_id, note_id = _make_category_with_note(db)
    ok, err = db.delete_category(USER_A, cat_id)
    assert ok is True and err is None
    assert db.get_category_by_id_and_user(cat_id, USER_A) is None


# --- Изоляция по пользователю / защита от подделанных чужих id (IDOR) ---


def test_read_notes_denied_for_wrong_user(isolated_db):
    db = isolated_db
    cat_id, _ = _make_category_with_note(db, user_id=USER_A)
    rows, err = db.get_notes_by_category_and_user(cat_id, USER_B)
    assert rows is None
    assert err is not None


def test_update_note_denied_for_wrong_user(isolated_db):
    db = isolated_db
    cat_id, note_id = _make_category_with_note(db, user_id=USER_A)
    ok, err = db.update_note(cat_id, note_id, USER_B, "Подмена текста")
    assert ok is False
    assert err is not None
    # Заметка не изменилась
    rows, _ = db.get_notes_by_category_and_user(cat_id, USER_A)
    assert rows[0][1] == "Купить молоко"


def test_delete_category_denied_for_wrong_user(isolated_db):
    db = isolated_db
    cat_id, _ = _make_category_with_note(db, user_id=USER_A)
    ok, err = db.delete_category(USER_B, cat_id)
    assert ok is False
    assert err is not None
    # Категория осталась у настоящего владельца
    assert db.get_category_by_id_and_user(cat_id, USER_A) is not None


def test_delete_note_by_wrong_user_does_not_delete_it(isolated_db):
    """delete_note возвращает (True, None) и для чужого id (см. идемпотентность), но
    заметка при этом реально не должна удаляться — это и есть граница изоляции."""
    db = isolated_db
    cat_id, note_id = _make_category_with_note(db, user_id=USER_A)
    ok, err = db.delete_note(cat_id, note_id, USER_B)
    assert ok is True
    assert err is None
    # Настоящий владелец всё ещё видит заметку — данные не были удалены чужим пользователем.
    note = db.get_note_by_id_and_user(cat_id, note_id, USER_A)
    assert note is not None


def test_forged_nonexistent_category_id(isolated_db):
    db = isolated_db
    assert db.get_category_by_id_and_user(999999, USER_A) is None
    note_id, err = db.add_note(999999, USER_A, "текст")
    assert note_id is None
    assert err is not None


# --- Идемпотентность удаления заметки ---


def test_delete_note_is_idempotent(isolated_db):
    db = isolated_db
    cat_id, note_id = _make_category_with_note(db)
    ok1, err1 = db.delete_note(cat_id, note_id, USER_A)
    assert ok1 is True and err1 is None
    assert db.get_note_by_id_and_user(cat_id, note_id, USER_A) is None

    # Повторное удаление той же (уже отсутствующей) заметки не должно падать
    # и не должно возвращать ошибку — именно так задумана идемпотентность.
    ok2, err2 = db.delete_note(cat_id, note_id, USER_A)
    assert ok2 is True
    assert err2 is None
