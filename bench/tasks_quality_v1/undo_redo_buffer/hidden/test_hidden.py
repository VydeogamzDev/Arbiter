import pytest

from buffer import TextBuffer


def typed(b, s):
    for ch in s:
        b.insert(len(b.text), ch)


def test_basic_undo_redo():
    b = TextBuffer()
    b.insert(0, "hello")
    b.insert(5, " world")
    assert b.undo() and b.text == "hello"
    assert b.redo() and b.text == "hello world"


def test_return_values():
    b = TextBuffer()
    assert b.undo() is False and b.redo() is False
    b.insert(0, "x")
    assert b.undo() is True and b.redo() is True and b.redo() is False


def test_new_edit_clears_redo():
    b = TextBuffer()
    b.insert(0, "a")
    b.undo()
    b.insert(0, "b")
    assert b.redo() is False and b.text == "b"


def test_typing_grouped():
    b = TextBuffer()
    typed(b, "cat")
    assert b.undo() and b.text == ""


def test_space_starts_step():
    b = TextBuffer()
    typed(b, "hi yo")
    assert b.undo() and b.text == "hi"
    assert b.undo() and b.text == ""


def test_not_adjacent_not_grouped():
    b = TextBuffer()
    b.insert(0, "a")
    b.insert(0, "b")
    assert b.undo() and b.text == "a"


def test_multi_char_not_grouped():
    b = TextBuffer()
    b.insert(0, "ab")
    b.insert(2, "c")
    assert b.undo() and b.text == "ab"


def test_delete_undo():
    b = TextBuffer()
    b.insert(0, "hello")
    b.delete(1, 3)
    assert b.text == "ho"
    assert b.undo() and b.text == "hello"


def test_bounds():
    b = TextBuffer()
    b.insert(0, "hello")
    for call in (lambda: b.insert(6, "x"), lambda: b.insert(-1, "x"), lambda: b.delete(3, 5),
                 lambda: b.delete(-1, 1)):
        with pytest.raises(IndexError):
            call()
    assert b.text == "hello"


def test_history_limit():
    b = TextBuffer(history_limit=2)
    for ch in "abc":
        b.insert(0, ch)
    assert b.undo() and b.undo() and b.undo() is False
    assert b.text == "a"
