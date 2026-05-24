"""
Public-API query layer.

Each `query_*` function returns a QueryResult with explicit status, so we
can distinguish "the API failed" from "the paper doesn't exist".

QueryResult.status values:
  ok              — got candidates back
  no_results      — API responded fine but found nothing
  rate_limited    — 429s on every retry; couldn't get data
  network_error   — timeouts / connection errors
  parse_error     — got a response but couldn't parse it
"""
from __future__ import annotations

import re
import time
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

import requests

log = logging.getLogger("veracite.sources")

USER_AGENT = "veracite/0.1 (mailto:t.huang2@student.tue.nl)"

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})


@dataclass
class QueryResult:
    source: str
    status: str  # ok | no_results | rate_limited | network_error | parse_error
    candidates: list[dict] = field(default_factory=list)
    error: str | None = None

    def to_dict(self):
        return {
            "source": self.source,
            "status": self.status,
            "candidates_n": len(self.candidates),
            "error": self.error,
        }


class _HttpError(Exception):
    """Wraps the kind of HTTP failure that occurred."""
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind  # rate_limited | network_error
        self.detail = detail


def _get(url: str, params: dict | None = None, timeout: int = 20,
         headers: dict | None = None, max_retries: int = 3):
    """
    GET with retries on 429/5xx. Raises _HttpError on permanent failure.
    Distinguishes rate_limited vs network_error so the caller can categorize.
    """
    last_exc = None
    rate_limited_ever = False
    for attempt in range(max_retries):
        try:
            r = _session.get(url, params=params, timeout=timeout,
                             headers=headers or {})
            if r.status_code == 429:
                rate_limited_ever = True
                wait = int(r.headers.get("Retry-After", "5"))
                log.warning(f"  rate-limited ({url}); sleeping {wait}s")
                time.sleep(wait)
                continue
            if 500 <= r.status_code < 600:
                last_exc = f"HTTP {r.status_code}"
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r
        except requests.exceptions.Timeout as exc:
            last_exc = f"timeout: {exc}"
            log.warning(f"  {url} attempt {attempt+1}: timeout")
            time.sleep(2 ** attempt)
        except requests.exceptions.ConnectionError as exc:
            last_exc = f"connection: {exc}"
            log.warning(f"  {url} attempt {attempt+1}: connection error")
            time.sleep(2 ** attempt)
        except requests.exceptions.RequestException as exc:
            last_exc = f"request: {exc}"
            log.warning(f"  {url} attempt {attempt+1}: {exc}")
            time.sleep(2 ** attempt)
    if rate_limited_ever:
        raise _HttpError("rate_limited", last_exc or "exhausted retries on 429")
    raise _HttpError("network_error", last_exc or "exhausted retries")


# ---------- Crossref ----------

def query_crossref(title: str, year_hint: Optional[str] = None, rows: int = 5) -> QueryResult:
    if not title:
        return QueryResult("crossref", "no_results")
    try:
        r = _get("https://api.crossref.org/works",
                 params={"query.title": title[:300], "rows": rows})
    except _HttpError as e:
        return QueryResult("crossref", e.kind, error=e.detail)
    try:
        data = r.json()
    except Exception as e:
        return QueryResult("crossref", "parse_error", error=str(e))
    out = []
    for item in data.get("message", {}).get("items", []):
        title_list = item.get("title") or []
        cand_title = title_list[0] if title_list else ""
        authors = []
        for a in item.get("author", []) or []:
            authors.append({
                "given": a.get("given", "") or "",
                "family": a.get("family", "") or "",
            })
        issued = item.get("issued", {}).get("date-parts", [[None]])
        year = ""
        if issued and issued[0] and issued[0][0]:
            year = str(issued[0][0])
        venue = ""
        ct = item.get("container-title") or []
        if ct:
            venue = ct[0]
        out.append({
            "source": "crossref",
            "title": cand_title,
            "authors": authors,
            "year": year,
            "venue": venue,
            "doi": (item.get("DOI") or ""),
            "url": (item.get("URL") or ""),
            "type": item.get("type", ""),
            "raw": item,
        })
    return QueryResult("crossref", "ok" if out else "no_results", candidates=out)


# ---------- OpenAlex ----------

def query_openalex(title: str, year_hint: Optional[str] = None, rows: int = 5) -> QueryResult:
    if not title:
        return QueryResult("openalex", "no_results")
    params = {"search": title[:300], "per-page": rows}
    try:
        r = _get("https://api.openalex.org/works", params=params)
    except _HttpError as e:
        return QueryResult("openalex", e.kind, error=e.detail)
    try:
        data = r.json()
    except Exception as e:
        return QueryResult("openalex", "parse_error", error=str(e))
    out = []
    for w in data.get("results", []):
        cand_title = w.get("title") or w.get("display_name") or ""
        year = str(w.get("publication_year") or "")
        authors = []
        for a in w.get("authorships", []) or []:
            disp = a.get("author", {}).get("display_name", "") or ""
            toks = disp.split()
            if len(toks) >= 2:
                authors.append({"given": " ".join(toks[:-1]), "family": toks[-1]})
            elif toks:
                authors.append({"given": "", "family": toks[0]})
        venue = ""
        loc = w.get("primary_location") or {}
        src = loc.get("source") or {}
        venue = src.get("display_name", "") or ""
        out.append({
            "source": "openalex",
            "title": cand_title,
            "authors": authors,
            "year": year,
            "venue": venue,
            "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
            "url": w.get("id", ""),
            "type": w.get("type", ""),
            "raw": w,
        })
    return QueryResult("openalex", "ok" if out else "no_results", candidates=out)


# ---------- arXiv ----------

_ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}


def query_arxiv(title: str, year_hint: Optional[str] = None, rows: int = 5) -> QueryResult:
    if not title:
        return QueryResult("arxiv", "no_results")
    q = f'ti:"{title[:200]}"'
    try:
        r = _get("http://export.arxiv.org/api/query",
                 params={"search_query": q, "max_results": rows})
    except _HttpError as e:
        return QueryResult("arxiv", e.kind, error=e.detail)
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError as e:
        return QueryResult("arxiv", "parse_error", error=str(e))
    out = []
    for entry in root.findall("atom:entry", _ARXIV_NS):
        t = (entry.findtext("atom:title", default="", namespaces=_ARXIV_NS) or "").strip()
        t = re.sub(r"\s+", " ", t)
        published = entry.findtext("atom:published", default="", namespaces=_ARXIV_NS) or ""
        year = published[:4]
        authors = []
        for a in entry.findall("atom:author", _ARXIV_NS):
            name = a.findtext("atom:name", default="", namespaces=_ARXIV_NS) or ""
            toks = name.split()
            if len(toks) >= 2:
                authors.append({"given": " ".join(toks[:-1]), "family": toks[-1]})
            elif toks:
                authors.append({"given": "", "family": toks[0]})
        link = ""
        arxiv_id = ""
        for l in entry.findall("atom:link", _ARXIV_NS):
            href = l.get("href", "")
            if l.get("rel") == "alternate":
                link = href
                m = re.search(r"abs/(\d{4}\.\d{4,5})", href)
                if m:
                    arxiv_id = m.group(1)
        if not arxiv_id:
            id_text = entry.findtext("atom:id", default="", namespaces=_ARXIV_NS) or ""
            m = re.search(r"(\d{4}\.\d{4,5})", id_text)
            if m:
                arxiv_id = m.group(1)
        out.append({
            "source": "arxiv",
            "title": t,
            "authors": authors,
            "year": year,
            "venue": "arXiv",
            "doi": "",
            "url": link or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""),
            "arxiv_id": arxiv_id,
            "type": "preprint",
            "raw": {"id": arxiv_id, "title": t},
        })
    return QueryResult("arxiv", "ok" if out else "no_results", candidates=out)


# ---------- Semantic Scholar ----------

def query_semantic_scholar(title: str, year_hint: Optional[str] = None,
                           rows: int = 5) -> QueryResult:
    if not title:
        return QueryResult("s2", "no_results")
    fields = "title,authors,year,venue,externalIds,publicationTypes,url"
    params = {"query": title[:300], "limit": rows, "fields": fields}
    try:
        r = _get("https://api.semanticscholar.org/graph/v1/paper/search",
                 params=params)
    except _HttpError as e:
        return QueryResult("s2", e.kind, error=e.detail)
    try:
        data = r.json()
    except Exception as e:
        return QueryResult("s2", "parse_error", error=str(e))
    out = []
    for p in data.get("data", []) or []:
        authors = []
        for a in p.get("authors", []) or []:
            name = a.get("name", "") or ""
            toks = name.split()
            if len(toks) >= 2:
                authors.append({"given": " ".join(toks[:-1]), "family": toks[-1]})
            elif toks:
                authors.append({"given": "", "family": toks[0]})
        ext = p.get("externalIds") or {}
        out.append({
            "source": "s2",
            "title": p.get("title", "") or "",
            "authors": authors,
            "year": str(p.get("year") or ""),
            "venue": p.get("venue", "") or "",
            "doi": ext.get("DOI", "") or "",
            "url": p.get("url", "") or "",
            "arxiv_id": ext.get("ArXiv", "") or "",
            "type": (p.get("publicationTypes") or [""])[0] if p.get("publicationTypes") else "",
            "raw": p,
        })
    return QueryResult("s2", "ok" if out else "no_results", candidates=out)


# ---------- Orchestrator ----------

SOURCES = {
    "crossref": query_crossref,
    "openalex": query_openalex,
    "arxiv": query_arxiv,
    "s2": query_semantic_scholar,
}


@dataclass
class AllSourcesResult:
    candidates: list[dict] = field(default_factory=list)
    per_source: dict[str, dict] = field(default_factory=dict)  # source -> status dict

    @property
    def failed_sources(self) -> list[str]:
        return [k for k, v in self.per_source.items()
                if v["status"] in {"rate_limited", "network_error", "parse_error"}]

    @property
    def working_sources(self) -> list[str]:
        return [k for k, v in self.per_source.items()
                if v["status"] in {"ok", "no_results"}]

    @property
    def all_sources_failed(self) -> bool:
        if not self.per_source:
            return False
        return len(self.working_sources) == 0


def query_all(title: str, year_hint: Optional[str] = None,
              enabled: set[str] | None = None, sleep: float = 0.3) -> AllSourcesResult:
    """Query every enabled source. Return AllSourcesResult with both candidates and per-source status."""
    result = AllSourcesResult()
    for name, fn in SOURCES.items():
        if enabled is not None and name not in enabled:
            continue
        try:
            qr = fn(title, year_hint=year_hint)
        except Exception as e:
            log.warning(f"{name}: unexpected error: {e}")
            qr = QueryResult(name, "network_error", error=f"unhandled: {e}")
        result.candidates.extend(qr.candidates)
        result.per_source[name] = qr.to_dict()
        time.sleep(sleep)
    return result
