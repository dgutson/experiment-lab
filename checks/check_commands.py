"""Runs rec, q and lab-server on a new lab that make_scratch.py makes, and
compares what they print and their exit statuses with
checks/check_commands.txt. The commands run in order on the same lab, each through
bash in the folder of the project audio-codec, with this clone's commands first
on PATH. With --update, it writes checks/check_commands.txt instead; read its diff
before committing it.

It also fails when a call of experiment_lab reads a table outside a write
transaction and then writes that table in one, unless SAFE lists it: another
process may write between the two, so the write can rest on what is no longer
true (two records given one id). Every SQL statement the commands run is logged
through checks/sql_trace/sitecustomize.py.

usage: uv run python checks/check_commands.py [--update]
"""
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
SAVED = ROOT / "checks" / "check_commands.txt"
# Each call that reads a table outside a write and then writes it, with why that is safe.
SAFE = {
    ("rec.py:run_add", "records"): "it reads records only to print how many are open",
    ("rec.py:run_set", "records"): "it reads records only to print how many are open",
    ("lab.py:Lab.end_lost", "runs"): "its UPDATE repeats the status = 'RUNNING' test of its read",
}
WRITE = re.compile(r"\s*(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE|DELETE\s+FROM)\s+(\w+)", re.I)
READ = re.compile(r"\b(?:FROM|JOIN)\s+(\w+)", re.I)
type Place = tuple[str, str]  # a call and a table
type Calls = dict[int, str]  # frame id: name
type Opened = tuple[int, int]  # process id, connection id


class Read(NamedTuple):
    table: str
    calls: Calls


class Connection:
    """The tables one connection read, with the calls each read ran inside,
    since its last transaction ended, and whether one is open."""

    def __init__(self) -> None:
        self.writing = False
        self.reads: list[Read] = []

    def run(self, sql: str, calls: Calls) -> set[Place]:
        """Takes the next statement, which ran inside calls; returns what stale
        finds in it."""
        word = (sql.split(maxsplit=1) or [""])[0].upper()
        if word == "BEGIN":
            self.writing = True
        elif word in ("COMMIT", "ROLLBACK", "END"):
            self.writing, self.reads = False, []
        elif self.writing:
            return self.stale(WRITE.match(sql), calls)
        elif word in ("SELECT", "WITH"):
            self.reads += [Read(table.lower(), calls) for table in READ.findall(sql)]
        return set()

    def stale(self, write: re.Match[str] | None, calls: Calls) -> set[Place]:
        """For a statement of a transaction, which ran inside calls, that
        writes a table: for each earlier read of that table, the innermost
        call that held both."""
        return {(call, read.table) for read in self.reads
                if write and read.table == write[1].lower() and (call := shared(read.calls, calls))}


def shared(read: Calls, calls: Calls) -> str:
    """The innermost call of read that calls holds too; '' if none."""
    return next((name for frame, name in read.items() if calls.get(frame) == name), "")


def read_then_written(trace: Path) -> set[Place]:
    """Each call that, in the log of sitecustomize.py, reads a table outside a
    write transaction and then writes it in one."""
    connections: dict[Opened, Connection] = {}
    found: set[Place] = set()
    for line in trace.read_text().splitlines():
        e = json.loads(line)
        found |= connections.setdefault((e["pid"], e["con"]), Connection()).run(
            e["sql"], {frame: name for frame, name in e["calls"]})
    return found


COMMANDS = """
rec --help
rec add --help
rec set --help
q --help
rec tree
rec tree "16-bit output"
rec set listen-on-headphones status=superseded --quiet && rec tree "16-bit output" && rec set listen-on-headphones status=closed --quiet
rec open
rec project audio-codec --text "A lossy audio codec for voice recordings, at 48 kHz." --replace
rec experiment bitrate --in "sample rate sweep" --text "Lowers the bitrate at 48 kHz until accuracy drops."
rec experiment "noise gate" --dead-end
rec tree
rec add finding "64 kbit/s keeps the accuracy" --in bitrate --text "Accuracy stayed at 0.93." --time 2026-09-10T12:00 --evidence record:48-khz-is-the-most-accurate-rate "sql:SELECT name FROM runs WHERE experiment = 'bitrate'"
rec add question "Where does accuracy drop?" --in bitrate --text "Not measured below 64 kbit/s."
rec add decision "Ship 64 kbit/s" --in bitrate --experiment "the bitrate" --by owner --text "It keeps the accuracy." --answers where-does-accuracy-drop
rec set where-does-accuracy-drop status=open
rec add finding "Quietly" --in bitrate --text "x" --quiet
rec add question "Quiet question" --in bitrate --text "x" --quiet && rec set quiet-question status=closed --quiet && rec open
rec set 64-kbit-s-keeps-the-accuracy "evidence=record:48-khz-is-the-most-accurate-rate; file:$HOME/audio-codec/tone.wav" "text=Accuracy stayed at 0.93 at 64 kbit/s."
rec add idea "An idea" --in bitrate --text "x"
rec add finding "Lost" --in nowhere --text "x"
rec add finding "Empty" --in bitrate --text ""
rec add finding "Unsure" --in bitrate --text "x" --status maybe
rec add finding "Odd evidence" --in bitrate --text "x" --evidence ticket:123
rec add finding "Bad time" --in bitrate --text "x" --time yesterday
rec add finding "Taken" --in bitrate --text "x" --id keep-48-khz
rec add finding "Elsewhere" --in "blur detector" --text "x"
rec set no-such-record status=closed
rec set keep-48-khz color=red
rec set keep-48-khz status=maybe
mkdir ~/plots && echo one > ~/notes.md && echo a > ~/plots/a.txt && truncate -s 101M ~/big.bin
rec add finding "From stdin" --in bitrate --text - <<< $'48 kHz kept "every" word; `rate=48000`, not $RATE.\\n\\n- one\\n' && q "SELECT text FROM records WHERE id = 'from-stdin'"
rec add finding "Empty stdin" --in bitrate --text - < /dev/null
rec set from-stdin "title=Set from stdin" text=- <<< $'Kept "every" word; `rate=48000`, not $RATE.\\n' && q "SELECT title, text FROM records WHERE id = 'from-stdin'"
rec set from-stdin text=- title=- < /dev/null
rec add finding "Dither noise is heard below 16 bits on headphones" --in "16-bit output" --text "x"
rec add finding "Pneumonoultramicroscopicsilicovolcanoconiosis checked" --in bitrate --text "x"
rec add finding "Cites a run" --in bitrate --text "x" --evidence "run:$(q "SELECT run FROM runs WHERE name = 'rate 48000, listened to'" | sed -n 2p)" record:keep-48-khz && q "SELECT evidence FROM records WHERE id = 'cites-a-run'"
rec add finding "Cites no run" --in bitrate --text "x" --evidence run:nosuch
rec set cites-a-run "evidence=record:nosuch"
rec add finding "Names a run" --in bitrate --text "Run $(q "SELECT run FROM runs WHERE name = 'rate 48000, listened to'" | sed -n 2p) and checksum d41d8cd98f00b204e9800998ecf8427e." --evidence record:keep-48-khz
rec add finding "Fails a query" --in bitrate --text "x" --evidence "sql:SELECT nosuch FROM runs"
rec add finding "Reads a file in a query" --in bitrate --text "x" --evidence "sql:FROM read_csv('/etc/hostname')"
rec add finding "Cites no file" --in bitrate --text "x" --evidence file:~/nosuch.txt
rec add finding "Cites a query" --in bitrate --text "x" --evidence "sql:SELECT run, name FROM runs WHERE experiment = 'sample rate sweep'" && q "SELECT evidence FROM records WHERE id = 'cites-a-query'"
rec add finding "Cites files" --in bitrate --text "x" --evidence file:~/notes.md file:~/plots && echo two > ~/notes.md && rec add finding "Cites a changed file" --in bitrate --text "x" --evidence file:~/notes.md && q --full "SELECT id, evidence FROM records WHERE id LIKE 'cites-%file%'" && cd "$EXPERIMENT_LAB_DATA" && grep -rI . evidence | sort
rec set cites-files "evidence=$(q --full "SELECT evidence FROM records WHERE id = 'cites-files'" | sed -n 2p); file:~/notes.md" && q "SELECT evidence FROM records WHERE id = 'cites-files'"
rec add finding "Cites a large file" --in bitrate --text "x" --evidence file:~/big.bin
rec experiment "blur detector" --text "x"
rec experiment "noise shaping" --text "A new idea."
rec experiment "a name of thirty-three characters" --text "x"
rec experiment bitrate --in "sample rate sweep" --text "Lowers the bitrate at 48 kHz until accuracy drops."
rec experiment bitrate --text "Lowers the bitrate until accuracy drops." --replace && q "SELECT description FROM experiments WHERE name = 'bitrate'"
rec project audio-codec --text "A new project."
rec tree "blur detector"
cd ~ && rec open
rec project audio-codec --files ~/nowhere
mkdir ~/speech && cd ~/speech && rec project speech
cd ~/speech && rec project speech --text "Detects speech in recordings."
cd ~/speech && rec experiment vad --text "Voice activity detection." && rec tree
echo "CREATE VIEW broken AS SELECT nosuch FROM runs;" > ~/speech/views.sql
cd ~/speech && rec project speech --views views.sql missing.sql && q .views
cd ~/speech && rec project speech --text "Detects speech in recordings."
git -c init.defaultBranch=main init -q ~/mono && git -C ~/mono -c user.name=x -c user.email=x commit -q --allow-empty -m x && mkdir ~/mono/sub && cd ~/mono && rec project mono --text "A repository with a project in a subfolder."
cd ~/mono/sub && rec project inner --text "The project of a subfolder."
cd ~/mono/sub && rec project inner --text "The project of a subfolder." --folder . && rec experiment "subfolder test" --text "x"
git -C ~/mono worktree add -q ~/mono-b && mkdir ~/mono-b/sub && cd ~/mono-b/sub && rec tree && cd .. && rec tree
git -c init.defaultBranch=main init -q ~/ws && git -C ~/ws -c user.name=x -c user.email=x commit -q --allow-empty -m ws && mkdir ~/ws/a ~/ws/b && cd ~/ws/a && rec project ws-a --folder . --text "A project in a subfolder of a repository."
cd ~/ws/b && rec tree
cd ~/ws/b && rec project ws-b --text "Another subfolder of the same repository."
EXPERIMENT_LAB_DATA=~/missing/lab-data rec open
EXPERIMENT_LAB_DATA=/proc/lab-data rec open
cp -r "$EXPERIMENT_LAB_DATA" ~/old && python -c "import sqlite3; db = sqlite3.connect('$HOME/old/lab.db', isolation_level=None); db.executescript('DROP TABLE files; ALTER TABLE runs DROP COLUMN host; ALTER TABLE runs DROP COLUMN pid; PRAGMA user_version = 0')" && EXPERIMENT_LAB_DATA=~/old rec open > /dev/null && ls ~/old && EXPERIMENT_LAB_DATA=~/old q "SELECT count(*) AS files FROM files" && q "SELECT count(*) AS files FROM files"
cp -r "$EXPERIMENT_LAB_DATA" ~/old-q && python -c "import sqlite3; db = sqlite3.connect('$HOME/old-q/lab.db', isolation_level=None); db.executescript('DROP TABLE files; ALTER TABLE runs DROP COLUMN host; ALTER TABLE runs DROP COLUMN pid; PRAGMA user_version = 0')" && EXPERIMENT_LAB_DATA=~/old-q q "SELECT count(*) AS files FROM files"
cp -r "$EXPERIMENT_LAB_DATA" ~/newer && python -c "import sqlite3; sqlite3.connect('$HOME/newer/lab.db', isolation_level=None).execute('PRAGMA user_version = 9000000')" && EXPERIMENT_LAB_DATA=~/newer rec open
EXPERIMENT_LAB_DATA=~/newer q "SELECT count(*) FROM runs"
q "SELECT name, status FROM runs WHERE experiment = 'sample rate sweep' ORDER BY name"
q "SELECT kind, id, experiment, title FROM experiments_runs_records WHERE holds_words(title || ' ' || text, 'RATE sweep') ORDER BY kind, id"
q "SELECT id, kind, status, title FROM records WHERE holds_words(title || ' ' || text, 'gate  hold') ORDER BY id"
q "SET TimeZone = 'UTC'; SELECT name, started, ended, minutes FROM runs WHERE experiment = 'sample rate sweep' ORDER BY name LIMIT 2"
q "SELECT name, params['rate'] AS rate FROM latest_runs('sample rate sweep', ['rate']) ORDER BY name"
q .views
q --project vision .views
q --project vision "FROM blur_scores"
q "SELECT DISTINCT project FROM runs"
q --project vision "SELECT DISTINCT project FROM runs"
q --all "SELECT DISTINCT project FROM runs ORDER BY project"
q --all .views
q --all --project vision .views
cd ~ && q "SELECT DISTINCT project FROM runs"
q .keys
q --project vision .keys
q --max 2 .keys
cd ~ && q .keys
q --max 2 "SELECT name FROM runs ORDER BY name"
q "SELECT id, text FROM records WHERE id = '48-khz-is-the-most-accurate-rate'"
q --full "SELECT text FROM records WHERE id = '48-khz-is-the-most-accurate-rate'"
q "SELECT text FROM records WHERE id = 'keep-48-khz'"
echo "SELECT count(*) AS runs FROM runs" | q
q "SELECT experiment, name, bytes FROM files ORDER BY name"
q --full "SELECT path FROM files ORDER BY name" | sed '1d;$d' | while read -r p; do test -f "$p" && echo "exists $p" || echo "missing $p"; done
echo '[{"judge": "a", "preferred": "run 7"}, {"judge": "b", "preferred": "run 6"}]' > ~/tally.json && python -c "from experiment_lab.lab import Lab; r = Lab().run('bitrate', 'judged'); r.log_file('$HOME/tally.json'); r.end()" && q "SET VARIABLE f = (SELECT path FROM files WHERE name = 'tally.json'); FROM read_json(getvariable('f'))"
q "SELECT 1 AS x; SET threads = 1; SELECT name FROM runs ORDER BY name LIMIT 2; SELECT 2 AS y"
q "CREATE TABLE t AS SELECT 1 AS x; SELECT x FROM t"
q "CREATE TABLE t (x INT)"
q "COPY (SELECT name FROM runs ORDER BY name LIMIT 2) TO '/dev/stdout' (FORMAT csv, HEADER false)" | while read -r n; do echo "read $n"; done
q "SET TimeZone = 'UTC'; SELECT TIMESTAMPTZ '2026-01-01 00:00:00+00' AS t"
q --project nosuch .views
q "SELECT nosuch FROM runs"
q "FROM params"
q "SELECT kind, id FROM texts LIMIT 1"
q "SELECT params['rate'] + 1 FROM runs"
q "SELECT name FROM runs WHERE params['rate'] > 20000"
q "SELECT name FROM lab.experiments WHERE depth = 0"
q "SELECT r.project FROM lab.records r"
q --max 0 "SELECT 1 AS x"
q --max -1 "SELECT 1 AS x"
EXPERIMENT_LAB_DATA=~/absent/lab-data q "SELECT 1"
id=$(rec run bitrate "background job" --note "Ran in a background job." --start "2026-09-10 12:00") && echo "$id" && rec end "$id" --at "2026-09-10 12:58" && q "SELECT name, status, started, ended, minutes, note FROM runs WHERE run = '$id'"
rec run nowhere "a run"
rec run "blur detector" "a run"
rec end nosuch
rec end "$(q --project vision "SELECT run FROM runs ORDER BY name LIMIT 1" | sed -n 2p)"
rec end "$(q "SELECT run FROM runs WHERE name = 'background job'" | sed -n 2p)"
id=$(rec run bitrate "late job" --start 2026-09-10T12:00) && rec end "$id" --at 2026-09-10T11:00
rec end x --status RUNNING
rec end x --at yesterday
id=$(rec run bitrate "utc job" --start 2026-09-10T10:00Z) && rec end "$id" --at 2026-09-10T12:30+02:00 && q "SET TimeZone = 'UTC'; SELECT started, ended FROM runs WHERE run = '$id'"
python -c "import datetime; from experiment_lab.lab import Lab; r = Lab().run('bitrate', 'python times', start='2026-09-10T10:00Z'); r.log_metrics({'x': 1}, at='2026-09-10 10:10+00:00'); r.end(at=datetime.datetime(2026, 9, 10, 10, 30, tzinfo=datetime.UTC))" && q "SET TimeZone = 'UTC'; SELECT r.started, r.ended, local_time(m.time) AS measured FROM runs r JOIN lab.metrics m ON m.run = r.run WHERE r.name = 'python times'"
python -c "import os, signal; from experiment_lab.lab import Lab; Lab().run('bitrate', 'stopped').__enter__(); os.kill(os.getpid(), signal.SIGTERM)"; echo "exit $?"; q "SELECT name, status, ended IS NOT NULL AS ended FROM runs WHERE name = 'stopped'"
python -c "from experiment_lab.lab import Lab; Lab().run('bitrate', 'lost', start='2026-09-10 12:00').__enter__().log_metrics({'x': 1}, at='2026-09-10 12:30')" && q "SELECT status FROM runs WHERE name = 'lost'" && rec tree > /dev/null && q "SELECT name, status, started, ended FROM runs WHERE name = 'lost'"
python -c "import os, signal, time; from experiment_lab.lab import Lab; lab = Lab(); r = lab.run('bitrate', 'forked').__enter__(); child = os.fork() or time.sleep(9); time.sleep(0.5); os.kill(child, signal.SIGTERM); os.waitpid(child, 0); print('after the child is stopped:', lab.db.execute('SELECT status FROM runs WHERE id = ?', (r.id,)).fetchone()[0]); r.__exit__(None)"
echo a > ~/out.txt && id=$(rec run bitrate "shell job" --start "2026-09-10 12:00") && { rec end "$id" --metric accuracy=0.93 --file ~/out.txt ~/missing.txt; q "SELECT status, metrics FROM runs WHERE run = '$id'"; }
rec end x --metric accuracy=high
rec end x --tag outcome
id=$(q "SELECT run FROM runs WHERE name = 'shell job'" | sed -n 2p) && rec end "$id" --at "2026-09-10 12:30" --metric accuracy=0.93 loss=nan --tag outcome=pass --file ~/out.txt ~/out.txt=logs/b.txt && q "SELECT status, tags, metrics FROM runs WHERE run = '$id'" && q "SELECT name, bytes FROM files WHERE run = '$id' ORDER BY name"
cd ~ && rec add finding "From home" --in bitrate --text "x"
cd ~ && rec experiment "home test" --in bitrate --text "x"
cd ~ && rec experiment bitrate --dead-end
cd ~ && rec set keep-48-khz status=closed
cd ~ && rec run bitrate "from home"
cd ~ && python -c "from experiment_lab.lab import Lab; Lab().run('bitrate', 'from a script')"
cd ~ && rec add finding "From home, named" --in bitrate --text "x" --project audio-codec
cd ~ && python -c "from experiment_lab.lab import Lab; print(Lab(project='audio-codec').run('bitrate', 'from a script').id)"
rec experiment sharpness --in "blur detector" --text "Scores sharpness by edges." --project vision
rec experiment "noise gate" --in vision
rec experiment "noise gate" --in vision --project vision
rec experiment "noise gate" --move
rec experiment vision --in audio-codec --move
rec experiment "noise gate" --in sharpness --move && rec tree --project vision && q --project vision "SELECT name FROM runs WHERE experiment = 'noise gate'" && rec experiment "noise gate" --in audio-codec --move --project vision && rec tree | grep -n "noise gate"
rec add question "Do edges score sharpness?" --in sharpness --text "Not measured yet." --project vision
rec open --project vision
rec tree --project vision
rec --project vision tree
rec add finding "Named elsewhere" --in bitrate --text "x" --project vision
rec open --project nosuch
python -c "from experiment_lab.lab import Lab; lab = Lab(); [lab.run('bitrate', f'batch {i}').end() for i in range(21)]" && rec add finding "Cites a long query" --in bitrate --text "x" --evidence "sql:SELECT run FROM runs WHERE name LIKE 'batch %'" && q "SELECT evidence FROM records WHERE id = 'cites-a-long-query'"
EXPERIMENT_LAB_PORT=abc lab-server
EXPERIMENT_LAB_PORT=70000 lab-server
EXPERIMENT_LAB_DATA=/proc/lab-data lab-server
cd ~ && git -C audio-codec -c init.defaultBranch=main init -q && git -C audio-codec add -A && git -C audio-codec -c user.name=t -c user.email=t@t commit -qm x && git clone -q audio-codec codec-clone && cd codec-clone && rec tree | sed -n 1p && q "SELECT count(*) AS runs FROM runs" | sed -n 2p
cd ~/codec-clone && rec project codec-fork --text "Work of its own." && rec tree
cd ~ && git clone -q audio-codec third-clone && cd third-clone && rec tree
""".strip().splitlines()

with tempfile.TemporaryDirectory() as folder:
    subprocess.run([sys.executable, ROOT / "checks" / "make_scratch.py", folder], check=True)
    env = dict(os.environ, HOME=f"{folder}/home", EXPERIMENT_LAB_DATA=f"{folder}/lab-data",
               PATH=f"{Path(sys.executable).parent}:{os.environ['PATH']}",
               PYTHONUNBUFFERED="1", COLUMNS="100", NO_COLOR="1", LC_ALL="C.UTF-8",  # the same output anywhere
               PYTHONPATH=os.pathsep.join([str(ROOT / "checks" / "sql_trace"), *os.environ.get("PYTHONPATH", "").split(
                   os.pathsep)]).rstrip(os.pathsep), SQL_TRACE=f"{folder}/sql.jsonl")
    out = []
    for command in COMMANDS:
        done = subprocess.run(["bash", "-c", command], cwd=f"{folder}/home/audio-codec", env=env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out += [f"$ {command}", *done.stdout.splitlines(), f"exit {done.returncode}", ""]
    text = "\n".join(out).replace(str(Path(folder).resolve()), "FOLDER").replace(folder, "FOLDER")
    found = read_then_written(Path(folder) / "sql.jsonl")
text = re.sub(r"Traceback \(most recent call last\):\n(?:[ \t].*\n)*", "Traceback: ", text)
text = re.sub(r"\b[0-9a-f]{32}\b", "RUN", text)
# argparse quotes the choices it lists in some Python versions and not in others
text = re.sub(r"\(choose from .*\)", lambda m: m[0].replace("'", ""), text)

if sys.argv[1:] == ["--update"]:
    SAVED.write_text(text)
    print(f"wrote {SAVED}")
elif text != SAVED.read_text():
    sys.stdout.writelines(difflib.unified_diff(SAVED.read_text().splitlines(True), text.splitlines(True),
                                               "check_commands.txt", "now"))
    sys.exit("the commands print something else; --update writes it to check_commands.txt")
else:
    print(f"the {len(COMMANDS)} commands print what check_commands.txt holds")
problems = [f"{call} reads {table} outside a write and then writes it, so another process may write between"
            " the two: read inside lab.write(), or list it in SAFE with why it is safe"
            for call, table in sorted(found - SAFE.keys())]
problems += [f"SAFE lists {call} and {table}, which no command runs now: remove it"
             for call, table in sorted(SAFE.keys() - found)]
if problems:
    sys.exit("\n".join(problems))
print(f"no call reads a table outside a write and then writes it, but the {len(SAFE)} that SAFE lists")
