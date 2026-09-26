import re


def _para(words, width):
    lines, cur = [], ""
    for w in words:
        while len(w) > width:
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(w[:width])
            w = w[width:]
        if not w:
            continue
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def wrap(text: str, width: int) -> list[str]:
    if width < 1:
        raise ValueError("width must be >= 1")
    paras = [p.split() for p in re.split(r"\n[ \t]*(?:\n[ \t]*)+", text)]
    out: list[str] = []
    for words in paras:
        if not words:
            continue
        if out:
            out.append("")
        out += _para(words, width)
    return out
