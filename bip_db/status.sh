#!/bin/bash
# Progress of download.sh: each file against its Zenodo size, then the last log lines.
# Works on any node, or on the laptop through the /gpfs mount.

cd "$(dirname "$0")"
grep -vE '^#|^$' files.txt | while read -r f bytes md5; do
    have=$(stat -c %s "$f" 2>/dev/null || echo 0)
    state=waiting
    [ "$have" -gt 0 ] && state=downloading
    [ -f "$f.md5ok" ] && state=verified
    echo "$f $have $bytes $state"
done | awk '{ h += $2; t += $3; printf "%-66s %6.1f%%  %s\n", $1, 100 * $2 / $3, $4 }
            END { printf "%-66s %6.1f%%  %.2f of %.2f GB\n", "TOTAL", 100 * h / t, h / 1e9, t / 1e9 }'
echo
tail -n 3 download.log 2>/dev/null
