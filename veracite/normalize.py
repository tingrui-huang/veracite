"""Text & name normalization helpers — used everywhere for matching."""
from __future__ import annotations

import re
from unidecode import unidecode
from rapidfuzz import fuzz


def normalize_text(s: str) -> str:
    """Lowercase, strip diacritics, drop LaTeX/punctuation, collapse whitespace."""
    if not s:
        return ""
    # strip {} braces and backslash commands like \emph{...} -> ...
    s = re.sub(r"\\[a-zA-Z]+\s*", " ", s)
    s = re.sub(r"[{}]", "", s)
    s = unidecode(s)
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def title_similarity(a: str, b: str) -> float:
    """0..1. Uses token-set ratio so word order and minor edits matter less than content."""
    a = normalize_text(a)
    b = normalize_text(b)
    if not a or not b:
        return 0.0
    return fuzz.token_set_ratio(a, b) / 100.0


def parse_authors(authors: str) -> list[dict]:
    """
    Parse a BibTeX author string into a list of {given, family, raw}.
    Handles 'Last, First Middle' and 'First Middle Last' and 'X and Y and Z'.
    Skips 'others'.
    """
    if not authors:
        return []
    authors = re.sub(r"\s+", " ", authors).strip()
    out = []
    for chunk in re.split(r"\s+and\s+", authors):
        chunk = chunk.strip()
        if not chunk or chunk.lower() == "others":
            continue
        # strip braces around whole token e.g. {L{\'o}pez de Prado}
        chunk = re.sub(r"^[{]|[}]$", "", chunk).strip()
        if "," in chunk:
            family, _, given = chunk.partition(",")
            out.append({
                "given": given.strip(),
                "family": family.strip(),
                "raw": chunk,
            })
        else:
            tokens = chunk.split()
            if len(tokens) == 1:
                out.append({"given": "", "family": tokens[0], "raw": chunk})
            else:
                out.append({
                    "given": " ".join(tokens[:-1]),
                    "family": tokens[-1],
                    "raw": chunk,
                })
    return out


def surname_key(name: str) -> str:
    """Normalize a family-name string for comparison.

    Caveat: compound surnames like 'López de Prado' lose information here —
    we drop nobility particles and keep the last token ('prado'). The .bib
    field is sometimes written as the full compound, sometimes as just the
    last part; matching against the last token is the best heuristic across
    both. Same applies to 'van der Berg', 'de la Torre', etc. If you see
    spurious WARN flags on a compound-surname author, that's likely why.
    """
    if not name:
        return ""
    s = normalize_text(name)
    tokens = [t for t in s.split() if t not in {"de", "del", "la", "le", "van", "von", "der", "den"}]
    return tokens[-1] if tokens else s


def first_author_surname(authors: str) -> str:
    parsed = parse_authors(authors)
    if not parsed:
        return ""
    return surname_key(parsed[0]["family"])


def year_diff(a: str | int | None, b: str | int | None) -> int | None:
    """Return |a-b| as integer, or None if either is missing/unparseable."""
    try:
        return abs(int(str(a)) - int(str(b)))
    except (TypeError, ValueError):
        return None
