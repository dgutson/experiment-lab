"""Adds to the eval lab of make_lab.py two experiments whose jobs ran
elsewhere, each with two finished runs and others left RUNNING: "night batch",
with three (night 3, 4 and 5), and "noon batch", with one (noon 3).

usage: python3 running_batches.py    (after lab.sh audio-codec)
"""
from experiment_lab.lab import Lab

lab = Lab(project="audio-codec")
for experiment, day, running in (("night batch", "2026-10-27", 3), ("noon batch", "2026-10-28", 1)):
    lab.experiment(experiment, "Encodes the archive with the batch encoder on the cluster, one job per run.")
    for n in range(1, 3 + running):
        run = lab.run(experiment, f"{experiment.split()[0]} {n}", start=f"{day} 0{n}:00",
                      params={"shard": n})
        if n < 3:
            run.log_metrics({"files": 120 + n}, step=0)
            run.end(at=f"{day} 0{n}:40")
lab.close()
