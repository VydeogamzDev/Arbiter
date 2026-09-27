import pytest
from sympy.utilities.iterables import common_prefix, common_suffix, runs


def test_prefix_keyfunc():
    assert common_prefix(['A', 'b', 'C'], ['a', 'B', 'd'], keyfunc=str.lower) == ['A', 'b']


def test_suffix_keyfunc():
    assert common_suffix(['x', 'B', 'c'], ['y', 'b', 'C'], keyfunc=str.lower) == ['B', 'c']


def test_default_unchanged():
    assert common_prefix([1, 2, 3], [1, 2, 5]) == [1, 2]
    assert common_suffix([1, 2, 3], [9, 2, 3]) == [2, 3]
    assert runs([0, 1, 2, 2, 1]) == [[0, 1, 2], [2], [1]]


def test_runs_keyfunc():
    assert runs(['a', 'B', 'c', 'A'], keyfunc=str.lower) == [['a', 'B', 'c'], ['A']]


def test_key_alias_deprecated():
    with pytest.warns(DeprecationWarning):
        assert common_prefix(['A'], ['a'], key=str.lower) == ['A']
    with pytest.warns(DeprecationWarning):
        assert common_suffix(['A'], ['a'], key=str.lower) == ['A']
    with pytest.warns(DeprecationWarning):
        assert runs(['a', 'B'], key=str.lower) == [['a', 'B']]
