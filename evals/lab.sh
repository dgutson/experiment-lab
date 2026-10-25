#!/usr/bin/env bash
# A case's starting point, in the run's home folder: the package experiment_lab
# in ~/experiment-lab, where the skill says the clone is; DuckDB's SQLite
# extension, since the run has no network to download it; and the lab of
# make_lab.py. It runs under evals/run.sh, whose venv, with the clone's
# package installed, is first on PATH.
#   lab.sh [PROJECT]    PROJECT's folder is the current folder; none without it
set -euo pipefail
mkdir "$HOME/experiment-lab"
cp -r "$(python3 -c 'import experiment_lab, os; print(os.path.dirname(experiment_lab.__file__))')" \
    "$HOME/experiment-lab/"
extensions=.duckdb/extensions/v$(python3 -c 'import duckdb; print(duckdb.__version__)')
real_home=$(getent passwd "$(id -u)" | cut -d: -f6)
mkdir -p "$HOME/$extensions"
cp -r "$real_home/$extensions/." "$HOME/$extensions/"
python3 "$(dirname "$0")/make_lab.py" "$@"
