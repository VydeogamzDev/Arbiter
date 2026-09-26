"""Platform settings with environment-style overrides."""
import warnings

DEFAULTS = {"smtp_host": "localhost", "smtp_port": 25, "max_cart_items": 50, "currency": "USD"}
_overrides: dict = {}


def configure(**values):
    _overrides.update(values)


def reset():
    _overrides.clear()


def get_setting(name):
    if name == "mail_host":
        warnings.warn("'mail_host' is deprecated; use 'smtp_host'", DeprecationWarning, stacklevel=2)
        name = "smtp_host"
    if name in _overrides:
        return _overrides[name]
    if name == "smtp_host" and "mail_host" in _overrides:
        warnings.warn("'mail_host' is deprecated; use 'smtp_host'", DeprecationWarning, stacklevel=2)
        return _overrides["mail_host"]
    return DEFAULTS[name]
