import pytest

from roman import from_roman, to_roman


def test_to_roman_examples():
    assert [to_roman(n) for n in (1994, 3999, 4, 9, 40, 90, 400, 900)] == [
        "MCMXCIV", "MMMCMXCIX", "IV", "IX", "XL", "XC", "CD", "CM"]


@pytest.mark.parametrize("n", [0, 4000, -1])
def test_to_roman_bounds(n):
    with pytest.raises(ValueError):
        to_roman(n)


@pytest.mark.parametrize("n", [3.0, "5", True, None])
def test_to_roman_types(n):
    with pytest.raises(ValueError):
        to_roman(n)


def test_from_roman_examples():
    assert from_roman("MCMXCIV") == 1994 and from_roman("MMMCMXCIX") == 3999 and from_roman("XL") == 40


@pytest.mark.parametrize("s", ["IIII", "VV", "IC", "IL", "XM", "VX", "MMMM", "IIV", "XXXX", "LL", "DD", "CCCC",
                               "IXI", "XCX"])
def test_reject_noncanonical(s):
    with pytest.raises(ValueError):
        from_roman(s)


@pytest.mark.parametrize("s", ["", "mcm", "ABC", " X", "X ", "1"])
def test_reject_bad_input(s):
    with pytest.raises(ValueError):
        from_roman(s)


def test_roundtrip():
    assert all(from_roman(to_roman(n)) == n for n in range(1, 4000))
