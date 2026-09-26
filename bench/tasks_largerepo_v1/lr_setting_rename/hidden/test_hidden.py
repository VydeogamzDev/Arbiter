import pathlib
import warnings

import pytest

from shopcore.config import settings
from shopcore.notifications.email.sender import connection_info


def test_new_name():
    settings.reset()
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert settings.get_setting("smtp_host") == "localhost"
        settings.configure(smtp_host="mx.example")
        assert settings.get_setting("smtp_host") == "mx.example"
    settings.reset()


def test_old_lookup_warns():
    settings.reset()
    with pytest.warns(DeprecationWarning):
        assert settings.get_setting("mail_host") == "localhost"


def test_old_override_warns():
    settings.reset()
    settings.configure(mail_host="legacy.example")
    with pytest.warns(DeprecationWarning):
        assert settings.get_setting("smtp_host") == "legacy.example"
    settings.reset()


def test_sender_uses_new():
    settings.reset()
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert connection_info() == {"host": "localhost", "port": 25}
    src = pathlib.Path("shopcore/notifications/email/sender.py").read_text()
    assert "smtp_host" in src and "mail_host" not in src
