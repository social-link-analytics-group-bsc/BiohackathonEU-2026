"""Keep one row per paper: its definitive journal version.

The Europe PMC export lists the same paper several times:
  - in PubMed (Source MED) and in PubMed Central (PMC), often one copy without DOI
  - as a preprint (PPR) and as the published journal article
  - once per version: F1000/Open Research Europe ".1", ".2"; OSF "_v1"; Research Square "/v1"

Which rows are the same paper:
  Rows that share a DOI (ignoring the version) or a title (ignoring case,
  punctuation and HTML tags).

Which row is kept for each paper (the first rule that tells them apart decides):
  1. The journal record, whether or not it has a DOI:
     MED, then PMC, then other sources (CTX, AGR...), preprints (PPR) last.
  2. A row with a DOI.
  3. The most recent year.
  4. The highest version.

DOI columns in the output (each holds one kind of DOI only):
  DOI                  the journal DOI. NA when the paper has no journal record
                       (preprint only) or its journal record has no DOI.
  latest_preprint_doi  the most recent preprint DOI of the paper (latest year,
                       then highest version). NA when the paper has no preprint.
                       For example the bioRxiv preprint of a journal article, or
                       version .3 of an Open Research Europe article whose
                       journal record is version .2.

Usage:
  python scripts/dedup_papers.py publications_october2026.csv
Writes, in data/:
  publications_october2026_dedup.csv               one row per paper
  publications_october2026_removed_duplicates.csv  the rows that were dropped
"""
import html
import pathlib
import re
import sys

import pandas as pd

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

# The year column has a different name in each export
YEAR_COLUMNS = ["Year", "Pub Year"]  # publications_october2026.csv has "Pub Year"

# Spreadsheet programs turn an empty DOI into 0
MISSING_DOI = ["", "0"]

# Written in the output DOI columns when there is no DOI of that kind
NOT_AVAILABLE = "NA"

# Lower number = more definitive. Sources not listed here get OTHER_SOURCE.
SOURCE_PRIORITY = {"MED": 0, "PMC": 1, "PPR": 3}
OTHER_SOURCE = 2

# The only HTML tags found in titles
HTML_TAGS = re.compile(r"</?(?:i|sup|sub)>", re.IGNORECASE)

# DOIs that carry a version number. The version is always a separate part after
# the article id. Not a version: MDPI Viruses 10.3390/v18010084, where the
# article id itself starts with "v".
VERSIONED_DOIS = [
    re.compile(r"^(10\.\d+/.+)/v(\d+)$"),                 # Research Square rs.3.rs-8767029/v1
    re.compile(r"^(10\.\d+/osf\.io/[a-z0-9]+)_v(\d+)$"),  # OSF / BioHackrXiv osf.io/zah28_v2
    re.compile(r"^(10\.12688/.+\.\d+)\.(\d+)$"),          # F1000 family f1000research.146301.2
]


# ---------------------------------------------------------------- text helpers

def clean_text(text):
    """'&lt;i&gt;Boechera&lt;/i&gt; or not?' -> 'Boechera or not?'"""
    text = html.unescape(str(text))           # &lt;i&gt; -> <i>, &amp; -> &
    text = HTML_TAGS.sub("", text)            # <i>Boechera</i> -> Boechera
    return re.sub(r"\s+", " ", text).strip()  # line breaks, double spaces -> one space


def title_key(title):
    """Title reduced to lowercase letters and digits, to compare titles.

    'Taxonomic refinement of <i>Bacillus</i>.' -> 'taxonomicrefinementofbacillus'
    """
    title = clean_text(title).lower()
    return re.sub(r"[^a-z0-9]", "", title)


def has_doi(doi):
    return str(doi).strip() not in MISSING_DOI


def split_doi_version(doi):
    """Split a DOI into (DOI without version, version number).

    '10.12688/f1000research.146301.2' -> ('10.12688/f1000research.146301', 2)
    '10.1093/nar/gkag591'             -> ('10.1093/nar/gkag591', 0)
    """
    doi = str(doi).strip().lower()
    for pattern in VERSIONED_DOIS:
        match = pattern.match(doi)
        if match:
            return match.group(1), int(match.group(2))
    return doi, 0


# ------------------------------------------------------- grouping into papers

def assign_paper_ids(df):
    """Return one paper id per row; rows of the same paper get the same id.

    Two rows are the same paper if they share a DOI without version or a title
    key. This also works indirectly: if A and B share a DOI and B and C share a
    title, then A, B and C are all one paper.
    """
    # Start with every row as its own paper; paper_of[i] points to another row
    # of the same paper, and the row that points to itself names the paper.
    paper_of = list(range(len(df)))

    def paper(row):
        while paper_of[row] != row:
            row = paper_of[row]
        return row

    for column in ["_doi_base", "_title_key"]:
        first_row_with = {}
        for row, value in enumerate(df[column]):
            if value == "":
                continue
            if value in first_row_with:
                paper_of[paper(row)] = paper(first_row_with[value])  # merge the two papers
            else:
                first_row_with[value] = row

    return [paper(row) for row in range(len(df))]


# ------------------------------------------------------------- choosing rows

def add_helper_columns(papers):
    """Copy of the table with the columns used to group and rank rows."""
    df = papers.copy()
    year_column = next(c for c in YEAR_COLUMNS if c in df.columns)

    df["_has_doi"] = df["DOI"].map(has_doi)
    df["_doi_base"] = df["DOI"].map(lambda d: split_doi_version(d)[0] if has_doi(d) else "")
    df["_doi_version"] = df["DOI"].map(lambda d: split_doi_version(d)[1])
    df["_title_key"] = df["Article Title"].map(title_key)
    df["_source_priority"] = df["Source"].map(SOURCE_PRIORITY).fillna(OTHER_SOURCE)
    # Empty years become 0 (oldest). Not NaN: some pandas versions cannot sort
    # several columns when one contains NaN.
    df["_year"] = pd.to_numeric(df[year_column], errors="coerce").fillna(0)
    df["_paper_id"] = assign_paper_ids(df)
    return df


def latest_preprint_doi_per_paper(df):
    """Most recent preprint DOI of each paper: latest year, then highest version."""
    preprints = df[(df["Source"] == "PPR") & df["_has_doi"]]
    preprints = preprints.sort_values(["_year", "_doi_version"], ascending=False)
    first_per_paper = preprints.drop_duplicates("_paper_id")
    return first_per_paper.set_index("_paper_id")["DOI"]


def keep_definitive(papers):
    """One row per paper, chosen by the rules in the module docstring."""
    df = add_helper_columns(papers)

    # Put the best row of each paper first, then keep that first row
    ranked = df.sort_values(
        ["_source_priority", "_has_doi", "_year", "_doi_version"],
        ascending=[True, False, False, False],
    )
    kept = ranked.drop_duplicates("_paper_id").sort_index()

    # Preprint DOI column, from all rows of the paper (not only the kept one)
    latest_preprint = kept["_paper_id"].map(latest_preprint_doi_per_paper(df))
    kept["latest_preprint_doi"] = latest_preprint.fillna(NOT_AVAILABLE)

    # Journal DOI column: never a preprint DOI
    is_journal_doi = (kept["Source"] != "PPR") & kept["_has_doi"]
    kept["DOI"] = kept["DOI"].where(is_journal_doi, NOT_AVAILABLE)

    # Output columns: the original ones, with latest_preprint_doi right after DOI
    columns = list(papers.columns)
    columns.insert(columns.index("DOI") + 1, "latest_preprint_doi")
    return kept[columns]


# ----------------------------------------------------------------------- main

def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: python dedup_papers.py <file in data/>, e.g. publications_october2026.csv")
    input_name = pathlib.Path(sys.argv[1]).name
    if not (DATA_DIR / input_name).exists():
        available = ", ".join(sorted(p.name for p in DATA_DIR.glob("*.csv")))
        sys.exit(f"Not found: data/{input_name} (names are case-sensitive)\nAvailable: {available}")

    papers = pd.read_csv(DATA_DIR / input_name, dtype=str,
                         keep_default_na=False, encoding="utf-8-sig")
    papers["Article Title"] = papers["Article Title"].map(clean_text)

    kept = keep_definitive(papers)
    removed = papers.drop(kept.index)

    stem = pathlib.Path(input_name).stem
    kept.to_csv(DATA_DIR / f"{stem}_dedup.csv", index=False)
    removed.to_csv(DATA_DIR / f"{stem}_removed_duplicates.csv", index=False)

    has_journal = kept["DOI"] != NOT_AVAILABLE
    has_preprint = kept["latest_preprint_doi"] != NOT_AVAILABLE
    print(f"{input_name}: {len(papers)} rows -> kept {len(kept)} papers, removed {len(removed)} rows")
    print(f"  journal DOI only:     {(has_journal & ~has_preprint).sum()}")
    print(f"  journal + preprint:   {(has_journal & has_preprint).sum()}")
    print(f"  preprint DOI only:    {(~has_journal & has_preprint).sum()}")
    print(f"  no DOI of either kind: {(~has_journal & ~has_preprint).sum()}")


if __name__ == "__main__":
    main()
