#!/bin/bash
# Download the BIP! DB v23 files listed in files.txt, one after the other
# (parallel downloads give no extra speed on glogin4), and check each md5.
#
# Run on glogin4, the only MareNostrum node with internet:
#   cd /gpfs/scratch/bsc64/MN4/bsc64/bsc64212/BioHackathonEU-2026/pip-services/finder/bip_db_v23
#   setsid nohup bash download.sh > download.log 2>&1 < /dev/null &
# Progress: bash status.sh (any node, or the laptop through the /gpfs mount).
#
# Running it again continues unfinished files and skips verified ones (*.md5ok).
# One curl per file keeps every process below glogin4's CPU limit (ulimit -t 300).

set -u
cd "$(dirname "$0")"
URL=https://zenodo.org/api/records/22300293/files

log() { echo "$(date '+%F %T') $*"; }
size_of() { stat -c %s "$1" 2>/dev/null || echo 0; }

log "BEGIN BIP! DB v23 download on $(hostname)"
failed=0
while read -r f bytes md5; do
    if [ -f "$f.md5ok" ]; then
        log "SKIP $f (already verified)"
        continue
    fi
    log "START $f ($bytes bytes)"
    for attempt in $(seq 1 10); do
        [ "$(size_of "$f")" -ge "$bytes" ] && break
        # -C - continues a partial file instead of starting again
        curl -sS -L -C - --retry 5 --retry-delay 10 --connect-timeout 30 \
             -o "$f" "$URL/$f/content" < /dev/null \
            || log "RETRY $f attempt $attempt, curl exit $?, $(size_of "$f") of $bytes bytes"
        [ "$(size_of "$f")" -ge "$bytes" ] || sleep 30
    done
    if [ "$(size_of "$f")" -ne "$bytes" ]; then
        log "FAILED $f: $(size_of "$f") of $bytes bytes"
        failed=$((failed + 1))
        continue
    fi
    if [ "$(md5sum "$f" | cut -d' ' -f1)" = "$md5" ]; then
        echo "$md5  $f" > "$f.md5ok"
        log "OK $f (md5 verified)"
    else
        log "FAILED $f: md5 mismatch, delete the file and run again"
        failed=$((failed + 1))
    fi
done < <(grep -vE '^#|^$' files.txt)

{
    echo "BIP! DB v23, Zenodo record 22300293 (concept DOI 10.5281/zenodo.4386934), published 2026-09-07"
    echo "Downloaded on $(hostname), finished $(date '+%F %T')"
    echo "md5 verified:"
    cat ./*.md5ok 2>/dev/null
} > VERSION.txt

if [ "$failed" -eq 0 ]; then
    log "ALL DONE: every file verified"
else
    log "ALL DONE WITH ERRORS: $failed file(s) failed, run download.sh again"
fi
