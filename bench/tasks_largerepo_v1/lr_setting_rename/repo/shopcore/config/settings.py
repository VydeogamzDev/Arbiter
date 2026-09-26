"""Platform settings with environment-style overrides."""

DEFAULTS = {"mail_host": "localhost", "smtp_port": 25, "max_cart_items": 50, "currency": "USD"}
_overrides: dict = {}


def configure(**values):
    _overrides.update(values)


def reset():
    _overrides.clear()


def get_setting(name):
    if name in _overrides:
        return _overrides[name]
    return DEFAULTS[name]
