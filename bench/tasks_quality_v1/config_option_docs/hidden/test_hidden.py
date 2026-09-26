import pathlib
import tomllib
import warnings

import pytest

from app.config import load


def section(text, head):
    body = text.split(head, 1)[1]
    return body.split("\n## ", 1)[0]


def test_default_max_retries():
    assert load({})["max_retries"] == 3


def test_max_retries_bounds():
    assert load({"max_retries": 0})["max_retries"] == 0 and load({"max_retries": 10})["max_retries"] == 10
    for bad in (-1, 11, "3", 3.5, None):
        with pytest.raises(ValueError):
            load({"max_retries": bad})


def test_timeout_renamed():
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        cfg = load({"timeout_s": 5})
        assert cfg["timeout_s"] == 5 and load({})["timeout_s"] == 30


def test_old_timeout_warns():
    with pytest.warns(DeprecationWarning):
        cfg = load({"timeout": 7})
    assert cfg["timeout_s"] == 7


def test_readme_table():
    table = section(pathlib.Path("README.md").read_text(), "## Configuration")
    assert "max_retries" in table and "timeout_s" in table


def test_changelog():
    unreleased = section(pathlib.Path("CHANGELOG.md").read_text(), "## Unreleased")
    assert "max_retries" in unreleased and "timeout_s" in unreleased


def test_example_toml():
    data = tomllib.loads(pathlib.Path("config.example.toml").read_text())
    assert data.get("max_retries") == 3 and "timeout_s" in data and "timeout" not in data
