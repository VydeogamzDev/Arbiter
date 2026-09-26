import pathlib
import warnings

import pytest


def test_make_slug_basic():
    from lib.strings import make_slug
    assert make_slug("Hello World") == "hello-world"


def test_collapse_dashes():
    from lib.strings import make_slug
    assert make_slug("a  --  b") == "a-b"


def test_strip_dashes():
    from lib.strings import make_slug
    assert make_slug("  !Hi! ") == "hi"


def test_alias_works():
    from lib.strings import slugify
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert slugify("Hello World") == "hello-world"


def test_alias_warns():
    from lib.strings import slugify
    with pytest.warns(DeprecationWarning):
        slugify("x")


def test_cli_uses_new():
    src = pathlib.Path("cli.py").read_text()
    assert "make_slug" in src
