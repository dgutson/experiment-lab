#!/usr/bin/env bash
set -euo pipefail
"$(dirname "$0")/../lab.sh" audio-codec
python3 "$(dirname "$0")/../panels.py"
cp "$(dirname "$0")/clipcheck.sh" .
