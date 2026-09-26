import re


def parse_duration(text: str) -> int:
    total = 0
    for n, unit in re.findall(r"(\d+)([hms])", text):
        total += int(n) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total
