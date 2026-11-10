"""Get the full names of all authors of each paper, from Europe PMC or Crossref.

Input: a *_dedup.csv made by dedup_papers.py (one row per paper).

How each paper is found in Europe PMC (the first lookup that works is used):
  1. its Europe PMC id (column "EuroPMC ID (pmid)", only in the October 2026 export)
  2. its journal DOI (column DOI)
  3. its latest preprint DOI (column latest_preprint_doi)
  4. its exact title
Every answer is checked: the id, DOI or title of the record must be the paper's.

Crossref is used instead, by DOI, when Europe PMC does not find the paper or
gives only initials for some first names and Crossref has them all in full.

Writes, in data/:
  <input>_authors.csv            one row per author of each paper, in author order
  <input>_authors_not_found.csv  papers for which no author list was found
  <input>_api_cache.jsonl        every API answer. If the run stops, run the
                                 same command again: it continues where it was.

Usage (needs internet, so run it on the laptop, not on MareNostrum):
  python scripts/fetch_authors.py publications_october2026_dedup.csv
  python scripts/fetch_authors.py publications_october2026_dedup.csv --limit 20   # quick test
"""
import argparse
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

from dedup_papers import NOT_AVAILABLE, YEAR_COLUMNS, title_key

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

EUROPEPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
CROSSREF_WORKS = "https://api.crossref.org/works/"

# Optional. Crossref answers faster when requests include a contact email.
CONTACT_EMAIL = ""

# Pause after each API call, to be polite to the servers
PAUSE_SECONDS = 0.1

# Fields kept from each Europe PMC record (the cache stays small)
EUROPEPMC_FIELDS = ["id", "source", "pmid", "pmcid", "doi", "title", "authorList"]


# ------------------------------------------------------------------- web access

def get_json(url):
    """Download a JSON answer. Returns None if the server says it does not exist (404).

    Retries a few times when the network fails or the server is busy.
    """
    user_agent = "BHEU26-fetch-authors"
    if CONTACT_EMAIL:
        user_agent += f" (mailto:{CONTACT_EMAIL})"
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})

    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                answer = json.load(response)
            time.sleep(PAUSE_SECONDS)
            return answer
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            if error.code not in (429, 500, 502, 503, 504):  # not a temporary problem
                raise
            problem = f"server busy (HTTP {error.code})"
        except (urllib.error.URLError, TimeoutError) as error:
            problem = f"no connection ({error})"
        wait = 5 * 2 ** attempt  # 5, 10, 20, 40, 80 seconds
        print(f"  {problem}, retrying in {wait}s. No internet? MareNostrum login nodes "
              f"have none: run this on the laptop.", flush=True)
        time.sleep(wait)
    raise RuntimeError(f"No answer after 5 attempts: {url}")


class Cache:
    """Keeps every API answer in a .jsonl file, so no request is made twice."""

    def __init__(self, path):
        self.path = path
        self.answers = {}
        if path.exists():
            with path.open(encoding="utf-8") as file:
                for line in file:
                    item = json.loads(line)
                    self.answers[item["key"]] = item["answer"]

    def get(self, key, ask_api):
        """The saved answer for this key, or ask the API and save its answer."""
        if key not in self.answers:
            self.answers[key] = ask_api()
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps({"key": key, "answer": self.answers[key]}) + "\n")
        return self.answers[key]


def europepmc_search(query, cache):
    """Europe PMC records matching the query, with their author lists."""
    def ask_api():
        params = {"query": query, "resultType": "core", "format": "json", "pageSize": 10}
        answer = get_json(EUROPEPMC_SEARCH + "?" + urllib.parse.urlencode(params))
        records = answer["resultList"]["result"] if answer else []
        return [{field: record.get(field) for field in EUROPEPMC_FIELDS} for record in records]
    return cache.get("europepmc|" + query, ask_api)


def crossref_authors(doi, cache):
    """Crossref's author list for a DOI, or None if Crossref does not know the DOI."""
    def ask_api():
        answer = get_json(CROSSREF_WORKS + urllib.parse.quote(doi))
        return answer["message"].get("author", []) if answer else None
    return cache.get("crossref|" + doi, ask_api)


# ------------------------------------------------------------ finding a paper

def europepmc_lookups(paper):
    """Ways to find the paper, most precise first: (name, query, field, expected value).

    Order: Europe PMC id, journal DOI, then
      - journal papers:  title, then preprint DOI (the title finds the journal
                         record even when the export lost its DOI)
      - preprint papers: preprint DOI, then title
    """
    by_id, by_journal_doi, by_preprint_doi, by_title = [], [], [], []

    europepmc_id = paper.get("EuroPMC ID (pmid)", "").strip()
    if re.fullmatch(r"\d+|PPR\d+|PMC\d+", europepmc_id) and paper["Source"]:
        query = f"EXT_ID:{europepmc_id} AND SRC:{paper['Source']}"
        by_id = [("europepmc_id", query, "id", europepmc_id.lower())]

    journal_doi = paper["DOI"].strip()
    if journal_doi and journal_doi != NOT_AVAILABLE:
        by_journal_doi = [("DOI", f'DOI:"{journal_doi}"', "doi", journal_doi.lower())]

    preprint_doi = paper["latest_preprint_doi"].strip()
    if preprint_doi and preprint_doi != NOT_AVAILABLE:
        by_preprint_doi = [("latest_preprint_doi", f'DOI:"{preprint_doi}"', "doi", preprint_doi.lower())]

    key = title_key(paper["Article Title"])
    if key:
        title = paper["Article Title"].replace('"', " ")
        by_title = [("title", f'TITLE:"{title}"', "title", key)]

    if paper["Source"] == "PPR":
        return by_id + by_journal_doi + by_preprint_doi + by_title
    return by_id + by_journal_doi + by_title + by_preprint_doi


def is_same_paper(record, field, expected):
    """True if the record's id, DOI or title is the expected one."""
    value = record.get(field) or ""
    if field == "title":
        return title_key(value) == expected
    return value.strip().lower() == expected


def find_in_europepmc(paper, cache):
    """Return (record, name of the lookup that found it), or (None, "").

    A record from the same source as the paper's row is preferred (the journal
    record for a journal paper), so the lookups go on until one finds it. If
    none does, the first record found by any lookup is used (e.g. the preprint).
    """
    first_found = (None, "")
    for name, query, field, expected in europepmc_lookups(paper):
        records = [r for r in europepmc_search(query, cache)
                   if r.get("authorList") and is_same_paper(r, field, expected)]
        same_source = [r for r in records if r.get("source") == paper["Source"]]
        if same_source:
            return same_source[0], name
        if records and first_found[0] is None:
            first_found = (records[0], name)
    return first_found


# -------------------------------------------------------------- author lists

def has_full_first_name(author):
    """'Mihaly' is a full name; 'M', 'MJ' or 'M.-J.' are only initials."""
    return author["is_collective"] or any(c.islower() for c in author["first_name"])


def authors_from_europepmc(record):
    authors = []
    for item in record["authorList"]["author"]:
        if item.get("collectiveName"):
            authors.append(new_author(full_name=item["collectiveName"], is_collective=True))
            continue
        orcid = item.get("authorId") or {}
        affiliations = (item.get("authorAffiliationDetailsList") or {}).get("authorAffiliation", [])
        has_split_name = item.get("firstName") or item.get("lastName")
        authors.append(new_author(
            first_name=item.get("firstName", ""),
            last_name=item.get("lastName", ""),
            # Only when Europe PMC gives no first name and no surname (as in July)
            full_name="" if has_split_name else item.get("fullName", ""),
            initials=item.get("initials", ""),
            orcid=orcid.get("value", "") if orcid.get("type") == "ORCID" else "",
            affiliation=affiliations[0].get("affiliation", "") if affiliations else "",
        ))
    return authors


def authors_from_crossref(items):
    authors = []
    for item in items:
        if "family" not in item and "given" not in item:  # an organisation
            authors.append(new_author(full_name=item.get("name", ""), is_collective=True))
            continue
        affiliations = item.get("affiliation") or []
        authors.append(new_author(
            first_name=item.get("given", ""),
            last_name=item.get("family", ""),
            orcid=item.get("ORCID", "").replace("https://orcid.org/", "").replace("http://orcid.org/", ""),
            affiliation=affiliations[0].get("name", "") if affiliations else "",
        ))
    return authors


def new_author(first_name="", last_name="", full_name="", initials="", orcid="",
               affiliation="", is_collective=False):
    """One author as a dict. full_name is 'First Last' unless given (consortia)."""
    first_name, last_name = first_name.strip(), last_name.strip()
    return {
        "full_name": full_name.strip() or f"{first_name} {last_name}".strip(),
        "first_name": first_name,
        "last_name": last_name,
        "initials": initials,
        "orcid": orcid,
        "affiliation": affiliation,
        "is_collective": is_collective,
    }


def get_authors(paper, cache):
    """Author list of a paper and where it came from.

    Returns (authors, names_from, found_by, Europe PMC record or None).
    """
    record, found_by = find_in_europepmc(paper, cache)
    authors = authors_from_europepmc(record) if record else []
    names_from = "europepmc" if authors else ""

    if authors and all(has_full_first_name(a) for a in authors):
        return authors, names_from, found_by, record

    # Europe PMC missing or with initials only: try Crossref with the paper's DOIs
    dois = [paper["DOI"], paper["latest_preprint_doi"], (record or {}).get("doi") or ""]
    for doi in dois:
        doi = doi.strip()
        if not doi or doi == NOT_AVAILABLE:
            continue
        items = crossref_authors(doi, cache)
        crossref = authors_from_crossref(items) if items else []
        if crossref and (not authors or all(has_full_first_name(a) for a in crossref)):
            return crossref, "crossref", found_by or "crossref_doi", record

    return authors, names_from, found_by, record


# ------------------------------------------------------------------------ main

def main():
    parser = argparse.ArgumentParser(description="Get author full names from Europe PMC / Crossref.")
    parser.add_argument("input", help="a *_dedup.csv in data/, e.g. publications_october2026_dedup.csv")
    parser.add_argument("--limit", type=int, help="only the first N papers (for testing)")
    args = parser.parse_args()

    input_name = pathlib.Path(args.input).name
    stem = pathlib.Path(input_name).stem
    if not (DATA_DIR / input_name).exists():
        available = ", ".join(sorted(p.name for p in DATA_DIR.glob("*_dedup.csv")))
        sys.exit(f"Not found: data/{input_name} (names are case-sensitive)\nAvailable: {available}")
    papers = pd.read_csv(DATA_DIR / input_name, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    if args.limit:
        papers = papers.head(args.limit)
    year_column = next(c for c in YEAR_COLUMNS if c in papers.columns)
    cache = Cache(DATA_DIR / f"{stem}_api_cache.jsonl")

    author_rows, not_found = [], []
    for number, (paper_index, paper) in enumerate(papers.iterrows(), start=1):
        authors, names_from, found_by, record = get_authors(paper, cache)

        paper_info = {
            "paper_index": paper_index,           # row number in the input file, from 0
            "Year": paper[year_column],
            "Article Title": paper["Article Title"],
            "DOI": paper["DOI"],
            "latest_preprint_doi": paper["latest_preprint_doi"],
            # The Europe PMC record the paper was found as. Its DOI can be more
            # complete or more recent than the export's.
            "europepmc_source": (record or {}).get("source") or "",
            "europepmc_doi": (record or {}).get("doi") or "",
            "pmid": (record or {}).get("pmid") or "",
            "pmcid": (record or {}).get("pmcid") or "",
        }
        if not authors:
            not_found.append(paper_info)
        for position, author in enumerate(authors, start=1):
            author_rows.append({**paper_info,
                                "author_position": position,
                                "n_authors": len(authors),
                                **author,
                                "names_from": names_from,
                                "found_by": found_by})

        if number % 100 == 0:
            print(f"  {number}/{len(papers)} papers")

    authors_table = pd.DataFrame(author_rows)
    authors_table.to_csv(DATA_DIR / f"{stem}_authors.csv", index=False)
    pd.DataFrame(not_found).to_csv(DATA_DIR / f"{stem}_authors_not_found.csv", index=False)

    found = len(papers) - len(not_found)
    print(f"{input_name}: authors found for {found}/{len(papers)} papers, "
          f"{len(authors_table)} author rows")
    if len(authors_table):
        print("  names from:", authors_table.drop_duplicates("paper_index")["names_from"].value_counts().to_dict())
        people = authors_table[~authors_table["is_collective"]]
        initials_only = (~people["first_name"].map(lambda n: any(c.islower() for c in n))).sum()
        print(f"  people: {len(people)}, with initials only: {initials_only}")


if __name__ == "__main__":
    main()
