from wc import counts


def test_counts():
    assert counts("a b\nc\n") == (2, 3, 6)
