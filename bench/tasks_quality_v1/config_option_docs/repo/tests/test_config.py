from app.config import load


def test_defaults():
    assert load({})["log_level"] == "info"
