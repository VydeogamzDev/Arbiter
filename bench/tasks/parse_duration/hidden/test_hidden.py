import pytest

from durations import parse_duration


def test_basic_units():
    assert parse_duration("45s") == 45 and parse_duration("2h") == 7200 and parse_duration("3m") == 180


def test_combinations():
    assert parse_duration("1h30m") == 5400 and parse_duration("1h0m5s") == 3605


def test_whitespace():
    assert parse_duration("  1h  ".strip()) == 3600 and parse_duration(" 2m ") == 120


def test_zero():
    assert parse_duration("0s") == 0


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty(bad):
    with pytest.raises(ValueError):
        parse_duration(bad)


def test_unknown_unit():
    with pytest.raises(ValueError):
        parse_duration("5d")


def test_order():
    with pytest.raises(ValueError):
        parse_duration("30m1h")


def test_repeated():
    with pytest.raises(ValueError):
        parse_duration("1h1h")


def test_negative():
    with pytest.raises(ValueError):
        parse_duration("-5s")


def test_bare_number():
    with pytest.raises(ValueError):
        parse_duration("90")
