#!/usr/bin/env bash
# The lab of lab.sh, and history.jsonl as the transcript of the session it
# continues, where the feedback module reads it: the run keeps none.
set -euo pipefail
"$(dirname "$0")/../lab.sh" audio-codec
session=$(head -1 "$(dirname "$0")/history.jsonl" | python3 -c 'import json, sys; print(json.load(sys.stdin)["sessionId"])')
# In ~/.claude, since the agent's shell cannot read the run's config folder.
mkdir -p "$HOME/.claude/projects/history"
cp "$(dirname "$0")/history.jsonl" "$HOME/.claude/projects/history/$session.jsonl"
