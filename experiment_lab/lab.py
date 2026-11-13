"""Writing to the lab's database, lab.db in the data folder: projects, the
experiments below them, and runs with their params, metrics, tags and files;
and reading runs back. It uses only Python's standard library, so a logging or
report script can import it from any Python 3.12 with
PYTHONPATH=<clone of experiment-lab>.

    from experiment_lab.lab import Lab
    lab = Lab()                                  # $EXPERIMENT_LAB_DATA, else ~/lab-data
    lab.project("my-project", "What the project is.")   # once, from the project's folder
    lab.experiment("rate test", "What it measures and why.")   # once, as rec experiment
    with lab.run("rate test", "run 1", params={"rate": 48000}) as run:
        run.log_metrics({"accuracy": 0.91}, step=3)
        run.set_tags({"outcome": "pass"})
        run.log_file("plot.png")                 # copied to files/<run id>/plot.png
        run.log_file("out/a.json", name="case-1/a.json")   # to files/<run id>/case-1/a.json
        run.log_json("result.json", {"passed": [1, 3]})    # written there; log_text for text
    for run in lab.runs("rate test", tags={"outcome": "pass"}, latest_by="rate"):
        print(run.params["rate"], run.metrics["accuracy"], lab.json(run.id, "case-1/a.json"))

The experiments form a tree whose roots are the projects. A Lab works in the
project it is given (Lab(project="my-project")), else in the project of the
current folder: the one whose folder holds it, or holds the folder at the same
place in its git repository's main worktree, else in another clone of that
repository. A new experiment goes directly below that project unless a
parent is given, and only that project's experiments, runs and records are
used; with no project, they are refused.

A run ends FINISHED when its with block ends, FAILED on an exception, KILLED
on Ctrl-C or SIGTERM; without a with block, call end(). A run whose with
block's process was killed otherwise (SIGKILL, a restart) is ended KILLED by
the next Lab opened on its host, at its last metric's time, else its start.
For work that runs elsewhere, a run started before it (rec run, or lab.run
without with) is ended by another process: lab.run_by_id(run_id).end(),
end(at=...) for an earlier end, or rec end. A time (lab.run's start,
log_metrics's and end's at) is milliseconds since 1970, a datetime, or text as
rec takes it: ISO 8601, as "2026-09-10 12:00", local unless it ends in Z or an
offset. Several processes may write at once without a server: the database is
in WAL mode, every write is one short transaction, and a writer waits up to a
minute for another's to end.

lab.runs reads an experiment's FINISHED runs back, oldest first; its keywords
keep those with the params, tags or metric given, and the latest for each
combination of the values of the params latest_by names, as q's latest_runs.
lab.file and lab.json read a file that a run logged, and name on stderr the
records whose evidence cites the run, each once, since one may correct what the
file holds.
"""
import contextlib
import datetime
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from functools import cached_property
from pathlib import Path
from types import FrameType
from typing import Any, NamedTuple, Self

from experiment_lab.db_upgrades import upgrade
from experiment_lab.settings import data_folder, database

# the experiment given as the parameter, and those below it, as the table below
BELOW = ("WITH RECURSIVE below(name) AS (SELECT ? UNION ALL"
         " SELECT e.name FROM experiments e JOIN below ON e.parent = below.name)")

RUNS, *READ = Path(__file__).with_name("read.sql").read_text().split(";")[:4]  # the runs; their params, tags, metrics
# the records whose evidence holds the item that the parameter matches as '%; run:<id>; %'
CITING = "SELECT id, kind, title FROM records WHERE '; ' || evidence || '; ' LIKE ? ORDER BY time, id"

type StrPath = str | Path
type Values = dict[str, Any]  # a run's params, tags or metrics, by key
type Names = Sequence[str]
type Keys = str | Names  # a param's name, or a list of them
type Time = int | str | datetime.datetime  # milliseconds since 1970, or what ms reads


class Project(NamedTuple):
    name: str
    folder: str


class RunRow(NamedTuple):
    """A run as Lab.runs reads it: params and tags as text, and the latest
    value of each metric, None for NaN."""
    id: str
    name: str
    status: str
    start_time: int
    end_time: int | None
    params: dict[str, str]
    tags: dict[str, str]
    metrics: dict[str, float | None]
    note: str


def now() -> int:
    return int(time.time() * 1000)


def ms(t: Time | None) -> int | None:
    """t in milliseconds since 1970; None if None. Text is read by
    datetime.fromisoformat: ISO 8601, local unless it ends in Z or an offset."""
    if isinstance(t, str):
        t = datetime.datetime.fromisoformat(t)
    return int(t.timestamp() * 1000) if isinstance(t, datetime.datetime) else t


def host() -> str:
    """This host's name and pid namespace: a pid names the same process only to
    a process of the same."""
    try:
        namespace = os.readlink("/proc/self/ns/pid")
    except OSError:  # not Linux
        namespace = ""
    return f"{socket.gethostname()} {namespace}".rstrip()


def alive(pid: int) -> bool:
    """Whether a process of this host has that pid."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # another user's
        pass
    return True


def worktrees(folder: Path) -> list[Path]:
    """The worktrees of the git repository that holds folder, its main worktree
    first; none without git or outside a git repository."""
    try:
        git = subprocess.run(["git", "-C", str(folder), "worktree", "list", "--porcelain"], capture_output=True,
                             text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return []
    return [Path(line.removeprefix("worktree ")).resolve() for line in git.stdout.splitlines()
            if line.startswith("worktree ")]


def top_folder(projects: Iterable[Project]) -> Path:
    """A new project's folder: the main worktree of the current git
    repository, else the current folder; but the current folder, at its place
    in the main worktree, when the main worktree holds one of the projects'
    folders, since a project there would hold that project too."""
    folders = working_folders()
    top = (worktrees(Path.cwd()) or folders)[0]
    return folders[-1] if any(Path(folder).is_relative_to(top) for _, folder in projects) else top


def listed(projects: Sequence[Project]) -> str:
    """' (a: /x/a, b: /x/b)', the projects and their folders; '' if none."""
    return f" ({', '.join(f'{name}: {folder}' for name, folder in sorted(projects))})" if projects else ""


def all_projects(db: sqlite3.Connection) -> list[Project]:
    """Every project's name and folder."""
    return [Project(*row) for row in db.execute("SELECT name, folder FROM experiments WHERE parent IS NULL")]


def working_folders() -> list[Path]:
    """The current folder, then, inside a worktree of a git repository, the
    folder at the same place in its main worktree, so that every worktree of a
    repository works in the same projects."""
    here = Path.cwd().resolve()
    trees = worktrees(here)
    own = [tree for tree in trees if here.is_relative_to(tree)]  # a linked worktree may be inside the main one
    return [here, trees[0] / here.relative_to(max(own, key=lambda tree: len(tree.parts)))] if own else [here]


def holding(projects: Iterable[Project], folders: Iterable[Path]) -> str | None:
    """Of the projects, the one whose folder holds any of folders, the
    innermost if several; None if none."""
    found = [(len(Path(folder).parts), name) for name, folder in projects
             if any(f.is_relative_to(folder) for f in folders)]
    return max(found)[1] if found else None


def first_commits(folder: Path) -> frozenset[str]:
    """The commits with no parent of the git repository that holds folder,
    which every clone of it has; none without git, outside a repository or
    before its first commit."""
    try:
        git = subprocess.run(["git", "-C", str(folder), "rev-list", "--max-parents=0", "HEAD"],
                             capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return frozenset()
    return frozenset(git.stdout.split())


def cloned_project(projects: Sequence[Project]) -> str | None:
    """The project whose folder holds the current folder's place in another
    clone of its git repository, one with the same first commits; None if no
    project's folder does, or if those of several projects do."""
    trees = worktrees(Path.cwd())
    first = first_commits(Path.cwd()) if trees else frozenset()
    if not first:
        return None
    place = working_folders()[-1].relative_to(trees[0])
    found = {holding(projects, [theirs[0] / place]) for _, folder in projects
             if (theirs := worktrees(Path(folder))) and theirs[0] != trees[0] and first_commits(theirs[0]) == first}
    found.discard(None)
    return found.pop() if len(found) == 1 else None


def working_project(projects: Sequence[Project], named: str | None) -> str | None:
    """named, which must be one of the projects, else the project of the
    current folder or of the folder at the same place in its repository's main
    worktree (holding), else in another clone of the repository
    (cloned_project); None if none."""
    if named and named not in [p.name for p in projects]:
        raise ValueError(f"no project {named}")
    return named or holding(projects, working_folders()) or cloned_project(projects)


def absolute(path: StrPath) -> str:
    return str(Path(path).expanduser().resolve())


def shown(path: StrPath) -> str:
    """path as ~/... under the home folder."""
    return "~/" + str(Path(path).relative_to(Path.home())) if Path(path).is_relative_to(Path.home()) else str(path)


def matches(values: Mapping[str, str], wanted: Mapping[str, object] | None) -> bool:
    """Whether values has each key of wanted with its value, or one of them if
    it is a tuple, list or set, compared as the text it is logged as."""
    return all(values.get(k) in ({str(x) for x in v} if isinstance(v, tuple | list | set | frozenset) else {str(v)})
               for k, v in (wanted or {}).items())


def latest(rows: list[RunRow], keys: Keys) -> list[RunRow]:
    """The last of rows, which are oldest first, for each combination of the
    values of the params named by keys; a param that a run lacks counts as None."""
    names = [keys] if isinstance(keys, str) else keys
    last = set({tuple(r.params.get(k) for k in names): r.id for r in rows}.values())
    return [r for r in rows if r.id in last]


def defaults(name: str, changes: dict[str, str | None], projects: Iterable[Project]) -> dict[str, str | None]:
    """The changes that make a new project: it needs a description; its folder
    is by default top_folder's, and its files folder its folder."""
    if not changes["description"]:
        raise ValueError(f"a new project needs a description: {name}")
    folder = changes["folder"] or str(top_folder(projects))
    return {**changes, "folder": folder, "files": changes["files"] or folder}


def check_folder(db: sqlite3.Connection, name: str, folder: str | None) -> None:
    """Refuses folder for the project name if it is another project's."""
    taken = folder and db.execute("SELECT name FROM experiments WHERE folder = ? AND name <> ?",
                                  (folder, name)).fetchone()
    if taken:
        raise ValueError(f"{folder} is the folder of the project {taken[0]}; --folder F (folder=F) gives this"
                         " project another folder, such as --folder . for the current one")


LONGEST_NAME = 32  # of a new experiment, which every q filter, --in and sql: item retypes


def check_name(name: str) -> None:
    """Refuses name for a new experiment if it is longer than LONGEST_NAME."""
    if len(name) > LONGEST_NAME:
        raise ValueError(f"a new experiment's name is at most {LONGEST_NAME} characters, since every q filter,"
                         f" --in and sql: item retypes it, and {name!r} has {len(name)}: use a short one, such as"
                         " a ticket id, and put the words a search needs in the description")


def check_description(kind: str, name: str, where: str, own: str | None, given: str | None, replace: bool,
                      hint: str = "") -> None:
    """Refuses given as the description of the existing experiment or project
    (kind) name, unless it is its own or replace; where tells which it is, and
    hint is added at the end."""
    if given and given != own and not replace:
        raise ValueError(f"{kind} {name} exists ({where}); a new {kind} needs another name;"
                         f" --replace (replace=True) changes its description{hint}")


def update(db: sqlite3.Connection, name: str, changes: Mapping[str, object]) -> None:
    """Sets those columns of the experiment's row that changes gives as not None."""
    for column, value in changes.items():
        if value is not None:
            db.execute(f"UPDATE experiments SET {column} = ? WHERE name = ?", (value, name))


class Lab:
    def __init__(self, data: StrPath | None = None, project: str | None = None) -> None:
        """Opens the data folder's lab.db, making the folder and the tables if
        missing, upgrading an older lab.db (db_upgrades) and ending the runs
        whose process is gone (end_lost). The lab works in project, else in
        the project of the current folder."""
        self.named = project
        self.told: set[str] = set()  # the records tell_citing has printed
        self.data = Path(data) if data else data_folder()
        self.data.mkdir(parents=True, exist_ok=True)
        self.db = self.connect()
        upgrade(self.db, self.data)
        self.end_lost()

    def connect(self) -> sqlite3.Connection:
        """lab.db, in WAL mode and with foreign keys on; a write waits up to a
        minute for another's."""
        db = sqlite3.connect(database(self.data), timeout=60, isolation_level=None)
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def close(self) -> None:
        self.db.close()

    @contextlib.contextmanager
    def closed(self) -> Iterator[None]:
        """Closes lab.db for the block, so that DuckDB may read it in this
        process (query.py's docstring), then opens it again."""
        self.db.close()
        try:
            yield
        finally:
            self.db = self.connect()

    def end_lost(self) -> None:
        """Ends KILLED every project's RUNNING runs whose with block ran in a
        process of this host that is gone, at the time of the run's last
        metric, else at its start."""
        lost = [(rid,) for rid, pid in self.db.execute("SELECT id, pid FROM runs WHERE status = 'RUNNING'"
                                                       " AND host = ?", (host(),)).fetchall() if not alive(pid)]
        if lost:
            with self.write() as db:
                db.executemany("UPDATE runs SET status = 'KILLED', end_time = coalesce(max(start_time,"
                               " (SELECT max(time) FROM metrics WHERE run = runs.id)), start_time)"
                               " WHERE id = ? AND status = 'RUNNING'", lost)

    def delete_project(self, name: str) -> None:
        """Deletes the project and, through ON DELETE CASCADE, the experiments
        below it with their runs and records; then the runs' folders in files/.
        Nothing outside the data folder is touched."""
        with self.write() as db:
            if not db.execute("SELECT 1 FROM experiments WHERE name = ? AND parent IS NULL", (name,)).fetchone():
                raise ValueError(f"no project {name}")
            runs = [rid for (rid,) in db.execute(f"{BELOW} SELECT id FROM runs WHERE experiment IN"
                                                  " (SELECT name FROM below)", (name,))]
            db.execute("DELETE FROM experiments WHERE name = ?", (name,))
        files = self.data / "files"
        for folder in (files / rid for rid in runs):
            if folder.parent == files and folder.is_dir():  # a run id with / or .. names no folder of files/
                shutil.rmtree(folder)

    @contextlib.contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """One transaction, started at once so that it waits for any other writer."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    @cached_property
    def current(self) -> str | None:
        """The project the lab works in (working_project); None if none."""
        return working_project(all_projects(self.db), self.named)

    def project(self, name: str, description: str | None = None, *, folder: StrPath | None = None,
                files: StrPath | None = None, views: Sequence[StrPath] | None = None, replace: bool = False) -> None:
        """Makes the project, an experiment with no parent, if there is none of
        that name, or changes what is given; a description other than its own
        only with replace. A new project needs a description (one line: what
        the project is). Its folder is by default top_folder's: the main
        worktree of the current git repository, else the current folder, but
        the current folder when the main worktree holds another project's
        folder; files, the folder its files page shows, is by default its
        folder; views lists its view files."""
        changes = {"description": description or None, "folder": absolute(folder) if folder else None,
                   "files": absolute(files) if files else None,
                   "views": None if views is None else "; ".join(map(absolute, views))}
        with self.write() as db:
            found = db.execute("SELECT parent, folder, description FROM experiments WHERE name = ?",
                               (name,)).fetchone()
            if found is None:
                changes = defaults(name, changes, all_projects(db))
            elif found[0] is not None:
                raise ValueError(f"{name} is an experiment below {found[0]}, not a project")
            else:
                check_description("project", name, f"folder {found[1]}", found[2], description, replace)
            check_folder(db, name, changes["folder"])
            if found is None:
                db.execute("INSERT INTO experiments (name, created, folder, files) VALUES (?, ?, ?, ?)",
                           (name, now(), changes["folder"], changes["files"]))
            update(db, name, changes)
        self.__dict__.pop("current", None)

    def experiment(self, name: str, description: str | None = None, *, parent: str | None = None,
                   dead_end: bool | None = None, replace: bool = False) -> list[str]:
        """Makes the experiment if there is none of that name, below parent (by
        default the project of the current folder), or changes what is given:
        its description (Markdown: what it measures and why, which a new
        experiment needs and a search finds), other than its own only with
        replace; its parent (moving it with the experiments below it); whether
        it is a dead end (an idea that failed). A new experiment's name is at
        most LONGEST_NAME characters. Returns the names from its project down
        to it."""
        with self.write() as db:
            found = db.execute("SELECT parent, description FROM experiments WHERE name = ?", (name,)).fetchone()
            if found is None:
                if not description:
                    raise ValueError(f"a new experiment needs a description: {name}")
                check_name(name)
                parent = parent or self.here()
            else:
                self.check_experiment(name, "; experiment names are unique across the lab, so a new experiment"
                                            " needs another name")
                check_description("experiment", name, " › ".join(self.path(name)), found[1], description, replace,
                                  "; a script that logs runs in it as it is needs only lab.run")
            if parent is not None:
                self.check_parent(name, parent, is_project=found is not None and found[0] is None)
            if found is None:
                db.execute("INSERT INTO experiments (name, parent, created) VALUES (?, ?, ?)", (name, parent, now()))
            update(db, name, {"parent": parent, "description": description or None, "dead_end": dead_end})
        return self.path(name)

    def check_parent(self, name: str, parent: str, is_project: bool) -> None:
        """Refuses parent as the parent of the experiment name: it must exist in
        the project the lab works in, name may not be a project, and parent may
        not be name or below it."""
        self.check_experiment(parent)
        if is_project:
            raise ValueError(f"{name} is a project, which has no parent")
        if name in self.path(parent):
            raise ValueError(f"{parent} is {name} or below it")

    def here(self) -> str:
        """The project the lab works in; a ValueError naming the ways to give
        one if there is none."""
        if self.current is None:
            projects = all_projects(self.db)
            folder = top_folder(projects)
            raise ValueError(f'no project holds {folder}: run in a project\'s folder{listed(projects)}, name one'
                             ' (rec --project P, Lab(project="P")), or make this folder a project:'
                             ' rec project NAME --text "what the project is, in one line"')
        return self.current

    def path(self, name: str | None) -> list[str]:
        """The names from the experiment's project down to it."""
        names: list[str] = []
        while name is not None:
            found = self.db.execute("SELECT parent FROM experiments WHERE name = ?", (name,)).fetchone()
            if found is None:
                raise ValueError(f"no experiment {name!r}; make it with rec experiment or Lab.experiment")
            names.insert(0, name)
            name = found[0]
        return names

    def check_experiment(self, name: str, hint: str = "") -> None:
        """Refuses an experiment that does not exist or is not in the project
        the lab works in, the latter with hint at the end, and any experiment
        when it works in none."""
        current = self.here()
        project = self.path(name)[0]
        if project != current:
            whose = "the one named" if self.named else "whose folder this is"
            raise ValueError(f"{name!r} is in the project {project}, not in {current}, {whose}{hint}")

    def run(self, experiment: str, name: str, *, note: str = "", params: Mapping[str, object] | None = None,
            tags: Mapping[str, object] | None = None, start: Time | None = None) -> "Run":
        """Starts a run, RUNNING, in an existing experiment."""
        self.check_experiment(experiment)
        run = Run(self, uuid.uuid4().hex)
        with self.write() as db:
            db.execute("INSERT INTO runs (id, experiment, name, status, start_time, note) VALUES (?, ?, ?, ?, ?, ?)",
                       (run.id, experiment, name, "RUNNING", ms(start) or now(), note))
        if params:
            run.log_params(params)
        if tags:
            run.set_tags(tags)
        return run

    def run_by_id(self, rid: str) -> "Run":
        """The run of that id, for a process other than the one that started
        it; it must be in the project the lab works in."""
        found = self.db.execute("SELECT experiment FROM runs WHERE id = ?", (rid,)).fetchone()
        if found is None:
            raise ValueError(f"no run {rid}")
        self.check_experiment(found[0])
        return Run(self, rid)

    def runs(self, experiment: str, *, status: str | None = "FINISHED", params: Mapping[str, object] | None = None,
             tags: Mapping[str, object] | None = None, has_metric: str | None = None,
             latest_by: Keys | None = None) -> list[RunRow]:
        """The runs of the experiment, oldest first, with status (None: any),
        the params and tags given (a value, or a tuple or list of those
        allowed) and the metric has_metric. latest_by, a param or a list of
        them, keeps the latest of those runs by start time for each combination
        of the params' values, as latest_runs of lab.sql."""
        self.check_experiment(experiment)
        rows = [r for r in self.every_run(experiment) if (status is None or r.status == status)
                and matches(r.params, params) and matches(r.tags, tags) and (has_metric is None or has_metric in r.metrics)]
        return rows if latest_by is None else latest(rows, latest_by)

    def every_run(self, experiment: str) -> list[RunRow]:
        """Every run of the experiment, by start time, then id."""
        runs = self.db.execute(RUNS, (experiment,)).fetchall()
        # read after the runs, so that a run read as ended has all it logged before its end
        params, tags, metrics = (self.by_run(sql, experiment) for sql in READ)
        return [RunRow(rid, name, status, start, end, params.get(rid, {}), tags.get(rid, {}), metrics.get(rid, {}), note)
                for rid, name, status, start, end, note in runs]

    def by_run(self, sql: str, experiment: str) -> dict[str, Values]:
        """The keys and values that sql selects for the experiment, by run."""
        found: dict[str, Values] = {}
        for rid, key, value in self.db.execute(sql, (experiment,)):
            found.setdefault(rid, {})[key] = value
        return found

    def file(self, rid: str, name: str) -> Path:
        """The copy of the file that the run logged as name: files/<run id>/<name>
        in the data folder. Names on stderr the records that cite the run, any
        of which may correct what the file holds (tell_citing)."""
        self.tell_citing(rid)
        return self.data / "files" / rid / name

    def tell_citing(self, rid: str) -> None:
        """Prints on stderr the records whose evidence cites the run, leaving
        out those this Lab has printed already."""
        found = self.db.execute("SELECT name FROM runs WHERE id = ?", (rid,)).fetchone()
        records = [r for r in self.db.execute(CITING, (f"%; run:{rid}; %",)) if r[0] not in self.told]
        if found and records:
            self.told.update(r[0] for r in records)
            listed = "; ".join(f"{rec_id} ({kind}) {title}" for rec_id, kind, title in records)
            print(f"records citing run {found[0]}: {listed}", file=sys.stderr)

    def json(self, rid: str, name: str) -> Any:  # noqa: ANN401, a JSON value as json.loads gives it
        """The value of the JSON file that the run logged as name."""
        return json.loads(self.file(rid, name).read_text())


class Run:
    def __init__(self, lab: Lab, rid: str) -> None:
        self.lab, self.id = lab, rid

    def __enter__(self) -> Self:
        """Stores this process's host and pid, by which Lab.end_lost finds the
        run once the process is gone, and has SIGTERM end the run KILLED."""
        with self.lab.write() as db:
            db.execute("UPDATE runs SET host = ?, pid = ? WHERE id = ?", (host(), os.getpid(), self.id))
        ENTERED[self] = os.getpid()
        watch_sigterm()
        return self

    def __exit__(self, kind: type[BaseException] | None, *_: object) -> None:
        ENTERED.pop(self, None)
        watch_sigterm()
        self.end("FINISHED" if kind is None else "KILLED" if issubclass(kind, KeyboardInterrupt) else "FAILED")

    def put(self, table: str, values: Mapping[str, object]) -> None:
        with self.lab.write() as db:
            db.executemany(f"INSERT INTO {table} (run, key, value) VALUES (?, ?, ?)"
                           " ON CONFLICT (run, key) DO UPDATE SET value = excluded.value",
                           [(self.id, k, str(v)) for k, v in values.items()])

    def log_params(self, params: Mapping[str, object]) -> None:
        self.put("params", params)

    def set_tags(self, tags: Mapping[str, object]) -> None:
        self.put("tags", tags)

    def log_metrics(self, metrics: Mapping[str, float], step: int = 0, at: Time | None = None) -> None:
        when = ms(at) or now()
        with self.lab.write() as db:
            db.executemany("INSERT INTO metrics (run, key, value, step, time) VALUES (?, ?, ?, ?, ?)",
                           [(self.id, k, float(v), step, when) for k, v in metrics.items()])

    def set_note(self, text: str) -> None:
        with self.lab.write() as db:
            db.execute("UPDATE runs SET note = ? WHERE id = ?", (text, self.id))

    def log_file(self, path: StrPath, name: str | None = None) -> Path:
        """Copies the file to files/<run id>/<name> in the data folder (name:
        the file's own; it may hold folders, as plots/a.png), adds it to the
        table files and returns the copy's path. Logging a name again replaces
        the file."""
        return self.store(name or Path(path).name, lambda target: shutil.copy2(path, target))

    def log_text(self, name: str, text: str) -> Path:
        """Writes text to files/<run id>/<name>, as log_file copies a file."""
        return self.store(name, lambda target: target.write_text(text, encoding="utf-8"))

    def log_json(self, name: str, value: object) -> Path:
        """Writes value as JSON to files/<run id>/<name>, as log_file copies a
        file; lab.json reads it back."""
        return self.log_text(name, json.dumps(value, indent=2))

    def store(self, name: str, write: Callable[[Path], object]) -> Path:
        """Makes files/<run id>/<name> with write, adds it to the table files
        and returns its path."""
        folder = self.lab.data / "files" / self.id
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        write(target)
        with self.lab.write() as db:
            db.execute("INSERT INTO files (run, name, bytes) VALUES (?, ?, ?)"
                       " ON CONFLICT (run, name) DO UPDATE SET bytes = excluded.bytes",
                       (self.id, target.relative_to(folder).as_posix(), target.stat().st_size))
        return target

    def end(self, status: str = "FINISHED", at: Time | None = None) -> None:
        """Ends the run with status, at the time at (now unless given)."""
        with self.lab.write() as db:
            db.execute("UPDATE runs SET status = ?, end_time = ? WHERE id = ?", (status, ms(at) or now(), self.id))


# the runs whose with block a process is in, with that process's pid, which a forked child's copy keeps
ENTERED: dict[Run, int] = {}


def entered() -> list[Run]:
    """The runs whose with block this process is in."""
    return [run for run, pid in ENTERED.items() if pid == os.getpid()]


def watch_sigterm() -> None:
    """Has SIGTERM end the runs of entered() (stopped) while there are any,
    else gives it back its default action; only from the main thread, the one
    that can set a handler, and never over a handler of the script's own."""
    if threading.current_thread() is not threading.main_thread():
        return
    handler = signal.getsignal(signal.SIGTERM)
    if entered() and handler == signal.SIG_DFL:
        signal.signal(signal.SIGTERM, stopped)
    elif not entered() and handler is stopped:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)


def stopped(signum: int, _: FrameType | None) -> None:
    """The SIGTERM handler: ends KILLED the runs of entered(), rolling back the
    write their lab may be in, which the process would lose anyway, then ends
    the process by the signal, as without the handler."""
    for run in entered():
        with contextlib.suppress(sqlite3.Error):  # a connection of another thread, or lab.db locked for a minute
            if run.lab.db.in_transaction:
                run.lab.db.execute("ROLLBACK")
            run.end("KILLED")
    signal.signal(signum, signal.SIG_DFL)
    signal.raise_signal(signum)


if __name__ == "__main__":  # for lab-server and query.attach: makes or upgrades lab.db; --delete PROJECT deletes one
    try:
        lab = Lab()
        match sys.argv[1:]:
            case ["--delete", project]:
                lab.delete_project(project)
        lab.close()
    except (OSError, sqlite3.Error, ValueError) as e:
        raise SystemExit(f"error: {e}")
