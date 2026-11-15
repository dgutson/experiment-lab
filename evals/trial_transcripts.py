"""Adds to the eval lab of make_lab.py the experiment "changelog trials": two
agent trials, v1 and v2 of a prompt that asks an agent to write the project's
changelog, each a FINISHED run with its score and the agent's transcript,
logged as transcript.jsonl in Claude Code's session format: an API call's
thinking and each of its tool calls on a line of its own, with the call's
usage repeated on each, and Bash commands that failed marked is_error.

usage: python3 trial_transcripts.py    (after lab.sh audio-codec)
"""
import json
import tempfile
import uuid
from pathlib import Path

from experiment_lab.lab import Lab

BASE = 15400  # the context of the first call: system prompt, tools and the task
# Per trial: each API call's (thinking?, [(command, result, failed)], or a final text), its output tokens
TRIALS = {
    "v1": [
        (True, [("git log --oneline v1.2..HEAD | head -50", "a41f2c0 Add opus encoder\n9be1d37 Fix gapless", False)],
         412),
        (False, [("python3 tools/make_changelog.py --since v1.2",
                  "Exit code 1\nModuleNotFoundError: No module named 'yaml'", True),
                 ("ls tools", "make_changelog.py\nrelease.sh", False)], 236),
        (True, [("pip install --user pyyaml", "Successfully installed PyYAML-6.0.2", False)], 655),
        (False, [("python3 tools/make_changelog.py --since v1.2 > CHANGELOG.md",
                  "", False)], 128),
        (True, "CHANGELOG.md now lists the 14 changes since v1.2, by section.", 301),
    ],
    "v2": [
        (True, [("git describe --tags --abbrev=0", "v1.2", False),
                ("python3 tools/make_changelog.py --from v1.2",
                 "Exit code 2\nusage: make_changelog.py [-h] [--since TAG]\nmake_changelog.py: error: unrecognized"
                 " arguments: --from v1.2", True)], 948),
        (False, [("npm run changelog", "Exit code 1\nnpm error Missing script: \"changelog\"", True)], 187),
        (True, [("python3 tools/make_changelog.py --since v1.2",
                 "Exit code 1\nModuleNotFoundError: No module named 'yaml'", True)], 522),
        (False, [("pip install --user pyyaml", "Successfully installed PyYAML-6.0.2", False),
                 ("python3 tools/make_changelog.py --since v1.2 > CHANGELOG.md", "", False)], 301),
        (True, [("git diff --stat", " CHANGELOG.md | 41 ++++++++++++++++++++++++++++++++++++", False)], 266),
        (False, "CHANGELOG.md now lists the 14 changes since v1.2.", 95),
    ],
}


def transcript(calls: list, session: str) -> list[dict]:
    """The lines of a session transcript of the API calls given."""
    lines: list[dict] = [{"type": "permission-mode", "permissionMode": "default", "sessionId": session}]
    parent = None
    clock = 0

    def add(kind: str, message: dict, **extra: object) -> None:
        nonlocal parent, clock
        clock += 7
        entry = {"parentUuid": parent, "isSidechain": False, "userType": "external", "cwd": "/home/u/codec",
                 "sessionId": session, "version": "2.1.322", "gitBranch": "main", "type": kind, "message": message,
                 "uuid": str(uuid.uuid4()), "timestamp": f"2026-11-10T14:{clock // 60:02d}:{clock % 60:02d}.000Z",
                 **extra}
        parent = entry["uuid"]
        lines.append(entry)

    add("user", {"role": "user", "content": "Write CHANGELOG.md for the changes since v1.2."})
    context = BASE
    for thinking, work, output in calls:
        usage = {"input_tokens": 3, "cache_creation_input_tokens": 900, "cache_read_input_tokens": context - 903,
                 "output_tokens": output, "service_tier": "standard"}
        message_id = f"msg_{uuid.uuid4().hex[:24]}"
        stop = "end_turn" if isinstance(work, str) else "tool_use"
        blocks: list[dict] = [{"type": "thinking", "thinking": "", "signature": "x"}] if thinking else []
        blocks += ([{"type": "text", "text": work}] if isinstance(work, str) else
                   [{"type": "tool_use", "id": f"toolu_{uuid.uuid4().hex[:24]}", "name": "Bash",
                     "input": {"command": command, "description": "Run a command"}} for command, _, _ in work])
        for block in blocks:
            add("assistant", {"id": message_id, "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
                              "content": [block], "stop_reason": stop, "stop_sequence": None, "usage": usage},
                requestId=f"req_{message_id[4:]}")
        if isinstance(work, str):
            break
        for block, (_, result, failed) in zip([b for b in blocks if b["type"] == "tool_use"], work):
            add("user", {"role": "user", "content": [{"tool_use_id": block["id"], "type": "tool_result",
                                                      "content": result, "is_error": failed}]},
                toolUseResult={"stdout": "" if failed else result, "stderr": result if failed else "",
                               "interrupted": False})
            lines.append({"type": "attachment", "attachment": {"type": "todo_reminder"}, "sessionId": session})
        context += 900 + output + sum(len(r) // 3 for _, r, _ in work)
    lines.append({"type": "last-prompt", "lastPrompt": "Write CHANGELOG.md", "sessionId": session})
    return lines


lab = Lab(project="audio-codec")
lab.experiment("changelog trials", "Agent trials of the prompt that asks an agent to write CHANGELOG.md, one run per"
               " version of the prompt; each run logs the agent's transcript and its score.")
with tempfile.TemporaryDirectory() as folder:
    for n, (version, calls) in enumerate(TRIALS.items()):
        path = Path(folder) / f"{version}.jsonl"
        path.write_text("".join(json.dumps(line) + "\n" for line in transcript(calls, str(uuid.uuid4()))))
        with lab.run("changelog trials", f"trial {version}", params={"prompt": version},
                     start=f"2026-11-10 1{4 + n}:00") as run:
            run.log_metrics({"score": (0.8, 0.9)[n]})
            run.log_file(path, "transcript.jsonl")
lab.close()
