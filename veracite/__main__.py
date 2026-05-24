"""
CLI entry point.

Usage:
    python -m veracite run path/to/references.bib --out ./out
    python -m veracite run path/to/references.bib --keys ansari2024chronos,jin2025raft
    python -m veracite run path/to/references.bib --limit 10
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

import bibtexparser
from bibtexparser.bparser import BibTexParser

from .sources import query_all, SOURCES
from .judge import judge


log = logging.getLogger("veracite")


def _clean_field(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def load_bib(path: Path):
    parser = BibTexParser(common_strings=True)
    parser.ignore_nonstandard_types = False
    with open(path, encoding="utf-8") as f:
        return bibtexparser.load(f, parser=parser)


def entry_for_verify(raw: dict) -> dict:
    return {
        "key": raw["ID"],
        "type": raw.get("ENTRYTYPE", ""),
        "title": _clean_field(raw.get("title", "")),
        "authors": _clean_field(raw.get("author", "")),
        "year": _clean_field(raw.get("year", "")),
        "venue": _clean_field(
            raw.get("booktitle", "") or raw.get("journal", "") or raw.get("publisher", "")
        ),
        "doi": _clean_field(raw.get("doi", "")),
        "url": _clean_field(raw.get("url", "")),
    }


def entry_preview(e: dict) -> dict:
    return {
        "title": e["title"],
        "year": e["year"],
        "venue": e["venue"][:100],
        "authors_preview": e["authors"][:120],
    }


def run(args):
    logging.basicConfig(
        level=logging.INFO if not args.verbose else logging.DEBUG,
        format="%(message)s",
    )

    bib_path = Path(args.bib)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    bib_db = load_bib(bib_path)
    log.info(f"Loaded {len(bib_db.entries)} entries from {bib_path}")

    # filter
    entries = bib_db.entries
    if args.keys:
        keyset = {k.strip() for k in args.keys.split(",")}
        entries = [e for e in entries if e["ID"] in keyset]
        log.info(f"Filtered to {len(entries)} entries by --keys")
    if args.limit:
        entries = entries[: args.limit]
        log.info(f"Limited to first {len(entries)} entries by --limit")

    enabled = set(SOURCES.keys())
    for src in ["crossref", "openalex", "arxiv", "s2"]:
        if getattr(args, f"no_{src}"):
            enabled.discard(src)
    log.info(f"Enabled sources: {sorted(enabled)}")

    # resume support
    results_path = out_dir / "verification_results.json"
    existing = {}
    if (args.resume or args.retry_unverified or args.skip_clean) and results_path.exists():
        with open(results_path, encoding="utf-8") as f:
            existing_list = json.load(f)
        for r in existing_list:
            existing[r["key"]] = r
        log.info(f"Resume: loaded {len(existing)} prior results")

    results = []
    t_start = time.time()
    for i, raw in enumerate(entries, 1):
        key = raw["ID"]
        # resume / skip logic
        prior = existing.get(key)
        if prior and not args.force:
            stable_verdicts = {"OK", "SKIPPED"}
            if args.retry_unverified:
                # only re-query UNVERIFIED; keep everything else
                if prior["verdict"] != "UNVERIFIED":
                    log.info(f"[{i}/{len(entries)}] {key} — skipping ({prior['verdict']})")
                    results.append(prior)
                    continue
            elif args.skip_clean:
                if prior["verdict"] in stable_verdicts:
                    log.info(f"[{i}/{len(entries)}] {key} — skipping ({prior['verdict']})")
                    results.append(prior)
                    continue
            elif args.resume:
                log.info(f"[{i}/{len(entries)}] {key} — skipping (resume)")
                results.append(prior)
                continue

        e = entry_for_verify(raw)
        log.info(f"[{i}/{len(entries)}] {key}: {e['title'][:60]}…")
        if not e["title"]:
            results.append({
                "key": key,
                "verdict": "MISSING",
                "issues": ["entry has no title"],
                "entry": entry_preview(e),
                "best": None, "best_score": None,
                "author_consensus": None,
                "all_candidates": [],
            })
            continue

        cands_result = query_all(e["title"], year_hint=e["year"],
                                 enabled=enabled, sleep=args.sleep)
        j = judge(e, cands_result.candidates, per_source=cands_result.per_source)

        # build a one-line summary of source status for the log
        status_parts = []
        for sname, sinfo in cands_result.per_source.items():
            short = {"ok": "✓", "no_results": "0", "rate_limited": "429",
                     "network_error": "net", "parse_error": "parse"}.get(sinfo["status"], sinfo["status"])
            status_parts.append(f"{sname}:{short}")
        status_line = " ".join(status_parts)
        log.info(f"  → {j['verdict']}  [{status_line}]" +
                 (f" ({len(j['issues'])} issues)" if j['issues'] else ""))

        results.append({
            "key": key,
            "verdict": j["verdict"],
            "issues": j["issues"],
            "entry": entry_preview(e),
            "best": j["best"],
            "best_score": j["best_score"],
            "author_consensus": j["author_consensus"],
            "all_candidates": j["all_candidates"],
            "per_source": cands_result.per_source,
        })

        # checkpoint every 10 entries
        if i % 10 == 0:
            from .report import write_json
            write_json(results, results_path)
            log.info(f"  [checkpoint saved to {results_path}]")

    elapsed = time.time() - t_start
    log.info(f"Done in {elapsed:.1f}s ({elapsed/max(len(entries),1):.2f}s/entry)")

    # write outputs
    from .report import write_json, write_markdown, write_suggested_bib
    write_json(results, results_path)
    log.info(f"  wrote {results_path}")
    md_path = out_dir / "verification_report.md"
    write_markdown(results, md_path)
    log.info(f"  wrote {md_path}")
    sug_path = out_dir / "references_suggested.bib"
    write_suggested_bib(results, sug_path, bib_db)
    log.info(f"  wrote {sug_path}")

    # summary
    counts = {"OK": 0, "WARN": 0, "MISSING": 0, "UNVERIFIED": 0, "SKIPPED": 0}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    log.info("")
    log.info(f"SUMMARY: ✅ OK={counts['OK']} | ⚠ WARN={counts['WARN']} | "
             f"❌ MISSING={counts['MISSING']} | ❓ UNVERIFIED={counts['UNVERIFIED']} | "
             f"⏭ SKIPPED={counts['SKIPPED']}")
    if counts["UNVERIFIED"]:
        log.info("")
        log.info(f"⚠  {counts['UNVERIFIED']} entries are UNVERIFIED because online "
                 f"sources failed (rate limit, network). Re-run with "
                 f"`--resume --skip-clean` to retry just those — or raise --sleep.")


def preflight_cmd(args):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from .preflight import preflight, write_preflight_report
    bib_path = Path(args.bib)
    bib_db = load_bib(bib_path)
    log.info(f"Loaded {len(bib_db.entries)} entries from {bib_path}")
    results = preflight(bib_db)
    log.info(f"Preflight flagged {len(results)} entries")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_preflight_report(results, out)
    log.info(f"Report written to {out}")
    # also dump JSON next to it
    json_path = out.with_suffix(".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    log.info(f"JSON: {json_path}")


def main():
    ap = argparse.ArgumentParser(prog="python -m veracite")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="verify a .bib file (online)")
    p.add_argument("bib", help="path to .bib file")
    p.add_argument("--out", default="./out", help="output directory")
    p.add_argument("--limit", type=int, default=0,
                   help="only process first N entries")
    p.add_argument("--keys", default="",
                   help="comma-separated cite keys to process")
    p.add_argument("--sleep", type=float, default=0.3,
                   help="seconds between API calls (raise if rate-limited)")
    p.add_argument("--resume", action="store_true",
                   help="reuse prior verification_results.json")
    p.add_argument("--force", action="store_true",
                   help="ignore --resume cache for matched keys")
    p.add_argument("--skip-clean", action="store_true",
                   help="don't re-query entries previously marked OK or SKIPPED")
    p.add_argument("--retry-unverified", action="store_true",
                   help="reuse prior results but re-query only UNVERIFIED entries "
                        "(useful when sources failed last time)")
    p.add_argument("--no-crossref", action="store_true")
    p.add_argument("--no-openalex", action="store_true")
    p.add_argument("--no-arxiv", action="store_true")
    p.add_argument("--no-s2", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=run)

    pf = sub.add_parser("preflight", help="offline structural checks on .bib (no network)")
    pf.add_argument("bib", help="path to .bib file")
    pf.add_argument("--out", default="./out/preflight_report.md",
                    help="output markdown path")
    pf.set_defaults(func=preflight_cmd)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
