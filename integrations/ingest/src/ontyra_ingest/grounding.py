"""The check that makes a statement admissible: its quote occurs in the chunk, word for word."""

from __future__ import annotations

import re

MIN_QUOTE = 12
MAX_QUOTE = 600


def find_quote(quote: str, text: str) -> str | None:
    """The quote as it occurs in `text` (whitespace aside), or None. The returned words are the
    document's own, so what is stored is literal even when the model changed the spacing."""
    words = quote.split()
    if not words:
        return None
    pattern = r"\s+".join(re.escape(w) for w in words)
    hit = re.search(pattern, text)
    if hit is None:
        return None
    found = re.sub(r"\s+", " ", hit.group(0))
    if not MIN_QUOTE <= len(found) <= MAX_QUOTE:
        return None
    return found
