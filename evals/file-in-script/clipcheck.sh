#!/usr/bin/env bash
# clipcheck.sh FILE: how many clips of a listening test's clips.json scored below 3.5
if [ ! -f "${1:-}" ]; then
    echo "clipcheck: '${1:-}' is not a file" >&2
    exit 1
fi
python3 -c 'import json, sys; print(sum(c["score"] < 3.5 for c in json.load(open(sys.argv[1]))), "clips below 3.5")' "$1"
