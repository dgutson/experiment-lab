"""Python imports this file at the start of every process that
check_commands.py runs, since its folder is first on their PYTHONPATH. When
SQL_TRACE names a file, each statement that a sqlite3 connection runs is
appended to it as a JSON line: the process id, the connection, the statement,
and the experiment_lab calls it ran inside, innermost first, each as its frame's
id and name (lab.py:Lab.end_lost)."""
import inspect
import json
import os
import sqlite3
from pathlib import Path
from types import FrameType
from typing import Any

LOG = os.environ.get("SQL_TRACE")
CONNECT = sqlite3.connect
type Call = tuple[int, str]  # frame id, name


def calls(frame: FrameType | None) -> list[Call]:
    """The experiment_lab calls from frame outward, but a command's main and a
    module's top level, which every statement of a process runs inside."""
    found = []
    while frame is not None:
        code = frame.f_code
        if "/experiment_lab/" in code.co_filename and code.co_name not in ("main", "<module>"):
            found.append((id(frame), f"{Path(code.co_filename).name}:{code.co_qualname}"))
        frame = frame.f_back
    return found


def connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:  # noqa: ANN401 (sqlite3.connect's own)
    con: sqlite3.Connection = CONNECT(*args, **kwargs)
    if LOG:
        log = LOG

        def trace(sql: str) -> None:
            with open(log, "a", encoding="utf-8") as out:
                out.write(json.dumps({"pid": os.getpid(), "con": id(con), "sql": sql,
                                      "calls": calls(inspect.currentframe())}) + "\n")
        con.set_trace_callback(trace)
    return con


setattr(sqlite3, "connect", connect)
