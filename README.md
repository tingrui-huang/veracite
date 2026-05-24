# veracite

**Catch AI-fabricated citations in your `.bib` file.** Cross-checks every entry against arXiv, Crossref, OpenAlex, and Semantic Scholar in one pass — no API keys, no paid services, pure Python.

```bash
pip install .
veracite run references.bib --out ./out
```

That's it. Open `./out/verification_report.md` to see what's real.

No `.bib` handy? Try the included sample — it has one entry per verdict so you can see what each looks like:

```bash
veracite run examples/sample.bib --out ./out
```

---

## Why this exists

LLMs hallucinate citations all the time: real-sounding titles by real authors that don't exist, or real papers with the wrong year, venue, or coauthors. Reference managers won't catch these — they trust whatever's in the `.bib`. Manually verifying a 100-entry bibliography is hours of work.

`veracite` does it in minutes. Point it at your `.bib`, get back a triage list.

## What you get

After one run, three files in `./out/`:

| File | What it's for |
|---|---|
| `verification_report.md` | Readable triage. Per entry: ✅/⚠/❌ + which sources agreed + what's wrong |
| `references_suggested.bib` | Auto-generated corrections, ready to diff against your `.bib` |
| `verification_results.json` | Full candidate list per entry, for scripting |

The summary at the end of a run looks like this:

```
SUMMARY: ✅ OK=56 | ⚠ WARN=57 | ❌ MISSING=0 | ❓ UNVERIFIED=0 | ⏭ SKIPPED=13
```

And a flagged entry in the report tells you exactly what's off:

> **`das2024timesfm`** — ⚠ WARN
> - first-author surname differs: entry has 'das', source has 'vishwas'
> - year off by 1 (often arXiv vs published — verify)
> - Best match (crossref, title_sim=0.96): *TimesFM: Time Series Forecasting Using Decoder-Only Foundation Model* (2025)

## How it decides

For each entry, it queries 4 free public sources, picks the best candidate per source by title + author + year similarity, then cross-checks the consensus against your `.bib`. **Multiple sources have to agree** before a verdict is issued — one flaky API can't flip the result.

| Verdict | Meaning |
|---|---|
| ✅ OK | Title, authors, year all match consensus. |
| ⚠ WARN | Real paper found, but ≥1 field disagrees. Usually a metadata fix. |
| ❌ MISSING | Sources ran and found nothing. Likely fabricated — verify by hand. |
| ❓ UNVERIFIED | Sources failed (rate limit / network). Re-run with `--retry-unverified`. |
| ⏭ SKIPPED | Books / theses — APIs cover them poorly; check manually. |

## Common flags

```bash
veracite run references.bib --limit 5       # try on 5 entries first
veracite run references.bib --keys k1,k2    # re-check specific entries
veracite run references.bib --retry-unverified  # re-run only failed entries
veracite run references.bib --no-s2 --sleep 0.5 # disable a flaky source, slow down
```

Full flag reference, verdict internals, and known limitations: see [USAGE.md](USAGE.md).

## Honest caveats

- **❌ MISSING is a flag, not a proof.** It means no public source confirmed the entry — not that the paper is fake. Verify by hand before deleting.
- **Books, theses, workshop papers** have spotty coverage on these APIs. Expect ⏭ SKIPPED or false ⚠ WARN.
- **Names with particles or non-Latin scripts** ("de", "van", CJK) can produce false WARNs in author matching.
- **Semantic Scholar rate-limits aggressively.** If you see many 429s, raise `--sleep` or pass `--no-s2`.

## License

MIT — see [LICENSE](LICENSE).
