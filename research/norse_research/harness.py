"""The research harness: every run is ledgered before it computes (G1.5).

``run(repo, request)`` is the only sanctioned way to compute an out-of-sample result.
The caller names a registered configuration and nothing else: the strategy that runs
it, its parameters and its defaults all come from the module the genesis entry pins
for that configuration, as committed at HEAD (security review H2, M3, M4). In order,
the harness:

1. refuses environment overrides that change a result or what git reads with no file
   at all: ``NORSE_*`` (except ``NORSE_DATASETS_DIR``, which only says where the
   verified files are), ``RISK_*``, any ``GIT_*`` outside a short allowlist, and
   ``PYTHONPATH``, ``PYTHONHOME``, ``PYTHONSTARTUP`` and ``PYTHONHASHSEED``; the
   strategy then runs with an empty environment (T15, M5);
2. refuses unless the local ledger head is the remote's (T3), then verifies the
   ledger with its anchors, on the remote the genesis entry pins: the chain, the
   append-only history, the remote head, the milestone tags, the pre-registration tag
   and the run rules (T4 to T6, T10, T24, H1, M2);
3. refuses without a genesis entry, and reads the registration from the commit it
   pins, checked against its hashes (T24);
4. refuses a dataset manifest that is not the file committed at HEAD, or whose
   SHA-256 is not the registered one (T12, T14, T20);
5. refuses a dirty tree: a changed tracked file, any untracked file, or a file git
   is told to ignore changes to (skip-worktree, assume-unchanged) (T15, M1);
6. refuses a HEAD that is not on the remote's main (T15, M9);
7. loads the pinned strategy module from its committed bytes, refuses one that does
   not implement the configuration, and parses every module under the hypothesis's
   strategy paths against the allowlist (T13, H2, L4);
8. refuses when any ``norse_research`` module, or any module loaded from the
   repository, is not byte for byte the file at HEAD (T15, M3);
9. resolves the request against the registration (T16 to T18) and applies the run
   rules, a rerun's fix commit being new since the run it repeats (section 9; L8),
   the freeze and the sensitivity rules (section 15, item 11);
10. appends the open entry, commits it and pushes it, refusing if the ledger moved
    since step 2 (M2). If any of that fails the run is refused and the strategy is
    never called (T8);
11. calls the strategy inside the open entry, with the frozen resolved configuration
    and a view that loads rows through the registered loader and nothing else, while
    a tripwire refuses file, socket and process access (T13). A strategy that fails
    still gets a result entry, with local paths removed from its error (L11), and one
    that never returns leaves the open entry, which counts (T1, T8);
12. appends the result entry with the committed daily return series and its hash
    (T7).

Python cannot be sandboxed from inside itself, so the allowlist and the tripwire stop
accidents and make evasion visible in the diff, nothing more.
"""

from __future__ import annotations

import ast
import hashlib
import os
import platform
import re
import statistics
import sys
import types
from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .canonical import sha256_hex
from .gate import RegisteredLedger, candidates, gate_ledger
from .gitledger import FREEZE_TAG, Git, GitLedger, period_constants, registered_ids
from .ledger import (
    CODE_BRANCH,
    COMPLETED,
    FAILED,
    OPEN,
    RESULT,
    Entry,
    LedgerError,
    LedgerSnapshot,
    consecutive_days,
    encode_returns,
    returns_path,
)
from .loader import DatasetLoader, Loaded, inside_ledger_entry
from .manifest import Manifest
from .registration import (
    CONTROL,
    HOLDOUT,
    SENSITIVITY,
    Registration,
    Resolved,
    RunNotRegistered,
    Window,
)
from .rules import run_rule_error
from .store import ArchiveStore

DEVELOPMENT_MANIFEST = "research/manifests/binance-vision-development.json"
PACKAGE_PATH = "research/norse_research"  # where this package lives in the repository
PACKAGE_DIR = Path(__file__).resolve().parent
STRATEGIES_DIR = PACKAGE_DIR / "strategies"

ENV_ALLOWED = frozenset(
    {
        "NORSE_DATASETS_DIR",
        "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_TERMINAL_PROMPT", "GIT_ASKPASS",
        "GIT_EDITOR", "GIT_PAGER", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE",
        "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE",
    }
)
ENV_REFUSED = re.compile(r"(NORSE|RISK|GIT)_|PYTHON(PATH|HOME|STARTUP|HASHSEED)$")

# What a strategy module may import (T13): computation only, no files, network,
# processes or package readers.
ALLOWED_IMPORTS = frozenset(
    {
        "__future__", "abc", "bisect", "collections", "collections.abc", "dataclasses",
        "datetime", "decimal", "enum", "fractions", "functools", "heapq", "itertools",
        "math", "numbers", "operator", "statistics", "typing",
        "norse_research.formats",
    }
)
ALLOWED_PACKAGES = ("norse_research.strategies",)
FORBIDDEN_CALLS = frozenset(
    {"open", "__import__", "eval", "exec", "compile", "breakpoint", "input", "getattr",
     "setattr", "delattr", "vars", "globals", "locals"}
)
# Audit events a running strategy may not raise (besides opening a file).
_BLOCKED_EVENTS = (
    "os.", "socket.", "subprocess.", "shutil.", "urllib.", "http.", "ftplib.", "smtplib.",
    "poplib.", "imaplib.", "nntplib.", "telnetlib.", "webbrowser.", "ctypes.", "sqlite3.",
    "glob.", "pty.", "fcntl.", "mmap.", "syslog.",
)
_ABSOLUTE_PATH = re.compile(r"(?<![\w.])/(?:[\w.@~+-]+/)*[\w.@~+-]+")


class RunRefused(LedgerError, PermissionError):
    """The harness refuses the run. Nothing was computed."""


class UnmanifestedData(RunRefused):
    """The dataset manifest is not the registered, committed one (T12)."""


class StrategyIOBlocked(PermissionError):
    """A running strategy tried to reach a file, a socket or a process (T13)."""


@dataclass(frozen=True)
class Strategy:
    """A strategy as the harness runs it, built from its pinned module at HEAD.

    ``run(configuration, data)`` receives the frozen resolved configuration and a
    ``DataView``, and returns the daily net returns over the window as ``(day,
    return)`` pairs. ``module`` is its pinned ``path:function``; ``parameters`` and
    ``defaults`` are the module's ``PARAMETERS`` and ``DEFAULTS``, which enter the
    configuration hash (T16).
    """

    module: str
    run: Callable[[Mapping, DataView], Iterable[tuple[str, float]]]
    parameters: frozenset[str] = frozenset()
    defaults: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunRecord:
    status: str
    open_entry: Entry
    result_entry: Entry
    error: str | None


# --- the strategy allowlist (T13) ------------------------------------------------------


def _allowed(name: str) -> bool:
    return name in ALLOWED_IMPORTS or any(
        name == p or name.startswith(p + ".") for p in ALLOWED_PACKAGES
    )


def disallowed_imports(source: str, module: str, is_package: bool = False) -> list[str]:
    """What a strategy module's source imports, calls or reaches outside the allowlist:
    imports beyond it, ``open``, ``eval``, ``getattr`` and their kind, and any dunder
    attribute (``__globals__``, ``__builtins__``), through which the tripwire's own flag
    could be reached (review L4)."""
    problems = []
    package = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            problems += [
                f"line {node.lineno}: import {a.name}" for a in node.names if not _allowed(a.name)
            ]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".") if package else []
                parent = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join([*parent, *([node.module] if node.module else [])])
            if not _allowed(base):
                problems.append(f"line {node.lineno}: from {base or '.'} import ...")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in FORBIDDEN_CALLS
        ):
            problems.append(f"line {node.lineno}: call to {node.func.id}()")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            problems.append(f"line {node.lineno}: dunder attribute {node.attr}")
        elif isinstance(node, ast.Name) and node.id in ("__builtins__", "__loader__", "__spec__"):
            problems.append(f"line {node.lineno}: name {node.id}")
    return problems


def strategy_modules() -> list[tuple[str, Path]]:
    """Every module of the ``norse_research.strategies`` package, by dotted name."""
    out = []
    for path in sorted(STRATEGIES_DIR.rglob("*.py")):
        parts = path.relative_to(STRATEGIES_DIR.parent.parent).with_suffix("").parts
        out.append((".".join(parts[:-1] if parts[-1] == "__init__" else parts), path))
    return out


def module_name_for(rel_path: str) -> str:
    """The dotted name a strategy file at ``rel_path`` is loaded under."""
    stem = rel_path.removesuffix(".py").removesuffix("/__init__")
    if stem.startswith("research/"):
        stem = stem.removeprefix("research/")
    return stem.replace("/", ".")


# --- strategy modules, from their committed bytes ---------------------------------------

_STRATEGY_MODULES: dict[tuple[str, str], types.ModuleType] = {}


def _strategy_module(git: Git, repo: Path, head: str, rel_path: str) -> types.ModuleType:
    """The module at ``rel_path``, executed from its bytes at ``head`` (never from a
    cached ``.pyc``, so a forged bytecode file cannot stand in for it: review L12)."""
    path = (repo / rel_path).resolve()
    if not path.is_relative_to(repo.resolve()):
        raise RunRefused(f"{rel_path} is outside the repository")
    committed = git.blob(head, rel_path)
    if committed is None:
        raise RunRefused(f"{rel_path} is not committed at HEAD")
    if not path.is_file() or path.read_bytes() != committed:
        raise RunRefused(f"{rel_path} differs from the file committed at HEAD")
    name = module_name_for(rel_path)
    key = (str(path), sha256_hex(committed))
    module = _STRATEGY_MODULES.get(key)
    if module is None:
        module = types.ModuleType(name)
        module.__file__ = str(path)
        module.__package__ = name.rpartition(".")[0]
        sys.modules[name] = module
        exec(compile(committed, str(path), "exec"), module.__dict__)
        _STRATEGY_MODULES[key] = module
    sys.modules[name] = module
    return module


def load_strategy_module(repo: Path, rel_path: str) -> types.ModuleType:
    """The strategy module the harness runs for ``rel_path`` at HEAD; the same object
    the next run uses while the file is unchanged."""
    git = Git(repo)
    return _strategy_module(git, Path(repo), git.text("rev-parse", "HEAD"), rel_path)


def load_strategy(
    git: Git, repo: Path, head: str, genesis: Entry, registration: Registration, cid: Any
) -> Strategy:
    """The strategy the genesis entry pins for configuration ``cid`` (review H2)."""
    if cid == HOLDOUT:
        raise RunNotRegistered("the holdout run needs the unseal, which G1.6 builds")
    pins = genesis.body["strategies"]
    if not isinstance(cid, str) or cid not in pins:
        raise RunNotRegistered(f"{cid!r} is not a registered configuration")
    pin = pins[cid]
    module = _strategy_module(git, repo, head, pin["path"])
    run = getattr(module, pin["function"], None)
    code = getattr(run, "__code__", None)
    if code is None or code.co_filename != module.__file__ or run.__module__ != module.__name__:
        raise RunRefused(f"{pin['path']} defines no function {pin['function']} of its own")
    if cid not in getattr(module, "CONFIGURATIONS", ()):
        raise RunRefused(f"{pin['path']} does not implement {cid}, which genesis pins to it")
    hypothesis = registered_ids(registration)[cid]
    sources = git.text("ls-tree", "-r", "--name-only", "-z", head, "--",
                       *genesis.body["tree_paths"][hypothesis]).split("\0")
    for rel in sorted(p for p in sources if p.endswith(".py")):
        problems = disallowed_imports(
            git.blob(head, rel).decode(), module_name_for(rel), rel.endswith("__init__.py")
        )
        if problems:
            raise RunRefused(f"{rel} reaches outside the strategy allowlist: {problems}")
    return Strategy(
        module=f"{pin['path']}:{pin['function']}",
        run=run,
        parameters=frozenset(getattr(module, "PARAMETERS", ())),
        defaults=dict(getattr(module, "DEFAULTS", {})),
    )


# --- the tripwire (T13) ----------------------------------------------------------------

_STRATEGY_RUNNING: ContextVar[bool] = ContextVar("norse_strategy_running", default=False)
_HARNESS_IO: ContextVar[bool] = ContextVar("norse_harness_io", default=False)
_installed: list[tuple[str, ...]] = []


def _python_roots() -> tuple[str, ...]:
    roots = set()
    for prefix in (sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix):
        roots.add(os.path.normpath(prefix))
        roots.add(os.path.normpath(os.path.realpath(prefix)))
    return tuple(sorted(r.rstrip(os.sep) + os.sep for r in roots))


def _in_python_library(path: Any) -> bool:
    """Whether ``path`` lies under the interpreter's own prefixes, so a lazy import
    of the standard library can still read its source."""
    if isinstance(path, int):
        return False
    text = os.fsdecode(path)
    if not os.path.isabs(text):
        return False
    text = os.path.normpath(text)
    return any(text.startswith(root) for root in _installed[0])


def _audit(event: str, args: tuple) -> None:
    if not _STRATEGY_RUNNING.get() or _HARNESS_IO.get():
        return
    if event == "open":
        path, mode, flags = args
        read_only = mode in ("r", "rb") or (
            mode is None and isinstance(flags, int) and flags & os.O_ACCMODE == os.O_RDONLY
        )
        if read_only and _in_python_library(path):
            return
        raise StrategyIOBlocked(f"a strategy run may not open files ({path!r})")
    if event.startswith(_BLOCKED_EVENTS):
        if event in ("os.listdir", "os.scandir") and args and _in_python_library(args[0]):
            return
        raise StrategyIOBlocked(f"a strategy run may not use {event}")


def _install_tripwire() -> None:
    if not _installed:
        _installed.append(_python_roots())
        sys.addaudithook(_audit)


class DataView:
    """What a strategy receives: rows through the registered loader, nothing else."""

    __slots__ = ("_loader",)

    def __init__(self, loader: DatasetLoader | None) -> None:
        self._loader = loader

    def load(self, series: str, start, end, accept_recorded_gaps: bool = False) -> Loaded:
        if self._loader is None:
            raise RunRefused("this run was given no dataset store")
        token = _HARNESS_IO.set(True)
        try:
            return self._loader.load(series, start, end, accept_recorded_gaps)
        finally:
            _HARNESS_IO.reset(token)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(v) for v in value)
    return value


def _call_strategy(strategy: Strategy, configuration: Mapping, data: DataView, entry: str):
    """Run the strategy inside the open entry, with no environment and the tripwire
    set. Returns its daily returns as a list."""
    _install_tripwire()
    saved = dict(os.environ)
    os.environ.clear()
    running = _STRATEGY_RUNNING.set(True)
    try:
        with inside_ledger_entry(entry):
            return list(strategy.run(configuration, data))
    finally:
        _STRATEGY_RUNNING.reset(running)
        os.environ.clear()
        os.environ.update(saved)


def public_error(exc: BaseException) -> str:
    """A failure as the public ledger records it: the type and message, with every
    absolute local path replaced (review L11)."""
    return _ABSOLUTE_PATH.sub("<path>", f"{type(exc).__name__}: {exc}")[:500]


# --- preconditions ---------------------------------------------------------------------


def check_environment(environ: Mapping[str, str]) -> None:
    bad = sorted(k for k in environ if ENV_REFUSED.match(k) and k not in ENV_ALLOWED)
    if bad:
        raise RunRefused(f"environment overrides {bad} could change a result; unset them")


def check_manifest(git: Git, repo: Path, manifest_path: str | Path, genesis: Entry) -> Manifest:
    """The registered development manifest, exactly as committed at HEAD (T12)."""
    path = Path(manifest_path)
    path = (path if path.is_absolute() else repo / path).resolve()
    try:
        rel = path.relative_to(repo.resolve()).as_posix()
    except ValueError:
        raise UnmanifestedData(f"{path} is outside the repository, so in no commit") from None
    committed = git.blob("HEAD", rel)
    if committed is None:
        raise UnmanifestedData(f"{rel} is not committed at HEAD")
    if not path.is_file() or path.read_bytes() != committed:
        raise UnmanifestedData(f"{rel} differs from the file committed at HEAD")
    if sha256_hex(committed) != genesis.body["manifests"]["development"]:
        raise UnmanifestedData(
            f"{rel} (sha256 {sha256_hex(committed)}) is not the development manifest the "
            f"genesis entry registers ({genesis.body['manifests']['development']})"
        )
    return Manifest.load(path)


def check_clean_tree(git: Git) -> None:
    """Refuse a changed or untracked file, read without the filesystem monitor, and
    any file git is told not to look at (review M1)."""
    status = git.run(
        "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
        "status", "--porcelain=v1", "-z", "--untracked-files=all",
    )
    changed = [item[3:].decode(errors="replace") for item in status.split(b"\0") if item]
    if changed:
        raise RunRefused(f"the tree is dirty ({len(changed)} paths, e.g. {changed[:5]})")
    hidden = [
        item[2:].decode(errors="replace")
        for item in git.run("ls-files", "-v", "-z").split(b"\0")
        if item and (item[:1].islower() or item[:1] == b"S")
    ]
    if hidden:
        raise RunRefused(
            f"git is told to ignore changes to {hidden[:5]} (assume-unchanged or "
            "skip-worktree); clear the flags"
        )


def _head_blobs(git: Git, head: str) -> tuple[dict[str, str], str]:
    """Every file at ``head`` with its blob id, and the repository's hash algorithm,
    in one call, so a file's bytes can be checked against HEAD without reading it."""
    listing = git.run("ls-tree", "-r", "-z", "--full-tree", head)
    blobs = {}
    for item in listing.split(b"\0"):
        if item:
            meta, path = item.split(b"\t", 1)
            blobs[path.decode(errors="replace")] = meta.split()[2].decode()
    return blobs, git.text("rev-parse", "--show-object-format")


def _blob_id(data: bytes, algorithm: str) -> str:
    header = f"blob {len(data)}\0".encode()
    return (hashlib.sha1 if algorithm == "sha1" else hashlib.sha256)(header + data).hexdigest()


def check_loaded_modules(git: Git, repo: Path, head: str) -> None:
    """Refuse when the code that runs is not the code at HEAD (T15, review M3):
    every ``norse_research`` module must be byte for byte its file under
    ``research/norse_research`` at HEAD, wherever it was imported from, and every module
    loaded from inside the repository must be tracked and unchanged."""
    root = repo.resolve()
    blobs, algorithm = _head_blobs(git, head)

    def at_head(rel: str, file: Path) -> bool:
        return rel in blobs and _blob_id(file.read_bytes(), algorithm) == blobs[rel]

    bad = []
    for name, module in list(sys.modules.items()):
        location = getattr(module, "__file__", None)
        if not isinstance(location, str):
            continue
        file = Path(location).resolve()
        if name == "norse_research" or name.startswith("norse_research."):
            try:
                rel = f"{PACKAGE_PATH}/{file.relative_to(PACKAGE_DIR).as_posix()}"
            except ValueError:
                bad.append(f"{name} (loaded from outside the package)")
                continue
            if not rel.endswith(".py") or not at_head(rel, file):
                bad.append(f"{name} (not {rel} as at HEAD)")
            continue
        try:
            rel_path = file.relative_to(root)
        except ValueError:
            continue
        if "__pycache__" in rel_path.parts and rel_path.suffix == ".pyc":
            rel_path = rel_path.parent.parent / (rel_path.name.split(".", 1)[0] + ".py")
        rel = rel_path.as_posix()
        if rel not in blobs:
            bad.append(f"{name} ({rel})")
        elif not at_head(rel, root / rel):
            bad.append(f"{name} ({rel} differs from HEAD)")
    if bad:
        raise RunRefused(f"modules loaded from files not tracked or not as at HEAD: {sorted(bad)}")


def check_head_on_main(git: Git, remote: str, head: str) -> None:
    """HEAD must be on the remote's main, which nobody deletes or force pushes, so the
    run can be reproduced from its commit at G2.7 and G3.4 (review M9)."""
    git.run(
        "fetch", "--quiet", "--no-tags", "--", remote,
        f"+refs/heads/{CODE_BRANCH}:refs/remotes/{remote}/{CODE_BRANCH}",
    )
    main = git.text("rev-parse", f"refs/remotes/{remote}/{CODE_BRANCH}")
    if not git.is_ancestor(head, main):
        raise RunRefused(
            f"HEAD {head} is not on {remote}'s {CODE_BRANCH}, so the run could not be "
            "reproduced from it"
        )


def check_periods(genesis: Entry, registration: Registration) -> None:
    constants = period_constants()
    if genesis.body["periods"] != constants:
        raise RunRefused(
            f"norse_research.periods {constants} differs from the genesis entry's "
            f"{genesis.body['periods']}"
        )
    if constants["out_of_sample_start"] != registration.out_of_sample_start:
        raise RunRefused("the loader's out-of-sample start is not the registered one")


def _previous_run(snapshot: LedgerSnapshot, resolved: Resolved) -> Entry | None:
    found = None
    for entry in snapshot.opens(resolved.kind):
        same = entry.body["configuration_id"] == resolved.configuration_id
        if resolved.kind == CONTROL:
            same = same and entry.body["configuration"].get("seed") == (
                resolved.configuration["seed"]
            )
        if same:
            found = entry
    return found


def check_rerun_commit(git: Git, snapshot: LedgerSnapshot, resolved: Resolved, head: str) -> None:
    """A rerun's fix is in the code that runs, and is new since the run it repeats
    (review L8): an older commit cannot be named as the fix."""
    if resolved.rerun is None:
        return
    fix = resolved.rerun["fix_commit"]
    if git.try_text("cat-file", "-t", fix) != "commit" or not git.is_ancestor(fix, head):
        raise RunRefused("a rerun's fix commit must be in the code that runs")
    previous = _previous_run(snapshot, resolved)
    if previous is not None and git.is_ancestor(fix, previous.body["commit"]):
        raise RunRefused(
            f"the fix commit {fix} was already in the code of the run it repeats "
            f"(entry {previous.seq}); name the commit that fixes the defect"
        )


# --- the run rules (section 9) ---------------------------------------------------------


def admit(
    snapshot: LedgerSnapshot,
    registration: Registration,
    resolved: Resolved,
    frozen: RegisteredLedger | None,
) -> None:
    """Refuse a run the registered rules do not allow. Reads the ledger, never the
    clock: "after the freeze" means the freeze tag exists (T11)."""
    kind, h, cid = resolved.kind, resolved.hypothesis, resolved.configuration_id
    if kind == SENSITIVITY:
        if frozen is None:
            raise RunRefused("a sensitivity run comes only after the ledger freeze (section 8)")
        candidate = candidates(frozen).get(h)
        if candidate is None or candidate.configuration_id != cid:
            chosen = candidate.configuration_id if candidate else "none"
            raise RunRefused(
                f"{cid} is not {h}'s candidate ({chosen}); only a candidate is re-costed "
                "(section 8)"
            )
        return
    if frozen is not None:
        raise RunRefused(
            f"the ledger is frozen ({FREEZE_TAG}): only the candidates' sensitivity runs remain"
        )
    error = run_rule_error(
        snapshot.entries,
        registration,
        {
            "kind": kind,
            "hypothesis": h,
            "configuration_id": cid,
            "seed": resolved.configuration["seed"],
            "rerun": resolved.rerun,
            "reserve_reason": resolved.reserve_reason,
        },
    )
    if error:
        raise RunRefused(error)


# --- the run ---------------------------------------------------------------------------


def _summary(series: list, window: Window) -> tuple[list[tuple[str, float]], dict]:
    pairs = [(str(day), float(value)) for day, value in series]
    if not pairs:
        raise ValueError("the strategy returned no returns")
    allowed = set(window.day_list())
    outside = [d for d, _ in pairs if d not in allowed]
    if outside:
        raise ValueError(f"returns outside the registered window, e.g. {outside[0]}")
    if not consecutive_days(pairs):
        raise ValueError("the returns are not one value per consecutive day")
    values = [v for _, v in pairs]
    mean = statistics.fmean(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return pairs, {
        "days": len(pairs),
        "first_day": pairs[0][0],
        "last_day": pairs[-1][0],
        "covers_window": [d for d, _ in pairs] == window.day_list(),
        "mean": mean,
        "sd": sd,
        "sharpe": mean / sd if sd > 0 else 0.0,
    }


def run(
    repo: Path,
    request: Mapping[str, Any],
    *,
    store: ArchiveStore | None = None,
    remote: str = "origin",
    manifest_path: str | Path = DEVELOPMENT_MANIFEST,
) -> RunRecord:
    """Run one registered configuration through the ledger. See the module docstring."""
    repo = Path(repo)
    check_environment(os.environ)
    ledger = GitLedger(repo, remote)
    git = ledger.git
    admitted_on = ledger.require_in_step()
    verified = ledger.verify(admitted_on)
    snapshot = verified.snapshot
    genesis = snapshot.genesis
    if genesis is None or verified.registration is None:
        raise RunRefused(
            "the ledger has no genesis entry naming the pre-registration, so no run may start"
        )
    registration = verified.registration
    check_periods(genesis, registration)
    manifest = check_manifest(git, repo, manifest_path, genesis)
    check_clean_tree(git)
    head = git.text("rev-parse", "HEAD")
    check_head_on_main(git, remote, head)
    strategy = load_strategy(git, repo, head, genesis, registration, request.get("configuration"))
    check_loaded_modules(git, repo, head)
    resolved = registration.resolve(request, strategy)
    check_rerun_commit(git, snapshot, resolved, head)
    frozen = gate_ledger(ledger) if FREEZE_TAG in verified.tags else None
    admit(snapshot, registration, resolved, frozen)

    paths = [p for group in genesis.body["tree_paths"].values() for p in group]
    opened = ledger.append(
        {
            "type": OPEN,
            "kind": resolved.kind,
            "hypothesis": resolved.hypothesis,
            "configuration_id": resolved.configuration_id,
            "configuration": resolved.configuration,
            "configuration_sha256": resolved.configuration_sha256,
            "commit": head,
            "trees": {p: git.try_text("rev-parse", "--verify", "--quiet", f"{head}:{p}")
                      for p in sorted(set(paths))},
            "manifest_sha256": manifest.sha256,
            "cost_basis": resolved.cost_basis,
            "window": resolved.window.as_dict(),
            "label": resolved.label,
            "rerun": resolved.rerun,
            "reserve_reason": resolved.reserve_reason,
            "strategy": strategy.module,
            "environment": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": sys.platform,
            },
        },
        expected_head=admitted_on,
    )

    loader = DatasetLoader(manifest, store) if store is not None else None
    error = None
    files: dict[str, bytes] = {}
    try:
        returned = _call_strategy(
            strategy, _freeze(resolved.configuration), DataView(loader), opened.hash
        )
        pairs, summary = _summary(returned, resolved.window)
        data = encode_returns(pairs)
        digest = sha256_hex(data)
        files[returns_path(digest)] = data
        status = COMPLETED
    except Exception as exc:  # a failed run is still a ledgered run
        error = public_error(exc)
        status, summary, digest = FAILED, {"error": error}, None
    closed = ledger.append(
        {
            "type": RESULT,
            "opens": opened.hash,
            "open_seq": opened.seq,
            "status": status,
            "configuration_sha256": resolved.configuration_sha256,
            "commit": head,
            "manifest_sha256": manifest.sha256,
            "cost_basis": resolved.cost_basis,
            "result": summary,
            "result_sha256": digest,
            "returns_path": returns_path(digest) if digest else None,
        },
        files,
        require_in_step=False,
        attempts=3,
    )
    return RunRecord(status, opened, closed, error)


__all__ = [
    "ALLOWED_IMPORTS",
    "DataView",
    "RunRecord",
    "RunRefused",
    "Strategy",
    "StrategyIOBlocked",
    "UnmanifestedData",
    "admit",
    "disallowed_imports",
    "load_strategy_module",
    "run",
    "strategy_modules",
]
