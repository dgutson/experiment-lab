"""A report on how the lab served a Claude Code session, for the people who
make experiment-lab, which the plugin's skill feedback has the agent write. It
reads the session's transcript, so that the agent need not recall the session,
and makes the report's names neutral, so that the agent need not rewrite it.
Standard library only.

python3 -m experiment_lab.feedback draft --session ID [--plugin DIR] [--real]
    writes a draft report to the data folder's feedback/<UTC time>.md, and
    prints its path and its text: the versions; how many lab commands (rec, q,
    lab-server and Python that uses experiment_lab) the session ran, how many
    failed and how much they printed; each lab command that failed, with its
    error and the command that next did the same work without failing; each
    q that printed no rows, with the q that next printed some; the commands
    run more than once; those that printed much; and the help, skill
    files and lab code the session read. Without --real, the lab's project,
    experiment and record names, run ids, paths, numbers and quoted texts are
    replaced with neutral ones. ID is the session's, whose transcript is in
    the projects folder of Claude Code's config folder ($CLAUDE_CONFIG_DIR,
    else ~/.claude); DIR is the plugin's folder, which holds its version.
python3 -m experiment_lab.feedback send FILE --title TITLE
    opens an issue of dgutson/experiment-lab with FILE as its text, labelled
    agent-feedback, with gh; without gh or its login, prints how to open it by
    hand.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from experiment_lab.lab import Lab
from experiment_lab.settings import data_folder

REPO = "dgutson/experiment-lab"
LABEL = "agent-feedback"
CLONE = Path(__file__).resolve().parents[1]
LONG = 4000  # characters a command prints that count as much
SHOWN = 160  # characters of a command or error the report shows
MOST = 10  # items the report shows in a list

LAB_TOOLS = {"rec", "q", "lab-server"}
PYTHON_LAB = re.compile(r"experiment_lab(?![/\w])|PYTHONPATH=\S*experiment-lab")  # Python that uses the lab
LAB_FILES = re.compile(r"skills/experiment-db/|experiment_lab/\w+\.(?:py|sql)")
ENDS = set(";&|()\n")  # the shell's characters that end a simple command
# The words before a command's own: the shell's keywords and the commands that run another.
BEFORE = {"do", "then", "else", "elif", "if", "while", "until", "!", "{", "env", "time", "exec", "nohup", "setsid"}
ASSIGNMENT = re.compile(r"\w+=.*", re.S)
EMPTY = re.compile(r"^\(0 rows\)$", re.M)  # what q prints for no rows
FAILED = re.compile(r"^(?:error:|Traceback \(most recent call last\))|command not found", re.M)
WRONG = re.compile(r"^error:|command not found|denied|No such file")  # a line that says what went wrong
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1([^\n]*)\n.*?\n\2(?=\n|$)", re.S)
QUOTED = re.compile(r'"(?:[^"\\\n]|\\.)*"|(?<!\w)\'[^\'\n]*\'(?!\w)')
SQL = re.compile(r"\s*(?:SELECT|WITH|FROM|SET|SHOW|DESCRIBE|SUMMARIZE|PRAGMA|CREATE)\b", re.I)
PATH = re.compile(r"(?<![\w:\x00])(?:~|/)[\w.-]*(?:/[\w.-]+)+")
NUMBER = re.compile(r"(?<![\w.\x00&>])(?<!\s-)\d+(?:\.\d+)?(?![\w.>])")
RUN_ID = re.compile(r"\b[0-9a-f]{32}\b")
KEPT = re.compile(r"\x00([a-z]+)\x00")
# The lab's own words, which a project or experiment of that name must not replace in commands.
WORDS = {"tree", "project", "experiment", "add", "set", "open", "run", "end", "runs", "records", "experiments",
         "files", "metrics", "params", "tags", "finding", "decision", "question", "protocol", "select", "from",
         "where", "views", "keys"}


class ToolCall(NamedTuple):
    """A tool call: the assistant message it is in, counted from 0; the tool;
    its command, path or skill; what it printed; whether it failed."""
    turn: int
    tool: str
    text: str
    output: str
    failed: bool


class Friction(NamedTuple):
    """A lab command that failed or found nothing, how many tries did, and the
    call that next did the same work, if any."""
    first: ToolCall
    tries: int
    done: ToolCall | None


class LabName(NamedTuple):
    """What a name of the lab is (project, folder, experiment, record or run)
    and the name it stands for: a folder's project, else itself."""
    kind: str
    stands_for: str


class ToolUse(NamedTuple):
    """A tool call whose result has not come yet: its turn, tool and text."""
    turn: int
    tool: str
    text: str


type Unanswered = dict[str, ToolUse]  # by the tool call's id
type Misses = list[ToolCall]
type Words = list[str]  # a simple command's, as the shell splits them


class Transcript(NamedTuple):
    calls: list[ToolCall]
    versions: set[str]
    models: set[str]


def letter_name(i: int) -> str:
    """a, b, ..., z, aa, ab, ...: the i-th neutral name, from 0."""
    text = ""
    i += 1
    while i:
        i, rest = divmod(i - 1, 26)
        text = chr(ord("a") + rest) + text
    return text


def transcript_files(session_id: str) -> list[Path]:
    """The session's transcript, then its subagents', in the projects folder
    of $CLAUDE_CONFIG_DIR, else of ~/.claude."""
    folders = [Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser(), Path.home() / ".claude"]
    found = [path for folder in folders for path in sorted((folder / "projects").glob(f"*/{session_id}.jsonl"))]
    if not found:
        sys.exit(f"error: no transcript of the session {session_id} in {folders[0] / 'projects'}"
                 f" or {folders[1] / 'projects'}")
    return found[:1] + sorted(found[0].with_suffix("").glob("subagents/*.jsonl"))


def result_text(content: object) -> str:
    """A tool result's text, which is text or a list of blocks."""
    if isinstance(content, list):
        return "\n".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
    return str(content or "")


def note_tool_uses(message: dict[str, Any], turn: int, unanswered: Unanswered) -> None:
    """Notes the tool calls of an assistant message, by id, until their results come."""
    for block in message["content"]:
        if block.get("type") == "tool_use":
            given = block.get("input", {})
            text = given.get("command") or given.get("file_path") or given.get("skill") or ""
            unanswered[block["id"]] = ToolUse(turn, block["name"], str(text))


def add_tool_results(message: dict[str, Any], unanswered: Unanswered, calls: list[ToolCall]) -> None:
    """Adds to calls the noted tool calls whose results the message holds."""
    for block in message["content"]:
        if block.get("type") == "tool_result" and block.get("tool_use_id") in unanswered:
            output = result_text(block.get("content"))
            failed = bool(block.get("is_error")) or bool(FAILED.search(output))
            calls.append(ToolCall(*unanswered.pop(block["tool_use_id"]), output, failed))


def read_transcript(path: Path, transcript: Transcript) -> None:
    """Adds to session the tool calls of a transcript, and the Claude Code
    versions and models in it."""
    unanswered: Unanswered = {}
    turns: dict[str, int] = {}
    for line in path.read_text().splitlines():
        entry = json.loads(line)
        message = entry.get("message")
        transcript.versions.add(entry.get("version") or "")
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            continue
        if entry.get("type") == "assistant":
            transcript.models.add(message.get("model") or "")
            note_tool_uses(message, turns.setdefault(message.get("id", len(turns)), len(turns)), unanswered)
        add_tool_results(message, unanswered, transcript.calls)


def substitutions(command: str) -> Iterator[str]:
    """The commands inside each `...` and $(...) of command."""
    yield from re.findall(r"`([^`]*)`", command)
    start = command.find("$(")
    while start >= 0:
        depth, end = 1, start + 2
        while end < len(command) and depth:
            depth += {"(": 1, ")": -1}.get(command[end], 0)
            end += 1
        yield command[start + 2:end - 1]
        start = command.find("$(", end)


def simple_commands(command: str) -> Iterator[Words]:
    """The words of each simple command of a shell command, as shell_words
    splits them: those of its `...` and $(...) first, and those of a bash -c
    or sh -c command after it. Heredoc bodies are left out."""
    command = HEREDOC.sub(lambda found: f"<<{found[2]}{found[3]}", command)
    for inner in substitutions(command):
        yield from simple_commands(inner)
    for words in shell_words(command):
        yield words
        if tool(words) in ("bash", "sh") and "-c" in words[:-1]:
            yield from simple_commands(words[words.index("-c") + 1])


def shell_words(command: str) -> Iterator[Words]:
    """The words of each simple command of command, as shlex splits them,
    without the assignments and keywords before the command's own; what
    follows a quote that never closes is left out."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&\n")
    lexer.whitespace, lexer.whitespace_split, lexer.commenters = " \t\r", True, ""
    words: Words = []
    try:
        for token in lexer:
            if set(token) <= ENDS:
                yield from [words] if words else []
                words = []
            elif words or not (token in BEFORE or ASSIGNMENT.fullmatch(token)):
                words.append(token)
    except ValueError:
        pass
    yield from [words] if words else []


def tool(words: Words) -> str:
    """The command a simple command runs, without its folder."""
    return words[0].rsplit("/", 1)[-1]


def is_lab_command(call: ToolCall) -> bool:
    """Whether the call ran rec, q or lab-server, or Python that uses the lab."""
    if call.tool != "Bash" or "experiment_lab.feedback" in call.text:
        return False
    tools = {tool(words) for words in simple_commands(call.text)}
    return bool(tools & LAB_TOOLS) or any(name.startswith("python") for name in tools) and bool(
        PYTHON_LAB.search(call.text))


def reads_lab_docs(call: ToolCall) -> bool:
    """Whether the call read the help of a lab command, the skill's files or
    the lab's code."""
    if "feedback" in call.text or call.tool not in ("Bash", "Read"):
        return False
    return bool(LAB_FILES.search(call.text)) or call.tool == "Bash" and "--help" in call.text and is_lab_command(call)


def lab_work(command: str) -> str:
    """What a lab command does, to match one that failed with the next that did
    the same work: rec and its command, q, lab-server, or Python."""
    for words in simple_commands(command):
        if tool(words) == "rec" and len(words) > 1 and not words[1].startswith("-"):
            return f"rec {words[1]}"
        if tool(words) in LAB_TOOLS:
            return tool(words)
    return "python"


def found_no_rows(call: ToolCall) -> bool:
    """Whether call is a q that printed no rows."""
    return lab_work(call.text) == "q" and bool(EMPTY.search(call.output))


def frictions(calls: list[ToolCall], missed: Callable[[ToolCall], bool]) -> list[Friction]:
    """Each run of lab commands of the same work that missed (failed, or found
    nothing), with the call that next did that work, in the order they
    began."""
    failing: dict[str, Misses] = {}
    found: list[Friction] = []
    for call in calls:
        if missed(call):
            failing.setdefault(lab_work(call.text), []).append(call)
        elif (tries := failing.pop(lab_work(call.text), None)) is not None:
            found.append(Friction(tries[0], len(tries), call))
    found += [Friction(tries[0], len(tries), None) for tries in failing.values()]
    return sorted(found, key=lambda friction: friction.first.turn)


def error_line(output: str) -> str:
    """The line of what a failed command printed that says what went wrong."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if "Traceback (most recent call last)" in output:
        return next((line for line in reversed(lines) if re.match(r"\w*(?:Error|Exception)\b", line)), lines[-1])
    return next((line for line in lines if WRONG.search(line)),
                next((line for line in lines if not line.startswith("Exit code")), ""))


class Neutralizer:
    """Replaces, in what the session ran and printed, the lab's names, run ids,
    paths, numbers and quoted texts with neutral ones: project-a and its
    folder ~/project-a, experiment-a, record-a, run-a, <path>, <n>, <text>.
    Paths into experiment-lab and its plugin stay. A name is the same neutral
    name throughout the report."""

    def __init__(self, lab: Lab | None) -> None:
        self.real = lab is None
        self.labels: dict[LabName, str] = {}
        self.kinds: dict[str, LabName] = {}
        self.kept: dict[str, str] = {}
        if lab is not None:
            self.learn_names(lab)
        names = sorted(self.kinds, key=len, reverse=True)
        self.names = re.compile(f"(?<![\\w-])({'|'.join(map(re.escape, names))})((?:/[\\w.-]+)*)(?![\\w-])"
                                if names else r"(?!)")

    def learn_names(self, lab: Lab) -> None:
        """The names to replace: projects, their folders, experiments and
        records, of 3 characters or more, since a shorter one is likely a word."""
        home = str(Path.home())
        for name, folder, files in lab.db.execute("SELECT name, folder, files FROM experiments"):
            self.kinds[name] = LabName("project" if folder else "experiment", name)
            for path in {folder, files} - {None}:
                self.kinds[path.replace(home, "~", 1)] = LabName("folder", name)
        self.kinds |= {rid: LabName("record", rid) for (rid,) in lab.db.execute("SELECT id FROM records")}
        self.kinds = {name: kind for name, kind in self.kinds.items() if len(name) >= 3 and name.lower() not in WORDS}

    def label(self, name: LabName) -> str:
        """The neutral name of name: its kind and a letter, the next for its kind."""
        if name not in self.labels:
            self.labels[name] = f"{name.kind}-{letter_name(sum(n.kind == name.kind for n in self.labels))}"
        return self.labels[name]

    def keep(self, text: str) -> str:
        """text, which later replacements leave as it is."""
        key = letter_name(len(self.kept))
        self.kept[key] = text
        return f"\x00{key}\x00"

    def replace_name(self, found: re.Match[str]) -> str:
        """A name of the lab, and the path that follows it, as neutral ones."""
        name = self.kinds[found[1]]
        rest = "/<path>" if found[2] else ""
        if name.kind == "folder":
            return self.keep(f"~/{self.label(LabName('project', name.stands_for))}{rest}")
        return self.keep(self.label(name) + rest)

    def replace_quoted(self, found: re.Match[str]) -> str:
        """A quoted text, as <text>, unless it holds only neutral names; in SQL,
        only its quoted values."""
        mark, inside = found[0][0], found[0][1:-1]
        if not KEPT.sub("", inside).strip() or inside.lower() in WORDS or "experiment" in inside and "lab" in inside:
            return found[0]
        if mark == '"' and SQL.match(inside):
            return mark + QUOTED.sub(self.replace_quoted, inside) + mark
        return self.keep(f"{mark}<text>{mark}")

    def replace_path(self, found: re.Match[str]) -> str:
        lab_file = any(part in found[0] for part in ("experiment-lab", "experiment_lab", ".claude/plugins", "/dev/"))
        return found[0] if lab_file else self.keep("<path>")

    def rewrite(self, text: str) -> str:
        text = text.replace(str(Path.home()), "~")
        if self.real:
            return text
        text = HEREDOC.sub(lambda m: f"{self.keep(f'<<{m[1]}{m[2]}{m[1]}')}{m[3]}\n{self.keep('<text>')}\n"
                                     f"{self.keep(m[2])}", text)
        text = self.names.sub(self.replace_name, text)
        text = RUN_ID.sub(lambda m: self.keep(self.label(LabName("run", m[0]))), text)
        text = QUOTED.sub(self.replace_quoted, text)
        text = PATH.sub(self.replace_path, text)
        text = NUMBER.sub("<n>", text)
        while KEPT.search(text):
            text = KEPT.sub(lambda m: self.kept[m[1]], text)
        return text


def inline_code(text: str) -> str:
    """text as inline code, on one line and at most SHOWN characters."""
    text = text.replace("\n", " ⏎ ")
    text = text if len(text) <= SHOWN else text[:SHOWN - 1] + "…"
    fence = "``" if "`" in text else "`"
    return f"{fence}{text}{fence}" if fence == "`" else f"{fence} {text} {fence}"


def run_command(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def lab_version() -> str:
    """experiment-lab's version and the clone's commit, as far as they are
    known: a copy of the package alone has neither."""
    toml = CLONE / "pyproject.toml"
    version = tomllib.loads(toml.read_text())["project"]["version"] if toml.exists() else "unknown"
    git = run_command("git", "-C", str(CLONE), "log", "-1", "--format=%h, %cs")
    return version + (f" ({git.stdout.strip()})" if git.returncode == 0 else "")


def versions_line(transcript: Transcript, plugin: Path | None) -> str:
    """The plugin's version, experiment-lab's version and commit, Claude
    Code's versions and the models."""
    manifest = plugin / ".claude-plugin" / "plugin.json" if plugin else None
    shown = [f"plugin {json.loads(manifest.read_text())['version']}" if manifest and manifest.exists()
             else "plugin unknown"]
    shown.append(f"experiment-lab {lab_version()}")
    shown.append("Claude Code " + ", ".join(sorted(transcript.versions - {""})))
    return "; ".join(shown + sorted(model for model in transcript.models if model and not model.startswith("<")))


def section(title: str, items: list[str]) -> Iterator[str]:
    if items:
        yield f"\n## {title}\n"
        yield from (f"- {item}" for item in items[:MOST])
        if len(items) > MOST:
            yield f"- and {len(items) - MOST} more"


def turns_text(n: int) -> str:
    return f"{n} turn" if n == 1 else f"{n} turns"


def friction_line(friction: Friction, what: str, done: str, neutralizer: Neutralizer) -> str:
    """friction as a line of the report: the command, what it printed, its
    tries, and the call that next did the same work."""
    tries = f" ({friction.tries} tries)" if friction.tries > 1 else ""
    later = (f"; {done} {turns_text(friction.done.turn - friction.first.turn)} later by"
             f" {inline_code(neutralizer.rewrite(friction.done.text))}" if friction.done else f"; not {done} later")
    return f"{inline_code(neutralizer.rewrite(friction.first.text))}: {what}{tries}{later}"


def failure_lines(calls: list[ToolCall], neutralizer: Neutralizer) -> list[str]:
    return [friction_line(friction, inline_code(neutralizer.rewrite(error_line(friction.first.output))), "done", neutralizer)
            for friction in frictions(calls, lambda call: call.failed)]


def search_lines(calls: list[ToolCall], neutralizer: Neutralizer) -> list[str]:
    """Each q that printed no rows and that a later q, which printed some,
    searched again; a q of no rows that nothing searched again checked that
    something was not there."""
    return [friction_line(friction, "no rows", "found", neutralizer)
            for friction in frictions([call for call in calls if not call.failed], found_no_rows) if friction.done]


def counted_lines(texts: list[str], neutralizer: Neutralizer, least: int) -> list[str]:
    """Each text that comes least times or more, with how many times if more
    than once, the most frequent first."""
    return [inline_code(neutralizer.rewrite(text)) + (f": {count} times" if count > 1 else "")
            for text, count in Counter(texts).most_common() if count >= least]


def usage_line(calls: list[ToolCall], every: int) -> str:
    """How many lab commands of every tool call, of what kind, how many failed
    and how much they printed."""
    kinds = Counter(lab_work(call.text).split()[0] for call in calls)
    size = sum(len(call.output) for call in calls)
    return (f"{len(calls)} lab commands of {every} tool calls: {kinds['rec']} rec, {kinds['q']} q,"
            f" {len(calls) - kinds['rec'] - kinds['q']} other; {sum(call.failed for call in calls)} failed;"
            f" they printed {size / 1000:.1f} kB, about {size // 4:,} tokens.")


def report_text(transcript: Transcript, plugin: Path | None, neutralizer: Neutralizer) -> str:
    calls = [call for call in transcript.calls if is_lab_command(call)]
    head = [f"## Versions\n\n{versions_line(transcript, plugin)}\n", "## The lab in this session\n",
            usage_line(calls, len(transcript.calls))]
    much = [f"{inline_code(neutralizer.rewrite(call.text))}: {len(call.output) / 1000:.1f} kB" for call in calls
            if len(call.output) > LONG]
    return "\n".join([*head, *section("Lab commands that failed", failure_lines(calls, neutralizer)),
                      *section("Searches that found nothing, run again", search_lines(calls, neutralizer)),
                      *section("Run more than once", counted_lines([call.text for call in calls], neutralizer, 2)),
                      *section("Printed much", much),
                      *section("Help, skill files and lab code read",
                             counted_lines([call.text for call in transcript.calls if reads_lab_docs(call)], neutralizer, 1))]) + "\n"


def report_path(folder: Path) -> Path:
    """A file of folder that does not exist, named for the UTC time, with -2,
    -3 and so on when one of that name exists."""
    time = f"{datetime.now(UTC):%Y-%m-%dT%H-%M-%SZ}"
    path, i = folder / f"{time}.md", 1
    while path.exists():
        i += 1
        path = folder / f"{time}-{i}.md"
    return path


def draft(session_id: str, plugin: Path | None, real: bool) -> None:
    transcript = Transcript([], set(), set())
    for path in transcript_files(session_id):
        read_transcript(path, transcript)
    lab = None if real else Lab()
    text = report_text(transcript, plugin, Neutralizer(lab))
    if lab is not None:
        lab.close()
    folder = data_folder() / "feedback"
    folder.mkdir(parents=True, exist_ok=True)
    path = report_path(folder)
    path.write_text(text)
    print(f"{path}\n\n{text}", end="")


def send(path: Path, title: str) -> int:
    """Opens the issue with gh, labelled if gh can label it; else says how to
    open it by hand."""
    issue = ["gh", "issue", "create", "--repo", REPO, "--title", title, "--body-file", str(path)]
    if shutil.which("gh") and run_command("gh", "auth", "status").returncode == 0:
        sent = run_command(*issue, "--label", LABEL)
        if sent.returncode and "label" in sent.stderr.lower():
            sent = run_command(*issue)
        if sent.returncode == 0:
            print(sent.stdout.strip())
            return 0
        print(sent.stderr.strip(), file=sys.stderr)
    print(f"not sent: open https://github.com/{REPO}/issues/new, give it the title {title!r} and paste"
          f" the text of {path}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m experiment_lab.feedback", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    drafting = commands.add_parser("draft")
    drafting.add_argument("--session", required=True)
    drafting.add_argument("--plugin", type=Path)
    drafting.add_argument("--real", action="store_true")
    sending = commands.add_parser("send")
    sending.add_argument("file", type=Path)
    sending.add_argument("--title", required=True)
    given: Any = parser.parse_args(argv)
    if given.command == "draft":
        draft(given.session, given.plugin, given.real)
        return 0
    return send(given.file, given.title)


if __name__ == "__main__":
    sys.exit(main())
