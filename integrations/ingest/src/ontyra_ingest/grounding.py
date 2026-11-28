"""The check that makes a statement admissible: its quote occurs in the chunk, word for word."""

from __future__ import annotations

MIN_QUOTE = 12
MAX_QUOTE = 600


def find_quote(quote: str, text: str) -> str | None:
    """The quote as it occurs in `text`, or None. Spacing and letter case are not held against the
    model; the words are. What is returned is the document's own text for that span, so what is
    stored is literal even when the model changed the spacing or case."""
    wanted = "".join(quote.split()).casefold()
    if not wanted:
        return None
    kept: list[str] = []
    where: list[int] = []
    for i, ch in enumerate(text):
        if not ch.isspace():
            kept.append(ch.casefold())
            where.append(i)
    flat = "".join(kept)
    at = flat.find(wanted)
    if at < 0:
        return None
    # casefold can change a character's length: map by the original characters, not by offsets
    start = where[at]
    end = where[min(at + len(wanted), len(where)) - 1] + 1
    found = " ".join(text[start:end].split())
    if len(found) < MIN_QUOTE:
        # A list item or a table cell quoted alone ("SPI,") says nothing by itself: the line it
        # sits on is the document's own context for it.
        start = text.rfind("\n", 0, start) + 1
        end = text.find("\n", end)
        found = " ".join(text[start : end if end >= 0 else len(text)].split())
    if not MIN_QUOTE <= len(found) <= MAX_QUOTE:
        return None
    return found
