#!/usr/bin/env bash
set -euo pipefail
"$(dirname "$0")/../lab.sh" audio-codec
python3 "$(dirname "$0")/../codec_sweep.py"
