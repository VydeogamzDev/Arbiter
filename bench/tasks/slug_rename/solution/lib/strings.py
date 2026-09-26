import re
import warnings


def make_slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]", "-", text.lower())).strip("-")


def slugify(text: str) -> str:
    warnings.warn("slugify is deprecated; use make_slug", DeprecationWarning, stacklevel=2)
    return make_slug(text)
