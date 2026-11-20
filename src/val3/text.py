"""Text safety for terminal output: vault text comes from transcripts and is shown escaped."""
import unicodedata


def _escape(char: str) -> str:
    code = ord(char)
    return f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}" if code < 0x10000 else f"\\U{code:08x}"


def safe(text: str) -> str:
    """Text for a terminal: control characters other than newline and tab, and format characters
    (bidi overrides, zero-width), shown as escapes.

    Vault text comes from transcripts; an ANSI or OSC sequence must not redraw what a person approves,
    nor a bidi override reorder it."""
    return "".join(_escape(char) if char not in "\n\t" and unicodedata.category(char) in ("Cc", "Cf")
                   else char for char in text)


def _one_line(text: str) -> str:
    """A cited path on one line: a file named with a line break and `WITHDRAWN …` must not print a status line."""
    return "".join(_escape(char) if char in _LINE_BREAKS else char for char in text)


_LINE_BREAKS = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"  # every boundary of str.splitlines
