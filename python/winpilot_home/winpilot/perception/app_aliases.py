"""Canonicalize literal application names without guessing product identity."""
from __future__ import annotations

import unicodedata


def expand(name: str) -> list[str]:
    """Return only the normalized literal supplied by the caller."""
    literal = unicodedata.normalize("NFKC", str(name or "")).casefold().strip()
    return [literal] if literal else []
