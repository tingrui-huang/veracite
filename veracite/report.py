"""Produce markdown report + JSON dump from verification results."""
from __future__ import annotations

import json
from pathlib import Path


VERDICT_EMOJI = {
    "OK": "✅",
    "WARN": "⚠",
    "MISSING": "❌",
    "UNVERIFIED": "❓",
    "SKIPPED": "⏭",
}


def fmt_authors(authors: list[dict], limit: int = 6) -> str:
    if not authors:
        return "(no authors)"
    parts = []
    for a in authors[:limit]:
        full = (a.get("given", "") + " " + a.get("family", "")).strip()
        parts.append(full or "?")
    if len(authors) > limit:
        parts.append(f"… (+{len(authors) - limit} more)")
    return ", ".join(parts)


def fmt_per_source(per_source: dict | None) -> str:
    if not per_source:
        return ""
    bits = []
    for name, info in per_source.items():
        s = info.get("status", "?")
        n = info.get("candidates_n", 0)
        if s == "ok":
            bits.append(f"{name}=ok({n})")
        elif s == "no_results":
            bits.append(f"{name}=empty")
        else:
            err = info.get("error") or ""
            bits.append(f"{name}=**{s}**" + (f" ({err[:40]})" if err else ""))
    return " · ".join(bits)


def write_json(results: list[dict], path: Path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)


def write_markdown(results: list[dict], path: Path):
    counts = {"OK": 0, "WARN": 0, "MISSING": 0, "UNVERIFIED": 0, "SKIPPED": 0}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    lines = []
    a = lines.append

    a("# Bibliography verification report")
    a("")
    a(f"Total entries: **{len(results)}**")
    a("")
    a("| Verdict | Count |")
    a("|---|---:|")
    for v in ["OK", "WARN", "MISSING", "UNVERIFIED", "SKIPPED"]:
        a(f"| {VERDICT_EMOJI[v]} {v} | {counts.get(v, 0)} |")
    a("")
    a("## Action triage")
    a("")
    a("- **❌ MISSING**: handle first. Online sources ran successfully and found nothing — likely fabricated or wrong title.")
    a("- **⚠ WARN**: scan; usually a metadata fix (year / venue / author spelling).")
    a("- **❓ UNVERIFIED**: online sources failed (rate limit, network). Re-run with `--retry-unverified`.")
    a("- **⏭ SKIPPED**: books — verify by hand against publisher catalog or library record.")
    a("- **✅ OK**: low priority; spot-check a few to make sure thresholds are sane.")
    a("")

    # Group by verdict for easier scanning
    for verdict in ["MISSING", "WARN", "UNVERIFIED", "SKIPPED", "OK"]:
        bucket = [r for r in results if r["verdict"] == verdict]
        if not bucket:
            continue
        a(f"## {VERDICT_EMOJI[verdict]} {verdict} ({len(bucket)})")
        a("")
        for r in bucket:
            a(f"### `{r['key']}`")
            a("")
            a(f"- **Entry**: {r['entry']['title']}")
            a(f"- **Entry authors**: {r['entry']['authors_preview']}")
            a(f"- **Entry year/venue**: {r['entry']['year']} — {r['entry']['venue']}")
            ps = fmt_per_source(r.get("per_source"))
            if ps:
                a(f"- **Source status**: {ps}")
            if r.get("issues"):
                a(f"- **Issues**:")
                for iss in r["issues"]:
                    a(f"  - {iss}")

            best = r.get("best")
            if best:
                a(f"- **Best match** (`{best.get('source')}`, score={r['best_score']['score']:.1f}, "
                  f"title_sim={r['best_score']['title_sim']:.2f}):")
                a(f"  - title: {best.get('title', '')}")
                a(f"  - authors: {fmt_authors(best.get('authors') or [])}")
                a(f"  - year: {best.get('year', '')}")
                a(f"  - venue: {best.get('venue', '')}")
                if best.get("doi"):
                    a(f"  - doi: {best.get('doi')}")
                if best.get("url"):
                    a(f"  - url: {best.get('url')}")
                if best.get("arxiv_id"):
                    a(f"  - arxiv: {best.get('arxiv_id')}")

            ac = r.get("author_consensus")
            if ac and ac.get("entry_n", 0) >= 2:
                a(f"- **Author cross-check** (vs `{ac.get('best_source')}`): "
                  f"{ac['overlap']}/{ac['entry_n']} entry surnames matched")
                if ac.get("missing_from_cand"):
                    a(f"  - in entry but not in source: {', '.join(ac['missing_from_cand'][:8])}")
                if ac.get("extra_in_cand"):
                    a(f"  - in source but not in entry: {', '.join(ac['extra_in_cand'][:8])}")

            # show other candidates briefly so the user can pick a different match
            others = [c for c in r.get("all_candidates", []) if c is not best][:3]
            if others:
                a(f"- **Other candidates considered**:")
                for c in others:
                    a(f"  - [{c.get('source')}] {c.get('title','')[:90]} "
                      f"({c.get('year','')}, "
                      f"{(c.get('authors') or [{}])[0].get('family','?')})")
            a("")
        a("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_suggested_bib(results: list[dict], path: Path, bib_db):
    """
    For WARN/MISSING entries, write a 'suggested' bib block with the best match
    (commented out — user must opt in).
    Original entries are preserved verbatim above their suggested replacement.
    """
    blocks = []
    blocks.append("% Auto-generated suggestions from veracite.")
    blocks.append("% Original entry is shown commented above each suggestion.")
    blocks.append("% Review carefully before applying! Some 'suggestions' may match")
    blocks.append("% a different paper than you originally intended.")
    blocks.append("")

    for r in results:
        if r["verdict"] in {"OK", "SKIPPED", "UNVERIFIED"}:
            continue
        best = r.get("best")
        if not best:
            blocks.append(f"% [{r['key']}] no candidate found — verify by hand or remove")
            blocks.append("")
            continue
        # Build a minimal @misc / @article / @inproceedings stub
        cand_type = (best.get("type") or "").lower()
        if "journal" in cand_type or cand_type == "article":
            etype = "article"
            venue_field = "journal"
        elif "book" in cand_type:
            etype = "book"
            venue_field = "publisher"
        elif cand_type in {"preprint"}:
            etype = "misc"
            venue_field = "howpublished"
        else:
            etype = "inproceedings"
            venue_field = "booktitle"

        authors_str = " and ".join(
            f"{a.get('family','')}, {a.get('given','')}".strip(" ,")
            for a in (best.get("authors") or [])
            if a.get("family") or a.get("given")
        )

        blocks.append(f"% --- suggestion for {r['key']} (verdict={r['verdict']}) ---")
        # original verbatim for comparison
        original = bib_db.entries_dict.get(r["key"])
        if original:
            blocks.append(f"% ORIGINAL:")
            for k, v in original.items():
                if k in ("ID", "ENTRYTYPE"):
                    continue
                blocks.append(f"%   {k} = {{{v}}},")
        blocks.append(f"@{etype}{{{r['key']},")
        blocks.append(f"  title     = {{{best.get('title','')}}},")
        blocks.append(f"  author    = {{{authors_str}}},")
        if best.get("year"):
            blocks.append(f"  year      = {{{best.get('year')}}},")
        if best.get("venue"):
            blocks.append(f"  {venue_field:<9} = {{{best.get('venue')}}},")
        if best.get("doi"):
            blocks.append(f"  doi       = {{{best.get('doi')}}},")
        if best.get("url"):
            blocks.append(f"  url       = {{{best.get('url')}}},")
        if best.get("arxiv_id"):
            blocks.append(f"  note      = {{arXiv:{best.get('arxiv_id')}}},")
        blocks.append("}")
        blocks.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(blocks))
