from paginate import page, page_count

ITEMS = list(range(10))


def test_first_page():
    assert page(ITEMS, 1, 4) == [0, 1, 2, 3]


def test_last_partial_page():
    assert page(ITEMS, 3, 4) == [8, 9]


def test_out_of_range():
    assert page(ITEMS, 4, 4) == [] and page(ITEMS, 0, 4) == []


def test_page_count():
    assert page_count(10, 4) == 3 and page_count(0, 4) == 0
