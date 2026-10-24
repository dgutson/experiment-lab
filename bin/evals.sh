#!/usr/bin/env bash
# Runs the eval cases of evals/ with this clone's code. Options go to
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
# claude plugin eval reads the cases from a folder inside the plugin's, with
# no links, and they are kept out of plugin/ so that an installed plugin has
# none: the run's plugin is a copy of plugin/ and evals/, and the results go
# to evals/results/.
run=$(mktemp -d)
trap 'rm -rf "$run"' EXIT
cp -r "$clone/plugin/." "$run/"
mkdir "$run/evals"
find "$clone/evals" -mindepth 1 -maxdepth 1 ! -name results -exec cp -r {} "$run/evals/" \;
claude plugin eval "$run" --scaffold --allow-tools Bash Write Edit --trust-plugin \
    --output-dir "$clone/evals/results/$(date -u +%Y-%m-%dT%H-%M-%S)" "$@"
