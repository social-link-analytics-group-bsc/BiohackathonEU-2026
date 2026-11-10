"""Attach the BIP! DB v23 indicators and the OpenAlex metrics to every paper (plan_bip.md, 1.3-1.5).

Input, in <BIP_SERVICES_DIR>/finder/:
  <dataset>/doi_check.tsv        check_dois.py: verified DOI, candidate DOIs, PMID, PMCID
  bip_db_v23/filtered/*.tsv      filter.sbatch: the BIP! rows of the ELIXIR papers
  cache/api_cache.jsonl          the OpenAlex works fetched by check_dois.py

Each paper is matched to its BIP! row by, in order: doi_final, its other
candidate DOIs (sheet, Europe PMC, preprint), PMID, PMCID (matched_by says
which). The same paper under its DOI and under its PMID/PMCID has the same
citation count for 99.8% of the papers; the DOI is preferred.

FWCI, 3-year FWCI and the classes within the topic are given by BIP! per
(DOI, OpenAlex topic), and BIP!'s topics are not always today's OpenAlex
topics. One row is kept per paper (topic_choice says which):
  primary         the paper's OpenAlex primary topic
  openalex_other  else its next OpenAlex topic that BIP! has, in OpenAlex's order
  bip_only        else BIP!'s topic, when BIP! gives only one for the DOI
n_topics is the number of topics BIP! gives for the DOI. An empty FWCI with a
topic means the expected citations of that topic and year are 0 (recent papers).

Writes, in <BIP_SERVICES_DIR>/finder/<dataset>/:
  bip_indicators.tsv    1.3  popularity (AttRank), influence (PageRank), impulse
                             (3-year CC), cc (citation count) and their classes
                             (C1 = top 0.01% ... C4 = top 10%, C5 = the other 90%)
  bip_extras.tsv        1.4  RAM and its class; FWCI, 3-year FWCI and the 4 classes
                             within the primary topic
  openalex_metrics.tsv  1.5  OpenAlex fwci and percentiles, citations, open access,
                             type, primary topic. Not BIP!: OpenAlex recomputes
                             them every night (openalex_retrieved is the cache date)
  <dataset>_with_bip.csv     the original table (data/<dataset>_dedup.csv, every
                             column, one row per paper) with doi_final, doi_status
                             and the indicators of the 2024 analysis (step 02):
                             popularity, influence, impulse, cc, pop_class,
                             inf_class, imp_class

Usage:
  python scripts/bip_join.py publications_october2026
"""
import argparse
import datetime
import json
import sys

import pandas as pd

from check_dois import DATA_DIR, SERVICES_DIR

BIP_VERSION = "v23"

# The BIP! columns of the 2024 analysis (step 02), for <dataset>_with_bip.csv
COLUMNS_2024 = ["popularity", "influence", "impulse", "cc", "pop_class", "inf_class", "imp_class"]
FILTERED = SERVICES_DIR / "finder" / "bip_db_v23" / "filtered"

# Indicator: (filtered file, score column, class column)
BASIC = {
    "popularity": ("AttRank_a0.2_b0.5_c0.3_rho-0.16_year2021-2023_error1e-12.tsv", "popularity", "pop_class"),
    "influence": ("PR_a0.5_error1e-12.tsv", "influence", "inf_class"),
    "impulse": ("3-year_CC.tsv", "impulse", "imp_class"),
    "cc": ("CC.tsv", "cc", "cc_class"),
}
RAM = ("RAM_c0.6_year2023.tsv", "ram", "ram_class")
TOPIC_CLASSES = ["topic_pagerank_class", "topic_attrank_class", "topic_3cc_class", "topic_cc_class"]

# BIP! identifier type of each kind of identifier of a paper
BIP_TYPES = {"doi": "doi", "pmid": "pmid", "pmcid": "pmc"}


def read_tsv(name, columns):
    return pd.read_csv(FILTERED / name, sep="\t", header=None, names=columns, dtype=str, keep_default_na=False)


def pid_scores(name):
    """{(identifier type, identifier): (score, class)} of a PID-level file."""
    table = read_tsv(name, ["id", "type", "score", "class"])
    return {(t, i): (s, c) for i, t, s, c in table.itertuples(index=False)}


def per_doi_topic(name, columns):
    """{doi: {topic: [values]}} of a file given per (DOI, topic)."""
    table = read_tsv(name, ["id", "topic", *columns])
    result = {}
    for row in table.itertuples(index=False):
        result.setdefault(row[0], {})[row[1]] = list(row[2:])
    return result


def load_openalex_works():
    """{doi: OpenAlex work} from the check_dois.py cache."""
    path = SERVICES_DIR / "finder" / "cache" / "api_cache.jsonl"
    works = {}
    with path.open(encoding="utf-8") as file:
        for line in file:
            item = json.loads(line)
            if item["key"].startswith("openalex|doi:") and item["answer"]:
                works[item["key"].removeprefix("openalex|doi:")] = item["answer"]
    retrieved = datetime.date.fromtimestamp(path.stat().st_mtime).isoformat()
    return works, retrieved


def short_id(url):
    """'https://openalex.org/T11937' -> 'T11937'."""
    return (url or "").rsplit("/", 1)[-1]


def choose_topic(bip_topics, work):
    """(topic, topic_choice) of the BIP! topic rows to keep for a paper."""
    primary = short_id((work.get("primary_topic") or {}).get("id"))
    if primary in bip_topics:
        return primary, "primary"
    for topic in (short_id(t.get("id")) for t in work.get("topics") or []):
        if topic in bip_topics:
            return topic, "openalex_other"
    if len(bip_topics) == 1:
        return next(iter(bip_topics)), "bip_only"
    return "", ""


def candidates(paper):
    """The paper's identifiers as (kind, value), in matching order."""
    dois = [paper["doi_final"], paper["doi_sheet"], paper["doi_europepmc"], paper["doi_preprint"]]
    result = []
    for doi in dois:
        if doi and ("doi", doi) not in result:
            result.append(("doi", doi))
    if paper["pmid"]:
        result.append(("pmid", paper["pmid"]))
    if paper["pmcid"]:
        result.append(("pmcid", paper["pmcid"].lower()))
    return result


def main():
    parser = argparse.ArgumentParser(description="Attach BIP! DB v23 indicators and OpenAlex metrics to the papers.")
    parser.add_argument("dataset", help="e.g. publications_october2026 (reads finder/<dataset>/doi_check.tsv)")
    args = parser.parse_args()

    out_dir = SERVICES_DIR / "finder" / args.dataset
    if not (out_dir / "doi_check.tsv").exists():
        sys.exit(f"Not found: {out_dir / 'doi_check.tsv'}: run scripts/check_dois.py {args.dataset} first")
    papers = pd.read_csv(out_dir / "doi_check.tsv", sep="\t", dtype=str, keep_default_na=False)

    basic = {name: pid_scores(file) for name, (file, _, _) in BASIC.items()}
    ram = pid_scores(RAM[0])
    fwci = per_doi_topic("FWCI.tsv", ["score"])
    fwci3 = per_doi_topic("3-year_FWCI.tsv", ["score"])
    topic_classes = per_doi_topic("topic_based_impact_classes.tsv", TOPIC_CLASSES)
    works, retrieved = load_openalex_works()

    indicators, extras, metrics = [], [], []
    for paper in papers.to_dict("records"):
        ids = {"paper_index": paper["paper_index"], "doi_final": paper["doi_final"],
               "doi_status": paper["doi_status"]}

        # 1.3: the first identifier with a BIP! row
        match = next(((kind, value) for kind, value in candidates(paper)
                      if (BIP_TYPES[kind], value) in basic["cc"]), None)
        key = (BIP_TYPES[match[0]], match[1]) if match else None
        row = {**ids, "matched_by": match[0] if match else "", "matched_id": match[1] if match else ""}
        for name, (_, score_column, class_column) in BASIC.items():
            score, cls = basic[name].get(key, ("", "")) if key else ("", "")
            row[score_column], row[class_column] = score, cls
        indicators.append({**row, "bip_db_version": BIP_VERSION})

        # 1.4: RAM by the same identifier; topic indicators by DOI and primary topic
        ram_score, ram_class = ram.get(key, ("", "")) if key else ("", "")
        doi = next((d for d in [paper["doi_final"], match[1] if match and match[0] == "doi" else ""]
                    if d in fwci or d in topic_classes), "")
        work = works.get(paper["doi_final"]) or works.get(doi) or {}
        topic, topic_choice = choose_topic(set(fwci.get(doi, {})) | set(topic_classes.get(doi, {})), work)
        topic_names = {short_id(t.get("id")): t.get("display_name", "") for t in work.get("topics") or []}
        classes = topic_classes.get(doi, {}).get(topic, [""] * len(TOPIC_CLASSES))
        extras.append({
            **ids, "matched_by": row["matched_by"], "ram": ram_score, "ram_class": ram_class,
            "topic_doi": doi, "topic": topic, "topic_name": topic_names.get(topic, ""),
            "topic_choice": topic_choice, "n_topics": len(fwci.get(doi, {})),
            "fwci_bip": fwci.get(doi, {}).get(topic, [""])[0],
            "fwci3_bip": fwci3.get(doi, {}).get(topic, [""])[0],
            **dict(zip(TOPIC_CLASSES, classes)),
            "bip_db_version": BIP_VERSION,
        })

        # 1.5: OpenAlex metrics of the verified DOI's work
        percentile = work.get("citation_normalized_percentile") or {}
        by_year = work.get("cited_by_percentile_year") or {}
        primary = work.get("primary_topic") or {}
        metrics.append({
            **ids, "openalex_id": short_id(work.get("id")),
            "fwci": work.get("fwci", ""),
            "citation_normalized_percentile": percentile.get("value", ""),
            "is_in_top_1_percent": percentile.get("is_in_top_1_percent", ""),
            "is_in_top_10_percent": percentile.get("is_in_top_10_percent", ""),
            "cited_by_percentile_year_min": by_year.get("min", ""),
            "cited_by_percentile_year_max": by_year.get("max", ""),
            "cited_by_count": work.get("cited_by_count", ""),
            "is_oa": (work.get("open_access") or {}).get("is_oa", ""),
            "oa_status": (work.get("open_access") or {}).get("oa_status", ""),
            "type": work.get("type", ""),
            "primary_topic": short_id(primary.get("id")),
            "subfield": (primary.get("subfield") or {}).get("display_name", ""),
            "field": (primary.get("field") or {}).get("display_name", ""),
            "domain": (primary.get("domain") or {}).get("display_name", ""),
            "openalex_retrieved": retrieved if work else "",
        })

    for name, rows in [("bip_indicators", indicators), ("bip_extras", extras), ("openalex_metrics", metrics)]:
        pd.DataFrame(rows).to_csv(out_dir / f"{name}.tsv", sep="\t", index=False)

    # The original table with the verified DOI and the 2024 indicators
    original = pd.read_csv(DATA_DIR / f"{args.dataset}_dedup.csv", dtype=str, keep_default_na=False,
                           encoding="utf-8-sig")
    original.insert(0, "paper_index", original.index.astype(str))
    columns_2024 = pd.DataFrame(indicators)[["paper_index", "doi_final", "doi_status", *COLUMNS_2024]]
    original.merge(columns_2024, on="paper_index", how="left").to_csv(
        out_dir / f"{args.dataset}_with_bip.csv", index=False)

    table = pd.DataFrame(indicators)
    found = (table["matched_by"] != "").sum()
    print(f"{args.dataset}: {len(table)} papers, {found} with BIP! indicators ({found / len(table):.1%})")
    print("  matched_by:", table["matched_by"].replace("", "not in BIP!").value_counts().to_dict())
    extra = pd.DataFrame(extras)
    print("  topic_choice:", extra["topic_choice"].replace("", "no topic").value_counts().to_dict())
    print(f"  FWCI (not empty): {(extra['fwci_bip'] != '').sum()} papers")
    print(f"  OpenAlex work: {(pd.DataFrame(metrics)['openalex_id'] != '').sum()} papers")
    print(f"  -> {out_dir}/bip_indicators.tsv, bip_extras.tsv, openalex_metrics.tsv, "
          f"{args.dataset}_with_bip.csv")


if __name__ == "__main__":
    main()
