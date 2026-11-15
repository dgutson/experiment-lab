"""Runs python3 -m experiment_lab.feedback on transcripts made here, with a
new lab that make_scratch.py makes, and compares what it prints and its exit
statuses with checks/check_feedback.txt: draft, which makes the lab's names
neutral, draft --real, which does not, draft of a session that sent a report
before, and send, with a gh that refuses the label and with no gh. The home folder is the lab's, so that its paths are
the same on every computer. With --update, it writes checks/check_feedback.txt
instead; read its diff before committing it.

usage: uv run python checks/check_feedback.py [--update]
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
SAVED = ROOT / "checks" / "check_feedback.txt"
SESSION = "0f0f0f0f-0000-4000-8000-000000000000"
SENT_BEFORE = "0e0e0e0e-0000-4000-8000-000000000000"  # a session that sent a report, then went on
RUN_ID = "0123456789abcdef0123456789abcdef"
FAKE_GH = """#!/bin/sh
case "$*" in
  "auth status") exit 0 ;;
  *--label*) echo "could not add label: 'agent-feedback' not found" >&2; exit 1 ;;
  *) echo "https://github.com/dgutson/experiment-lab/issues/1" ;;
esac
"""


class ToolCall(NamedTuple):
    """A tool call: the tool, its command, path or skill, what it printed, and
    whether it failed."""
    tool: str
    text: str
    output: str
    failed: bool


def session_calls(home: Path) -> list[ToolCall]:
    """The session's tool calls."""
    codec = home / "audio-codec"
    failed_run = f"Exit code 1\nerror: no run {RUN_ID}: rec run prints a run's id"
    return [ToolCall(*entry) for entry in [
        ("Skill", "experiment-lab:experiment-db", "Launching skill: experiment-lab:experiment-db", False),
        ("Bash", f"cd {codec} && rec tree", "audio-codec  (~/audio-codec)\n  sample rate sweep", False),
        ("Bash", f'rec add finding "Dither helps" --in "16-bit output" --text "Better at 44100 Hz."'
                 f" --evidence run:{RUN_ID}", failed_run, True),
        ("Bash", f'rec add finding "Dither helps" --in "16-bit output" --text "Better at 44100 Hz."'
                 f" --evidence run:{RUN_ID}", failed_run, True),
        ("Bash", 'rec add finding "Dither helps" --in "16-bit output" --text "Better at 44100 Hz."',
         "finding dither-helps, closed", False),
        ("Bash", "q \"SELECT id, title FROM records WHERE holds_words(title || ' ' || text, 'sample rate')\"",
         "id | title\n(0 rows)", False),
        ("Bash", "q \"SELECT kind, id FROM experiments_runs_records WHERE holds_words(title || ' ' || text, 'rate')\"",
         "kind | id\nexperiment | sample rate sweep\n(1 rows)", False),
        ("Bash", "q \"SELECT name, metrics['accuracy'] FROM runs WHERE experiment = 'sample rate sweep'\"",
         "name | accuracy\n" + "48k | 0.93\n" * 400 + "(400 rows)", False),
        ("Bash", f"cd {codec} && rec tree", "audio-codec  (~/audio-codec)\n  sample rate sweep", False),
        ("Read", f"{home}/.claude/plugins/cache/experiment-lab-marketplace/experiment-lab/0.2.40/skills/"
                 "experiment-db/references/runs.md", "Runs outside Python", False),
        ("Bash", "rec experiment --help", "usage: rec experiment NAME", False),
        ("Bash", "PYTHONPATH=~/experiment-lab python3 - <<'EOF'\nfrom experiment_lab.lab import Lab\n"
                 "print(Lab().runs('noise gate')[0].metrics['snr'])\nEOF",
         "Traceback (most recent call last):\n  File \"<stdin>\", line 2\nKeyError: 'snr'", False),
        ("Bash", "q \"SELECT id FROM records WHERE kind = 'question'\"", "id\n(0 rows)", False),
        # A shell command that runs two lab commands failed; the next that does one of them does it again.
        ("Bash", 'R=$(rec run "sample rate sweep" "48k again"); rec end $R --metric accuracy=0.9',
         "Exit code 1\nerror: the run id is empty, as a shell variable is when the command that set it failed", True),
        ("Bash", "rec end 0123abcd --metric accuracy=0.9", "", False),
        # A record of a kind that is none; the next rec add, of the right kind, does it again.
        ("Bash", 'rec add rule "One setup" --in "sample rate sweep" --text "Same machine."',
         "Exit code 1\nerror: kind 'rule' is not one of question, decision, protocol, finding", True),
        ("Bash", 'rec add protocol "One setup" --in "sample rate sweep" --text "Same machine."',
         "protocol one-setup, open", False),
        # A q that found rows, then one that found none: not a search that found nothing.
        ("Bash", "q \"SELECT name FROM runs\"; q \"SELECT name FROM runs WHERE status = 'RUNNING'\"",
         "name\n48k\n(1 rows)\nname\n(0 rows)", False),
        ("Bash", "q \"SELECT name, status FROM runs\"", "name | status\n48k | FINISHED\n(1 rows)", False),
        ("Bash", "ls", "bench.py", False),
        ("Bash", "xset q", "Exit code 1\nxset:  unable to open display", True),
        ("Bash", "git commit -q -F - <<'EOF'\nMake rec add print the id\nEOF", "", False),
        ("Bash", "PYTHONPATH=~/experiment-lab python3 -m experiment_lab.feedback draft --session x",
         "the draft", False),
    ]]


def sent_before_calls() -> list[ToolCall]:
    """A session's tool calls: lab work, a report sent, then more lab work."""
    return [ToolCall(*entry) for entry in [
        ("Bash", 'rec add finding "Old" --in nowhere --text "x."', "Exit code 1\nerror: no experiment 'nowhere'", True),
        ("Bash", "PYTHONPATH=~/experiment-lab python3 -m experiment_lab.feedback send FILE --title 'Agent report: x'",
         "https://github.com/dgutson/experiment-lab/issues/7", False),
        ("Bash", "q \"SELECT name FROM runs\"", "name\n48k\n(1 rows)", False),
        ("Bash", "q \"SELECT nosuch FROM runs\"", 'Exit code 1\nerror: Binder Error: Referenced column "nosuch" not found',
         True),
    ]]


def transcript_text(entries: list[ToolCall]) -> str:
    """A Claude Code transcript of the tool calls, one assistant message each,
    a second apart."""
    lines = []
    for i, (tool, text, output, failed) in enumerate(entries):
        key = {"Bash": "command", "Read": "file_path", "Skill": "skill"}[tool]
        time = f"2026-11-01T10:{i // 60:02d}:{i % 60:02d}.000Z"
        lines.append({"type": "assistant", "version": "2.1.316", "timestamp": time, "message": {
            "id": f"msg_{i}", "model": "claude-sonnet-5-5",
            "content": [{"type": "tool_use", "id": f"tool_{i}", "name": tool, "input": {key: text}}]}})
        lines.append({"type": "user", "version": "2.1.316", "timestamp": time, "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"tool_{i}", "content": output, "is_error": failed}]}})
    return "".join(json.dumps(line) + "\n" for line in lines)


def run_module(command: list[str], env: dict[str, str]) -> str:
    done = subprocess.run(command, capture_output=True, text=True, env=env, cwd=ROOT, check=False)
    return f"$ {' '.join(command[3:])}\n{done.stdout}{done.stderr}[exit {done.returncode}]\n"


def mask_changes(text: str, folder: Path) -> str:
    """text, with what changes from run to run made fixed: the folder, the
    clone's folder, the draft's time (and the -2 of a second draft in the
    same second), the plugin's version and experiment-lab's version and
    commit."""
    text = text.replace(str(folder), "FOLDER").replace(str(ROOT), "ROOT")
    text = re.sub(r"\d{4}-\d\d-\d\dT\d\d-\d\d-\d\dZ(?:-\d+)?", "TIME", text)
    return re.sub(r"plugin [\w.]+; experiment-lab [^;]+;", "plugin VERSION; experiment-lab VERSION;", text)


def main() -> str:
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary) / "lab"
        subprocess.run([sys.executable, ROOT / "checks" / "make_scratch.py", folder], check=True,
                       capture_output=True)
        home = folder / "home"
        projects = folder / "claude" / "projects" / "-home-audio-codec"
        (projects / SESSION / "subagents").mkdir(parents=True)
        (projects / f"{SESSION}.jsonl").write_text(transcript_text(session_calls(home)))
        (projects / f"{SENT_BEFORE}.jsonl").write_text(transcript_text(sent_before_calls()))
        (projects / SESSION / "subagents" / "agent-1.jsonl").write_text(transcript_text(
            [ToolCall("Bash", 'q "SELECT * FROM runz"', "error: Catalog Error: Table with name runz does not exist!",
                   True)]))
        gh = folder / "gh"
        gh.mkdir()
        (gh / "gh").write_text(FAKE_GH)
        (gh / "gh").chmod(0o755)
        env = {**os.environ, "HOME": str(home), "CLAUDE_CONFIG_DIR": str(folder / "claude"),
               "EXPERIMENT_LAB_DATA": str(folder / "lab-data"), "PYTHONPATH": str(ROOT)}
        module = [sys.executable, "-m", "experiment_lab.feedback"]
        draft = [*module, "draft", "--session", SESSION, "--plugin", str(ROOT / "plugin")]
        text = run_module(draft, env)
        report = next((folder / "lab-data" / "feedback").glob("*.md"))
        text += run_module([*draft, "--real"], env)
        text += run_module([*module, "draft", "--session", SENT_BEFORE, "--plugin", str(ROOT / "plugin")], env)
        text += run_module([*module, "draft", "--session", "none"], env)
        send = [*module, "send", str(report), "--title", "Agent report: a check"]
        text += run_module(send, {**env, "PATH": f"{gh}:{env['PATH']}"})
        text += run_module(send, {**env, "PATH": str(Path(sys.executable).parent)})
        return mask_changes(text, folder)


if __name__ == "__main__":
    printed = main()
    if sys.argv[1:] == ["--update"]:
        SAVED.write_text(printed)
    elif printed != SAVED.read_text():
        sys.stdout.writelines(difflib.unified_diff(SAVED.read_text().splitlines(True), printed.splitlines(True),
                                                   "check_feedback.txt", "printed"))
        sys.exit("feedback prints something else; --update writes it to check_feedback.txt")
