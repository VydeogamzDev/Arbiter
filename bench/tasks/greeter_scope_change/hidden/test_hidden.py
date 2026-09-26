import re

from greet import main


def test_verbose_uppercase(capsys):
    main(["Ada", "--verbose"])
    assert "ADA" in capsys.readouterr().out


def test_no_timestamp(capsys):
    main(["Ada", "--verbose"])
    out = capsys.readouterr().out
    assert not re.search(r"\d{2}:\d{2}|\d{4}-\d{2}-\d{2}", out)


def test_default_unchanged(capsys):
    main(["Ada"])
    assert capsys.readouterr().out == "Hello, Ada!\n"
