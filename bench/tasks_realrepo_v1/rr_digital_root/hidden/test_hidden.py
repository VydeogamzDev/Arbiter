import pytest
from sympy.ntheory import digital_root, multiplicative_digital_root


def test_dr_basic():
    assert [digital_root(n) for n in (38, 0, 9875, 7)] == [2, 0, 2, 7]


def test_dr_base():
    assert digital_root(255, 16) == 15 and digital_root(0b1011, 2) == 1


def test_dr_negative():
    assert digital_root(-38) == -2


def test_dr_bad_base():
    with pytest.raises(ValueError, match="b must be greater than 1"):
        digital_root(5, 1)


def test_mdr():
    assert [multiplicative_digital_root(n) for n in (39, 0, 10, 77, -39)] == [4, 0, 0, 8, -4]


def test_mdr_base():
    assert multiplicative_digital_root(0o77, 8) == 6


def test_exports():
    import sympy.ntheory as nt
    assert "digital_root" in nt.__all__ and "multiplicative_digital_root" in nt.__all__
