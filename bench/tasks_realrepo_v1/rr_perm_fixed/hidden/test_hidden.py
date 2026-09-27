from sympy.combinatorics import Permutation


def test_involution():
    assert Permutation([1, 0, 2]).is_involution is True
    assert Permutation([1, 2, 0]).is_involution is False
    assert Permutation(3).is_involution is True
    assert Permutation([[0, 1], [2, 3]]).is_involution is True


def test_fixed_points_values():
    assert Permutation([0, 2, 1, 3]).fixed_points == (0, 3)
    assert Permutation([1, 2, 0]).fixed_points == ()
    assert Permutation(2).fixed_points == (0, 1, 2)


def test_fixed_points_tuple_property():
    fp = Permutation([0, 2, 1]).fixed_points
    assert isinstance(fp, tuple) and not callable(fp)
