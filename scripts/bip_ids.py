"""Write the identifiers of the ELIXIR papers, to filter the BIP! DB files (plan_bip.md, 1.3).

Every identifier a paper may have in BIP! DB, from both datasets:
  - its candidate DOIs: sheet, Europe PMC and preprint (normalized as in check_dois.py)
  - doi_final, when check_dois.py has written doi_check.tsv (it can be a DOI
    found in OpenAlex that is none of the above)
  - its PMID and PMCID (BIP! types "pmid" and "pmc": '10000302', 'pmc10000344')
The filter keeps every BIP! row with one of these identifiers, whatever its
type. The type column is kept, so the join decides later which row is the paper's.

Writes <BIP_SERVICES_DIR>/finder/elixir_ids.txt: one lowercase identifier per line.

Usage:
  python scripts/bip_ids.py
"""
import pandas as pd

from check_dois import SERVICES_DIR, load_papers, normalize_doi

DATASETS = ["publications_october2026"]


def main():
    ids = set()
    for dataset in DATASETS:
        for paper in load_papers(dataset):
            ids |= {paper["doi_sheet"], paper["doi_europepmc"], paper["doi_preprint"],
                    paper["pmid"], paper["pmcid"].lower()}
        check = SERVICES_DIR / "finder" / dataset / "doi_check.tsv"
        if check.exists():
            doi_final = pd.read_csv(check, sep="\t", dtype=str, keep_default_na=False)["doi_final"]
            ids |= set(doi_final.map(normalize_doi))
    ids.discard("")

    out = SERVICES_DIR / "finder" / "elixir_ids.txt"
    out.write_text("\n".join(sorted(ids)) + "\n")
    print(f"{len(ids)} identifiers -> {out}")


if __name__ == "__main__":
    main()
