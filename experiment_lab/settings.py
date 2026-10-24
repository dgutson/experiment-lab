"""Where the lab keeps its files: the data folder, its database lab.db, and the
settings in its lab.toml."""
import os
import tomllib
from pathlib import Path


def data_folder():
    """$EXPERIMENT_LAB_DATA, else ~/lab-data."""
    return Path(os.environ.get("EXPERIMENT_LAB_DATA", "~/lab-data")).expanduser()


def database(data):
    return data / "lab.db"


def settings(data):
    """The data folder's lab.toml: the folder /lab/file shows (None without
    one) and the view files run after lab.sql."""
    path = data / "lab.toml"
    found = tomllib.loads(path.read_text()) if path.exists() else {}
    unknown = set(found) - {"files", "views"}
    if unknown:
        raise ValueError(f"{path}: unknown settings {', '.join(sorted(unknown))}")

    def resolve(p):
        return (data / Path(p).expanduser()).resolve()

    files = resolve(found["files"]) if "files" in found else None
    return files, [resolve(p) for p in found.get("views", [])]
