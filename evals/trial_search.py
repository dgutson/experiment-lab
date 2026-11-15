"""Adds to the eval lab of make_lab.py, after trial_transcripts.py, agent trials
whose transcripts are logged with their runs, as trial_transcripts.py makes
them: audio-codec's "release trials" v1 to v3, of a prompt that asks an agent
to tag and push a release, and vision's "label trials" v1. In audio-codec only
release trials' v2 had a git push rejected; vision's v1 had one too.

usage: python3 trial_search.py    (after lab.sh audio-codec and trial_transcripts.py)
"""
import json
import tempfile
import uuid
from pathlib import Path

from trial_transcripts import transcript

from experiment_lab.lab import Lab

REJECTED = ("Exit code 1\nTo github.com:acme/{repo}.git\n ! [rejected]        main -> main (fetch first)\nerror: failed"
            " to push some refs to 'github.com:acme/{repo}.git'\nhint: Updates were rejected because the remote"
            " contains work that you do not have locally.")
PUSHED = "To github.com:acme/{repo}.git\n   4c1d2e9..8f0a3b1  main -> main\n * [new tag]         v1.3 -> v1.3"
RELEASE = "Tag the release v1.3 and push it."
TRIALS = {  # (project, experiment, its description, prompt): {version: API calls, as trial_transcripts.TRIALS}
    ("audio-codec", "release trials", "Agent trials of the prompt that asks an agent to tag and push a release.",
     RELEASE): {
        "v1": [(True, [("git tag -a v1.3 -m 'v1.3'", "", False)], 388),
               (False, [("git push --follow-tags origin main", PUSHED.format(repo="codec"), False)], 141),
               (True, "Tagged v1.3 and pushed it with main.", 97)],
        "v2": [(True, [("git status --short", "", False), ("git tag -a v1.3 -m 'Release v1.3'", "", False)], 512),
               (False, [("git push origin main --tags", REJECTED.format(repo="codec"), True)], 204),
               (True, [("git pull --rebase origin main", "Successfully rebased and updated refs/heads/main.",
                        False)], 433),
               (False, [("git push origin main --tags", PUSHED.format(repo="codec"), False)], 120),
               (True, "Tagged v1.3, rebased on the remote's main and pushed both.", 118)],
        "v3": [(True, [("python3 -m pytest -q", "Exit code 1\n1 failed, 41 passed in 3.2s", True)], 610),
               (False, [("python3 -m pytest -q --lf", "1 passed in 0.4s", False)], 233),
               (True, [("git tag -a v1.3 -m 'v1.3' && git push --follow-tags origin main",
                        PUSHED.format(repo="codec"), False)], 301),
               (False, "The tests pass; v1.3 is tagged and pushed.", 88)],
    },
    ("vision", "label trials", "Agent trials of the prompt that asks an agent to label the archive's new photos and"
     " push the labels.", "Label the new photos in inbox/ and push labels.csv."): {
        "v1": [(True, [("python3 label.py inbox/ >> labels.csv", "", False)], 455),
               (False, [("git commit -am 'Label 32 photos' && git push origin main", REJECTED.format(repo="vision"),
                         True)], 190),
               (True, [("git pull --rebase origin main && git push origin main",
                        PUSHED.format(repo="vision").replace("\n * [new tag]         v1.3 -> v1.3", ""), False)],
                402),
               (False, "Labeled 32 photos and pushed labels.csv.", 76)],
    },
}

with tempfile.TemporaryDirectory() as folder:
    for (project, experiment, description, prompt), versions in TRIALS.items():
        lab = Lab(project=project)
        lab.experiment(experiment, description)
        for n, (version, calls) in enumerate(versions.items()):
            path = Path(folder) / f"{project}-{version}.jsonl"
            path.write_text("".join(json.dumps(line) + "\n" for line in transcript(calls, str(uuid.uuid4()), prompt)))
            with lab.run(experiment, f"trial {version}", params={"prompt": version},
                         start=f"2026-11-11 1{n}:00") as run:
                run.log_metrics({"score": 1.0})
                run.log_file(path, "transcript.jsonl")
        lab.close()
