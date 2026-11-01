"""Adds the experiment "batch size sweep" to the eval lab of make_lab.py: a
finished run for each of 11 batch sizes, 1 to 1024, and 5 seeds, more runs
than q prints at once, whose batch sizes have one to four digits, so that
their order as text differs from their order as numbers. The runs are logged
in neither order. The mean accuracy of a batch size over its seeds is BASE's.

usage: python3 batch_sweep.py    (after lab.sh audio-codec)
"""
import random

from experiment_lab.lab import Lab

BASE = {1: 0.70, 2: 0.74, 4: 0.78, 8: 0.81, 16: 0.86, 32: 0.90, 64: 0.89, 128: 0.84, 256: 0.80, 512: 0.75,
        1024: 0.69}
runs = [(batch, seed) for batch in BASE for seed in range(1, 6)]
random.Random(0).shuffle(runs)
lab = Lab(project="audio-codec")
lab.experiment("batch size sweep", "Trains the voice model at 11 batch sizes, 5 seeds each, and measures its"
               " accuracy.")
for batch, seed in runs:
    with lab.run("batch size sweep", f"batch {batch} seed {seed}",
                 params={"batch": batch, "seed": seed, "optimizer": "adam"}) as run:
        run.log_metrics({"accuracy": round(BASE[batch] + (seed - 3) / 100, 2)}, step=0)
lab.close()
