"""Runs one pinned strategy in a fresh interpreter (G1.5; security review N1, N4, N9).

The harness starts this module as ``python -I -B -X pycache_prefix=<an empty folder>``
with an empty environment, after the run's open entry is pushed, in a folder holding
only the files committed at HEAD that the run needs: this package, the hypothesis's
strategy paths and the registered manifest. So the code that computes a result is the
committed code, executed from its source (never a cached ``.pyc``), in a process no
caller has touched, and nothing it does survives the run.

Before the strategy module is executed, an audit hook is installed: the process may
read the interpreter's own library, the committed code it was given, and the dataset
files the registered manifest pins, read-only, and nothing else; it may open no socket
and start no process. The strategy's module-level code runs under the hook too. The
hook deters accidents, not intent: committed strategy code that reaches the hook's
state through frames or the garbage collector can disable it (security review round 3,
R1), so it is not a sandbox, and the control against a deliberate escape is that the
code is committed, recorded in the ledger and reviewed (threat T13). The result goes
back over standard output as one JSON line, the last.

For the holdout run alone (G1.6), the payload also names the committed holdout
manifest and the unseal entry: the loader is then given the holdout manifest, its
files join the readable set, and the strategy runs inside the unsealed run, so its
loads may reach 2026. The harness names the unseal only after it has pushed the run's
open entry and found the unseal entry on the remote's ledger.
"""

from __future__ import annotations

import json
import os
import sys
import types
from collections.abc import Mapping
from contextlib import nullcontext
from pathlib import Path
from types import MappingProxyType

from .periods import HOLDOUT_START_MS

_BLOCKED_EVENTS = (
    "os.", "socket.", "subprocess.", "shutil.", "urllib.", "http.", "ftplib.", "smtplib.",
    "poplib.", "imaplib.", "nntplib.", "telnetlib.", "webbrowser.", "ctypes.", "sqlite3.",
    "glob.", "pty.", "fcntl.", "mmap.", "syslog.",
)
RESULT_PREFIX = "norse-strategy-result "


class StrategyIOBlocked(PermissionError):
    """A running strategy tried to reach a file, a socket or a process (T13)."""


def _normal(path) -> str | None:
    if isinstance(path, int):
        return None
    text = os.fsdecode(path)
    return os.path.normpath(text) if os.path.isabs(text) else None


def install_tripwire(roots: list[str], files: set[str]) -> None:
    """Allow read-only opens under ``roots`` and of exactly ``files``; refuse every
    other file, socket and process event for the rest of this process."""
    prefixes = tuple(os.path.normpath(r).rstrip(os.sep) + os.sep for r in roots)

    def allowed(path) -> bool:
        text = _normal(path)
        return text is not None and (text in files or text.startswith(prefixes))

    def hook(event: str, args: tuple) -> None:
        if event == "open":
            path, mode, flags = args
            read_only = mode in ("r", "rb") or (
                mode is None and isinstance(flags, int) and flags & os.O_ACCMODE == os.O_RDONLY
            )
            if read_only and allowed(path):
                return
            raise StrategyIOBlocked(f"a strategy run may not open files ({path!r})")
        if event.startswith(_BLOCKED_EVENTS):
            if event in ("os.listdir", "os.scandir") and args and allowed(args[0]):
                return
            raise StrategyIOBlocked(f"a strategy run may not use {event}")

    sys.addaudithook(hook)


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(v) for v in value)
    return value


class DataView:
    """What a strategy receives: rows through the registered loader, nothing else. It
    notes whether any row it hands over is from 2026-01-01 on, which the holdout
    attempt's close records (security review H2)."""

    __slots__ = ("_load", "_served")

    def __init__(self, loader, served: list | None = None) -> None:
        self._load = loader.load if loader is not None else None
        self._served = served if served is not None else [False]

    def load(self, series: str, start, end, accept_recorded_gaps: bool = False):
        if self._load is None:
            raise LookupError("this run was given no dataset store")
        loaded = self._load(series, start, end, accept_recorded_gaps)
        if any(row[0] >= HOLDOUT_START_MS for row in loaded.rows):
            self._served[0] = True
        return loaded


def run(payload: dict, served: list | None = None) -> list:
    from .formats import DatasetSpec
    from .loader import DatasetLoader, inside_ledger_entry
    from .manifest import Manifest
    from .periods import inside_unsealed_run
    from .store import ArchiveStore

    code_root = payload["code_root"]
    files: set[str] = set()
    loader = None
    unseal = payload.get("unseal")
    if payload.get("store_root"):
        manifest = Manifest.load(Path(code_root) / payload["manifest"])
        holdout = None
        if unseal and payload.get("holdout_manifest"):
            holdout = Manifest.load(Path(code_root) / payload["holdout_manifest"])
        store = ArchiveStore(Path(payload["store_root"]))
        for pinned in (manifest, holdout):
            for name, entry in (pinned.datasets.items() if pinned is not None else ()):
                spec = DatasetSpec.from_name(name)
                for month, *_ in entry["files"]:
                    path = store.path(spec.key(month))
                    files.add(os.path.normpath(str(path)))
                    files.add(os.path.normpath(str(path) + ".CHECKSUM"))
        loader = DatasetLoader(manifest, store, holdout=holdout)
    module_file = os.path.join(code_root, payload["module_path"])
    source = Path(module_file).read_bytes()
    roots = [code_root, payload["pycache"], sys.prefix, sys.base_prefix, sys.exec_prefix,
             sys.base_exec_prefix]
    roots += [os.path.realpath(r) for r in roots]
    install_tripwire(roots, files)
    module = types.ModuleType(payload["module_name"])
    module.__file__ = module_file
    module.__package__ = payload["module_name"].rpartition(".")[0]
    sys.modules[payload["module_name"]] = module
    sealed = inside_unsealed_run(unseal) if unseal else nullcontext()
    with inside_ledger_entry(payload["entry"]), sealed:
        exec(compile(source, module_file, "exec"), module.__dict__)
        function = module.__dict__[payload["function"]]
        series = function(_freeze(payload["configuration"]), DataView(loader, served))
        return [[str(day), float(value)] for day, value in series]


def main() -> None:
    payload = json.loads(sys.stdin.read())
    out = sys.stdout
    sys.stdout = sys.stderr  # anything the strategy prints stays out of the result
    served = [False]
    try:
        result = {"ok": True, "returns": run(payload, served)}
    except BaseException as exc:  # reported to the harness, which ledgers the failure
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    result["holdout_rows_served"] = served[0]
    out.write("\n" + RESULT_PREFIX + json.dumps(result) + "\n")
    out.flush()
