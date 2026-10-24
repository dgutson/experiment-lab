#!/usr/bin/env bash
# Runs the eval cases of plugin/evals with this clone's code. Options go to
# claude plugin eval, e.g. bin/evals.sh --case dead-end --runs 1 --ablation none
set -euo pipefail
clone=$(cd "$(dirname "$0")/.." && pwd)
cache=${XDG_CACHE_HOME:-$HOME/.cache}
# The runs' sandbox hides the git repository that holds the plugin, so the
# commands come from a copy of this clone installed in a venv of its own. The
# sandbox reads only the run's folder and the folders on PATH: the venv itself
# is on PATH so that its packages can be read, and its Python is the system's.
venv=$cache/experiment-lab-evals-venv
uv venv -q --allow-existing --python-preference only-system "$venv"
uv pip install -q --python "$venv/bin/python" --reinstall-package experiment-lab "$clone"
export PATH=$venv:$venv/bin:$PATH
# The agent's shell writes below the run's folder only, and nowhere below /tmp,
# so the runs' folders are made elsewhere.
export TMPDIR=$cache/experiment-lab-evals
mkdir -p "$TMPDIR"
exec claude plugin eval "$clone/plugin" --scaffold --allow-tools Bash Write Edit --trust-plugin "$@"
