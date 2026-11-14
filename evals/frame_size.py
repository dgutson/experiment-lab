"""Adds the experiment "frame size" to the eval lab of make_lab.py, and
encoder.py to the current folder, which encodes 512-sample frames: two runs, at
1024 and 512 samples, and a finding that encoder.py encodes 512-sample frames
since, kept for the time they save.

usage: python3 frame_size.py    (after lab.sh audio-codec)
"""
from pathlib import Path

from experiment_lab import records
from experiment_lab.lab import Lab, ms

EXPERIMENT = "frame size"

Path("encoder.py").write_text('''"""The codec's encoder."""
FRAME = 512  # samples per frame


def frames(samples):
    return [samples[i:i + FRAME] for i in range(0, len(samples), FRAME)]
''')
lab = Lab(project="audio-codec")
lab.experiment(EXPERIMENT, "Encodes the test set with frames of 1024 and of 512 samples and times bench.py, to"
               " see whether shorter frames encode faster.")
runs = []
for i, (frame, seconds) in enumerate(((1024, 1.50), (512, 1.05))):
    run = lab.run(EXPERIMENT, f"frame {frame}", params={"frame": frame}, start=ms(f"2026-10-20 10:{i * 5:02d}"))
    run.log_metrics({"encode_seconds": seconds}, step=0)
    run.end(at=ms(f"2026-10-20 10:{i * 5 + 3:02d}"))
    runs.append(f"run:{run.id}")
records.add(lab, EXPERIMENT, "finding", "512-sample frames in encoder.py cut encoding time by 30%", author="agent",
            evidence=runs, time=ms("2026-10-20 10:30"), text="encoder.py encodes 512-sample frames instead of 1024:"
            " bench.py took 1.05 s against 1.50 s. Kept.")
lab.close()
