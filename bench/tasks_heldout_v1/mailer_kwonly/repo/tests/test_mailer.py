from mailer.api import send


def test_send():
    assert send("a@x", "s", "b")["to"] == "a@x"
