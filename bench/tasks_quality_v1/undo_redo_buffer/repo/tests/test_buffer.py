from buffer import TextBuffer


def test_insert():
    b = TextBuffer()
    b.insert(0, "hi")
    assert b.text == "hi"
