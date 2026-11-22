"""Runs one pinned strategy in a fresh interpreter (G1.5; security review N1, N4, N9).

The harness starts this module as ``python -s -P -B -X pycache_prefix=<an empty folder>``
with an environment holding only ``PYTHONHASHSEED=0``, so that string hashing, and with
it the order of a set of names, is the same on every run (G1.6 security review round 2,
N3), after the run's open entry is pushed, in a folder holding
only the files committed at HEAD that the run needs: this package, the hypothesis's
strategy paths and the registered manifest. So the code that computes a result is the
committed code, executed from its source (never a cached ``.pyc``), in a process no
caller has touched, and nothing it does survives the run.

Before the strategy module is executed, an audit hook is installed: the process may
read the interpreter's own library, the committed code it was given, and the dataset
files the registered manifest pins, read-only, and nothing else; it may open no socket
and start no process. The strategy's module-level code runs under the hook too. The
hook refuses frames (``gi_frame``, ``cr_frame``, ``ag_frame``, ``tb_frame`` and the
``sys`` frame and trace calls) and the garbage collector too, since both reached the
engine's live run, the runner's payload and the hook's own state (security review round
3, R1; PR 61, round 4, M1; round 5, B-H1), and the source check refuses those names
before anything runs. Once the engine is sealed it also refuses compiling any text that
is not a file under the readable roots, which is how text the source check never parsed
(``dataclass`` field names, string annotations) would run. It is still not a sandbox:
Python cannot be sealed from inside itself, and the control against a deliberate
escape is that the code is committed, recorded in the ledger and reviewed (threat
T13). Also before the strategy module runs, the engine it may import is sealed
(``seal_engine``): its modules, classes and tables cannot be changed for the run. The
result goes back over standard output as one JSON line, the last.

For the holdout run alone (G1.6), the payload also names the committed holdout
manifest and the unseal entry: the loader is then given the holdout manifest, its
files join the readable set, and the strategy runs inside the unsealed run, so its
loads may reach 2026. The harness names the unseal only after it has pushed the run's
open entry and found the unseal entry on the remote's ledger. For the rehearsal
(``seal rehearse``), the payload names a development month instead: the development
manifest is split there, its later months stand in for the holdout, and the same
loading path runs across that boundary on development data only.
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

_BLOCKED_EVENTS = (
    "os.", "socket.", "subprocess.", "shutil.", "urllib.", "http.", "ftplib.", "smtplib.",
    "poplib.", "imaplib.", "nntplib.", "telnetlib.", "webbrowser.", "ctypes.", "sqlite3.",
    "glob.", "pty.", "fcntl.", "mmap.", "syslog.",
)
RESULT_PREFIX = "norse-strategy-result "
# The four ways in to a frame; each raises ``object.__getattr__`` before it returns
# (security review of PR 61, round 4, M1). From a frame, ``f_back``, ``f_locals``,
# ``f_globals`` and ``f_builtins`` raise nothing, so the way in is what is refused.
FRAME_ENTRIES = frozenset({"gi_frame", "cr_frame", "ag_frame", "tb_frame"})
# The ways to a frame through ``sys``, and the trace and profile hooks, which are handed
# frames (security review round 5, B-H1: from compiled text, ``sys._getframe`` reached the
# runner's frame and the tripwire's own file set again).
FRAME_EVENTS = frozenset({"sys._getframe", "sys._getframemodulename", "sys._current_frames",
                          "sys.settrace", "sys.setprofile"})


class StrategyIOBlocked(PermissionError):
    """A running strategy tried to reach a file, a socket or a process (T13)."""


def _normal(path) -> str | None:
    if isinstance(path, int):
        return None
    text = os.fsdecode(path)
    return os.path.normpath(text) if os.path.isabs(text) else None


def install_tripwire(roots: list[str], files: set[str]):
    """Allow read-only opens under ``roots`` and of exactly ``files``; refuse every
    other file, socket and process event, every way to a frame and every use of the
    garbage collector, for the rest of this process. Frames and ``gc`` reached the
    engine's live run, the runner's payload and this hook's own file set (security
    review of PR 61, round 4, M1; round 5, B-H1).

    Returns the switch that, once thrown, also refuses compiling any text that is not a
    file under ``roots``: imports and the strategy module itself are files there, while
    ``dataclass``, ``namedtuple``, ``eval`` and string annotations compile ``<string>``,
    text the source check never parsed (round 5, B-H1). The runner throws it after the
    engine is sealed, since the engine's own dataclasses compile text as they are
    imported."""
    prefixes = tuple(os.path.normpath(r).rstrip(os.sep) + os.sep for r in roots)
    compiling_text_refused = [False]

    def allowed(path) -> bool:
        text = _normal(path)
        return text is not None and (text in files or text.startswith(prefixes))

    def hook(event: str, args: tuple) -> None:
        if event == "object.__getattr__" and args[1] in FRAME_ENTRIES:
            raise StrategyIOBlocked(f"a strategy run may not reach a frame ({args[1]})")
        if event in FRAME_EVENTS:
            raise StrategyIOBlocked(f"a strategy run may not reach a frame ({event})")
        if event == "compile" and compiling_text_refused[0] and not (
            isinstance(args[1], str | bytes) and allowed(args[1])
        ):
            raise StrategyIOBlocked(f"a strategy run may not compile text ({args[1]!r})")
        if event.startswith("gc."):
            raise StrategyIOBlocked(f"a strategy run may not use {event}")
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

    def refuse_compiling_text() -> None:
        compiling_text_refused[0] = True

    return refuse_compiling_text


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(v) for v in value)
    return value


class FrozenModule(types.ModuleType):
    """A module whose attributes cannot be set or deleted, whatever name reaches it, and
    whose namespace cannot be reached through its ``__dict__`` attribute: helpers in the
    allowlisted standard library that take an attribute name as a string
    (``functools.update_wrapper``, ``enum.global_enum``) would otherwise write into it
    with no dunder in the strategy's source. Python itself reads a module's namespace
    directly, never through this attribute."""

    def __getattribute__(self, name: str):
        if name == "__dict__":
            module = super().__getattribute__("__name__")
            raise AttributeError(f"module {module} is sealed for this run: its namespace "
                                 "cannot be reached")
        return super().__getattribute__(name)

    def __setattr__(self, name: str, value) -> None:
        raise AttributeError(f"module {self.__name__} is sealed for this run: {name} cannot be set")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            f"module {self.__name__} is sealed for this run: {name} cannot be deleted"
        )


ENGINE = "norse_research.sim"
ENGINE_FORMATS = "norse_research.formats"


def seal_engine() -> list[str]:
    """Make the code a strategy imports immutable before the strategy runs (security
    review of PR 61, round 2). The source check reads names, not values, so it cannot
    see an engine module, class or table changed through an alias, a container, a
    parameter or a mutating method, and no audit event fires on such a change; this
    stops it at run time instead. Every module of the engine and ``formats`` becomes a
    ``FrozenModule``, every class in them refuses class-attribute changes
    (``formats.EngineClass``), and their module-level tables are read-only in the
    source. Undoing any of it needs a dunder attribute, a forbidden builtin, a frame or
    one of the standard library helpers that write by string name, which the source
    check refuses and, for frames and the garbage collector, the audit hook refuses
    too; ``formats.build`` writes only to the engine's own frozen objects while they
    are built. Returns the names of the sealed modules."""
    from . import formats
    from . import sim as _engine  # noqa: F401  imports every engine module before sealing

    formats.seal_engine_classes()
    sealed = sorted(n for n in sys.modules
                    if n in (ENGINE, ENGINE_FORMATS) or n.startswith(ENGINE + "."))
    for name in sealed:
        sys.modules[name].__class__ = FrozenModule
    return sealed


class DataView:
    """What a strategy receives: rows through the registered loader, the series the
    manifests the loader holds pin, and each series' archived coverage from them
    (G1.9), nothing else. All three are the loader's, so a fix to any is a data-loading
    fix (section 11, item 6)."""

    __slots__ = ("_coverage", "_list_series", "_load")

    def __init__(self, loader) -> None:
        self._load = loader.load if loader is not None else None
        self._coverage = loader.coverage if loader is not None else None
        self._list_series = loader.list_series if loader is not None else None

    def list_series(self) -> tuple[str, ...]:
        """Every series name this run's committed manifests pin, sorted, as a tuple:
        the development manifest's in a development run, and the holdout manifest's
        beside them in the holdout run (no holdout file is read)."""
        if self._list_series is None:
            raise LookupError("this run was given no dataset store")
        return self._list_series()

    def load(self, series: str, start, end, accept_recorded_gaps: bool = False):
        if self._load is None:
            raise LookupError("this run was given no dataset store")
        return self._load(series, start, end, accept_recorded_gaps)

    def coverage(self, series: str) -> list[tuple[int, int]]:
        if self._coverage is None:
            raise LookupError("this run was given no dataset store")
        return self._coverage(series)


def run(payload: dict) -> tuple[list, list]:
    """The strategy's daily returns, and the recorded gaps its loads crossed."""
    from .formats import DatasetSpec
    from .loader import DatasetLoader, inside_ledger_entry, rehearsal_split
    from .manifest import Manifest
    from .periods import HOLDOUT_START_MS, inside_unsealed_run
    from .store import ArchiveStore

    code_root = payload["code_root"]
    files: set[str] = set()
    loader = None
    unseal = payload.get("unseal")
    rehearsal = payload.get("rehearsal")  # a development month: ``seal rehearse`` only
    if payload.get("store_root"):
        manifest = Manifest.load(Path(code_root) / payload["manifest"])
        holdout, boundary = None, HOLDOUT_START_MS
        if rehearsal:
            manifest, holdout, boundary = rehearsal_split(manifest, rehearsal)
        elif unseal and payload.get("holdout_manifest"):
            holdout = Manifest.load(Path(code_root) / payload["holdout_manifest"])
        store = ArchiveStore(Path(payload["store_root"]))
        for pinned in (manifest, holdout):
            for name, entry in (pinned.datasets.items() if pinned is not None else ()):
                spec = DatasetSpec.from_name(name)
                for month, *_ in entry["files"]:
                    path = store.path(spec.key(month))
                    files.add(os.path.normpath(str(path)))
                    files.add(os.path.normpath(str(path) + ".CHECKSUM"))
        loader = DatasetLoader(manifest, store, holdout=holdout, boundary_ms=boundary)
    module_file = os.path.join(code_root, payload["module_path"])
    source = Path(module_file).read_bytes()
    roots = [code_root, payload["pycache"], sys.prefix, sys.base_prefix, sys.exec_prefix,
             sys.base_exec_prefix]
    roots += [os.path.realpath(r) for r in roots]
    refuse_compiling_text = install_tripwire(roots, files)
    seal_engine()  # the engine is used, never changed, by the strategy
    refuse_compiling_text()  # the engine compiled its own text while it was imported
    module = types.ModuleType(payload["module_name"])
    module.__file__ = module_file
    module.__package__ = payload["module_name"].rpartition(".")[0]
    sys.modules[payload["module_name"]] = module
    sealed = inside_unsealed_run(unseal) if unseal else nullcontext()
    with inside_ledger_entry(payload["entry"]), sealed:
        exec(compile(source, module_file, "exec"), module.__dict__)
        function = module.__dict__[payload["function"]]
        series = function(_freeze(payload["configuration"]), DataView(loader))
        returns = [[str(day), float(value)] for day, value in series]
        # Section 5, Gaps: the recorded gaps crossed, for the run's ledger entry.
        return returns, loader.crossed_gaps() if loader is not None else []


def main() -> None:
    payload = json.loads(sys.stdin.read())
    out = sys.stdout
    sys.stdout = sys.stderr  # anything the strategy prints stays out of the result
    try:
        returns, crossed = run(payload)
        result = {"ok": True, "returns": returns, "crossed_gaps": crossed}
    except BaseException as exc:  # reported to the harness, which ledgers the failure
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    out.write("\n" + RESULT_PREFIX + json.dumps(result) + "\n")
    out.flush()
