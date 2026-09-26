from csvparse import parse_csv


def test_basic():
    assert parse_csv("a,b\n") == [["a", "b"]]
