DEFAULTS = {"timeout": 30, "log_level": "info"}
LEVELS = ("debug", "info", "warning", "error")


def load(user: dict) -> dict:
    """Merge user settings over the defaults and validate them."""
    cfg = dict(DEFAULTS)
    cfg.update(user)
    if not isinstance(cfg["timeout"], int) or cfg["timeout"] <= 0:
        raise ValueError("timeout must be a positive integer")
    if cfg["log_level"] not in LEVELS:
        raise ValueError(f"log_level must be one of {LEVELS}")
    return cfg
