from durations import parse_duration


def test_example():
    assert parse_duration("1h30m") == 5400
