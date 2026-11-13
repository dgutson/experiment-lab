"""Adds the experiment "listening test" to the eval lab of make_lab.py: three
runs, panel 1 to panel 3, each of which logged clips.json, each clip's kind and
mean score. Panel 1 has 1 clip scored below 3.5, panel 2 has 3, panel 3 has 2.

usage: python3 panels.py    (after lab.sh audio-codec)
"""
from experiment_lab.lab import Lab

EXPERIMENT = "listening test"
PANELS = [[4.2, 4.0, 3.1, 4.4, 3.9, 3.8], [3.2, 4.0, 3.1, 3.4, 3.9, 4.1], [4.2, 3.0, 3.6, 3.3, 3.9, 4.5]]
KINDS = ["speech", "speech", "music", "music", "music", "speech"]

lab = Lab(project="audio-codec")
lab.experiment(EXPERIMENT, "Panels score clips encoded at 64 kbit/s, 1 to 5, to compare the codec's quality on"
               " speech and on music.")
for number, scores in enumerate(PANELS, 1):
    run = lab.run(EXPERIMENT, f"panel {number}", start=f"2026-10-2{number} 15:00",
                  note="Six clips, each scored by five listeners; clips.json holds each clip's kind and mean score.")
    run.log_json("clips.json", [{"clip": f"c{i}", "kind": k, "score": s}
                                for i, (k, s) in enumerate(zip(KINDS, scores, strict=True), 1)])
    run.log_metrics({"mean_score": sum(scores) / len(scores)}, step=0)
    run.end(at=f"2026-10-2{number} 16:10")
lab.close()
