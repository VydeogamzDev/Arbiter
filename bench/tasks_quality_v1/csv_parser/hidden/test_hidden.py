import pathlib

import pytest

from csvparse import parse_csv


def test_simple():
    assert parse_csv("a,b\n1,2\n") == [["a", "b"], ["1", "2"]]


def test_quoted_comma_newline():
    assert parse_csv('x,"a,b","line1\nline2"\n') == [["x", "a,b", "line1\nline2"]]


def test_escaped_quotes():
    assert parse_csv('"he said ""hi"""\n') == [['he said "hi"']]


def test_crlf():
    assert parse_csv("a,b\r\nc,d\r\n") == [["a", "b"], ["c", "d"]]


def test_no_trailing_newline():
    assert parse_csv("a,b") == [["a", "b"]]


def test_empty_line_middle():
    assert parse_csv("a\n\nb\n") == [["a"], [""], ["b"]]


def test_trailing_comma():
    assert parse_csv("a,b,\n") == [["a", "b", ""]]


def test_spaces_kept():
    assert parse_csv(" a , b ") == [[" a ", " b "]]


def test_unterminated():
    with pytest.raises(ValueError):
        parse_csv('"abc\n')


def test_literal_quote_unquoted():
    assert parse_csv('ab"c,d') == [['ab"c', "d"]]


def test_empty_input():
    assert parse_csv("") == []


def test_no_csv_module():
    src = pathlib.Path("csvparse.py").read_text()
    assert "import csv" not in src and "from csv" not in src
