"""Delimited-field splitting."""

from __future__ import annotations


def split_fields(line: str, sep: str = ",") -> list[str]:
    """Split one delimited line into its fields.

    Fields are returned exactly as written: surrounding whitespace is kept, an
    empty input yields a single empty field, and a trailing separator yields a
    trailing empty field.

    A field that *starts* with a double quote is quoted: it extends to the
    matching closing quote, may contain ``sep``, and ``""`` inside it stands for
    one literal quote. The surrounding quotes are not part of the value. A
    quote anywhere else in a field is an ordinary character.

    Args:
        line: A single line of delimited text.
        sep: Field separator. Must be exactly one character.

    Returns:
        The fields, in order.

    Raises:
        ValueError: If ``sep`` is not exactly one character, a quoted field is
            not terminated, or a closing quote is followed by anything other
            than ``sep`` or the end of the line.
    """
    if len(sep) != 1:
        raise ValueError("sep must be a single character")
    fields: list[str] = []
    pos, end = 0, len(line)
    while True:
        if pos < end and line[pos] == '"':
            chars: list[str] = []
            pos += 1
            while True:
                if pos >= end:
                    raise ValueError("unterminated quoted field")
                if line[pos] == '"':
                    if line[pos + 1 : pos + 2] == '"':
                        chars.append('"')
                        pos += 2
                        continue
                    pos += 1
                    break
                chars.append(line[pos])
                pos += 1
            if pos < end and line[pos] != sep:
                raise ValueError(f"unexpected character after closing quote at {pos}")
            fields.append("".join(chars))
        else:
            stop = line.find(sep, pos)
            stop = end if stop == -1 else stop
            fields.append(line[pos:stop])
            pos = stop
        if pos >= end:
            return fields
        pos += 1  # step over the separator
