"""Runs rec, q and lab-server on a new lab that make_scratch.py makes, and
compares what they print and their exit statuses with
checks/check_commands.txt. The commands run in order on the same lab, each through
bash in the folder of the project audio-codec, with this clone's commands first
on PATH. With --update, it writes checks/check_commands.txt instead; read its diff
before committing it.

usage: uv run python checks/check_commands.py [--update]
"""
import difflib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAVED = ROOT / "checks" / "check_commands.txt"
COMMANDS = """
rec tree
rec tree "16-bit output"
rec open
rec project audio-codec --text "A lossy audio codec for voice recordings, at 48 kHz."
rec experiment bitrate --in "sample rate sweep" --text "Lowers the bitrate at 48 kHz until accuracy drops."
rec experiment "noise gate" --dead-end
rec tree
rec add finding "64 kbit/s keeps the accuracy" --in bitrate --text "Accuracy stayed at 0.93." --time 2026-09-10T12:00 --evidence record:48-khz-is-the-most-accurate-rate "sql:SELECT name FROM runs WHERE experiment = 'bitrate'"
rec add question "Where does accuracy drop?" --in bitrate --text "Not measured below 64 kbit/s."
rec add decision "Ship 64 kbit/s" --in bitrate --experiment "the bitrate" --by owner --text "It keeps the accuracy." --answers where-does-accuracy-drop
rec set where-does-accuracy-drop status=open
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
git -c init.defaultBranch=main init -q ~/results && git -C ~/results -c user.name=x -c user.email=x commit -q --allow-empty -m x && git -C ~/results worktree add -q ~/results-b && touch ~/results-b/out.json
rec add finding "From a worktree" --in bitrate --text "x" --evidence file:~/results-b/out.json file:~/results/out.json run:x
rec set from-a-worktree "evidence=file:~/results-b/new/out.json; file:~/audio-codec/tone.wav"
rec experiment "blur detector" --text "x"
rec tree "blur detector"
cd ~ && rec open
rec project audio-codec --files ~/nowhere
mkdir ~/speech && cd ~/speech && rec project speech
cd ~/speech && rec project speech --text "Detects speech in recordings."
cd ~/speech && rec experiment vad --text "Voice activity detection." && rec tree
echo "CREATE VIEW broken AS SELECT nosuch FROM runs;" > ~/speech/views.sql
cd ~/speech && rec project speech --views views.sql missing.sql && q .views
EXPERIMENT_LAB_DATA=~/missing/lab-data rec open
EXPERIMENT_LAB_DATA=/proc/lab-data rec open
q "SELECT name, status FROM runs WHERE experiment = 'sample rate sweep' ORDER BY name"
q "SET TimeZone = 'UTC'; SELECT name, started, ended, minutes FROM runs WHERE experiment = 'sample rate sweep' ORDER BY name LIMIT 2"
q .views
q --project vision .views
q --project vision "FROM blur_scores"
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
q "SELECT 1 AS x; SET threads = 1; FROM runs LIMIT 1; SELECT 2 AS y"
q "CREATE TABLE t AS SELECT 1 AS x; SELECT x FROM t"
q "CREATE TABLE t (x INT)"
q "SET TimeZone = 'UTC'; SELECT TIMESTAMPTZ '2026-01-01 00:00:00+00' AS t"
q --project nosuch .views
q "SELECT nosuch FROM runs"
q "SELECT name FROM lab.experiments WHERE depth = 0"
q "SELECT r.project FROM lab.records r"
q --max 0 "SELECT 1 AS x"
q --max -1 "SELECT 1 AS x"
EXPERIMENT_LAB_DATA=~/absent/lab-data q "SELECT 1"
id=$(rec run bitrate "background job" --note "Ran in a background job." --start 2026-09-10T12:00) && echo "$id" && rec end "$id" --at 2026-09-10T12:58 && q "SELECT name, status, started, ended, minutes, note FROM runs WHERE run = '$id'"
rec run nowhere "a run"
rec run "blur detector" "a run"
rec end nosuch
rec end "$(q --project vision "SELECT run FROM runs WHERE project = 'vision' ORDER BY name LIMIT 1")"
rec end "$(q "SELECT run FROM runs WHERE name = 'background job'")"
id=$(rec run bitrate "late job" --start 2026-09-10T12:00) && rec end "$id" --at 2026-09-10T11:00
rec end x --status RUNNING
rec end x --at yesterday
EXPERIMENT_LAB_PORT=abc lab-server
EXPERIMENT_LAB_PORT=70000 lab-server
EXPERIMENT_LAB_DATA=/proc/lab-data lab-server
""".strip().splitlines()

with tempfile.TemporaryDirectory() as folder:
    subprocess.run([sys.executable, ROOT / "checks" / "make_scratch.py", folder], check=True)
    env = dict(os.environ, HOME=f"{folder}/home", EXPERIMENT_LAB_DATA=f"{folder}/lab-data",
               PATH=f"{Path(sys.executable).parent}:{os.environ['PATH']}",
               PYTHONUNBUFFERED="1", COLUMNS="100", NO_COLOR="1", LC_ALL="C.UTF-8")  # the same output anywhere
    out = []
    for command in COMMANDS:
        done = subprocess.run(["bash", "-c", command], cwd=f"{folder}/home/audio-codec", env=env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out += [f"$ {command}", *done.stdout.splitlines(), f"exit {done.returncode}", ""]
    text = "\n".join(out).replace(str(Path(folder).resolve()), "FOLDER").replace(folder, "FOLDER")
text = re.sub(r"Traceback \(most recent call last\):\n(?:[ \t].*\n)*", "Traceback: ", text)
text = re.sub(r"\b[0-9a-f]{32}\b", "RUN", text)

if sys.argv[1:] == ["--update"]:
    SAVED.write_text(text)
    print(f"wrote {SAVED}")
elif text != SAVED.read_text():
    sys.stdout.writelines(difflib.unified_diff(SAVED.read_text().splitlines(True), text.splitlines(True),
                                               "check_commands.txt", "now"))
    sys.exit("the commands print something else; --update writes it to check_commands.txt")
else:
    print(f"the {len(COMMANDS)} commands print what check_commands.txt holds")
