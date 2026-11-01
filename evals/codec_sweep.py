"""Adds the experiment "codec bitrate sweep" to the eval lab of make_lab.py: a
finished run for each codec (opus, aac) and bitrate (32, 64, 128), then a run
named "report" that holds their table as a file, with no params and no
metrics, then two of the six runs again, with a better quality. The latest
quality of each codec and bitrate is LATEST's.

usage: python3 codec_sweep.py    (after lab.sh audio-codec)
"""
from experiment_lab.lab import Lab

FIRST = {("opus", 32): 3.6, ("opus", 64): 4.1, ("opus", 128): 4.4, ("aac", 32): 3.1, ("aac", 64): 3.7,
         ("aac", 128): 4.2}
AGAIN = {("opus", 64): 4.3, ("aac", 32): 3.4}
LATEST = FIRST | AGAIN
EXPERIMENT = "codec bitrate sweep"


def sweep(lab: Lab, results: dict[tuple[str, int], float], day: str, note: str) -> None:
    for minute, ((codec, bitrate), quality) in enumerate(results.items()):
        run = lab.run(EXPERIMENT, f"{codec} {bitrate}", note=note, start=f"{day} 10:{minute * 9:02}",
                      params={"codec": codec, "bitrate": bitrate})
        run.log_metrics({"quality": quality}, step=0)
        run.end(at=f"{day} 10:{minute * 9 + 8:02}")


lab = Lab(project="audio-codec")
lab.experiment(EXPERIMENT, "Encodes the listening set with each codec at three bitrates and measures the"
               " listeners' mean quality score, 1 to 5.")
sweep(lab, FIRST, "2026-10-20", "")
report = lab.run(EXPERIMENT, "report", note="The latest quality of each codec and bitrate", start="2026-10-21 09:00")
report.log_text("report.md", "| codec | bitrate | quality |\n|---|---|---|\n"
                + "".join(f"| {c} | {b} | {q} |\n" for (c, b), q in FIRST.items()))
report.end(at="2026-10-21 09:01")
sweep(lab, AGAIN, "2026-10-22", "Again, with the resampler fixed")
lab.close()
