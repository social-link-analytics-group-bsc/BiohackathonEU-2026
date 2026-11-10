#!/bin/bash
#SBATCH --job-name=bip_filter
#SBATCH --account=bsc02
#SBATCH --qos=gp_debug
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --time=01:00:00
#SBATCH --output=filter_%j.log
#
# Keep only the rows of the ELIXIR papers from each BIP! DB v23 file (plan_bip.md, 1.3).
# Submit from this folder once download.sh has logged ALL DONE:
#   sbatch filter.sbatch
# Input:  the verified *.txt.gz (files.txt, *.md5ok) and ../elixir_ids.txt (scripts/bip_ids.py)
# Output: filtered/<file>.tsv: the rows whose identifier (first column) is in
#         elixir_ids.txt, with all the columns of the BIP! file, no header.
#         The log counts the rows per identifier type.

set -uo pipefail
cd "$SLURM_SUBMIT_DIR"
IDS=../elixir_ids.txt
[ -s "$IDS" ] || { echo "Missing or empty $IDS: run scripts/bip_ids.py first"; exit 1; }
mkdir -p filtered

filter() {
    local f=$1 out=filtered/${1%.txt.gz}.tsv
    # The identifier is matched in lower case, as written in elixir_ids.txt
    if zcat "$f" | awk -F'\t' 'NR == FNR { want[$1]; next } (tolower($1) in want)' "$IDS" - > "$out.part"; then
        mv "$out.part" "$out"
        echo "$(date '+%F %T') OK $f: $(wc -l < "$out") rows"
    else
        echo "$(date '+%F %T') FAILED $f"
    fi
}

echo "$(date '+%F %T') BEGIN on $(hostname), $(wc -l < "$IDS") identifiers"
for f in $(grep -vE '^#|^$' files.txt | cut -d' ' -f1); do
    if [ -f "$f.md5ok" ]; then
        filter "$f" &
    else
        echo "SKIP $f: not verified"
    fi
done
wait

echo "Rows per identifier type:"
for out in filtered/*.tsv; do
    case $out in *FWCI*|*topic_based*) continue ;; esac  # no type column (DOIs only)
    echo "  $out: $(cut -f2 "$out" | sort | uniq -c | tr -s ' \n' ' ')"
done
echo "$(date '+%F %T') FILTER DONE"
