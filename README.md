# BioHackathon Europe 2026: BIP! indicators for ELIXIR publications

BIP! impact indicators (BIP! DB v23) and OpenAlex metrics for the ELIXIR
publications of October 2026, with every DOI checked first.

## Contents

| Folder | What |
|---|---|
| `data/publications_october2026.csv` | The ELIXIR publications export (2,918 rows; 2,774 papers after removing duplicates) |
| `scripts/` | The Python steps, in the order below |
| `bip_db/` | Download of BIP! DB v23 (Zenodo record 22300293) and the job that filters it to our papers. The database itself (28 GB) is not here |
| `results/publications_october2026/` | The tables made on 2026-11-10 |

## Steps

The scripts write to `$BIP_SERVICES_DIR/finder/` (default: the project's scratch
folder on MareNostrum). The steps marked "internet" run on a machine with
internet: the laptop, or glogin4 on MareNostrum.

1. `python scripts/dedup_papers.py publications_october2026.csv` makes one row
   per paper.
2. `python scripts/fetch_authors.py publications_october2026_dedup.csv`
   (internet) gets the authors, Europe PMC DOI, PMID and PMCID.
3. `python scripts/check_dois.py publications_october2026` (internet) checks
   the DOI of every paper. Optional: set `OPENALEX_API_KEY` for the title
   searches.
4. `python scripts/bip_ids.py` lists the paper identifiers for the filter.
5. Copy `bip_db/` to `$BIP_SERVICES_DIR/finder/bip_db_v23/`, then:
   - on glogin4: `setsid nohup bash download.sh > download.log 2>&1 < /dev/null &`
     (about 36 min, every file checked against its md5);
   - once the log says ALL DONE: `sbatch filter.sbatch` (about 3 min).
6. `python scripts/bip_join.py publications_october2026` attaches the
   indicators to every paper.
7. `python scripts/category_tables.py` makes one table per category (column
   `Status`).

## Results (2026-11-10)

| File | What |
|---|---|
| `doi_check.tsv`, `review_dois.csv` | The DOI check, and the 49 papers whose DOI a person should check |
| `bip_indicators.tsv` | popularity, influence, impulse, citation count and their classes (BIP! DB v23), for 2,733 of 2,774 papers (98.5%) |
| `bip_extras.tsv` | RAM, FWCI, 3-year FWCI, classes within the paper's topic (BIP! DB v23) |
| `openalex_metrics.tsv` | OpenAlex fwci, citation percentiles, citations, open access, type, subfield |
| `publications_october2026_with_bip.csv` | The original table with the verified DOI and the indicators of the 2024 analysis |
| `publications_october2026_<category>_bip.csv` | Per `Status` ("RI supported", "Fine/Probably Fine"): the 8 BIP! values, identical to the BIP! API on 2026-11-10 |
| `publications_october2026_<category>_extra.csv` | The same categories, with the extra BIP! and OpenAlex indicators |

All tables are linked by `paper_index`, the row number in
`publications_october2026_dedup.csv` (step 1).
