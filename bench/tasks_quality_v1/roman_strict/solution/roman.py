import re

VALUES = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
          (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
CANON = re.compile(r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")


def to_roman(n: int) -> str:
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 3999:
        raise ValueError("n must be an int from 1 to 3999")
    out = []
    for v, s in VALUES:
        while n >= v:
            out.append(s)
            n -= v
    return "".join(out)


def from_roman(s: str) -> int:
    if not isinstance(s, str) or not s or not CANON.match(s):
        raise ValueError(f"not a canonical roman numeral: {s!r}")
    total, i = 0, 0
    for v, sym in VALUES:
        while s.startswith(sym, i):
            total += v
            i += len(sym)
    return total
