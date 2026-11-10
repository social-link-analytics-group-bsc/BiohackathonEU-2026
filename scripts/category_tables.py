"""One table per category of a dataset (column Status of publications_october2026), with the
BIP! indicators and, separately, the extra indicators.

The categories of publications_october2026 (column Status): "RI supported" and
"Fine/Probably Fine".

Input, for the dataset:
  data/<dataset>_dedup.csv                         the original table, one row per paper
  <BIP_SERVICES_DIR>/finder/<dataset>/doi_check.tsv, bip_indicators.tsv,
      bip_extras.tsv, openalex_metrics.tsv         check_dois.py, bip_join.py

Writes, in <BIP_SERVICES_DIR>/finder/<dataset>/, for each category:
  <dataset>_<category>_bip.csv    the 8 BIP! values that are identical to those
                                  of the old code (pipeline/s02_bip_scores.py,
                                  BIP! API) for every paper: popularity,
                                  influence, impulse, cc and their 4 classes
  <dataset>_<category>_extra.csv  the extra indicators: the rest of BIP! v23
                                  (RAM, FWCI, 3-year FWCI, classes within the
                                  topic) and OpenAlex (fwci, percentiles,
                                  citations, open access, type, subfield...)
Each table starts with the columns that identify the paper. The full original
table with the 2024 indicators is <dataset>_with_bip.csv (bip_join.py).
See variables_bip.md for what every column means.

Usage:
  python scripts/category_tables.py                         # publications_october2026, by Status
  python scripts/category_tables.py publications_october2026 --by Status
"""
import argparse
import re
import sys

import pandas as pd

from check_dois import DATA_DIR, SERVICES_DIR

# Columns that identify the paper, from the original table (those it has)
ID_COLUMNS = ["paper_index", "Article Title", "Journal Name", "Pub Year", "Year", "Source",
              "EuroPMC ID (pmid)", "DOI"]

# Identical to the old code for every paper (checked on 4,981 DOIs, 2026-11-10)
BIP_COLUMNS = ["popularity", "influence", "impulse", "cc", "pop_class", "inf_class", "imp_class", "cc_class"]

EXTRA_BIP = ["ram", "ram_class", "fwci_bip", "fwci3_bip", "topic", "topic_name", "topic_choice", "n_topics",
             "topic_pagerank_class", "topic_attrank_class", "topic_3cc_class", "topic_cc_class"]
EXTRA_OPENALEX = ["fwci", "citation_normalized_percentile", "is_in_top_1_percent", "is_in_top_10_percent",
                  "cited_by_percentile_year_min", "cited_by_percentile_year_max", "cited_by_count",
                  "is_oa", "oa_status", "type", "primary_topic", "subfield", "field", "domain",
                  "openalex_retrieved"]


def read(path, sep=","):
    return pd.read_csv(path, sep=sep, dtype=str, keep_default_na=False, encoding="utf-8-sig")


def slug(category):
    """'Fine/Probably Fine' -> 'fine_probably_fine'."""
    return re.sub(r"[^a-z0-9]+", "_", category.lower()).strip("_")


def main():
    parser = argparse.ArgumentParser(description="One table per category, with the BIP! and extra indicators.")
    parser.add_argument("dataset", nargs="?", default="publications_october2026")
    parser.add_argument("--by", default="Status", help="column of the original table with the categories")
    args = parser.parse_args()

    original = read(DATA_DIR / f"{args.dataset}_dedup.csv")
    if args.by not in original.columns:
        sys.exit(f"data/{args.dataset}_dedup.csv has no column {args.by!r}")
    original.insert(0, "paper_index", original.index.astype(str))
    folder = SERVICES_DIR / "finder" / args.dataset

    papers = original[[c for c in ID_COLUMNS if c in original.columns] + [args.by]]
    doi = read(folder / "doi_check.tsv", "\t")[["paper_index", "doi_final", "doi_status", "reason"]]
    papers = papers.merge(doi.rename(columns={"reason": "doi_review_reason"}), on="paper_index")
    bip = read(folder / "bip_indicators.tsv", "\t")[["paper_index", "matched_by", *BIP_COLUMNS]]
    extra = read(folder / "bip_extras.tsv", "\t")[["paper_index", *EXTRA_BIP]].merge(
        read(folder / "openalex_metrics.tsv", "\t")[["paper_index", *EXTRA_OPENALEX]], on="paper_index")

    tables = {"bip": papers.merge(bip, on="paper_index"), "extra": papers.merge(extra, on="paper_index")}
    for category, rows in papers.groupby(args.by, sort=False):
        if not category:
            continue
        for kind, table in tables.items():
            path = folder / f"{args.dataset}_{slug(category)}_{kind}.csv"
            table[table["paper_index"].isin(rows["paper_index"])].to_csv(path, index=False)
        part = tables["bip"][tables["bip"]["paper_index"].isin(rows["paper_index"])]
        found = part[part["matched_by"] != ""]
        top10 = {c: f"{found[c].isin(['C1', 'C2', 'C3', 'C4']).mean():.0%}" for c in ["pop_class", "inf_class"]}
        print(f"{category!r}: {len(part)} papers, {len(found)} with BIP! indicators, "
              f"median cc {pd.to_numeric(found['cc']).median():.0f}, top 10%: {top10}")
        print(f"  -> {args.dataset}_{slug(category)}_bip.csv, {args.dataset}_{slug(category)}_extra.csv")
    print(f"in {folder}")


if __name__ == "__main__":
    main()
