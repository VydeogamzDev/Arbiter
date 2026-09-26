from wrapping import wrap


def test_simple():
    assert wrap("a b", 10) == ["a b"]
