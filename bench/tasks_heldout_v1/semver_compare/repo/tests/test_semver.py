from semver import compare


def test_basic():
    assert compare("1.0.0", "2.0.0") == -1
