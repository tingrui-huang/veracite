"""
Given .bib entry + candidates from multiple sources, decide a verdict
and produce a list of human-readable issues.

Verdicts:
  OK      title+first-author+year all line up with at least one high-confidence candidate
  WARN    found a match but ≥1 field disagrees (likely fixable)
  MISSING no candidate from any source matches well enough
  SKIPPED books/theses/non-verifiable — we don't trust online sources for these
"""
from __future__ import annotations

from .normalize import (
    title_similarity,
    parse_authors,
    surname_key,
    first_author_surname,
    year_diff,
    normalize_text,
)


# threshold tuning — conservative; we'd rather WARN than falsely OK
TITLE_SIM_OK = 0.90        # title must be near-identical
TITLE_SIM_MATCH = 0.75     # below this, candidate isn't even "the right paper"
YEAR_OK_TOLERANCE = 1      # 1 year off = often arXiv vs published, allow


def score_candidate(entry: dict, cand: dict) -> dict:
    """
    Return per-candidate scoring dict including agreement on title/author/year.
    Higher is better. NOT a fixed scale; used for ranking only.
    """
    e_title = entry.get("title", "")
    e_year = entry.get("year", "")
    e_surname = first_author_surname(entry.get("authors", ""))

    sim = title_similarity(e_title, cand.get("title", ""))

    cand_authors = cand.get("authors") or []
    cand_surname = surname_key(cand_authors[0]["family"]) if cand_authors else ""
    author_match = bool(e_surname) and bool(cand_surname) and (e_surname == cand_surname)

    yd = year_diff(e_year, cand.get("year"))
    year_match_strict = (yd == 0)
    year_match_loose = (yd is not None and yd <= YEAR_OK_TOLERANCE)

    # composite score, title dominates
    score = sim * 100
    if author_match:
        score += 8
    if year_match_strict:
        score += 5
    elif year_match_loose:
        score += 2

    return {
        "score": score,
        "title_sim": sim,
        "author_match": author_match,
        "year_match_strict": year_match_strict,
        "year_match_loose": year_match_loose,
        "year_diff": yd,
        "cand_surname": cand_surname,
        "e_surname": e_surname,
    }


def pick_best(entry: dict, candidates: list[dict]) -> tuple[dict | None, dict | None]:
    """Return (best_candidate, scoring) or (None, None)."""
    best = None
    best_s = None
    for c in candidates:
        s = score_candidate(entry, c)
        if best is None or s["score"] > best_s["score"]:
            best = c
            best_s = s
    return best, best_s


def consensus_authors_check(entry: dict, candidates: list[dict]) -> dict:
    """
    Beyond first author: how many of the entry's listed authors appear
    (by surname) in the best candidate's author list?
    """
    e_parsed = parse_authors(entry.get("authors", ""))
    e_surnames = {surname_key(a["family"]) for a in e_parsed if a["family"]}
    e_surnames.discard("")

    if not candidates:
        return {"entry_n": len(e_surnames), "cand_n": 0, "overlap": 0}

    # pick most authoritative candidate for this — prefer arxiv/crossref/s2 over openalex
    priority = ["crossref", "s2", "arxiv", "openalex"]
    best = None
    for src in priority:
        for c in candidates:
            if c.get("source") == src:
                best = c
                break
        if best:
            break
    if not best:
        best = candidates[0]

    cand_surnames = {surname_key(a["family"]) for a in (best.get("authors") or []) if a.get("family")}
    cand_surnames.discard("")
    overlap = e_surnames & cand_surnames
    return {
        "entry_n": len(e_surnames),
        "cand_n": len(cand_surnames),
        "overlap": len(overlap),
        "missing_from_cand": sorted(e_surnames - cand_surnames),
        "extra_in_cand": sorted(cand_surnames - e_surnames),
        "best_source": best.get("source"),
    }


def judge(entry: dict, candidates: list[dict],
          per_source: dict | None = None) -> dict:
    """
    Produce final verdict for one entry.

    Arguments:
      entry        — the bib entry being judged
      candidates   — concatenated candidate list from all sources
      per_source   — optional dict of {source_name: {status, candidates_n, error}}
                     used to distinguish "really not found" from "API failed"

    Returns dict with: verdict, issues, best, best_score, scoring,
    author_consensus, all_candidates.

    Verdicts:
      OK          — title + first author + year all consistent with a high-confidence match
      WARN        — match found, but one or more fields differ; review
      MISSING     — at least one source ran successfully and none found this paper;
                    strong signal of fabrication
      UNVERIFIED  — no source ran successfully OR not enough working sources to be sure;
                    we don't know whether the paper exists
      SKIPPED     — book/thesis; online sources unreliable, do it manually
    """
    issues = []

    # Books/theses: don't try
    etype = (entry.get("type") or "").lower()
    if etype in {"book", "incollection", "phdthesis", "mastersthesis"}:
        return {
            "verdict": "SKIPPED",
            "issues": [f"entry type '{etype}' — online sources unreliable for books/theses; verify manually"],
            "best": None, "best_score": None,
            "author_consensus": None,
            "all_candidates": candidates,
            "per_source": per_source or {},
        }

    # No candidates at all — look at per_source to decide WHY
    if not candidates:
        if per_source is not None:
            failed = [k for k, v in per_source.items()
                      if v["status"] in {"rate_limited", "network_error", "parse_error"}]
            worked_with_no_results = [k for k, v in per_source.items()
                                       if v["status"] == "no_results"]
            if failed and not worked_with_no_results:
                # Every source we tried failed for a non-content reason → can't conclude
                detail = ", ".join(f"{k}:{per_source[k]['status']}" for k in failed)
                return {
                    "verdict": "UNVERIFIED",
                    "issues": [f"no source could complete the query ({detail}) — "
                               f"cannot tell whether this paper exists or is fabricated"],
                    "best": None, "best_score": None,
                    "author_consensus": None,
                    "all_candidates": [],
                    "per_source": per_source,
                }
            elif failed and worked_with_no_results:
                # Some worked + returned nothing; some failed. Weaker MISSING.
                ok_list = ", ".join(worked_with_no_results)
                fail_list = ", ".join(f"{k}:{per_source[k]['status']}" for k in failed)
                return {
                    "verdict": "MISSING",
                    "issues": [
                        f"no candidates from working sources ({ok_list}); "
                        f"these sources also failed and could not confirm: {fail_list}"
                    ],
                    "best": None, "best_score": None,
                    "author_consensus": None,
                    "all_candidates": [],
                    "per_source": per_source,
                }

        # All sources worked and none returned anything
        ok_list = ", ".join(
            k for k, v in (per_source or {}).items() if v["status"] == "no_results"
        ) if per_source else "all sources"
        return {
            "verdict": "MISSING",
            "issues": [f"no candidates returned ({ok_list} found nothing)"],
            "best": None, "best_score": None,
            "author_consensus": None,
            "all_candidates": [],
            "per_source": per_source or {},
        }

    best, scoring = pick_best(entry, candidates)
    sim = scoring["title_sim"]

    # If the best title sim is far below match threshold, it's not the right paper
    if sim < TITLE_SIM_MATCH:
        # but: if some sources failed, we shouldn't be too confident it's missing
        if per_source and any(
            v["status"] in {"rate_limited", "network_error", "parse_error"}
            for v in per_source.values()
        ):
            failed = [k for k, v in per_source.items()
                      if v["status"] in {"rate_limited", "network_error", "parse_error"}]
            return {
                "verdict": "UNVERIFIED",
                "issues": [
                    f"closest title only {sim:.2f} similar — but {','.join(failed)} "
                    f"failed; cannot rule out that the paper exists in those sources"
                ],
                "best": best, "best_score": scoring,
                "author_consensus": None,
                "all_candidates": candidates,
                "per_source": per_source,
            }
        return {
            "verdict": "MISSING",
            "issues": [
                f"closest title only {sim:.2f} similar (need ≥{TITLE_SIM_MATCH:.2f}) — "
                f"likely no real paper with this title exists in indexed sources"
            ],
            "best": best, "best_score": scoring,
            "author_consensus": None,
            "all_candidates": candidates,
            "per_source": per_source or {},
        }

    # Match found — now check fields
    author_consensus = consensus_authors_check(entry, candidates)

    if not scoring["author_match"]:
        if scoring["e_surname"] and scoring["cand_surname"]:
            issues.append(
                f"first-author surname differs: entry has '{scoring['e_surname']}', "
                f"source has '{scoring['cand_surname']}'"
            )
        elif not scoring["e_surname"]:
            issues.append("entry has no parseable first author")

    if not scoring["year_match_strict"]:
        if scoring["year_match_loose"]:
            issues.append(
                f"year off by {scoring['year_diff']} (often arXiv vs published — verify)"
            )
        else:
            issues.append(
                f"year mismatch: entry={entry.get('year')}, source={best.get('year')}"
                + (f" (diff={scoring['year_diff']})" if scoring['year_diff'] is not None else "")
            )

    if sim < TITLE_SIM_OK:
        issues.append(
            f"title similarity only {sim:.2f} — check for missing subtitle, "
            f"typo, or wrong paper"
        )

    if author_consensus and author_consensus["entry_n"] >= 2:
        # if entry claims many authors but candidate has very different list
        if author_consensus["overlap"] < min(2, author_consensus["entry_n"]) and author_consensus["cand_n"] >= 2:
            issues.append(
                f"author list overlap weak: {author_consensus['overlap']}/{author_consensus['entry_n']} "
                f"entry surnames found in source"
            )

    # venue sanity
    e_venue_norm = normalize_text(entry.get("venue", ""))
    c_venue_norm = normalize_text(best.get("venue", ""))
    if e_venue_norm and c_venue_norm:
        if title_similarity(e_venue_norm, c_venue_norm) < 0.40:
            issues.append(
                f"venue may differ: entry='{entry.get('venue','')[:60]}', "
                f"source='{best.get('venue','')[:60]}'"
            )

    # If some sources failed, note it as an info-level issue (but don't downgrade OK→WARN
    # just for that — title+author+year match is what matters)
    extra_note = None
    if per_source:
        failed = [k for k, v in per_source.items()
                  if v["status"] in {"rate_limited", "network_error", "parse_error"}]
        if failed:
            extra_note = (f"note: source(s) {','.join(failed)} failed during this query; "
                          f"verdict is based on the remaining sources")

    # decide
    if not issues:
        verdict = "OK"
    else:
        verdict = "WARN"

    final_issues = list(issues)
    if extra_note:
        final_issues.append(extra_note)

    return {
        "verdict": verdict,
        "issues": final_issues,
        "best": best,
        "best_score": scoring,
        "author_consensus": author_consensus,
        "all_candidates": candidates,
        "per_source": per_source or {},
    }
