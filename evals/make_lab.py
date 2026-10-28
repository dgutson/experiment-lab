"""Makes the small lab the eval cases start from, in the data folder
($EXPERIMENT_LAB_DATA, else ~/lab-data). The folder of the project named as the
argument is the current folder, and the other's is ~/NAME; with no argument, no
project holds the current folder. audio-codec's folder holds bench.py, and
vision's its view file views.sql.

audio-codec
  sample rate sweep      four runs, one failed; its finding says what to try next
  16-bit output          dither, named only in its description and finding
    noise shaping
  noise gate             a finding against it, not yet marked as a dead end
vision
  blur detector          one run per image, read by the view blur_scores

usage: python3 make_lab.py [PROJECT]
"""
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

from experiment_lab import records
from experiment_lab.lab import Lab

DAY, MINUTE = 86_400_000, 60_000
T0 = 1_788_220_800_000  # 2026-09-01 00:00 UTC

type RunRow = tuple[str, Mapping[str, object], Mapping[str, float], str, str]  # name, params, metrics, status, note

BENCH = '''"""Times the encoder at three frame sizes."""
import time


def encode(frame):
    time.sleep(1.5)  # the encoder, at this frame size


for frame in (256, 512, 1024):
    start = time.perf_counter()
    encode(frame)
    print(f"frame={frame} seconds={time.perf_counter() - start:.2f}")
'''

VIEWS = """-- The sharpness the blur detector measured for each image, sharpest first.
CREATE OR REPLACE VIEW blur_scores AS
SELECT params['image'] AS image, metrics['sharpness'] AS sharpness
FROM runs WHERE experiment = 'blur detector'
ORDER BY sharpness DESC;
"""


def experiment(lab: Lab, name: str, description: str, parent: str, day: int) -> None:
    """Makes an experiment, made at T0 + day: of two made in the same
    millisecond, the order of the tree would put the first by name first."""
    lab.experiment(name, description, parent=parent)
    with lab.write() as db:
        db.execute("UPDATE experiments SET created = ? WHERE name = ?", (T0 + day * DAY, name))


def runs(lab: Lab, experiment: str, day: int, rows: Iterable[RunRow]) -> None:
    """Logs finished runs of two minutes each, one a day apart from T0 + day."""
    for i, (name, params, metrics, status, note) in enumerate(rows):
        start = T0 + day * DAY + i * 3 * MINUTE
        run = lab.run(experiment, name, params=params, note=note, start=start)
        run.log_metrics(metrics, at=start + MINUTE)
        with lab.write() as db:
            db.execute("UPDATE runs SET status = ?, end_time = ? WHERE id = ?", (status, start + 2 * MINUTE, run.id))


def finding(lab: Lab, experiment: str, title: str, text: str, day: int) -> None:
    records.add(lab, experiment, "finding", title, text=text, author="owner", time=T0 + day * DAY)


def main(here: str | None = None) -> None:
    audio, vision = (Path.cwd() if name == here else Path.home() / name for name in ("audio-codec", "vision"))
    for folder, name, text in ((audio, "bench.py", BENCH), (vision, "views.sql", VIEWS)):
        folder.mkdir(exist_ok=True)
        (folder / name).write_text(text)
    lab = Lab(project="audio-codec")
    lab.project("audio-codec", "A lossy audio codec for voice recordings.", folder=audio)
    experiment(lab, "sample rate sweep", "Encodes the test set at four sample rates and measures how accurately "
               "the decoded audio matches the original.", "audio-codec", 0)
    runs(lab, "sample rate sweep", 0, [
        (f"rate {rate}", {"rate": rate}, {"accuracy": acc} if acc else {}, status, note)
        for rate, acc, status, note in [(16000, 0.71, "FINISHED", ""), (44100, 0.88, "FINISHED", ""),
                                        (48000, 0.93, "FINISHED", ""), (96000, None, "FAILED", "ran out of memory")]])
    finding(lab, "sample rate sweep", "48 kHz is the most accurate rate",
            "48 kHz reached an accuracy of 0.93, against 0.88 at 44.1 kHz; the 96 kHz run ran out of memory. "
            "Next: keep 48 kHz and lower the bitrate, to find where accuracy starts to drop.", 1)
    experiment(lab, "16-bit output", "Adds triangular (TPDF) dither before quantizing to 16 bits and measures "
               "distortion and the noise floor.", "audio-codec", 3)
    runs(lab, "16-bit output", 3, [(f"dither {d}", {"dither": d}, {"distortion_db": x, "noise_floor_db": n},
                                    "FINISHED", "") for d, x, n in [("none", -84, -98), ("tpdf", -90, -95)]])
    finding(lab, "16-bit output", "Dither lowers distortion",
            "TPDF dither lowered distortion by 6 dB and raised the noise floor by 3 dB. Kept on.", 4)
    experiment(lab, "noise shaping", "Shapes the dither noise towards high frequencies, where it is heard less.",
               "16-bit output", 5)
    experiment(lab, "noise gate", "Silences the input below -50 dB before encoding, to save bits in pauses.",
               "audio-codec", 6)
    runs(lab, "noise gate", 6, [("gate -50 dB", {"threshold_db": -50}, {"bits_saved": 0.12}, "FINISHED", "")])
    finding(lab, "noise gate", "The gate saves 12% of the bits", "The gate at -50 dB saved 12% of the bits.", 7)
    lab.close()

    lab = Lab(project="vision")
    lab.project("vision", "Image quality checks for the photo archive.", folder=vision, views=[vision / "views.sql"])
    experiment(lab, "blur detector", "Scores how sharp each archive photo is.", "vision", 2)
    runs(lab, "blur detector", 2, [(name, {"image": name}, {"sharpness": s}, "FINISHED", "")
                                   for name, s in [("harbour.jpg", 0.41), ("market.jpg", 0.87), ("tower.jpg", 0.63)]])
    lab.close()


if __name__ == "__main__":
    main(*sys.argv[1:])
