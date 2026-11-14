"""Adds the experiment "thumbnail blur" to the eval lab of make_lab.py, filed
in audio-codec although it is vision's work: a run that logged a file, and a
finding that cites the run.

usage: python3 thumbnail_blur.py    (after lab.sh audio-codec)
"""
from experiment_lab import records
from experiment_lab.lab import Lab, ms

EXPERIMENT = "thumbnail blur"

lab = Lab(project="audio-codec")
lab.experiment(EXPERIMENT, "Scores the sharpness of the archive photos' 200-pixel thumbnails, to see whether the"
               " blur detector can work on thumbnails instead of the full images.")
run = lab.run(EXPERIMENT, "thumbnails 200 px", params={"size": 200}, start=ms("2026-10-28 09:00"))
run.log_metrics({"mean_sharpness": 0.58}, step=0)
run.log_json("scores.json", {"harbour.jpg": 0.37, "market.jpg": 0.81, "tower.jpg": 0.56})
run.end(at=ms("2026-10-28 09:20"))
records.add(lab, EXPERIMENT, "finding", "Thumbnails rank the photos as the full images do", author="agent",
            evidence=[f"run:{run.id}"], time=ms("2026-10-28 09:40"), text="The 200-pixel thumbnails rank harbour,"
            " tower and market in the order of the full images, each about 0.05 lower.")
lab.close()
