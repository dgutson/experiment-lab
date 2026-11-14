"""Adds to the eval lab of make_lab.py the experiment "listening panel", six
FINISHED runs, each with the result.json it logged, and to the current folder
rescore.py, which scores a run again from that file, and rescore_ids.txt, the
first 8 characters of the ids of three of the runs, as a report shows them.

usage: python3 rescore_runs.py    (after lab.sh audio-codec)
"""
from pathlib import Path

from experiment_lab.lab import Lab

lab = Lab(project="audio-codec")
lab.experiment("listening panel", "Listeners score clips encoded at each bitrate; mos is their mean score.")
ids = []
for n, (bitrate, mos) in enumerate(((64, 3.1), (96, 3.6), (128, 4.0), (160, 4.2), (192, 4.3), (256, 4.4))):
    with lab.run("listening panel", f"panel {bitrate} kbps", params={"bitrate_kbps": bitrate},
                 start=f"2026-10-30 1{n}:00") as run:
        run.log_json("result.json", {"mos": mos, "listeners": 12})
        run.log_metrics({"mos": mos})
        ids.append(run.id)
lab.close()
Path("rescore_ids.txt").write_text("".join(f"{rid[:8]}\n" for rid in ids[1:4]))
Path("rescore.py").write_text('''"""Scores a run of the listening panel again with the new rubric, from the
result.json it logged, and logs the score as its metric mos_v2.

usage: PYTHONPATH=~/experiment-lab python3 rescore.py RUN_ID
"""
import sys

from experiment_lab.lab import Lab

lab = Lab()
rid = sys.argv[1]
result = lab.json(rid, "result.json")
score = round(result["mos"] * 0.9 + 0.3, 2)
lab.run_by_id(rid).log_metrics({"mos_v2": score})
print(f"{rid}: mos_v2 {score}")
''')
