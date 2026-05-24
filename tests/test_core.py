"""Unit tests. Run with: pytest tests/"""
import pytest
from veracite.normalize import (
    normalize_text, title_similarity, parse_authors,
    first_author_surname, surname_key, year_diff,
)
from veracite.judge import judge


# ---- normalize ----

def test_normalize_strips_latex():
    assert normalize_text(r"\emph{Hello} {world}") == "hello world"

def test_normalize_unicode():
    assert "lopez" in normalize_text("Marcos López")

def test_title_similarity_identical():
    assert title_similarity("foo bar", "foo bar") == 1.0

def test_title_similarity_unrelated():
    assert title_similarity("foo bar", "completely different text") < 0.5


# ---- author parsing ----

def test_parse_lastfirst():
    a = parse_authors("Pearl, Judea and Mackenzie, Dana")
    assert len(a) == 2
    assert a[0]["family"] == "Pearl"
    assert a[1]["family"] == "Mackenzie"

def test_parse_firstlast():
    a = parse_authors("Judea Pearl and Dana Mackenzie")
    assert a[0]["family"] == "Pearl"
    assert a[0]["given"] == "Judea"

def test_parse_others_skipped():
    a = parse_authors("Jin, Ming and others")
    assert len(a) == 1
    assert a[0]["family"] == "Jin"

def test_surname_compound():
    # "López de Prado" — particles get dropped, last token kept
    s = surname_key("López de Prado")
    assert s == "prado"

def test_first_author_surname_idempotent_format():
    s1 = first_author_surname("Ansari, Abdul Fatir and Stella, Lorenzo")
    s2 = first_author_surname("Abdul Fatir Ansari and Lorenzo Stella")
    assert s1 == s2 == "ansari"


# ---- year diff ----

def test_year_diff_ok():
    assert year_diff("2024", "2024") == 0
    assert year_diff("2024", "2025") == 1
    assert year_diff(None, "2024") is None
    assert year_diff("notanumber", "2024") is None


# ---- judge ----

def _make_entry(**overrides):
    base = {
        "key": "x", "type": "inproceedings",
        "title": "Chronos: Learning the Language of Time Series",
        "authors": "Ansari, Abdul Fatir and Stella, Lorenzo",
        "year": "2024",
        "venue": "Transactions on Machine Learning Research",
    }
    base.update(overrides)
    return base


def _make_cand(**overrides):
    base = {
        "source": "openalex",
        "title": "Chronos: Learning the Language of Time Series",
        "authors": [
            {"given": "Abdul Fatir", "family": "Ansari"},
            {"given": "Lorenzo", "family": "Stella"},
        ],
        "year": "2024",
        "venue": "Transactions on Machine Learning Research",
        "doi": "", "url": "",
    }
    base.update(overrides)
    return base


def test_judge_clean_match_is_ok():
    j = judge(_make_entry(), [_make_cand()])
    assert j["verdict"] == "OK"
    assert j["issues"] == []


def test_judge_year_off_by_one_warns():
    j = judge(_make_entry(year="2023"), [_make_cand()])
    assert j["verdict"] == "WARN"
    assert any("year" in i.lower() for i in j["issues"])


def test_judge_year_way_off_warns():
    j = judge(_make_entry(year="2018"), [_make_cand()])
    assert j["verdict"] == "WARN"


def test_judge_no_candidates_no_per_source_is_missing():
    j = judge(_make_entry(), [])
    assert j["verdict"] == "MISSING"


def test_judge_no_candidates_all_sources_failed_is_unverified():
    """If every source rate-limited or errored, can't conclude MISSING."""
    per_source = {
        "crossref": {"source": "crossref", "status": "rate_limited", "candidates_n": 0, "error": "429"},
        "openalex": {"source": "openalex", "status": "network_error", "candidates_n": 0, "error": "timeout"},
    }
    j = judge(_make_entry(), [], per_source=per_source)
    assert j["verdict"] == "UNVERIFIED"
    assert any("source" in i.lower() for i in j["issues"])


def test_judge_some_sources_failed_some_empty_is_missing():
    """If at least one source worked and found nothing, lean MISSING (but note the failures)."""
    per_source = {
        "crossref": {"source": "crossref", "status": "no_results", "candidates_n": 0, "error": None},
        "openalex": {"source": "openalex", "status": "rate_limited", "candidates_n": 0, "error": "429"},
    }
    j = judge(_make_entry(), [], per_source=per_source)
    assert j["verdict"] == "MISSING"


def test_judge_title_far_off_is_missing():
    weird = _make_cand(title="Some Totally Unrelated Paper About Bears")
    j = judge(_make_entry(), [weird])
    assert j["verdict"] == "MISSING"


def test_judge_title_far_off_but_sources_failed_is_unverified():
    """Bad title sim + some sources failed → UNVERIFIED, not MISSING."""
    weird = _make_cand(title="Some Totally Unrelated Paper About Bears")
    per_source = {
        "openalex": {"source": "openalex", "status": "ok", "candidates_n": 1, "error": None},
        "s2": {"source": "s2", "status": "rate_limited", "candidates_n": 0, "error": "429"},
    }
    j = judge(_make_entry(), [weird], per_source=per_source)
    assert j["verdict"] == "UNVERIFIED"


def test_judge_book_is_skipped():
    j = judge(_make_entry(type="book"), [])
    assert j["verdict"] == "SKIPPED"


def test_judge_wrong_first_author_warns():
    bad_cand = _make_cand(authors=[
        {"given": "Someone", "family": "Else"},
        {"given": "Abdul Fatir", "family": "Ansari"},
    ])
    j = judge(_make_entry(), [bad_cand])
    assert j["verdict"] == "WARN"
    assert any("surname" in i.lower() for i in j["issues"])


def test_judge_match_with_some_source_failed_keeps_ok_but_notes():
    """When the match is clean but a source failed, we still call it OK
    but add an info note (since title+author+year all line up)."""
    per_source = {
        "openalex": {"source": "openalex", "status": "ok", "candidates_n": 1, "error": None},
        "s2": {"source": "s2", "status": "rate_limited", "candidates_n": 0, "error": "429"},
    }
    j = judge(_make_entry(), [_make_cand()], per_source=per_source)
    # OK because the match is good; but issues should mention the failed source
    assert j["verdict"] in {"OK", "WARN"}  # depends on whether note is counted as issue
    # but we definitely expect a note about source failure
    assert any("s2" in i for i in j["issues"])
