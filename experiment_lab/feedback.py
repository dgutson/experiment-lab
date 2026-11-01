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

LAB = re.compile(r"(?:^|[\s;&|(`])(?:rec|q|lab-server)(?=\s|$)|experiment_lab(?![/\w])|PYTHONPATH=\S*experiment-lab")
EMPTY = re.compile(r"^\(0 rows\)$", re.M)  # what q prints for no rows
FAILED = re.compile(r"^(?:error:|Traceback \(most recent call last\))|command not found", re.M)
WRONG = re.compile(r"^error:|command not found|denied|No such file")  # a line that says what went wrong
READS = re.compile(r"--help|skills/experiment-db/|experiment_lab/\w+\.(?:py|sql)")
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


class Call(NamedTuple):
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
    first: Call
    tries: int
    done: Call | None


class Name(NamedTuple):
    """What a name of the lab is (project, folder, experiment, record or run)
    and the name it stands for: a folder's project, else itself."""
    kind: str
    key: str


class Started(NamedTuple):
    """A tool call whose result has not come yet: its turn, tool and text."""
    turn: int
    tool: str
    text: str


type Pending = dict[str, Started]  # by the tool call's id
type Tries = list[Call]


class Session(NamedTuple):
    calls: list[Call]
    versions: set[str]
    models: set[str]


def letters(i: int) -> str:
    """a, b, ..., z, aa, ab, ...: the i-th neutral name, from 0."""
    text = ""
    i += 1
    while i:
        i, rest = divmod(i - 1, 26)
        text = chr(ord("a") + rest) + text
    return text


def transcripts(session: str) -> list[Path]:
    """The session's transcript, then its subagents', in the projects folder
    of $CLAUDE_CONFIG_DIR, else of ~/.claude."""
    folders = [Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser(), Path.home() / ".claude"]
    found = [path for folder in folders for path in sorted((folder / "projects").glob(f"*/{session}.jsonl"))]
    if not found:
        sys.exit(f"error: no transcript of the session {session} in {folders[0] / 'projects'}"
                 f" or {folders[1] / 'projects'}")
    return found[:1] + sorted(found[0].with_suffix("").glob("subagents/*.jsonl"))


def printed(content: object) -> str:
    """A tool result's text, which is text or a list of blocks."""
    if isinstance(content, list):
        return "\n".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
    return str(content or "")


def tool_uses(message: dict[str, Any], turn: int, pending: Pending) -> None:
    """Notes the tool calls of an assistant message, by id, until their results come."""
    for block in message["content"]:
        if block.get("type") == "tool_use":
            given = block.get("input", {})
            text = given.get("command") or given.get("file_path") or given.get("skill") or ""
            pending[block["id"]] = Started(turn, block["name"], str(text))


def tool_results(message: dict[str, Any], pending: Pending, calls: list[Call]) -> None:
    """Adds to calls the noted tool calls whose results the message holds."""
    for block in message["content"]:
        if block.get("type") == "tool_result" and block.get("tool_use_id") in pending:
            output = printed(block.get("content"))
            failed = bool(block.get("is_error")) or bool(FAILED.search(output))
            calls.append(Call(*pending.pop(block["tool_use_id"]), output, failed))


def read(path: Path, session: Session) -> None:
    """Adds to session the tool calls of a transcript, and the Claude Code
    versions and models in it."""
    pending: Pending = {}
    turns: dict[str, int] = {}
    for line in path.read_text().splitlines():
        entry = json.loads(line)
        message = entry.get("message")
        session.versions.add(entry.get("version") or "")
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            continue
        if entry.get("type") == "assistant":
            session.models.add(message.get("model") or "")
            tool_uses(message, turns.setdefault(message.get("id", len(turns)), len(turns)), pending)
        tool_results(message, pending, session.calls)


def is_lab(call: Call) -> bool:
    return call.tool == "Bash" and bool(LAB.search(call.text)) and "experiment_lab.feedback" not in call.text


def is_read(call: Call) -> bool:
    """Whether the call read the help, the skill's files or the lab's code."""
    return call.tool in ("Bash", "Read") and bool(READS.search(call.text)) and "feedback" not in call.text


def work(command: str) -> str:
    """What a lab command does, to match one that failed with the next that did
    the same work: rec and its command, q, lab-server, or Python."""
    found = re.search(r"\brec\s+([a-z]+)|(?:^|[\s;&|(])(q|lab-server)(?=\s|$)", command)
    if found:
        return f"rec {found[1]}" if found[1] else found[2]
    return "python"


def is_empty(call: Call) -> bool:
    """Whether call is a q that printed no rows."""
    return work(call.text) == "q" and bool(EMPTY.search(call.output))


def frictions(calls: list[Call], missed: Callable[[Call], bool]) -> list[Friction]:
    """Each run of lab commands of the same work that missed (failed, or found
    nothing), with the call that next did that work, in the order they
    began."""
    failing: dict[str, Tries] = {}
    found: list[Friction] = []
    for call in calls:
        if missed(call):
            failing.setdefault(work(call.text), []).append(call)
        elif (tries := failing.pop(work(call.text), None)) is not None:
            found.append(Friction(tries[0], len(tries), call))
    found += [Friction(tries[0], len(tries), None) for tries in failing.values()]
    return sorted(found, key=lambda friction: friction.first.turn)


def error(output: str) -> str:
    """The line of what a failed command printed that says what went wrong."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if "Traceback (most recent call last)" in output:
        return next((line for line in reversed(lines) if re.match(r"\w*(?:Error|Exception)\b", line)), lines[-1])
    return next((line for line in lines if WRONG.search(line)),
                next((line for line in lines if not line.startswith("Exit code")), ""))


class Neutral:
    """Replaces, in what the session ran and printed, the lab's names, run ids,
    paths, numbers and quoted texts with neutral ones: project-a and its
    folder ~/project-a, experiment-a, record-a, run-a, <path>, <n>, <text>.
    Paths into experiment-lab and its plugin stay. A name is the same neutral
    name throughout the report."""

    def __init__(self, lab: Lab | None) -> None:
        self.real = lab is None
        self.labels: dict[Name, str] = {}
        self.kinds: dict[str, Name] = {}
        self.kept: dict[str, str] = {}
        if lab is not None:
            self.learn(lab)
        names = sorted(self.kinds, key=len, reverse=True)
        self.names = re.compile(f"(?<![\\w-])({'|'.join(map(re.escape, names))})((?:/[\\w.-]+)*)(?![\\w-])"
                                if names else r"(?!)")

    def learn(self, lab: Lab) -> None:
        """The names to replace: projects, their folders, experiments and
        records, of 3 characters or more, since a shorter one is likely a word."""
        home = str(Path.home())
        for name, folder, files in lab.db.execute("SELECT name, folder, files FROM experiments"):
            self.kinds[name] = Name("project" if folder else "experiment", name)
            for path in {folder, files} - {None}:
                self.kinds[path.replace(home, "~", 1)] = Name("folder", name)
        self.kinds |= {rid: Name("record", rid) for (rid,) in lab.db.execute("SELECT id FROM records")}
        self.kinds = {name: kind for name, kind in self.kinds.items() if len(name) >= 3 and name.lower() not in WORDS}

    def label(self, name: Name) -> str:
        """The neutral name of name: its kind and a letter, the next for its kind."""
        if name not in self.labels:
            self.labels[name] = f"{name.kind}-{letters(sum(n.kind == name.kind for n in self.labels))}"
        return self.labels[name]

    def keep(self, text: str) -> str:
        """text, which later replacements leave as it is."""
        key = letters(len(self.kept))
        self.kept[key] = text
        return f"\x00{key}\x00"

    def name(self, found: re.Match[str]) -> str:
        """A name of the lab, and the path that follows it, as neutral ones."""
        name = self.kinds[found[1]]
        rest = "/<path>" if found[2] else ""
        if name.kind == "folder":
            return self.keep(f"~/{self.label(Name('project', name.key))}{rest}")
        return self.keep(self.label(name) + rest)

    def quoted(self, found: re.Match[str]) -> str:
        """A quoted text, as <text>, unless it holds only neutral names; in SQL,
        only its quoted values."""
        mark, inside = found[0][0], found[0][1:-1]
        if not KEPT.sub("", inside).strip() or inside.lower() in WORDS or "experiment" in inside and "lab" in inside:
            return found[0]
        if mark == '"' and SQL.match(inside):
            return mark + QUOTED.sub(self.quoted, inside) + mark
        return self.keep(f"{mark}<text>{mark}")

    def path(self, found: re.Match[str]) -> str:
        lab_file = any(part in found[0] for part in ("experiment-lab", "experiment_lab", ".claude/plugins", "/dev/"))
        return found[0] if lab_file else self.keep("<path>")

    def text(self, text: str) -> str:
        text = text.replace(str(Path.home()), "~")
        if self.real:
            return text
        text = HEREDOC.sub(lambda m: f"{self.keep(f'<<{m[1]}{m[2]}{m[1]}')}{m[3]}\n{self.keep('<text>')}\n"
                                     f"{self.keep(m[2])}", text)
        text = self.names.sub(self.name, text)
        text = RUN_ID.sub(lambda m: self.keep(self.label(Name("run", m[0]))), text)
        text = QUOTED.sub(self.quoted, text)
        text = PATH.sub(self.path, text)
        text = NUMBER.sub("<n>", text)
        while KEPT.search(text):
            text = KEPT.sub(lambda m: self.kept[m[1]], text)
        return text


def code(text: str) -> str:
    """text as inline code, on one line and at most SHOWN characters."""
    text = text.replace("\n", " ⏎ ")
    text = text if len(text) <= SHOWN else text[:SHOWN - 1] + "…"
    fence = "``" if "`" in text else "`"
    return f"{fence}{text}{fence}" if fence == "`" else f"{fence} {text} {fence}"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def package() -> str:
    """experiment-lab's version and the clone's commit, as far as they are
    known: a copy of the package alone has neither."""
    toml = CLONE / "pyproject.toml"
    version = tomllib.loads(toml.read_text())["project"]["version"] if toml.exists() else "unknown"
    git = run("git", "-C", str(CLONE), "log", "-1", "--format=%h, %cs")
    return version + (f" ({git.stdout.strip()})" if git.returncode == 0 else "")


def versions(session: Session, plugin: Path | None) -> str:
    """The plugin's version, experiment-lab's version and commit, Claude
    Code's versions and the models."""
    manifest = plugin / ".claude-plugin" / "plugin.json" if plugin else None
    shown = [f"plugin {json.loads(manifest.read_text())['version']}" if manifest and manifest.exists()
             else "plugin unknown"]
    shown.append(f"experiment-lab {package()}")
    shown.append("Claude Code " + ", ".join(sorted(session.versions - {""})))
    return "; ".join(shown + sorted(model for model in session.models if model and not model.startswith("<")))


def lines(title: str, items: list[str]) -> Iterator[str]:
    if items:
        yield f"\n## {title}\n"
        yield from (f"- {item}" for item in items[:MOST])
        if len(items) > MOST:
            yield f"- and {len(items) - MOST} more"


def turns(n: int) -> str:
    return f"{n} turn" if n == 1 else f"{n} turns"


def described(friction: Friction, what: str, done: str, neutral: Neutral) -> str:
    """friction as a line of the report: the command, what it printed, its
    tries, and the call that next did the same work."""
    tries = f" ({friction.tries} tries)" if friction.tries > 1 else ""
    later = (f"; {done} {turns(friction.done.turn - friction.first.turn)} later by"
             f" {code(neutral.text(friction.done.text))}" if friction.done else f"; not {done} later")
    return f"{code(neutral.text(friction.first.text))}: {what}{tries}{later}"


def failures(calls: list[Call], neutral: Neutral) -> list[str]:
    return [described(friction, code(neutral.text(error(friction.first.output))), "done", neutral)
            for friction in frictions(calls, lambda call: call.failed)]


def searches(calls: list[Call], neutral: Neutral) -> list[str]:
    """Each q that printed no rows and that a later q, which printed some,
    searched again; a q of no rows that nothing searched again checked that
    something was not there."""
    return [described(friction, "no rows", "found", neutral)
            for friction in frictions([call for call in calls if not call.failed], is_empty) if friction.done]


def counted(texts: list[str], neutral: Neutral, least: int) -> list[str]:
    """Each text that comes least times or more, with how many times if more
    than once, the most frequent first."""
    return [code(neutral.text(text)) + (f": {count} times" if count > 1 else "")
            for text, count in Counter(texts).most_common() if count >= least]


def summary(calls: list[Call], every: int) -> str:
    """How many lab commands of every tool call, of what kind, how many failed
    and how much they printed."""
    kinds = Counter(work(call.text).split()[0] for call in calls)
    size = sum(len(call.output) for call in calls)
    return (f"{len(calls)} lab commands of {every} tool calls: {kinds['rec']} rec, {kinds['q']} q,"
            f" {len(calls) - kinds['rec'] - kinds['q']} other; {sum(call.failed for call in calls)} failed;"
            f" they printed {size / 1000:.1f} kB, about {size // 4:,} tokens.")


def report(session: Session, plugin: Path | None, neutral: Neutral) -> str:
    calls = [call for call in session.calls if is_lab(call)]
    head = [f"## Versions\n\n{versions(session, plugin)}\n", "## The lab in this session\n",
            summary(calls, len(session.calls))]
    much = [f"{code(neutral.text(call.text))}: {len(call.output) / 1000:.1f} kB" for call in calls
            if len(call.output) > LONG]
    return "\n".join([*head, *lines("Lab commands that failed", failures(calls, neutral)),
                      *lines("Searches that found nothing, run again", searches(calls, neutral)),
                      *lines("Run more than once", counted([call.text for call in calls], neutral, 2)),
                      *lines("Printed much", much),
                      *lines("Help, skill files and lab code read",
                             counted([call.text for call in session.calls if is_read(call)], neutral, 1))]) + "\n"


def new_file(folder: Path) -> Path:
    """A file of folder that does not exist, named for the UTC time, with -2,
    -3 and so on when one of that name exists."""
    time = f"{datetime.now(UTC):%Y-%m-%dT%H-%M-%SZ}"
    path, i = folder / f"{time}.md", 1
    while path.exists():
        i += 1
        path = folder / f"{time}-{i}.md"
    return path


def draft(session_id: str, plugin: Path | None, real: bool) -> None:
    session = Session([], set(), set())
    for path in transcripts(session_id):
        read(path, session)
    lab = None if real else Lab()
    text = report(session, plugin, Neutral(lab))
    if lab is not None:
        lab.close()
    folder = data_folder() / "feedback"
    folder.mkdir(parents=True, exist_ok=True)
    path = new_file(folder)
    path.write_text(text)
    print(f"{path}\n\n{text}", end="")


def send(path: Path, title: str) -> int:
    """Opens the issue with gh, labelled if gh can label it; else says how to
    open it by hand."""
    issue = ["gh", "issue", "create", "--repo", REPO, "--title", title, "--body-file", str(path)]
    if shutil.which("gh") and run("gh", "auth", "status").returncode == 0:
        sent = run(*issue, "--label", LABEL)
        if sent.returncode and "label" in sent.stderr.lower():
            sent = run(*issue)
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
