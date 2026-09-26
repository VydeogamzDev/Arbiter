import re
import urllib.parse
from pathlib import Path


def safe_join(root: str, user_path: str) -> str:
    if "\x00" in user_path:
        raise PermissionError("NUL byte in path")
    p = urllib.parse.unquote(user_path)
    if "\x00" in p:
        raise PermissionError("NUL byte in path")
    p = p.replace("\\", "/")
    if p.startswith("/") or re.match(r"^[A-Za-z]:", p):
        raise PermissionError("absolute path")
    base = Path(root).resolve()
    target = (base / p).resolve()
    if target != base and base not in target.parents:
        raise PermissionError("path escapes root")
    return str(target)
