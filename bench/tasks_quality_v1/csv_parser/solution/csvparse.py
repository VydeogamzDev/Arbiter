def parse_csv(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    row: list[str] = []
    field: list[str] = []
    i, n = 0, len(text)
    in_quotes = False
    quoted_field = False
    while i < n:
        ch = text[i]
        if in_quotes:
            if ch == '"':
                if i + 1 < n and text[i + 1] == '"':
                    field.append('"')
                    i += 2
                    continue
                in_quotes = False
            else:
                field.append(ch)
            i += 1
            continue
        if ch == '"' and not field and not quoted_field:
            in_quotes = quoted_field = True
        elif ch == ",":
            row.append("".join(field))
            field, quoted_field = [], False
        elif ch == "\n" or (ch == "\r" and i + 1 < n and text[i + 1] == "\n"):
            row.append("".join(field))
            rows.append(row)
            row, field, quoted_field = [], [], False
            if ch == "\r":
                i += 1
        else:
            field.append(ch)
        i += 1
    if in_quotes:
        raise ValueError("unterminated quoted field")
    if field or row or quoted_field:
        row.append("".join(field))
        rows.append(row)
    return rows
