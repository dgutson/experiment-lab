#!/usr/bin/env bash
set -euo pipefail
"$(dirname "$0")/../lab.sh" audio-codec
rec add protocol "Predict before running" --in audio-codec --by owner --quiet --text \
    "Every experiment's expected outcome is written as a record before its first run, and the finding afterwards says whether it held."
