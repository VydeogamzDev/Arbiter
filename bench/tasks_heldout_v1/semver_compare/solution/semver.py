import functools
import re

_ID = r"(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
_RX = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
                 r"(?:-(" + _ID + r"(?:\." + _ID + r")*))?"
                 r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$")


def _parse(v):
    m = _RX.match(v) if isinstance(v, str) else None
    if not m:
        raise ValueError(f"invalid version: {v!r}")
    core = tuple(int(x) for x in m.group(1, 2, 3))
    pre = m.group(4).split(".") if m.group(4) else []
    return core, pre


def _cmp(x, y):
    return (x > y) - (x < y)


def compare(a: str, b: str) -> int:
    """-1, 0 or 1 by Semantic Versioning 2.0.0 precedence."""
    (ca, pa), (cb, pb) = _parse(a), _parse(b)
    if ca != cb:
        return _cmp(ca, cb)
    if not pa or not pb:
        return _cmp(not pa, not pb)
    for x, y in zip(pa, pb):
        if x == y:
            continue
        xd, yd = x.isdigit(), y.isdigit()
        if xd and yd:
            return _cmp(int(x), int(y))
        if xd != yd:
            return -1 if xd else 1
        return _cmp(x, y)
    return _cmp(len(pa), len(pb))


def sort_versions(versions):
    return sorted(versions, key=functools.cmp_to_key(compare))
