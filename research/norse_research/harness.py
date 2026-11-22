"""The research harness: every run is ledgered before it computes (G1.5).

``run(repo, request)`` is the only sanctioned way to compute an out-of-sample result.
The caller names a registered configuration and nothing else. The strategy that runs
it is the module and function the genesis entry pins for that configuration, as
committed at HEAD; the harness never imports or executes it. Its declared
``CONFIGURATIONS``, ``PARAMETERS`` and ``DEFAULTS`` are read by parsing the committed
source, and after the open entry is pushed it runs in a fresh interpreter built from
HEAD's bytes (``norse_research.runner``; security review N1, N4). In order, the
harness:

1. refuses environment overrides and a ``git`` inside the repository
   (``norse_research.environment``; T15, M5, N10);
2. refuses unless the local ledger head is the remote's (T3), then verifies the
   ledger on the pinned remote: the chain, the append-only history, the remote head,
   the anchors and milestone tags, the pre-registration tag, the run rules and every
   entry against its registration (T4 to T6, T10, T24, H1, M2, N7);
3. refuses without a genesis entry, and reads the registration from the commit it
   pins, checked against its hashes (T24);
4. refuses a dataset manifest that is not the file committed at HEAD, or whose
   SHA-256 is not the registered one (T12, T14, T20);
5. refuses a dirty tree: a changed tracked file, any untracked file, or a file git
   is told to ignore changes to (T15, M1);
6. refuses a HEAD that is not on the remote's main (T15, M9);
7. parses the pinned module and every module under the hypothesis's strategy paths
   against the allowlist, before anything executes; a strategy may import only the
   allowlist and modules inside its own hypothesis's paths (T13, H2, L4, N4, N9, N12);
8. refuses when any ``norse_research`` module this process runs, or any module
   loaded from the repository, is not byte for byte the file at HEAD (T15, M3);
9. resolves the request against the registration (T16 to T18) and applies the run
   rules, a rerun's fix commit being new since the run it repeats (section 9; L8),
   the unseal closing development (decision D21) and the bounded sensitivity runs,
   each re-costing a candidate (section 8; section 15, item 11; N13);
10. appends the open entry, commits it and pushes it, refusing if the ledger moved
    since step 2 or the unseal was written meanwhile (M2, N2). If any of that fails the
    run is refused and nothing of the strategy has run (T8);
11. runs the strategy in the fresh interpreter, inside the open entry, with the
    resolved configuration and a view that loads rows through the registered loader,
    under an audit hook that lets it read only its own code, the interpreter's library
    and the files the registered manifest pins (T13). A strategy that fails or exits
    still gets a result entry, with local paths removed from its error (L11, N11); a
    harness killed while the strategy runs leaves the open entry, which counts (T1, T8);
12. appends the result entry with the committed daily return series and its hash
    (T7).

**The holdout run** (G1.6; section 11, items 5 and 6) is requested as
``{"configuration": "holdout", "candidate_sha256": <the unseal's configuration hash>}``
and takes steps 1 to 6 and 8 as above. It is then refused without the unseal entry,
for any configuration but the unseal's (T22), once a holdout result has spent the
seal or three runs have reached the unsealed stage, when the candidate's configuration
no longer resolves the same at HEAD, at any commit but the one the unseal records
(item 6's rule was applied there, once), when the committed holdout manifest is not
the registered one, and when the holdout files on disk do not all verify against it.
Its open entry is appended as
any other; then, before any holdout row is read, the remote's ledger must hold the
unseal entry and that open entry (T23). The strategy first runs over the candidate's
own development window, without the unseal, and must reproduce the candidate's
committed series exactly, so no holdout return is computed unless it does; only then
does it run once from the start of the development window through 2026-10-31,
positions carried across the boundary, inside the unsealed run, whose development
part must match again. That unsealed run is bracketed by an attempt entry, written
first, and its close, recording the outcome; the run holds the clone's holdout lock
throughout, so a second one there is refused (security review H2, M1). A failure is
ledgered and may be retried under the same unseal, at the same commit, until three
runs in all have reached the unsealed stage, an attempt entry each; a run refused or
failed before its attempt entry does not count (security review round 2, N1; round 3,
C1). Once the strategy has been given the unseal, a failure's own text is withheld
from the ledger (L7). A completed result spends the seal. HOLD-1 and the verdict read
that result later (G3.2, G3.3); nothing here computes a statistic of holdout returns.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from . import seal
from .canonical import sha256_hex
from .environment import ENV_ALLOWED, ENV_REFUSED, check_environment, git_version
from .gate import RegisteredLedger, gate_ledger, holdout_candidate, sensitivity_error
from .gitledger import (
    PACKAGE_PATH,
    Git,
    GitLedger,
    LedgerBusy,
    holdout_commit_error,
    period_constants,
    registered_ids,
    tree_digests,
)
from .ledger import (
    ABANDONED,
    ATTEMPT,
    ATTEMPT_CLOSE,
    CODE_BRANCH,
    COMPLETED,
    FAILED,
    OPEN,
    RESULT,
    RETURNED,
    Entry,
    LedgerError,
    LedgerSnapshot,
    consecutive_days,
    encode_returns,
    returns_path,
    unseal_of,
)
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
from .rules import after_unseal_error, run_rule_error
from .runner import RESULT_PREFIX
from .sources import (
    ALLOWED_IMPORTS,
    StrategySourceRefused,
    check_strategy_sources,
    disallowed_imports,
    module_name_for,
    strategy_declarations,
)
from .store import ArchiveStore

DEVELOPMENT_MANIFEST = "research/manifests/binance-vision-development.json"
PACKAGE_DIR = Path(__file__).resolve().parent
STRATEGIES_DIR = PACKAGE_DIR / "strategies"
STRATEGIES_PATH = f"{PACKAGE_PATH}/strategies"

_PATHS = re.compile(r"(?<![\w.])/(?:[\w.@~+-]+/)*[\w.@~+-]+")
_VOLUME = re.compile(r"/Volumes/[^/\n'\"]+")
# The strategy process's whole environment: string hashing fixed, nothing else (N3).
STRATEGY_ENVIRONMENT = {"PYTHONHASHSEED": "0"}
_RUNNER = (
    "import sys; sys.path[:0] = [sys.argv[1], sys.argv[2]]; "
    "from norse_research.runner import main; main()"
)


class RunRefused(LedgerError, PermissionError):
    """The harness refuses the run. Nothing was computed."""


class UnmanifestedData(RunRefused):
    """The dataset manifest is not the registered, committed one (T12)."""


class StrategyFailed(RuntimeError):
    """The strategy process returned no result."""


class StrategyOutput(NamedTuple):
    """What the strategy process reported: its daily returns, and the recorded gaps its
    loads crossed, as the runner's loader collected them."""

    returns: list
    crossed_gaps: Any


@dataclass(frozen=True)
class Strategy:
    """A strategy as the harness resolves it: its pinned ``path:function`` and the
    ``PARAMETERS`` and ``DEFAULTS`` its committed module declares, which enter the
    configuration hash (T16). The harness never holds its code."""

    module: str
    run: Any = None
    parameters: frozenset[str] = frozenset()
    defaults: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunRecord:
    status: str
    open_entry: Entry
    result_entry: Entry
    error: str | None


# --- the strategy's source, checked before anything runs (T13, N4, N9, N12) -------------


def strategy_modules() -> list[tuple[str, Path]]:
    """Every module of the ``norse_research.strategies`` package, by dotted name."""
    out = []
    for path in sorted(STRATEGIES_DIR.rglob("*.py")):
        parts = path.relative_to(STRATEGIES_DIR.parent.parent).with_suffix("").parts
        out.append((".".join(parts[:-1] if parts[-1] == "__init__" else parts), path))
    return out


def load_strategy(
    git: Git, head: str, genesis: Entry, registration: Registration, cid: Any
) -> Strategy:
    """The strategy the genesis entry pins for configuration ``cid``, read from its
    committed source and never executed here (review H2, N1, N4)."""
    if cid == HOLDOUT:
        raise RunNotRegistered("the holdout run runs the unseal's candidate configuration")
    pins = genesis.body["strategies"]
    if not isinstance(cid, str) or cid not in pins:
        raise RunNotRegistered(f"{cid!r} is not a registered configuration")
    pin = pins[cid]
    paths = genesis.body["tree_paths"][registered_ids(registration)[cid]]
    source = git.blob(head, pin["path"])
    if source is None:
        raise RunRefused(f"{pin['path']} is not committed at HEAD")
    try:
        check_strategy_sources(git, head, paths)
        configurations, parameters, defaults = strategy_declarations(
            source.decode(), pin["function"]
        )
    except StrategySourceRefused as exc:
        raise RunRefused(str(exc)) from None
    if cid not in configurations:
        raise RunRefused(f"{pin['path']} does not implement {cid}, which genesis pins to it")
    return Strategy(f"{pin['path']}:{pin['function']}", None, parameters, defaults)


# --- running the strategy, in a fresh interpreter from HEAD's bytes (N1) ----------------


def export(git: Git, commit: str, paths: Sequence[str], dest: Path) -> None:
    """Write the regular files under ``paths`` at ``commit`` into ``dest``."""
    listing = git.run("ls-tree", "-r", "-z", "--full-tree", commit, "--", *paths)
    items = []
    for item in listing.split(b"\0"):
        if item:
            meta, path = item.split(b"\t", 1)
            mode, _, oid = meta.split()
            if mode in (b"100644", b"100755"):
                items.append((path.decode(), oid.decode()))
    for (path, _), data in zip(items, git.objects([oid for _, oid in items]), strict=True):
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _run_strategy(
    git: Git,
    head: str,
    pin: Mapping[str, str],
    code_paths: Sequence[str],
    manifest: str,
    configuration: Mapping,
    entry: str,
    store: ArchiveStore | None,
    unsealed: tuple[str, str] | None = None,
    rehearsal: str | None = None,
) -> StrategyOutput:
    """The strategy's daily returns, and the recorded gaps its loads crossed (section 5,
    Gaps), computed in a fresh ``python -s -P -B`` process
    whose environment holds only ``PYTHONHASHSEED=0``, from the files committed at
    ``head``. ``unsealed`` is the
    holdout run's alone: the committed holdout manifest's path and the unseal entry,
    which the runner needs to read holdout rows (G1.6). ``rehearsal`` is the rehearsal's
    alone: the development month whose data stands in for the holdout."""
    holdout_manifest, unseal_entry = unsealed if unsealed is not None else (None, None)
    if rehearsal is not None:
        unseal_entry = REHEARSAL_TOKEN
    with tempfile.TemporaryDirectory(prefix="norse-strategy-") as tmp:
        code_root, pycache = Path(tmp) / "code", Path(tmp) / "pycache"
        pycache.mkdir()
        files = [PACKAGE_PATH, *code_paths, manifest, *([holdout_manifest] if unsealed else [])]
        export(git, head, files, code_root)
        payload = {
            "code_root": str(code_root),
            "pycache": str(pycache),
            "module_path": pin["path"],
            "module_name": module_name_for(pin["path"]),
            "function": pin["function"],
            "configuration": configuration,
            "entry": entry,
            "manifest": manifest,
            "store_root": str(store.root) if store is not None else None,
            "holdout_manifest": holdout_manifest,
            "unseal": unseal_entry,
            "rehearsal": rehearsal,
        }
        # -s -P instead of -I, whose -E would ignore PYTHONHASHSEED: with string hashing
        # randomised per process, a strategy that iterates a set of symbols could give a
        # different series on each run of the same commit (security review round 2, N3).
        # The environment holds that one variable and nothing else.
        proc = subprocess.run(
            [sys.executable, "-s", "-P", "-B", "-X", f"pycache_prefix={pycache}", "-c", _RUNNER,
             str(code_root / "research"), str(code_root)],
            input=json.dumps(payload).encode(), capture_output=True, env=STRATEGY_ENVIRONMENT,
            cwd=tmp, check=False,
        )
    lines = [
        line for line in proc.stdout.decode(errors="replace").splitlines()
        if line.startswith(RESULT_PREFIX)
    ]
    if not lines:
        raise StrategyFailed(f"the strategy process exited with {proc.returncode} and no result")
    result = json.loads(lines[-1][len(RESULT_PREFIX):])
    if not result.get("ok"):
        raise StrategyFailed(result.get("error", "the strategy failed"))
    return StrategyOutput(result["returns"], result.get("crossed_gaps"))


def crossed_gaps(reported: Any, manifests: Sequence[Manifest]) -> list[list]:
    """The gaps the runner reports a run crossed, for its result entry (section 5, Gaps:
    "a gap the committed manifest records is crossed and listed in the run's ledger
    entry"). Each must be a gap one of the registered ``manifests`` records for that
    series; anything else fails the run rather than reach the ledger."""
    if not isinstance(reported, list):
        raise StrategyFailed("the strategy process reported no crossed gaps")
    gaps = set()
    for gap in reported:
        recorded = (
            isinstance(gap, list) and len(gap) == 4 and isinstance(gap[0], str)
            and any(gap[0] in m.datasets and gap[1:] in m.datasets[gap[0]]["gaps"]
                    for m in manifests)
        )
        if not recorded:
            raise StrategyFailed(f"the run reported a crossed gap no registered manifest records: "
                                 f"{str(gap)[:200]}")
        gaps.add(tuple(gap))
    return [list(g) for g in sorted(gaps)]


def public_error(exc: BaseException, repo: Path | None = None, store=None) -> str:
    """A failure as the public ledger records it: the type and message, with the
    repository, the dataset store, the home folder and every other absolute local path
    replaced, spaces in folder names included (review L11, N11)."""
    text = f"{type(exc).__name__}: {exc}" if not isinstance(exc, StrategyFailed) else str(exc)
    known = [(repo, "<repo>"), (getattr(store, "root", None), "<store>"),
             (Path.home(), "<home>"), (Path(tempfile.gettempdir()), "<tmp>")]
    for path, label in known:
        if path is not None:
            for form in {str(Path(path)), str(Path(path).resolve())}:
                text = text.replace(form, label)
    text = _VOLUME.sub("<volume>", text)
    return _PATHS.sub("<path>", text)[:500]


# --- preconditions ---------------------------------------------------------------------


def check_manifest(
    git: Git, repo: Path, manifest_path: str | Path, genesis: Entry
) -> tuple[Manifest, str]:
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
    return Manifest.load(path), rel


def check_clean_tree(git: Git) -> None:
    """Refuse a changed or untracked file, read without the filesystem monitor, and
    any file git is told not to look at (review M1). The ignore and attributes files git
    would read under the home folder (``~/.config/git/ignore`` and ``attributes``) are not
    read here, so nothing outside the clone can hide an untracked file from this check
    (review of #70, I-8)."""
    status = git.run(
        "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
        "-c", f"core.excludesFile={os.devnull}", "-c", f"core.attributesFile={os.devnull}",
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
    """Refuse when the code this process runs is not the code at HEAD (T15, review
    M3): every ``norse_research`` module must be byte for byte its file under
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
        if resolved.kind in (CONTROL, SENSITIVITY):
            same = same and entry.body["configuration"] == {
                **entry.body["configuration"],
                "seed": resolved.configuration["seed"],
                "costs": resolved.configuration["costs"],
            }
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
    issued: RegisteredLedger | None = None,
) -> None:
    """Refuse a run the registered rules do not allow. Reads the ledger, never the
    clock (T11): no trial, benchmark or control run is opened after the unseal entry,
    which closes development (decision D21), and a sensitivity run only of its hypothesis's
    candidate on ``issued``, the gate's ledger, before or after the unseal (section 8)."""
    kind, h, cid = resolved.kind, resolved.hypothesis, resolved.configuration_id
    closed = after_unseal_error(snapshot.entries, kind)
    if closed:
        raise RunRefused(closed)
    if kind == SENSITIVITY:
        if issued is None:
            raise RunRefused("a sensitivity run is admitted only on the gate's ledger (section 8)")
        problem = sensitivity_error(issued, h, cid)
        if problem:
            raise RunRefused(problem)
    error = run_rule_error(
        snapshot.entries,
        registration,
        {
            "kind": kind,
            "hypothesis": h,
            "configuration_id": cid,
            "seed": resolved.configuration["seed"],
            "costs": resolved.configuration["costs"],
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
    try:
        check_environment(os.environ, repo)
    except PermissionError as exc:
        raise RunRefused(str(exc)) from None
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
    manifest, manifest_rel = check_manifest(git, repo, manifest_path, genesis)
    check_clean_tree(git)
    head = git.text("rev-parse", "HEAD")
    check_head_on_main(git, remote, head)
    cid = request.get("configuration")
    if cid == HOLDOUT:
        return _run_holdout(ledger, snapshot, registration, request, repo, head, manifest,
                            manifest_rel, store, admitted_on)
    strategy = load_strategy(git, head, genesis, registration, cid)
    check_loaded_modules(git, repo, head)
    resolved = registration.resolve(request, strategy)
    check_rerun_commit(git, snapshot, resolved, head)
    issued = gate_ledger(ledger, snapshot) if resolved.kind == SENSITIVITY else None
    admit(snapshot, registration, resolved, issued)

    opened = ledger.append(
        {
            "type": OPEN,
            "kind": resolved.kind,
            "hypothesis": resolved.hypothesis,
            "configuration_id": resolved.configuration_id,
            "configuration": resolved.configuration,
            "configuration_sha256": resolved.configuration_sha256,
            "commit": head,
            "trees": tree_digests(git, head, genesis.body["tree_paths"]),
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
                "git": git_version(),
            },
        },
        expected_head=admitted_on,
    )

    error = None
    files: dict[str, bytes] = {}
    pin = genesis.body["strategies"][cid]
    crossed: list[list] = []
    try:
        output = _run_strategy(
            git, head, pin, genesis.body["tree_paths"][resolved.hypothesis], manifest_rel,
            resolved.configuration, opened.hash, store,
        )
        pairs, summary = _summary(output.returns, resolved.window)
        crossed = crossed_gaps(output.crossed_gaps, [manifest])
        data = encode_returns(pairs)
        digest = sha256_hex(data)
        files[returns_path(digest)] = data
        status = COMPLETED
    except Exception as exc:  # a failed run is still a ledgered run
        error = public_error(exc, repo, store)
        status, summary, digest, crossed = FAILED, {"error": error}, None, []
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
            "crossed_gaps": crossed,
        },
        files,
        require_in_step=False,
        attempts=3,
    )
    return RunRecord(status, opened, closed, error)


# --- the holdout run (G1.6; section 11, items 5 and 6) ---------------------------------

HOLDOUT_REQUEST_KEYS = frozenset({"configuration", "candidate_sha256"})
# The rehearsal (``seal rehearse``): the last ten development months, 2025-03 to 2025-12,
# stand in for the holdout's ten, so the holdout run's loading path crosses a boundary
# on development data only. Its token names no ledger entry.
REHEARSAL_BOUNDARY = "2025-03"
REHEARSAL_TOKEN = hashlib.sha256(b"norse-research holdout rehearsal, no ledger entry").hexdigest()
WITHHELD = (
    "the unsealed run failed; its message is withheld because it may carry holdout values "
    "(security review L7)"
)


class HoldoutCheckFailed(ValueError):
    """A check the harness makes on a holdout run; its message is the harness's own and
    holds no value computed from holdout data."""


def _holdout_failure(exc: BaseException, unsealed: bool, repo: Path, store) -> str:
    """A failed holdout attempt's error as the public ledger records it. Once the
    strategy has been given the unseal, a failure's text is the strategy's and may carry
    holdout values, so only its type is published, unless the harness or the ledger
    raised it with a message of its own (security review L7)."""
    if unsealed and not isinstance(exc, HoldoutCheckFailed | LedgerError):
        name = type(exc).__name__
        if isinstance(exc, StrategyFailed):  # the runner reports "<type>: <message>"
            reported = str(exc).split(":", 1)[0]
            name = reported if reported.isidentifier() else name
        return f"{name}: {WITHHELD}"
    return public_error(exc, repo, store)


def _close_abandoned_attempts(ledger: GitLedger) -> None:
    """Close, as abandoned, every attempt a killed run left open. This run holds the
    clone's holdout lock, so none is in flight here; one in flight on another clone is
    ended by it and cannot then complete (security review M1)."""
    entries = ledger.snapshot(ledger.local_head()).entries
    closed = {e.body["attempt"] for e in entries if e.type == ATTEMPT_CLOSE}
    for attempt in (e for e in entries if e.type == ATTEMPT and e.hash not in closed):
        ledger.append(
            {"type": ATTEMPT_CLOSE, "attempt": attempt.hash, "attempt_seq": attempt.seq,
             "outcome": ABANDONED},
            expected_head=ledger.local_head(),
        )


def _holdout_summary(
    returned: list, window: Window, committed: list[tuple[str, float]]
) -> tuple[list[tuple[str, float]], dict]:
    """The holdout run's series, checked: one return per day of its window, and its
    development part exactly the candidate's committed series (item 5). No statistic:
    HOLD-1 reads the committed series later (G3.2)."""
    pairs = [(str(day), float(value)) for day, value in returned]
    if [d for d, _ in pairs] != window.day_list():
        raise HoldoutCheckFailed(
            "the holdout run's returns are not one per day from the start of the development "
            "window through the holdout's end"
        )
    if pairs[: len(committed)] != committed:
        raise HoldoutCheckFailed(
            "its development returns differ from the candidate's committed series"
        )
    return pairs, {
        "days": len(pairs),
        "first_day": pairs[0][0],
        "last_day": pairs[-1][0],
        "covers_window": True,
        "development_days": len(committed),
        "holdout_days": len(pairs) - len(committed),
        "development_reproduced": True,
    }


def _run_holdout(
    ledger: GitLedger,
    snapshot: LedgerSnapshot,
    registration: Registration,
    request: Mapping[str, Any],
    repo: Path,
    head: str,
    manifest: Manifest,
    manifest_rel: str,
    store: ArchiveStore | None,
    admitted_on: str | None,
) -> RunRecord:
    """The one holdout run, or a ledgered attempt at it, holding the clone's holdout
    lock throughout (security review M1). See the module docstring."""
    with ExitStack() as stack:
        try:
            stack.enter_context(ledger.holdout_lock())
        except LedgerBusy as exc:
            raise RunRefused(str(exc)) from None
        return _run_holdout_locked(ledger, snapshot, registration, request, repo, head,
                                   manifest, manifest_rel, store, admitted_on)


def _run_holdout_locked(
    ledger: GitLedger,
    snapshot: LedgerSnapshot,
    registration: Registration,
    request: Mapping[str, Any],
    repo: Path,
    head: str,
    manifest: Manifest,
    manifest_rel: str,
    store: ArchiveStore | None,
    admitted_on: str | None,
) -> RunRecord:
    git = ledger.git
    genesis = snapshot.genesis
    if set(request) != HOLDOUT_REQUEST_KEYS:
        raise RunNotRegistered(f"a holdout run request holds exactly {sorted(HOLDOUT_REQUEST_KEYS)}")
    unseal = unseal_of(snapshot.entries)
    if unseal is None:
        raise RunRefused("the holdout run needs the unseal entry (section 11, item 4)")
    if request["candidate_sha256"] != unseal.body["configuration_sha256"]:
        raise RunRefused(
            f"the unseal opens the holdout to configuration {unseal.body['configuration_sha256']} "
            f"({unseal.body['configuration_id']}) alone, not {request['candidate_sha256']!r} "
            "(section 11, items 4 and 6; T22)"
        )
    try:
        holdout_manifest = seal.committed_holdout_manifest(git, genesis, head)
    except seal.SealRefused as exc:
        raise UnmanifestedData(str(exc)) from None
    held = {e.hash: e for e in snapshot.entries}
    candidate = held[unseal.body["candidate"]["open"]]
    candidate_result = held[unseal.body["candidate"]["result"]]
    cid, h = candidate.body["configuration_id"], candidate.body["hypothesis"]
    strategy = load_strategy(git, head, genesis, registration, cid)
    check_loaded_modules(git, repo, head)
    if registration.resolve({"configuration": cid}, strategy).configuration != (
        candidate.body["configuration"]
    ):
        raise RunRefused(f"{cid}'s configuration does not resolve at HEAD as the candidate's did")
    paths = genesis.body["tree_paths"]
    if head != unseal.body["commit"]:
        raise RunRefused(
            f"every holdout run is at the unseal's commit {unseal.body['commit']}, where item 6's "
            f"rule was applied; HEAD is {head} (security review round 2, N1)"
        )
    resolved = registration.resolve_holdout(candidate.body, genesis.body["manifests"]["holdout"])
    error = run_rule_error(
        snapshot.entries,
        registration,
        {
            "kind": resolved.kind,
            "hypothesis": h,
            "configuration_id": cid,
            "seed": resolved.configuration["seed"],
            "costs": resolved.configuration["costs"],
            "rerun": None,
            "reserve_reason": None,
            "candidate_sha256": resolved.configuration["candidate_sha256"],
            "commit": head,
        },
    )
    if error:
        raise RunRefused(error)
    if store is None:
        raise RunRefused("the holdout run reads the dataset store; none was given")
    try:
        seal.verify_holdout_files(holdout_manifest, store)
    except seal.SealRefused as exc:
        raise RunRefused(str(exc)) from None

    opened = ledger.append(
        {
            "type": OPEN,
            "kind": resolved.kind,
            "hypothesis": h,
            "configuration_id": cid,
            "configuration": resolved.configuration,
            "configuration_sha256": resolved.configuration_sha256,
            "commit": head,
            "trees": tree_digests(git, head, paths),
            "manifest_sha256": manifest.sha256,
            "cost_basis": resolved.cost_basis,
            "window": resolved.window.as_dict(),
            "label": None,
            "rerun": None,
            "reserve_reason": None,
            "strategy": strategy.module,
            "environment": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": sys.platform,
                "git": git_version(),
            },
        },
        expected_head=admitted_on,
    )

    error = None
    files: dict[str, bytes] = {}
    pin = genesis.body["strategies"][cid]
    unsealed = False
    crossed: list[list] = []
    try:
        seal.require_unseal_on_remote(ledger, unseal.hash, opened.hash)
        committed = [(d, float(v)) for d, v in snapshot.returns_of(candidate_result)]
        reproduced = _run_strategy(
            git, head, pin, paths[h], manifest_rel, candidate.body["configuration"],
            opened.hash, store,
        ).returns
        if [(str(d), float(v)) for d, v in reproduced] != committed:
            raise HoldoutCheckFailed(
                "the development returns do not reproduce the candidate's committed series, so "
                "no holdout return was computed (section 11, item 5)"
            )
        # The attempt bracket (security review H2): written before the strategy is first
        # given the unseal, closed after it with the outcome. A process killed in between
        # leaves it open, and the next run closes it as abandoned.
        _close_abandoned_attempts(ledger)
        attempt = ledger.append(
            {"type": ATTEMPT, "opens": opened.hash, "open_seq": opened.seq, "commit": head},
            expected_head=ledger.local_head(),
        )
        unsealed = True
        outcome = FAILED
        try:
            output = _run_strategy(
                git, head, pin, paths[h], manifest_rel, resolved.configuration, opened.hash,
                store, unsealed=(seal.HOLDOUT_MANIFEST, unseal.hash),
            )
            outcome = RETURNED
        finally:
            ledger.append(
                {"type": ATTEMPT_CLOSE, "attempt": attempt.hash, "attempt_seq": attempt.seq,
                 "outcome": outcome},
                require_in_step=False,
                attempts=3,
            )
        pairs, summary = _holdout_summary(output.returns, resolved.window, committed)
        crossed = crossed_gaps(output.crossed_gaps, [manifest, holdout_manifest])
        data = encode_returns(pairs)
        digest = sha256_hex(data)
        files[returns_path(digest)] = data
        status = COMPLETED
    except Exception as exc:  # a failed attempt is still a ledgered attempt (item 6)
        error = _holdout_failure(exc, unsealed, repo, store)
        status, summary, digest, crossed = FAILED, {"error": error}, None, []
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
            "crossed_gaps": crossed,
        },
        files,
        require_in_step=False,
        attempts=3,
    )
    return RunRecord(status, opened, closed, error)


def rehearse_holdout(
    repo: Path,
    *,
    store: ArchiveStore | None,
    remote: str = "origin",
    manifest_path: str | Path = DEVELOPMENT_MANIFEST,
    boundary: str = REHEARSAL_BOUNDARY,
) -> dict:
    """``seal rehearse``: the holdout run's path end to end at HEAD, on development data
    only, writing nothing to the ledger. It takes the checks the unseal and the holdout
    run take (item 6's rule between the candidate's commit and HEAD among them), then
    runs the holdout candidate's strategy with the development manifest split at
    ``boundary``, its later months standing in for the holdout, through the unsealed
    loading path, and requires the candidate's committed series exactly. Any refusal or
    failure raises, so a data loading or seal fix is found and committed before the
    unseal, after which the code cannot change (security review round 2, N1)."""
    repo = Path(repo)
    try:
        check_environment(os.environ, repo)
    except PermissionError as exc:
        raise RunRefused(str(exc)) from None
    ledger = GitLedger(repo, remote)
    git = ledger.git
    verified = ledger.verify(ledger.require_in_step())
    genesis, registration = verified.snapshot.genesis, verified.registration
    if genesis is None or registration is None:
        raise RunRefused("the ledger has no genesis entry, so there is nothing to rehearse")
    check_periods(genesis, registration)
    manifest, manifest_rel = check_manifest(git, repo, manifest_path, genesis)
    check_clean_tree(git)
    head = git.text("rev-parse", "HEAD")
    check_head_on_main(git, remote, head)
    issued = gate_ledger(ledger, verified.snapshot)
    candidate = holdout_candidate(issued)
    if candidate is None:
        raise RunRefused(
            "no hypothesis has passed development with a selectable run of every registered "
            "configuration: no holdout candidate, so no unseal and nothing to rehearse"
        )
    cid, h = candidate.configuration_id, candidate.hypothesis
    strategy = load_strategy(git, head, genesis, registration, cid)
    check_loaded_modules(git, repo, head)
    if registration.resolve({"configuration": cid}, strategy).configuration != (
        candidate.open_entry.body["configuration"]
    ):
        raise RunRefused(f"{cid}'s configuration does not resolve at HEAD as the candidate's did")
    paths = genesis.body["tree_paths"]
    problem = holdout_commit_error(
        git, candidate.open_entry.body["commit"], head, [*paths["harness"], *paths[h]]
    )
    if problem:
        raise RunRefused(f"the unseal would refuse this commit: {problem}")
    try:
        seal.committed_holdout_manifest(git, genesis, head)
    except seal.SealRefused as exc:
        raise UnmanifestedData(str(exc)) from None
    if store is None:
        raise RunRefused("the rehearsal reads the dataset store; none was given")
    committed = [(d, float(v)) for d, v in issued.snapshot.returns_of(candidate.result_entry)]
    try:
        returned = _run_strategy(
            git, head, genesis.body["strategies"][cid], paths[h], manifest_rel,
            candidate.open_entry.body["configuration"], REHEARSAL_TOKEN, store,
            rehearsal=boundary,
        ).returns
    except StrategyFailed as exc:
        raise RunRefused(
            f"the rehearsal failed ({public_error(exc, repo, store)}); fix data loading or the "
            "seal and commit the fix before the unseal"
        ) from None
    if [(str(d), float(v)) for d, v in returned] != committed:
        raise RunRefused(
            f"across the rehearsal's boundary {boundary} the candidate's returns differ from its "
            "committed series; fix data loading and commit the fix before the unseal"
        )
    return {"configuration_id": cid, "commit": head, "boundary": boundary,
            "days": len(committed), "manifest_sha256": manifest.sha256}


__all__ = [
    "ALLOWED_IMPORTS",
    "ENV_ALLOWED",
    "ENV_REFUSED",
    "RunRecord",
    "RunRefused",
    "Strategy",
    "UnmanifestedData",
    "admit",
    "disallowed_imports",
    "run",
    "strategy_declarations",
    "strategy_modules",
]
