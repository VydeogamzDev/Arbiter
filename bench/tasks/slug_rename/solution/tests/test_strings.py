from lib.strings import make_slug


def test_make_slug():
    assert make_slug("Hello  World!") == "hello-world"
