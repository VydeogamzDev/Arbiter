import warnings

import pytest

from billing.receipts import send_receipt
from mailer.api import send
from notify.reset import send_reset
from notify.welcome import send_welcome


def test_keyword_cc():
    assert send("a@x", "s", "b", cc=["c@x"])["cc"] == ["c@x"]


def test_bcc_included():
    assert send("a@x", "s", "b", bcc=["h@x"])["bcc"] == ["h@x"]


def test_bcc_absent():
    assert "bcc" not in send("a@x", "s", "b") and "bcc" not in send("a@x", "s", "b", cc=["c@x"])


def test_positional_cc_warns():
    with pytest.warns(DeprecationWarning):
        msg = send("a@x", "s", "b", ["c@x"])
    assert msg["cc"] == ["c@x"]


def test_callers_updated():
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert send_welcome("u@x", "admin@x")["cc"] == ["admin@x"]
        assert send_reset("u@x", "http://r")["cc"] == []
        assert send_receipt("u@x", 5, "acct@x")["cc"] == ["acct@x"]
