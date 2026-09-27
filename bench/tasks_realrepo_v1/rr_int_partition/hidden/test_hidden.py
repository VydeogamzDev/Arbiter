import pytest
from sympy.combinatorics.partitions import IntegerPartition as IP


def test_len_parts():
    assert len(IP([3, 1, 1])) == 3 and len(IP([5])) == 1


def test_self_conjugate():
    assert IP([3, 2, 1]).is_self_conjugate and IP([2, 2]).is_self_conjugate and IP([1]).is_self_conjugate
    assert not IP([3, 1]).is_self_conjugate


def test_ferrers_sep():
    assert IP([3, 1]).as_ferrers(sep="|") == "###|#"
    assert IP([2, 1]).as_ferrers("*", sep=" ") == "** *"


def test_ferrers_default():
    assert IP([3, 1]).as_ferrers() == "###\n#"


def test_ferrers_empty_char():
    with pytest.raises(ValueError):
        IP([3, 1]).as_ferrers("")
