# Projects and installation

`q`, `rec` and `lab-server` are commands of experiment-lab, installed with `uv tool install` from
the clone at `~/experiment-lab`. The data folder is `$EXPERIMENT_LAB_DATA`, default `~/lab-data`;
the database is its `lab.db`.

The lab holds several projects. q, rec and `lab.py` work in the project of the current folder: the
one whose folder holds it, or holds the folder at the same place in its git repository's main
worktree. A script works in the project of its working folder, not of the folder the script is in.
From another folder, name the project: `--project P` for q and rec, `Lab(project="P")` in a script;
with no project, rec and `lab.py` write nothing. If rec says no project holds the folder and the
work is a new project, make it once there: `rec project NAME --text "what the project is, in one line"`.
