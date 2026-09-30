from sympy.utilities import misc
from sympy.utilities.misc import ordinal


def test_words_1_12():
    assert [ordinal(i, words=True) for i in (1, 2, 3, 5, 8, 9, 12)] == [
        "first", "second", "third", "fifth", "eighth", "ninth", "twelfth"]


def test_words_13_20():
    assert ordinal(13, words=True) == "thirteenth" and ordinal(20, words=True) == "twentieth"
    assert ordinal(21, words=True) == "21st"


def test_numeric_default():
    assert (ordinal(1), ordinal(12), ordinal(0), ordinal(-3)) == ("1st", "12th", "0th", "-3rd")


def test_negative():
    assert ordinal(-3, words=True) == "minus third" and ordinal(-25, words=True) == "-25th"


def test_zero():
    assert ordinal(0, words=True) == "zeroth"


def test_table():
    assert misc.ORDINAL_WORDS[1] == "first" and misc.ORDINAL_WORDS[20] == "twentieth"
    assert len(misc.ORDINAL_WORDS) >= 20
