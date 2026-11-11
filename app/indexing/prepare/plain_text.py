"""Inline Markdown in blocks, reduced to the text a reader sees.

Every source's blocks use one dialect (the one the API writes guides in), made of
these and nothing else:

- `**bold**`, `*italic*`, `~~struck~~`
- `<u>`, `<sup>`, `<sub>`, and `<br>` inside table cells
- `[text](url)` links, including `<...>` targets
- `[text]{.red}` and `[text]{.blue}` colour spans
- `![](name)` images
- backslash escapes and the `&amp;`, `&lt;`, `&gt;` entities

Works on one line at a time; `blocks` decides where blocks and lines break.
"""

import html
import re

# Placeholders for escaped characters, so `\*` survives the emphasis pass and
# `\[` never opens a link. ASCII punctuation is all CommonMark lets you escape.
_ESCAPE_BASE = 0xE000
_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")
_PROTECTED = re.compile(f"[{chr(_ESCAPE_BASE)}-{chr(_ESCAPE_BASE + 127)}]")

# A target is `<...>`, or unbracketed with no spaces and balanced parentheses,
# as in `file(2021).xlsx`.
_TARGET = r"<[^<>]*>|(?:[^()\s]|\([^()\s]*\))*"
_IMAGE = re.compile(rf"!\[[^\[\]]*\]\((?:{_TARGET})\)")
_SPAN = re.compile(r"\[([^\[\]]*)\]\{\.(?:red|blue)\}")
_LINK = re.compile(rf"\[([^\[\]]*)\]\(({_TARGET})\)")
_EMPHASIS = re.compile(r"\*+|~~")
_TAG = re.compile(r"</?(?:u|sup|sub)>")
_BREAK = re.compile(r"<br\s*/?>")
_TRAILING_BREAK = re.compile(r"\\$")


def strip_inline(markdown: str) -> str:
    """`"**Pay** [now](#3-pay)\\*"` -> `"Pay now*"`, whitespace collapsed."""
    text = _resolve_spans(_IMAGE.sub("", _protect(markdown)))
    text = _replace_until_stable(_LINK, r"\1", text)
    text = _EMPHASIS.sub("", text)
    text = _TAG.sub("", text)
    text = _BREAK.sub(" ", text)
    text = _TRAILING_BREAK.sub("", text)
    # Entities are decoded while escapes are still placeholders, so `\\&amp;`
    # stays `&amp;` as the reader sees it.
    return " ".join(_restore(html.unescape(text)).split())


def links(markdown: str) -> list[tuple[str, str]]:
    """Each link as `(plain label, target)`, in order; images are not links."""
    text = _resolve_spans(_IMAGE.sub("", _protect(markdown)))

    return [
        (strip_inline(_unprotect(match[1])), _restore(html.unescape(_target(match[2]))))
        for match in _LINK.finditer(text)
    ]


def _target(raw: str) -> str:
    return raw[1:-1] if raw.startswith("<") and raw.endswith(">") else raw


def _resolve_spans(text: str) -> str:
    return _replace_until_stable(_SPAN, r"\1", text)


def _replace_until_stable(pattern: re.Pattern[str], replacement: str, text: str) -> str:
    while True:
        replaced = pattern.sub(replacement, text)

        if replaced == text:
            return text

        text = replaced


def _protect(text: str) -> str:
    return _ESCAPE.sub(lambda match: chr(_ESCAPE_BASE + ord(match[1])), text)


def _restore(text: str) -> str:
    """Placeholders back to the characters they stand for."""
    return _PROTECTED.sub(lambda match: chr(ord(match[0]) - _ESCAPE_BASE), text)


def _unprotect(text: str) -> str:
    """Placeholders back to their escapes, for text that will be stripped again."""
    return _PROTECTED.sub(lambda match: "\\" + chr(ord(match[0]) - _ESCAPE_BASE), text)
