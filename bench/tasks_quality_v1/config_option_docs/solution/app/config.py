import warnings

DEFAULTS = {"timeout_s": 30, "log_level": "info", "max_retries": 3}
LEVELS = ("debug", "info", "warning", "error")


def load(user: dict) -> dict:
    """Merge user settings over the defaults and validate them."""
    user = dict(user)
    if "timeout" in user:
        warnings.warn("`timeout` is deprecated; use `timeout_s`", DeprecationWarning, stacklevel=2)
        user.setdefault("timeout_s", user.pop("timeout"))
    cfg = dict(DEFAULTS)
    cfg.update(user)
    if not isinstance(cfg["timeout_s"], int) or cfg["timeout_s"] <= 0:
        raise ValueError("timeout_s must be a positive integer")
    if cfg["log_level"] not in LEVELS:
        raise ValueError(f"log_level must be one of {LEVELS}")
    r = cfg["max_retries"]
    if not isinstance(r, int) or isinstance(r, bool) or not 0 <= r <= 10:
        raise ValueError("max_retries must be an integer from 0 to 10")
    return cfg
