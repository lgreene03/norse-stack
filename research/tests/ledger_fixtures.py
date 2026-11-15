"""Temporary git repositories, a synthetic registration and synthetic strategies for
the ledger's tests (G1.5).

Nothing here touches the real repository, its ledger or its tags: every test builds
a bare "remote" and a clone under pytest's temporary directory, and the genesis entry
pins that bare remote. The clone holds a committed copy of ``research/norse_research``,
byte for byte the code under test, because the harness refuses to run code that is
not the code at HEAD. Returns are synthetic numbers from a fixed generator; no market
data is read. The registration is the real docs/preregistration.json with one change,
the development manifest's SHA-256, which names a small synthetic manifest instead.

A strategy runs in a fresh interpreter, so tests choose its behaviour by which
function the genesis entry pins for a configuration, never by changing module state.
"""

from __future__ import annotations

import itertools
import json
import subprocess
import sys
from pathlib import Path

from norse_research.canonical import sha256_hex
from norse_research.gitledger import (
    GitLedger,
    genesis_fields,
    period_constants,
    registered_ids,
    write_genesis,
)
from norse_research.harness import Strategy
from norse_research.ledger import (
    COMPLETED,
    FAILED,
    FORMAT,
    GENESIS,
    OPEN,
    RESULT,
    Entry,
    LedgerSnapshot,
    encode_returns,
    make_entry,
    returns_path,
)
from norse_research.manifest import build, dumps
from norse_research.periods import DEVELOPMENT
from norse_research.registration import Registration
from norse_research.store import ArchiveStore
from synthetic_store import make_series

RESEARCH = Path(__file__).resolve().parent.parent
REPO = RESEARCH.parent
PACKAGE = RESEARCH / "norse_research"
REAL_PREREG_JSON = REPO / "docs" / "preregistration.json"
REAL_PREREG_MD = REPO / "docs" / "PREREGISTRATION.md"
MANIFEST = "research/manifests/binance-vision-development.json"
KEY_RECORD = "research/keys/holdout-key-record.json"
KEY_RECORD_DOC = {
    "kdf": "scrypt", "n": 2**17, "r": 8, "p": 1, "dklen": 32, "salt": "ab" * 16,
    "derived_key": "cd" * 32,
}
KEY_RECORD_TEXT = json.dumps(KEY_RECORD_DOC) + "\n"
TREE_PATHS = {"harness": ["research/norse_research"], "H1": ["strategies/h1"],
              "H2": ["strategies/h2"]}

_counter = itertools.count()

# Every key any registered grid configuration holds.
ALL_PARAMETERS = frozenset(
    k
    for h in ("H1", "H2")
    for c in json.loads(REAL_PREREG_JSON.read_text())["hypotheses"][h]["configurations"]
    for k in c
    if k not in ("id", "label")
)

# Functions any synthetic strategy module holds. All pass the source checks.
CLEAN_FUNCTIONS = '''

def _days(window):
    first = date.fromisoformat(window["first_return_day"])
    return [(first + timedelta(days=i)).isoformat() for i in range(window["days"])]


def run(config, data):
    seed = sum(ord(c) for c in config["id"]) * 7919 + (config["seed"] or 0)
    drift = (seed % 13) / 13000
    x = seed
    out = []
    for day in _days(config["window"]):
        x = (1103515245 * x + 12345) % 2147483648
        out.append((day, (x / 2147483648 - 0.5) * 0.02 + drift))
    return out


def outside(config, data):
    return [("2026-01-01", 0.01)]


def fails(config, data):
    raise RuntimeError(
        "synthetic failure reading /Users/someone/secret/path.csv and "
        "/Volumes/Mac 4T/norse/datasets/spot/x.zip"
    )


def loads(config, data):
    utc = timezone.utc
    rows = data.load("spot/klines/XUSDT/1d", datetime(2025, 12, 1, tzinfo=utc),
                     datetime(2025, 12, 5, tzinfo=utc)).rows
    if len(rows) != 4:
        raise AssertionError(f"loaded {len(rows)} rows, not 4")
    return run(config, data)


def spins(config, data):
    while True:
        pass


COUNT = [0]


def counter(config, data):
    COUNT[0] += 1  # module state: in a fresh process every run starts from 0
    return [(day, value + COUNT[0] / 1000) for day, value in run(config, data)]
'''

# Functions that reach past the source checks on purpose; a module holding them fails
# the checks, so the tests that use them turn the checks off to test the tripwire.
EVASIVE_FUNCTIONS = '''

def opens_file(config, data):
    dataclasses.sys.modules["io"].open(TARGET + ".read").read()
    return run(config, data)


def writes_file(config, data):
    dataclasses.sys.modules["io"].open(TARGET, "w").write("leak")
    return run(config, data)


def opens_socket(config, data):
    dataclasses.sys.modules["socket"].socket()
    return run(config, data)


def records_env(config, data):
    environ = dataclasses.sys.modules["os"].environ
    overrides = sorted(k for k in environ if k.startswith(("NORSE", "RISK", "GIT", "PYTHON")))
    raise RuntimeError(f"environment {len(environ)} {overrides!r}")


def exits(config, data):
    dataclasses.sys.modules["os"]._exit(137)


def flips_flag(config, data):
    for module in list(dataclasses.sys.modules.values()):
        for name in ("_HARNESS_IO", "_STRATEGY_RUNNING"):
            if hasattr(module, name):
                setattr(module, name, None)
    dataclasses.sys.modules["io"].open(TARGET + ".read").read()
    return run(config, data)
'''


def strategy_source(configurations, defaults=None, parameters=ALL_PARAMETERS,
                    evasive: bool = False, target: str = "", top_level: str = "") -> str:
    header = (
        '"""Synthetic strategies for the ledger tests: no market data."""\n\n'
        + ("import dataclasses\n" if evasive else "")
        + "from datetime import date, datetime, timedelta, timezone\n\n"
        + top_level
        + f"CONFIGURATIONS = {sorted(configurations)!r}\n"
        + f"PARAMETERS = {sorted(parameters | set(defaults or {}))!r}\n"
        + f"DEFAULTS = {dict(sorted((defaults or {}).items()))!r}\n"
        + (f"TARGET = {target!r}\n" if evasive else "")
    )
    return header + CLEAN_FUNCTIONS + (EVASIVE_FUNCTIONS if evasive else "")


def git(cwd: Path, *args: str, input: bytes | None = None) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, input=input, capture_output=True, check=False
    )
    if proc.returncode:
        raise AssertionError(f"git {' '.join(args)}: {proc.stderr.decode()}")
    return proc.stdout.decode().strip()


def synthetic_registration_bytes(manifest_sha256: str) -> bytes:
    doc = json.loads(REAL_PREREG_JSON.read_text())
    doc["data"]["development_manifest"]["sha256"] = manifest_sha256
    return (json.dumps(doc, indent=2) + "\n").encode()


def pins_for(registration: Registration, paths: dict[str, str], behaviours=None) -> dict:
    """One pinned module per hypothesis, and a function per registered id: ``run``
    unless ``behaviours`` names another."""
    behaviours = behaviours or {}
    return {
        cid: {"path": paths[h], "function": behaviours.get(cid, "run")}
        for cid, h in sorted(registered_ids(registration).items())
    }


class World:
    """A bare remote, a working clone with a committed copy of the package, a
    registration, manifest, key record and two strategy modules, ``prereg-v1`` on the
    remote, and (unless ``genesis=False``) a genesis entry and its anchor.

    ``behaviours`` maps a configuration id to the function genesis pins for it;
    ``configurations`` overrides which ids a module declares; ``evasive`` adds the
    functions that try to reach past the tripwire."""

    def __init__(self, tmp: Path, genesis: bool = True, configurations=None,
                 behaviours=None, evasive: bool = False) -> None:
        self.tmp = tmp
        self.remote = tmp / "remote.git"
        self.repo = tmp / "work"
        self.target = str(tmp / "written-by-the-strategy")
        git(tmp, "init", "--quiet", "--bare", "-b", "main", str(self.remote))
        git(tmp, "clone", "--quiet", str(self.remote), str(self.repo))
        self.store = ArchiveStore(tmp / "mirror")
        series = [make_series(self.store, "spot/klines/XUSDT/1d", "2025-11", "2025-12")]
        manifest = dumps(build(self.store, DEVELOPMENT, series))
        self.manifest_sha256 = sha256_hex(manifest)
        registration_bytes = synthetic_registration_bytes(self.manifest_sha256)
        self.registration = Registration.from_bytes(registration_bytes)
        self.ids = registered_ids(self.registration)
        n = next(_counter)
        self.paths = {"H1": f"strategies/h1/synthetic_h1_{n}.py",
                      "H2": f"strategies/h2/synthetic_h2_{n}.py"}
        self.write(".gitignore", "data/\n*.py[cod]\n__pycache__/\n.env\n")
        self.write("docs/PREREGISTRATION.md", REAL_PREREG_MD.read_bytes())
        self.write("docs/preregistration.json", registration_bytes)
        self.write(MANIFEST, manifest)
        self.write(KEY_RECORD, KEY_RECORD_TEXT)
        for source in sorted(PACKAGE.rglob("*.py")):
            if "__pycache__" not in source.parts:
                self.write(f"research/norse_research/{source.relative_to(PACKAGE).as_posix()}",
                           source.read_bytes())
        for h, path in self.paths.items():
            self.write(path, self.source(h, configurations, evasive=evasive))
        self.commit("code, registration and manifest")
        self.push()
        git(self.repo, "tag", "-a", "prereg-v1", "-m", "synthetic registration")
        git(self.repo, "push", "--quiet", "origin", "refs/tags/prereg-v1")
        self.prereg_commit = git(self.repo, "rev-parse", "HEAD")
        self.pins = pins_for(self.registration, self.paths, behaviours)
        self.ledger = GitLedger(self.repo)
        if genesis:
            self.write_genesis()

    def source(self, h: str, configurations=None, **kwargs) -> str:
        implements = (configurations or {}).get(h, {c for c, k in self.ids.items() if k == h})
        return strategy_source(implements, target=self.target, **kwargs)

    def pin(self, cid: str) -> str:
        return f"{self.pins[cid]['path']}:{self.pins[cid]['function']}"

    def write_genesis(self, **overrides) -> Entry:
        arguments = {"tree_paths": TREE_PATHS, "strategies": self.pins,
                     "key_record": (self.prereg_commit, KEY_RECORD), **overrides}
        return write_genesis(self.ledger, **arguments)

    def write(self, rel: str, content: str | bytes) -> Path:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            content = content.encode()
        path.write_bytes(content)
        return path

    def commit(self, message: str) -> str:
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "--quiet", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")

    def push(self) -> None:
        git(self.repo, "push", "--quiet", "origin", "HEAD:refs/heads/main")

    def clone(self, name: str) -> GitLedger:
        path = self.tmp / name
        git(self.tmp, "clone", "--quiet", str(self.remote), str(path))
        ledger = GitLedger(path)
        ledger.sync()
        return ledger

    def remote_ledger(self) -> bytes:
        return subprocess.run(
            ["git", "--git-dir", str(self.remote), "cat-file", "blob",
             "refs/heads/ledger:ledger/trials.jsonl"],
            capture_output=True, check=True,
        ).stdout

    def open_body(self, cid: str, **kwargs) -> dict:
        """An open entry's body as the harness would write it here, by hand."""
        return open_body(self.registration, cid, strategy=self.pin(cid),
                         commit=git(self.repo, "rev-parse", "HEAD"),
                         manifest_sha256=self.manifest_sha256, **kwargs)

    def close(self) -> None:
        for name, module in list(sys.modules.items()):
            location = getattr(module, "__file__", None)
            if isinstance(location, str) and location.startswith(str(self.tmp)):
                sys.modules.pop(name, None)


# --- a ledger built in memory, for the pure tests --------------------------------------

FAKE_COMMIT = "c" * 40
BOOK_PATHS = {"H1": "strategies/h1/s.py", "H2": "strategies/h2/s.py"}


class Book:
    """Entries chained in memory against the real registration's windows."""

    def __init__(self, registration_bytes: bytes | None = None, tree_paths=None) -> None:
        data = registration_bytes or synthetic_registration_bytes("d" * 64)
        self.registration = Registration.from_bytes(data)
        self.entries: list[Entry] = []
        self.files: dict[str, bytes] = {}
        self.tree_paths = tree_paths or TREE_PATHS
        self.pins = pins_for(self.registration, BOOK_PATHS)
        self.add(self.genesis_body())

    def genesis_body(self, tag: str = "prereg-v1") -> dict:
        return {
            "type": GENESIS,
            "format": FORMAT,
            "preregistration": {
                "tag": tag,
                "commit": "a" * 40,
                "files": {"docs/PREREGISTRATION.md": "b" * 64, "docs/preregistration.json": "e" * 64},
            },
            **genesis_fields(self.registration),
            "periods": period_constants(),
            "tree_paths": self.tree_paths,
            "key_record": {"commit": "a" * 40, "path": KEY_RECORD, "sha256": "f" * 64},
            "remote": {"url": "/synthetic/remote.git", "branch": "ledger", "code_branch": "main"},
            "strategies": self.pins,
        }

    def add(self, body: dict, time: str = "2026-11-20T00:00:00Z") -> Entry:
        last = self.entries[-1] if self.entries else None
        full = {**body, "seq": len(self.entries), "prev": last.hash if last else "0" * 64,
                "time": time}
        entry = make_entry(full)
        self.entries.append(entry)
        return entry

    def open(self, cid: str, **kwargs) -> Entry:
        pin = self.pins[cid]
        return self.add(open_body(self.registration, cid, strategy=f"{pin['path']}:{pin['function']}",
                                  **kwargs))

    def result(self, opened: Entry, values=None, days=None) -> Entry:
        common = {
            "type": RESULT,
            "opens": opened.hash,
            "open_seq": opened.seq,
            "configuration_sha256": opened.body["configuration_sha256"],
            "commit": opened.body["commit"],
            "manifest_sha256": opened.body["manifest_sha256"],
            "cost_basis": opened.body["cost_basis"],
        }
        if values is None:
            return self.add({**common, "status": FAILED, "result": {"error": "synthetic"},
                             "result_sha256": None, "returns_path": None})
        h = opened.body["hypothesis"]
        days = days or self.registration.windows[h].day_list()
        data = encode_returns(list(zip(days, values, strict=True)))
        digest = sha256_hex(data)
        self.files[returns_path(digest)] = data
        return self.add({**common, "status": COMPLETED, "result": {"days": len(values)},
                         "result_sha256": digest, "returns_path": returns_path(digest)})

    def run(self, cid: str, values=None, **kwargs) -> tuple[Entry, Entry]:
        opened = self.open(cid, **kwargs)
        return opened, self.result(opened, values)

    @property
    def data(self) -> bytes:
        return "".join(e.line + "\n" for e in self.entries).encode()

    def snapshot(self) -> LedgerSnapshot:
        return LedgerSnapshot.from_bytes(self.data, self.files)


def issued(book: Book, frozen: bool = False):
    """The gate's ledger for an in-memory book, issued as gate_ledger would."""
    from norse_research import gate

    return gate._issue(book.snapshot(), book.registration, None, frozen)


def open_body(registration: Registration, cid: str, *, seed=None, cost=None, rerun=None,
              reserve_reason=None, trees=None, strategy_defaults=None, label=None,
              commit: str = FAKE_COMMIT, manifest_sha256: str = "d" * 64,
              strategy: str = "synthetic", tree_paths=None) -> dict:
    """An open entry's body as the harness would write it, without running anything."""
    extra = {"seed": seed, "sensitivity_total_cost_bps_per_side": cost, "rerun": rerun,
             "reserve_reason": reserve_reason, "label": label}
    request = {"configuration": cid, **{k: v for k, v in extra.items() if v is not None}}
    declared = Strategy(strategy, None, ALL_PARAMETERS, strategy_defaults or {})
    resolved = registration.resolve(request, declared)
    return {
        "type": OPEN,
        "kind": resolved.kind,
        "hypothesis": resolved.hypothesis,
        "configuration_id": resolved.configuration_id,
        "configuration": resolved.configuration,
        "configuration_sha256": resolved.configuration_sha256,
        "commit": commit,
        "trees": trees or {group: "1" * 64 for group in ("harness", "H1", "H2")},
        "manifest_sha256": manifest_sha256,
        "cost_basis": resolved.cost_basis,
        "window": resolved.window.as_dict(),
        "label": resolved.label,
        "rerun": resolved.rerun,
        "reserve_reason": resolved.reserve_reason,
        "strategy": strategy,
        "environment": {"python": "3.12.0", "implementation": "CPython", "platform": "linux",
                        "git": "git version 2.56.1"},
    }


def lcg_returns(seed: int, n: int, drift: float = 0.0, scale: float = 0.02) -> list[float]:
    """Deterministic synthetic daily returns."""
    x = seed
    out = []
    for _ in range(n):
        x = (1103515245 * x + 12345) % 2147483648
        out.append((x / 2147483648 - 0.5) * scale + drift)
    return out
