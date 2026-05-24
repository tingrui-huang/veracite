# veracite — full usage

Detailed manual for `veracite`. For a quick overview, see [README.md](README.md).

## What it does

For each entry in your `.bib`:

1. Queries 4 public sources by title:
   - **arXiv** (for preprints)
   - **Crossref** (for DOI-backed papers)
   - **OpenAlex** (broad coverage)
   - **Semantic Scholar** (NLP/ML focus, fills arXiv gaps)
2. Picks the best candidate per source by title similarity + author + year
3. Cross-checks the .bib metadata against the consensus across sources
4. Outputs:
   - `verification_report.md` — human-readable, ✅/⚠/❌ per entry
   - `verification_results.json` — full candidates per entry (machine-readable)
   - `references_suggested.bib` — proposed corrections (review before using!)

## Install

Requires Python 3.10+.

```bash
pip install .
```

This installs the `veracite` CLI. For development, use `pip install -e .` so source edits take effect immediately.

## Run

```bash
veracite run path/to/references.bib --out ./out
```

Optional flags:
- `--limit N` — only process first N entries (testing)
- `--keys k1,k2,k3` — only process specific entries
- `--skip-clean` — skip entries already flagged clean in a previous run
- `--sleep 0.3` — seconds between API calls (default 0.3; raise if rate-limited)
- `--no-arxiv` / `--no-crossref` / `--no-openalex` / `--no-s2` — disable a source
- `--resume` — reuse prior `verification_results.json` for unchanged entries
- `--retry-unverified` — re-query only entries that came back UNVERIFIED last run
- `--force` — ignore the resume cache even for matched keys
- `--verbose` — debug-level logging

There's also a no-network structural check:

```bash
veracite preflight path/to/references.bib --out ./out/preflight.md
```

## What "verdict" means

| Verdict | Meaning | What to do |
|---------|---------|------------|
| ✅ OK | Title + author + year all match a high-confidence candidate | Keep as-is |
| ⚠ WARN | Found a match but ≥1 field differs (e.g. venue, year, author spelling) | Manually verify; usually a fix needed |
| ❌ MISSING | At least one source ran successfully and found nothing | Likely AI-fabricated. Verify by hand; may need to remove or replace |
| ❓ UNVERIFIED | All (or all relevant) online sources failed (rate-limit, network) | Re-run with `--retry-unverified` |
| ⏭ SKIPPED | Books, theses, or entries we don't try to verify online | Manual check |

Each entry's report also lists **per-source status** so you can see *which* source confirmed/denied/failed.

## Re-running after rate limits

If you see ❓ UNVERIFIED entries, re-run just those without re-querying anything else:

```bash
# wait a few minutes for rate limits to reset, then:
veracite run path/to/references.bib --out ./out --retry-unverified
```

This keeps every OK / WARN / MISSING / SKIPPED entry as-is and only re-queries
UNVERIFIED ones.

If a specific source (e.g. Semantic Scholar) keeps rate-limiting you, disable it:

```bash
veracite run path/to/references.bib --out ./out --no-s2
```

## Limitations

- Coverage of **books** is poor on Crossref/OpenAlex. Books mostly come back ⏭ SKIPPED.
- Coverage of **workshop papers** is uneven across sources.
- A ❌ MISSING verdict doesn't *prove* fabrication — it means no public source could confirm. Always verify by hand before deleting.
- Author surname matching uses simple lowercase + Unicode normalization. Names with multiple given names, particles ("de", "van"), or non-Latin scripts may produce false WARN flags.
- Rate limits: Semantic Scholar can throttle hard. If you see many timeout errors, raise `--sleep`.
