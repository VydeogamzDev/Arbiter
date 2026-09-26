import re

_RX = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")


def parse_duration(text: str) -> int:
    """Convert a duration like "1h30m" to seconds."""
    s = text.strip()
    m = _RX.fullmatch(s)
    if not s or not m or not any(m.groups()):
        raise ValueError(f"invalid duration: {text!r}")
    h, mi, se = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + se
