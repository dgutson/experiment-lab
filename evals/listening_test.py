"""Adds the experiment "listening test" to the eval lab of make_lab.py: a run,
panel 1, that logged clips.json, each clip's kind and mean score, with clip c4's
kind wrong (music; it is speech); a finding on the run; and a later finding that
corrects c4's kind, the logged file kept as it was. By the file, speech scores
4.0 and music 3.6; with the correction, speech 4.1 and music 3.2. With
--no-correction, the later finding is left out and c4's kind in the file is
right.

usage: python3 listening_test.py [--no-correction]    (after lab.sh audio-codec)
"""
import sys

from experiment_lab import records
from experiment_lab.lab import Lab
from experiment_lab.lab import ms

EXPERIMENT = "listening test"
CLIPS = [("c1", "speech", 4.2), ("c2", "speech", 4.0), ("c3", "music", 3.1), ("c4", "music", 4.4),
         ("c5", "music", 3.3), ("c6", "speech", 3.8)]

lab = Lab(project="audio-codec")
lab.experiment(EXPERIMENT, "A panel scores clips encoded at 64 kbit/s, 1 to 5, to compare the codec's quality on"
               " speech and on music.")
run = lab.run(EXPERIMENT, "panel 1", start="2026-10-23 15:00",
              note="Six clips, each scored by five listeners; clips.json holds each clip's kind and mean score.")
run.log_json("clips.json", [{"clip": c, "kind": k, "score": s} for c, k, s in CLIPS])
run.log_metrics({"mean_score": sum(s for *_, s in CLIPS) / len(CLIPS)}, step=0)
run.end(at="2026-10-23 16:10")
records.add(lab, EXPERIMENT, "finding", "Speech scores above music", author="agent", evidence=[f"run:{run.id}"],
            time=ms("2026-10-23 16:30"), text="The panel scored the speech clips above the music clips: the"
            " codec's voice tuning costs music quality.")
if sys.argv[1:] != ["--no-correction"]:
    records.add(lab, EXPERIMENT, "finding", "Clip c4 is speech, not music", author="agent",
                evidence=[f"run:{run.id}"],
                time=ms("2026-10-25 11:00"), text="Clip c4 is a news reading, so speech; clips.json gives its kind"
                " as music. The logged file stays as it was.")
lab.close()
