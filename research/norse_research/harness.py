"""The research harness: every run is ledgered before it computes (G1.5).

``run`` is the only sanctioned way to compute an out-of-sample result. In order, it:

1. refuses ``NORSE_*`` and ``RISK_*`` environment overrides, which change results
   with no file at all (only ``NORSE_DATASETS_DIR``, which says where the verified
   files are, is allowed), and runs the strategy with an empty environment (T15);
2. refuses unless the local ledger head is the remote's (T3), then verifies the
   ledger with its anchors: the chain, the append-only history, the remote head,
   the milestone tags and the pre-registration tag (T4 to T6, T10, T24);
3. refuses without a genesis entry, and reads the registration from the commit it
   pins, checked against its hashes (T24);
4. refuses a dataset manifest that is not the file committed at HEAD, or whose
   SHA-256 is not the registered one (T12, T14, T20);
5. refuses a dirty tree: a changed tracked file or any untracked file, anywhere
   (T15);
6. refuses a strategy whose module is not a tracked file or imports outside the
   allowlist, and any module loaded from the repository that is not tracked at HEAD,
   such as a sourceless ``.pyc`` in an ignored place (T13, T15);
7. refuses a commit the remote does not have (T15);
8. resolves the request against the registration: an unregistered configuration,
   an unknown key, or a label standing in for a kind is refused; the kind comes from
   the configuration (T16 to T18);
9. applies the run rules: the registered order, a recorded defect for a rerun, the
   budgets and the reserve, the freeze, and sensitivity runs only of a candidate
   after the freeze (section 9; section 15, item 11);
10. appends the open entry, commits it and pushes it. If any of that fails the run
    is refused and the strategy is never called (T8);
11. calls the strategy inside the open entry, with the frozen resolved configuration
    and a view that loads rows through the registered loader and nothing else, while
    a tripwire refuses file and socket access (T13). A strategy that fails still
    gets a result entry, and one that never returns leaves the open entry, which
    counts (T1, T8);
12. appends the result entry with the committed daily return series and its hash
    (T7).

Strategies live under ``norse_research.strategies`` and may import only the
allowlist below. Python cannot be sandboxed from inside itself, so the allowlist and
the tripwire stop accidents and make evasion visible in the diff, nothing more.
"""

from __future__ import annotations

import ast
import os
import platform
import re
import statistics
import sys
from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .canonical import sha256_hex
from .gate import RegisteredLedger, candidates
from .gitledger import FREEZE_TAG, Git, GitLedger, load_registration, period_constants
from .ledger import (
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
from .registration import SENSITIVITY, TRIAL, Registration, Resolved, Window
from .store import ArchiveStore

DEVELOPMENT_MANIFEST = "research/manifests/binance-vision-development.json"
STRATEGIES_DIR = Path(__file__).resolve().parent / "strategies"

ENV_ALLOWED = frozenset({"NORSE_DATASETS_DIR"})
ENV_REFUSED = re.compile(r"(NORSE|RISK)_")

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
    {"open", "__import__", "eval", "exec", "compile", "breakpoint", "input"}
)
# Audit events a running strategy may not raise (besides opening a file).
_BLOCKED_EVENTS = (
    "os.", "socket.", "subprocess.", "shutil.", "urllib.", "http.", "ftplib.", "smtplib.",
    "poplib.", "imaplib.", "nntplib.", "telnetlib.", "webbrowser.", "ctypes.", "sqlite3.",
    "glob.", "pty.", "fcntl.", "mmap.", "syslog.",
)


class RunRefused(LedgerError, PermissionError):
    """The harness refuses the run. Nothing was computed."""


class UnmanifestedData(RunRefused):
    """The dataset manifest is not the registered, committed one (T12)."""


class StrategyIOBlocked(PermissionError):
    """A running strategy tried to reach a file, a socket or a process (T13)."""


@dataclass(frozen=True)
class Strategy:
    """A strategy as the harness runs it.

    ``run(configuration, data)`` receives the frozen resolved configuration and a
    ``DataView``, and returns the daily net returns over the window as ``(day,
    return)`` pairs. ``parameters`` names every configuration key it reads and
    ``defaults`` its defaults in code, which enter the configuration hash (T16).
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
    """What a strategy module's source imports or calls outside the allowlist."""
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
    return problems


def strategy_modules() -> list[tuple[str, Path]]:
    """Every module of the ``norse_research.strategies`` package, by dotted name."""
    out = []
    for path in sorted(STRATEGIES_DIR.rglob("*.py")):
        parts = path.relative_to(STRATEGIES_DIR.parent.parent).with_suffix("").parts
        out.append((".".join(parts[:-1] if parts[-1] == "__init__" else parts), path))
    return out


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
    status = git.run("status", "--porcelain=v1", "-z", "--untracked-files=all")
    changed = [item[3:].decode(errors="replace") for item in status.split(b"\0") if item]
    if changed:
        raise RunRefused(f"the tree is dirty ({len(changed)} paths, e.g. {changed[:5]})")


def _tracked(git: Git) -> set[str]:
    return {p for p in git.run("ls-files", "-z").decode().split("\0") if p}


def check_loaded_modules(git: Git, repo: Path) -> None:
    """Refuse when a module loaded from the repository is not tracked at HEAD (T15)."""
    root = repo.resolve()
    tracked = _tracked(git)
    bad = []
    for name, module in list(sys.modules.items()):
        location = getattr(module, "__file__", None)
        if not isinstance(location, str):
            continue
        try:
            rel = Path(location).resolve().relative_to(root)
        except ValueError:
            continue
        if "__pycache__" in rel.parts and rel.suffix == ".pyc":
            rel = rel.parent.parent / (rel.name.split(".", 1)[0] + ".py")
        if rel.as_posix() not in tracked:
            bad.append(f"{name} ({rel.as_posix()})")
    if bad:
        raise RunRefused(f"modules loaded from files not tracked at HEAD: {sorted(bad)}")


def check_strategy(git: Git, repo: Path, strategy: Strategy) -> None:
    module = sys.modules.get(strategy.module)
    if module is None or getattr(strategy.run, "__module__", None) != strategy.module:
        raise RunRefused(f"the strategy's run function is not in module {strategy.module}")
    location = getattr(module, "__file__", None)
    try:
        rel = Path(location).resolve().relative_to(repo.resolve()).as_posix()
    except (TypeError, ValueError):
        raise RunRefused(f"{strategy.module} is not a file in the repository") from None
    if rel not in _tracked(git):
        raise RunRefused(f"{strategy.module} ({rel}) is not tracked")
    source = Path(location).read_text()
    problems = disallowed_imports(source, strategy.module, location.endswith("__init__.py"))
    if problems:
        raise RunRefused(f"{strategy.module} reaches outside the allowlist: {problems}")


def check_head_on_remote(git: Git, remote: str, head: str) -> None:
    git.run("fetch", "--quiet", "--prune", "--no-tags", remote, f"+refs/heads/*:refs/remotes/{remote}/*")
    holders = git.text(
        "for-each-ref", "--contains", head, "--format=%(refname)", f"refs/remotes/{remote}/"
    )
    if not holders:
        raise RunRefused(f"HEAD {head} is not on {remote}, so the run could not be reproduced")


def check_periods(genesis: Entry, registration: Registration) -> None:
    constants = period_constants()
    if genesis.body["periods"] != constants:
        raise RunRefused(
            f"norse_research.periods {constants} differs from the genesis entry's "
            f"{genesis.body['periods']}"
        )
    if constants["out_of_sample_start"] != registration.out_of_sample_start:
        raise RunRefused("the loader's out-of-sample start is not the registered one")


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
    if kind != TRIAL:
        return
    trials = snapshot.opens(TRIAL)
    prior = [e for e in trials if e.body["configuration_id"] == cid]
    if not prior:
        if resolved.rerun is not None:
            raise RunRefused(f"{cid} has not run, so this is not a rerun")
        order = registration.order[h]
        ran = {e.body["configuration_id"] for e in trials}
        missing = [c for c in order[: order.index(cid)] if c not in ran]
        if missing:
            raise RunRefused(
                f"{h}'s configurations run in registered order: {missing} first "
                "(section 9, run rule 1)"
            )
    elif resolved.rerun is None:
        raise RunRefused(
            f"{cid} has run before; a rerun records its defect and the commit that fixes it "
            "(section 9, run rule 3)"
        )
    own = sum(
        1 for e in trials if e.body["hypothesis"] == h and e.body["reserve_reason"] is None
    )
    reserve = sum(1 for e in trials if e.body["reserve_reason"] is not None)
    if own < registration.own_budget[h]:
        if resolved.reserve_reason is not None:
            raise RunRefused(
                f"the reserve is drawn only after {h} has used its own "
                f"{registration.own_budget[h]} entries (section 9, run rule 5)"
            )
        return
    if reserve >= registration.reserve_budget:
        raise RunRefused(
            f"{h} has used its {registration.own_budget[h]} trial entries and the reserve its "
            f"{registration.reserve_budget}: the budget is exhausted (section 9)"
        )
    if not prior:
        raise RunRefused("reserve entries are only for reruns (section 9, run rule 5)")
    if resolved.reserve_reason is None:
        raise RunRefused(
            f"{h}'s own entries are used: a reserve trial needs a recorded reason "
            "(section 9, run rule 5)"
        )


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
    strategy: Strategy,
    request: Mapping[str, Any],
    *,
    store: ArchiveStore | None = None,
    remote: str = "origin",
    manifest_path: str | Path = DEVELOPMENT_MANIFEST,
    ledger: GitLedger | None = None,
) -> RunRecord:
    """Run one registered configuration through the ledger. See the module docstring."""
    repo = Path(repo)
    check_environment(os.environ)
    ledger = GitLedger(repo, remote) if ledger is None else ledger
    git = ledger.git
    head_of_ledger = ledger.require_in_step()
    verified = ledger.verify(head_of_ledger)
    snapshot = verified.snapshot
    genesis = snapshot.genesis
    if genesis is None:
        raise RunRefused(
            "the ledger has no genesis entry naming the pre-registration, so no run may start"
        )
    registration = load_registration(git, genesis)
    check_periods(genesis, registration)
    manifest = check_manifest(git, repo, manifest_path, genesis)
    check_clean_tree(git)
    check_strategy(git, repo, strategy)
    check_loaded_modules(git, repo)
    head = git.text("rev-parse", "HEAD")
    check_head_on_remote(git, remote, head)
    resolved = registration.resolve(request, strategy)
    if resolved.rerun and not git.is_ancestor(resolved.rerun["fix_commit"], head):
        raise RunRefused("a rerun's fix commit must be in the code that runs")
    frozen = None
    freeze = verified.tags.get(FREEZE_TAG)
    if freeze is not None:
        frozen = RegisteredLedger(ledger.snapshot(freeze), registration)
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
        }
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
        error = f"{type(exc).__name__}: {exc}"[:500]
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
    "run",
    "strategy_modules",
]
