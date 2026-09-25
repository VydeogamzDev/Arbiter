import os

DEFAULTS = {"database_url": "sqlite:///shop.db", "currency": "EUR", "tax_rate": 0.21}


def load_settings(env):
    settings = dict(DEFAULTS)
    if env == "prod":
        settings["database_url"] = os.environ["SHOP_DATABASE_URL"]
    return settings
