from paginate import page, page_count


def test_pagination():
    items = list(range(10))
    assert page(items, 1, 4) == [0, 1, 2, 3]
    assert page(items, 3, 4) == [8, 9]
    assert page_count(10, 4) == 3
