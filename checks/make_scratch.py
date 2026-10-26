"""Makes a lab for the checks in FOLDER, which must not exist: the data folder
FOLDER/lab-data, and FOLDER/home with the projects' folders. It is the evals'
lab (evals/make_lab.py) with what the checks also need: a run with tags
and files (an audio file and an image), a record of every kind, evidence of
every kind, a text with a diagram and paths, and evidence items with no kind,
which rec refuses but lab.db may hold. Its paths are absolute, so the server
reads it with any home folder.

usage: uv run python checks/make_scratch.py FOLDER
then:  export EXPERIMENT_LAB_DATA=FOLDER/lab-data
"""
import math
import os
import struct
import sys
import wave
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "evals")]
import make_lab
from experiment_lab import records
from experiment_lab.lab import Lab

DECISION = """48 kHz stays: it was the most accurate rate, and `{tone}` sounds right.

```mermaid
flowchart LR
  rate[48 kHz] --> bitrate[lower the bitrate next]
```

The rates were timed with {bench}.
"""


def wav(path: Path) -> None:
    """One second of a 440 Hz tone."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 8000)))
                               for i in range(8000)))


def png(path: Path) -> None:
    """A gradient of 64 by 16 pixels."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\0" + bytes(c for x in range(64) for c in (x * 4, 120, 255 - x * 4)) for _ in range(16))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 16, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def main(where: str) -> None:
    folder = Path(where).resolve()
    (folder / "home").mkdir(parents=True)
    os.environ["HOME"] = str(folder / "home")
    os.environ["EXPERIMENT_LAB_DATA"] = str(folder / "lab-data")
    make_lab.main()
    audio = folder / "home" / "audio-codec"
    tone, plot, bench = audio / "tone.wav", audio / "plot.png", audio / "bench.py"
    wav(tone)
    png(plot)

    lab = Lab(project="audio-codec")
    with lab.run("sample rate sweep", "rate 48000, listened to", params={"rate": 48000}) as run:
        run.log_metrics({"accuracy": 0.93})
        run.set_tags({"outcome": "pass"})
        run.log_file(tone)
        run.log_file(plot)
    (finding,) = lab.db.execute("SELECT id FROM records WHERE title = '48 kHz is the most accurate rate'").fetchone()
    question = records.add(lab, "sample rate sweep", "question", "Which rate do we keep?",
                           text="The sweep measured four rates.")[0]
    records.add(lab, "sample rate sweep", "decision", "Keep 48 kHz", answers=question["id"], author="owner",
                text=DECISION.format(tone=tone, bench=bench),
                evidence=[f"run:{run.id}", f"file:{tone}", f"file:{plot}", f"record:{finding}",
                          "sql:SELECT name, metrics['accuracy'] AS accuracy FROM runs"
                          " WHERE experiment = 'sample rate sweep' ORDER BY accuracy DESC"])
    records.add(lab, "16-bit output", "protocol", "Listen on headphones",
                text="Every change to the dither is heard on headphones before it is kept.")
    records.add(lab, "noise shaping", "question", "Does noise shaping lower the audible noise?",
                text="Not measured yet.")
    records.add(lab, "noise gate", "question", "Does the gate need a hold time?", status="closed",
                text="Not measured before the gate was dropped.")
    copied = records.add(lab, "noise gate", "finding", "Pauses sound clipped",
                         text="The gate cut the ends of words.")[0]
    copied["evidence"] = [str(tone), "https://github.com/dgutson/experiment-lab", "owner reply in chat"]
    with lab.write() as db:
        records.save(db, copied)
    lab.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
