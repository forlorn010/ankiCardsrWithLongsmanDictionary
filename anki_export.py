"""Anki text-file export.

The generated file uses Anki's modern plain-text import headers: tab-separated
fields, HTML enabled, and four named columns. A note type with the matching
fields (Front, Word, Back, Audio) should be used when importing.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Iterable

from longman import Card

DECK_NAME = "Longman Dictionary"
FIELDS = ("Front", "Word", "Back", "Audio")


def _one_line(value: str) -> str:
    return re.sub(r"[\t\r\n]+", " ", value).strip()


def _html_field(value: str) -> str:
    """Escape text for an HTML-enabled Anki field."""
    return html.escape(_one_line(value), quote=False)


def _link(url: str) -> str:
    safe_url = html.escape(url, quote=True)
    return f'<a href="{safe_url}">{safe_url}</a>'


def render_anki_text(cards: Iterable[Card]) -> str:
    """Render cards as an Anki-importable TSV document."""
    lines = [
        "#separator:tab",
        "#html:true",
        f"#deck:{DECK_NAME}",
        "#columns:" + "\t".join(FIELDS),
    ]
    for card in cards:
        front = _html_field(card.front)
        word = _html_field(card.word)
        back = _link(card.url)
        audio = ""
        lines.append("\t".join((front, word, back, audio)))
    return "\n".join(lines) + "\n"


def write_anki_file(path: str | Path, cards: Iterable[Card]) -> Path:
    """Write an Anki-importable file and return its absolute path."""
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    content = render_anki_text(cards)
    target.write_text(content, encoding="utf-8", newline="\n")
    return target


def ensure_txt_suffix(path: str | Path) -> Path:
    """Add ``.txt`` when the user omitted an extension in the save dialog."""
    result = Path(path)
    if not result.suffix:
        result = result.with_suffix(".txt")
    return result
