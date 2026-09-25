from shop.users.auth import check_password, hash_password


def test_roundtrip():
    h = hash_password("pw")
    assert check_password("pw", h)
