import pathlib

import pytest

from wrapping import wrap


def test_greedy():
    assert wrap("the quick brown fox jumps", 10) == ["the quick", "brown fox", "jumps"]
    assert wrap("aa bb cc", 5) == ["aa bb", "cc"]


def test_long_word_split():
    assert wrap("abcdefghij", 4) == ["abcd", "efgh", "ij"]
    assert wrap("hi abcdefghij", 4) == ["hi", "abcd", "efgh", "ij"]


def test_paragraphs():
    assert wrap("one two\n\nthree", 20) == ["one two", "", "three"]


def test_multiple_blank_lines():
    assert wrap("a\n\n\n\nb", 5) == ["a", "", "b"]
    assert wrap("a\n  \n\t\nb", 5) == ["a", "", "b"]


def test_no_edge_spaces():
    out = wrap("  lots   of\tspace  here  ", 7)
    assert out == ["lots of", "space", "here"]
    assert all(line == line.strip() for line in out)


@pytest.mark.parametrize("w", [0, -3])
def test_width_invalid(w):
    with pytest.raises(ValueError):
        wrap("abc", w)


def test_empty():
    assert wrap("", 5) == [] and wrap(" \n\n\t ", 5) == []


def test_no_textwrap():
    assert "textwrap" not in pathlib.Path("wrapping.py").read_text()
