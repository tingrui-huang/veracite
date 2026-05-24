"""
Offline preflight: static checks that don't need the network.

Detects:
- Duplicate titles (same paper, two cite keys)
- 'and others' lone-author signatures (AI laziness)
- arXiv ID format problems (impossible months, future dates)
- arXiv year vs. entry year inconsistency
- Missing required fields
- Future years (post-2026)
- Inproceedings entry pointing at a journal venue
"""
from __future__ import annotations

import re
from collections import defaultdict
from .normalize import normalize_text


# we re-detect these flags ourselves rather than calling parse_authors,
# because the lazy-author signal needs the raw 'and others' substring


def preflight(bib_db) -> list[dict]:
    """Return list of {key, flags, score, ...} sorted by descending score."""
    entries = bib_db.entries

    # build title duplicates index
    by_norm_title = defaultdict(list)
    for e in entries:
        nt = normalize_text(e.get("title", ""))
        if nt:
            by_norm_title[nt].append(e["ID"])

    results = []
    for e in entries:
        flags = []
        score = 0

        key = e["ID"]
        title = e.get("title", "")
        authors = e.get("author", "")
        year = e.get("year", "")
        etype = e.get("ENTRYTYPE", "")
        venue = (e.get("booktitle") or e.get("journal") or e.get("publisher") or "")

        # 1. duplicates
        nt = normalize_text(title)
        dupes = [k for k in by_norm_title.get(nt, []) if k != key]
        if dupes:
            flags.append(("DUPLICATE_OF", ",".join(dupes)))
            score += 4

        # 2. 'and others' signature
        if re.search(r"\band others\b", authors, re.I):
            author_list = [a for a in re.split(r"\s+and\s+", authors)
                           if a.strip() and a.strip().lower() != "others"]
            if len(author_list) <= 1:
                flags.append(("LONE_AUTHOR_OTHERS", authors[:60]))
                score += 6
            elif len(author_list) <= 2:
                flags.append(("FEW_AUTHORS_OTHERS", f"{len(author_list)} listed"))
                score += 3
            else:
                flags.append(("AUTHORS_OTHERS", f"{len(author_list)} listed"))
                score += 1

        # 3. arXiv ID format
        arxiv_match = re.search(r"arXiv:(\d{4})\.(\d{4,5})", str(e), re.I)
        if arxiv_match:
            yymm = arxiv_match.group(1)
            yy = int(yymm[:2])
            mm = int(yymm[2:])
            if mm < 1 or mm > 12:
                flags.append(("ARXIV_BAD_MONTH", yymm))
                score += 10
            arxiv_year = 2000 + yy
            # year consistency
            if year.isdigit():
                if abs(int(year) - arxiv_year) > 1:
                    flags.append(("YEAR_VS_ARXIV", f"year={year}, arxiv={arxiv_year}"))
                    score += 7

        # 4. future year (>2026)
        if year.isdigit() and int(year) > 2026:
            flags.append(("FUTURE_YEAR", year))
            score += 10

        # 5. missing fields
        if not authors.strip():
            flags.append(("NO_AUTHORS", ""))
            score += 5
        if not year.strip():
            flags.append(("NO_YEAR", ""))
            score += 3
        if not title.strip():
            flags.append(("NO_TITLE", ""))
            score += 10

        # 6. type vs venue inconsistency
        venue_norm = normalize_text(venue)
        is_journal_named = any(j in venue_norm for j in [
            "transactions on", "journal of", "review of"
        ])
        if etype == "inproceedings" and is_journal_named:
            flags.append(("INPROC_BUT_JOURNAL_VENUE", venue[:60]))
            score += 3
        elif etype == "article" and any(c in venue_norm for c in [
            "proceedings of", "conference on", "workshop on", "international conference"
        ]):
            flags.append(("ARTICLE_BUT_CONFERENCE_VENUE", venue[:60]))
            score += 3

        # 7. very short title (often error-prone)
        if title and len(title.split()) <= 2:
            flags.append(("VERY_SHORT_TITLE", title[:40]))
            score += 1

        # 8. arxiv preprint claiming a non-arxiv venue
        if arxiv_match and venue and "arxiv" not in venue.lower() and "preprint" not in venue.lower():
            # this isn't always wrong (paper can be both arxiv and published)
            # but it's worth flagging if the venue is suspicious
            pass  # disabled — too many false positives

        if flags:
            results.append({
                "key": key,
                "type": etype,
                "year": year,
                "title": title[:100],
                "authors_preview": authors[:80],
                "venue": venue[:60],
                "score": score,
                "flags": flags,
            })

    results.sort(key=lambda r: (-r["score"], r["key"]))
    return results


def write_preflight_report(results: list[dict], path):
    """Write a markdown preflight report."""
    lines = []
    a = lines.append

    a("# Bibliography preflight report (offline)")
    a("")
    a(f"Checked structural / format issues without hitting the network.")
    a(f"Flagged: **{len(results)}** entries.")
    a("")
    a("Severity guide (rough):")
    a("- score ≥ 10: high-severity (impossible arXiv ID, future year, no title, …)")
    a("- score ≥ 5: medium (lone-author + 'others', dupes, missing field)")
    a("- score < 5: low (cosmetic — short title, mis-typed entry type)")
    a("")

    for r in results:
        a(f"### `{r['key']}` (score={r['score']}, year={r['year']}, type={r['type']})")
        a(f"- title: {r['title']}")
        a(f"- authors: {r['authors_preview']}")
        a(f"- venue: {r['venue']}")
        a(f"- flags:")
        for flag, detail in r["flags"]:
            line = f"  - **{flag}**"
            if detail:
                line += f": {detail}"
            a(line)
        a("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
