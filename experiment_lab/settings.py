"""Where the lab keeps its files: the data folder and its database lab.db."""
import os
from pathlib import Path


def data_folder() -> Path:
    """$EXPERIMENT_LAB_DATA, else ~/lab-data."""
    return Path(os.environ.get("EXPERIMENT_LAB_DATA", "~/lab-data")).expanduser()


def database(data: Path) -> Path:
    return data / "lab.db"
