"""Check the DOI of every paper before it is used to look up indicators (plan_bip.md, 1.1).

Input, for one dataset (in data/):
  <dataset>_dedup.csv                    one row per paper (dedup_papers.py)
  <dataset>_dedup_authors.csv            Europe PMC DOI, PMID and PMCID (fetch_authors.py)
  <dataset>_dedup_authors_not_found.csv  the same, for papers without an author list

Candidate DOIs of a paper, in the order step 00 uses them:
  sheet      the journal DOI of the export (column DOI)
  europepmc  the DOI of the Europe PMC record the paper was found as
  preprint   the latest preprint DOI
Each candidate is normalized (lowercase, no "https://doi.org/" or "doi:") and
passes when
  - doi.org knows it (handle API: covers Crossref and DataCite DOIs), and
  - the record behind it (OpenAlex, else Crossref) has the paper's title
    (similarity of title_key() >= 0.9) and year (+-1: online versus print).
The first candidate that passes is the paper's doi_final. When none passes, or
the paper has no DOI at all, OpenAlex is asked by PMID, PMCID, then exact title.

doi_status:
  ok      the DOI step 00 would use passes. When the sheet and Europe PMC DOIs
          are two versions of one article (F1000 '.2' and '.3'), the sheet's is
          kept and the other is in note
  fixed   it does not, but another candidate (or the OpenAlex record) does
  found   the paper had no DOI; OpenAlex found one that passes
  review  for a person to check, in review_dois.csv (reason says why): the
          sheet and Europe PMC DOIs differ and both pass, or no candidate
          passes. doi_final is then the best guess (the first that passes,
          else step 00's choice)
  none    no DOI anywhere

Writes, in <BIP_SERVICES_DIR>/finder/:
  <dataset>/doi_check.tsv    one row per paper
  <dataset>/review_dois.csv  the candidates of the papers to review, with links
  cache/api_cache.jsonl      every API answer, shared by the datasets. The
                             OpenAlex works are reused later (step 01, phase 3).
                             If the run stops, run the same command again.

Usage (needs internet: the laptop, or glogin4 on MareNostrum):
  python scripts/check_dois.py publications_october2026
  python scripts/check_dois.py publications_october2026 --limit 20   # quick test
"""
import argparse
import difflib
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

from dedup_papers import NOT_AVAILABLE, YEAR_COLUMNS, split_doi_version, title_key
from fetch_authors import Cache, get_json

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
SERVICES_DIR = pathlib.Path(os.getenv(
    "BIP_SERVICES_DIR", "/gpfs/scratch/bsc64/MN4/bsc64/bsc64212/BioHackathonEU-2026/pip-services"))

DOI_HANDLES = "https://doi.org/api/handles/"
OPENALEX_WORKS = "https://api.openalex.org/works"
CROSSREF_WORKS = "https://api.crossref.org/works/"

# Optional contact e-mail for the OpenAlex "polite pool" (as in pipeline/config.py)
OPENALEX_MAILTO = os.getenv("OPENALEX_MAILTO", "")
# Optional, free from openalex.org. Getting one work by DOI or id costs nothing,
# but searches do ($1 per 1,000): the daily budget is $0.10 without a key, $1 with one.
OPENALEX_API_KEY = os.getenv("OPENALEX_API_KEY", "")

MIN_TITLE_SIMILARITY = 0.9
MAX_YEAR_DIFFERENCE = 1

# Large fields not needed later, dropped so the cache stays small
OPENALEX_DROP = ["abstract_inverted_index", "referenced_works", "related_works", "concepts", "mesh"]
CROSSREF_DROP = ["reference"]

DOI_FORMAT = re.compile(r"10\.\d{4,9}/\S+")

# F1000 / Open Research Europe titles end with "[version 2; peer review: 2 approved]"
VERSION_SUFFIX = re.compile(r"\[version \d+;[^\]]*\]?\s*$", re.IGNORECASE)


# ------------------------------------------------------------------- web access

def get_json_or_none(url):
    """get_json(), but None also when the server rejects the request (e.g. a malformed DOI)."""
    try:
        return get_json(url)
    except urllib.error.HTTPError:
        return None


def quote(doi):
    return urllib.parse.quote(doi, safe="/")


def openalex_url(path, params=None):
    params = dict(params or {})
    if OPENALEX_MAILTO:
        params["mailto"] = OPENALEX_MAILTO
    if OPENALEX_API_KEY:
        params["api_key"] = OPENALEX_API_KEY
    return OPENALEX_WORKS + path + ("?" + urllib.parse.urlencode(params) if params else "")


class SearchBudgetUsedUp(Exception):
    """OpenAlex refused a search (HTTP 429): today's budget for searches is used up."""


search_refused = False  # after the first refusal, no more searches in this run


def slim(work):
    return {k: v for k, v in work.items() if k not in OPENALEX_DROP}


def doi_exists(doi, cache):
    def ask_api():
        answer = get_json_or_none(DOI_HANDLES + quote(doi))
        return bool(answer and answer.get("responseCode") == 1)
    return cache.get("doi.org|" + doi, ask_api)


def openalex_work(kind, value, cache):
    """The OpenAlex work for a doi, pmid or pmcid, or None."""
    def ask_api():
        work = get_json_or_none(openalex_url(f"/{kind}:{quote(value)}"))
        return slim(work) if work else None
    return cache.get(f"openalex|{kind}:{value}", ask_api)


def openalex_title_search(title, cache):
    """OpenAlex works whose title matches the words of the title (at most 5).

    None when OpenAlex refuses the search because today's budget is used up.
    The refusal is not cached, so the next run asks again.
    """
    words = " ".join(re.sub(r"[^\w\s]", " ", VERSION_SUFFIX.sub("", title)).split())
    if not words:
        return []
    def ask_api():
        global search_refused
        if search_refused:
            raise SearchBudgetUsedUp
        url = openalex_url("", {"filter": f"title.search:{words}", "per-page": 5})
        request = urllib.request.Request(url, headers={"User-Agent": "BHEU26-check-dois"})
        try:
            # One attempt only: get_json() would retry a refusal for minutes
            with urllib.request.urlopen(request, timeout=60) as response:
                answer = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 429:
                search_refused = True
                print("  OpenAlex refused a search (daily budget used up): no more title "
                      "searches in this run; run again later or set OPENALEX_API_KEY", flush=True)
                raise SearchBudgetUsedUp from error
            return []
        finally:
            time.sleep(0.1)
        return [slim(w) for w in answer.get("results", [])]
    try:
        return cache.get("openalex|title:" + words, ask_api)
    except SearchBudgetUsedUp:
        return None


def crossref_record(doi, cache):
    def ask_api():
        answer = get_json_or_none(CROSSREF_WORKS + quote(doi))
        return {k: v for k, v in answer["message"].items() if k not in CROSSREF_DROP} if answer else None
    return cache.get("crossref_record|" + doi, ask_api)


# ------------------------------------------------------------------ checks

def normalize_doi(value):
    """'https://doi.org/10.1/ABC.' -> '10.1/abc'; '' when there is no DOI."""
    doi = str(value or "").strip()
    if doi in ("", NOT_AVAILABLE, "0"):
        return ""
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.IGNORECASE)
    return doi.strip().rstrip(".,;").lower()


def title_similarity(a, b):
    a, b = title_key(VERSION_SUFFIX.sub("", a or "")), title_key(VERSION_SUFFIX.sub("", b or ""))
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def years_match(a, b):
    try:
        return abs(int(a) - int(b)) <= MAX_YEAR_DIFFERENCE
    except (TypeError, ValueError):
        return False


def record_of(doi, cache):
    """(title, year, openalex_id) of the record behind a DOI: OpenAlex, else Crossref."""
    work = openalex_work("doi", doi, cache)
    if work:
        return work.get("title") or work.get("display_name") or "", work.get("publication_year"), work.get("id") or ""
    record = crossref_record(doi, cache)
    if record:
        year = (((record.get("issued") or {}).get("date-parts") or [[None]])[0] or [None])[0]
        return (record.get("title") or [""])[0], year, ""
    return "", None, ""


def check_candidate(source, doi, paper, cache):
    """The checks of one candidate DOI, as a dict."""
    resolves = doi_exists(doi, cache) if DOI_FORMAT.fullmatch(doi) else False
    title, year, openalex_id = record_of(doi, cache) if resolves else ("", None, "")
    similarity = title_similarity(paper["title"], title)
    year_match = years_match(paper["year"], year)
    return {"source": source, "doi": doi, "valid_format": bool(DOI_FORMAT.fullmatch(doi)),
            "resolves": resolves, "record_title": title, "record_year": year,
            "title_similarity": round(similarity, 3), "year_match": year_match,
            "openalex_id": openalex_id,
            "passes": resolves and similarity >= MIN_TITLE_SIMILARITY and year_match}


def openalex_by_ids(paper, cache):
    """The paper's OpenAlex work found by PMID, PMCID, then exact title (and year).

    Returns (work or None, True if the title search was refused).
    """
    if paper["pmid"]:
        work = openalex_work("pmid", paper["pmid"], cache)
        if work:
            return work, False
    if paper["pmcid"]:
        work = openalex_work("pmcid", paper["pmcid"], cache)
        if work:
            return work, False
    results = openalex_title_search(paper["title"], cache)
    if results is None:
        return None, True
    key = title_key(VERSION_SUFFIX.sub("", paper["title"]))
    for work in results:
        if key and title_key(VERSION_SUFFIX.sub("", work.get("title") or "")) == key \
                and years_match(paper["year"], work.get("publication_year")):
            return work, False
    return None, False


def check_paper(paper, cache):
    """Returns (result row, checks of every candidate)."""
    candidates = []
    for source, value in [("sheet", paper["doi_sheet"]), ("europepmc", paper["doi_europepmc"]),
                          ("preprint", paper["doi_preprint"])]:
        if value and value not in (doi for _, doi in candidates):
            candidates.append((source, value))
    sources = dict(candidates)

    # Candidates in step 00's order until one passes. The sheet and Europe PMC
    # DOIs are both checked when they differ, to see if both match the paper.
    checks = []
    for source, doi in candidates:
        checks.append(check_candidate(source, doi, paper, cache))
        europepmc_next = source == "sheet" and "europepmc" in sources
        if any(c["passes"] for c in checks) and not europepmc_next:
            break
    passing = [c for c in checks if c["passes"]]

    # No candidate passes, or no DOI at all: ask OpenAlex by PMID, PMCID, title
    work, search_refused_here = None, False
    if not passing:
        work, search_refused_here = openalex_by_ids(paper, cache)
        found_doi = normalize_doi((work or {}).get("doi"))
        if found_doi and found_doi not in sources.values():
            checks.append(check_candidate("openalex", found_doi, paper, cache))
            if checks[-1]["passes"]:
                passing = [checks[-1]]

    sheet_and_europepmc = {c["source"] for c in passing} >= {"sheet", "europepmc"}
    same_article = sheet_and_europepmc and \
        split_doi_version(sources["sheet"])[0] == split_doi_version(sources["europepmc"])[0]
    note = ""
    if same_article:
        status, chosen = "ok", passing[0]
        note = f"other version: {sources['europepmc']}"
    elif sheet_and_europepmc:
        status, chosen = "review", passing[0]
    elif passing:
        chosen = passing[0]
        status = "found" if not candidates else "ok" if chosen["source"] == candidates[0][0] else "fixed"
    elif candidates:
        status, chosen = "review", checks[0]
    else:
        status, chosen = "none", None

    row = {
        "paper_index": paper["paper_index"],
        "Year": paper["year"],
        "Article Title": paper["title"],
        "doi_sheet": paper["doi_sheet"],
        "doi_europepmc": paper["doi_europepmc"],
        "doi_preprint": paper["doi_preprint"],
        "doi_final": chosen["doi"] if chosen else "",
        "doi_from": chosen["source"] if chosen else "",
        "doi_status": status,
        "valid_format": chosen["valid_format"] if chosen else "",
        "resolves": chosen["resolves"] if chosen else "",
        "title_similarity": chosen["title_similarity"] if chosen else "",
        "year_match": chosen["year_match"] if chosen else "",
        "record_title": chosen["record_title"] if chosen else "",
        "openalex_id": (chosen["openalex_id"] if chosen else "") or (work or {}).get("id") or "",
        "pmid": paper["pmid"],
        "pmcid": paper["pmcid"],
        "n_candidates_checked": len(checks),
        "reason": review_reason(checks) if status == "review" else "",
        "note": "title search refused (OpenAlex daily budget): run again" if search_refused_here else note,
    }
    return row, checks


def review_reason(checks):
    """Why a paper needs a person to check its DOI, from the checks of its candidates."""
    if sum(c["passes"] for c in checks) >= 2:
        return "sheet and Europe PMC DOIs differ (not versions of one DOI), both match"
    resolving = [c for c in checks if c["resolves"]]
    if not resolving:
        return "no candidate DOI exists"
    if not any(c["record_title"] for c in resolving):
        return "DOI exists, but neither OpenAlex nor Crossref has its record"
    best = max(resolving, key=lambda c: c["title_similarity"])
    if best["title_similarity"] >= MIN_TITLE_SIMILARITY:
        return "title matches, year differs by more than 1"
    if best["title_similarity"] >= 0.7:
        return "title similar (0.7-0.9): e.g. preprint title changed"
    return "title different (< 0.7): wrong DOI, or preprint title changed"


# ------------------------------------------------------------------------ main

def load_papers(dataset, limit=None):
    """The dataset's papers with their candidate DOIs, PMID and PMCID, as dicts."""
    read = dict(dtype=str, keep_default_na=False, encoding="utf-8-sig")
    papers = pd.read_csv(DATA_DIR / f"{dataset}_dedup.csv", **read)
    if limit:
        papers = papers.head(limit)
    year_column = next(c for c in YEAR_COLUMNS if c in papers.columns)

    # One row per paper_index from fetch_authors.py (found or not found)
    columns = ["paper_index", "europepmc_doi", "pmid", "pmcid"]
    europepmc = pd.concat([
        pd.read_csv(DATA_DIR / f"{dataset}_dedup_authors.csv", usecols=columns, **read),
        pd.read_csv(DATA_DIR / f"{dataset}_dedup_authors_not_found.csv", usecols=columns, **read),
    ]).drop_duplicates("paper_index").set_index("paper_index")

    rows = []
    for paper_index, paper in papers.iterrows():
        key = str(paper_index)
        record = europepmc.loc[key].to_dict() if key in europepmc.index else {}
        pmid = record.get("pmid", "")
        europepmc_id = paper.get("EuroPMC ID (pmid)", "").strip()  # October 2026 export only
        if not pmid and europepmc_id.isdigit():
            pmid = europepmc_id
        rows.append({
            "paper_index": paper_index,
            "year": paper[year_column],
            "title": paper["Article Title"],
            "doi_sheet": normalize_doi(paper["DOI"]),
            "doi_europepmc": normalize_doi(record.get("europepmc_doi", "")),
            "doi_preprint": normalize_doi(paper["latest_preprint_doi"]),
            "pmid": pmid,
            "pmcid": record.get("pmcid", ""),
        })
    return rows


def main():
    parser = argparse.ArgumentParser(description="Check the DOI of every paper of a dataset.")
    parser.add_argument("dataset", help="e.g. publications_october2026 (reads data/<dataset>_dedup.csv)")
    parser.add_argument("--limit", type=int, help="only the first N papers (for testing)")
    args = parser.parse_args()

    if not (DATA_DIR / f"{args.dataset}_dedup.csv").exists():
        available = ", ".join(sorted(p.name.replace("_dedup.csv", "") for p in DATA_DIR.glob("*_dedup.csv")))
        sys.exit(f"Not found: data/{args.dataset}_dedup.csv\nAvailable: {available}")
    out_dir = SERVICES_DIR / "finder" / args.dataset
    out_dir.mkdir(parents=True, exist_ok=True)
    (SERVICES_DIR / "finder" / "cache").mkdir(parents=True, exist_ok=True)
    cache = Cache(SERVICES_DIR / "finder" / "cache" / "api_cache.jsonl")

    papers = load_papers(args.dataset, args.limit)
    rows, review = [], []
    for number, paper in enumerate(papers, start=1):
        row, checks = check_paper(paper, cache)
        rows.append(row)
        if row["doi_status"] == "review":
            for check in checks:
                review.append({"paper_index": row["paper_index"], "Year": row["Year"],
                               "Article Title": row["Article Title"], "reason": row["reason"],
                               "doi_final_guess": row["doi_final"],
                               **{k: v for k, v in check.items() if k != "openalex_id"},
                               "link": f"https://doi.org/{check['doi']}", "correct_doi": ""})
        if number % 100 == 0:
            print(f"  {number}/{len(papers)} papers", flush=True)

    table = pd.DataFrame(rows)
    table.to_csv(out_dir / "doi_check.tsv", sep="\t", index=False)
    pd.DataFrame(review).to_csv(out_dir / "review_dois.csv", index=False)

    print(f"{args.dataset}: {len(table)} papers -> {out_dir / 'doi_check.tsv'}")
    print("  doi_status:", table["doi_status"].value_counts().to_dict())
    print("  doi_from:  ", table["doi_from"].replace("", "none").value_counts().to_dict())
    print(f"  to review: {(table['doi_status'] == 'review').sum()} papers -> {out_dir / 'review_dois.csv'}")
    if (table["doi_status"] == "review").any():
        print("  review reasons:", table.loc[table["doi_status"] == "review", "reason"].value_counts().to_dict())
    refused = table["note"].str.startswith("title search refused").sum()
    if refused:
        print(f"  title search refused by OpenAlex for {refused} papers: run again tomorrow "
              f"or with OPENALEX_API_KEY to complete them")


if __name__ == "__main__":
    main()
