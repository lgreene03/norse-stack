"""Temporary git repositories, a synthetic registration and synthetic strategies for
the ledger's tests (G1.5).

Nothing here touches the real repository, its ledger or its tags: every test builds
a bare "remote" and a clone under pytest's temporary directory. Returns are
synthetic numbers from a fixed generator; no market data is read. The registration
is the real docs/preregistration.json with one change, the development manifest's
SHA-256, which names a small synthetic manifest instead.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import subprocess
import sys
from pathlib import Path

from norse_research.canonical import sha256_hex
from norse_research.gitledger import GitLedger, genesis_fields, period_constants, write_genesis
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
REAL_PREREG_JSON = REPO / "docs" / "preregistration.json"
REAL_PREREG_MD = REPO / "docs" / "PREREGISTRATION.md"
MANIFEST = "research/manifests/binance-vision-development.json"
KEY_RECORD = "research/keys/holdout-key-record.json"
TREE_PATHS = {"harness": ["research/harness"], "H1": ["strategies/h1"], "H2": ["strategies/h2"]}

_counter = itertools.count()

# Every key any registered grid configuration holds, for a strategy that can run them all.
ALL_PARAMETERS = frozenset(
    k
    for h in ("H1", "H2")
    for c in json.loads(REAL_PREREG_JSON.read_text())["hypotheses"][h]["configurations"]
    for k in c
    if k not in ("id", "label")
)

STRATEGY_SOURCE = '''"""Synthetic strategies for the ledger tests: no market data."""

import dataclasses
from datetime import date, timedelta

CALLED = []
SEEN_ENV = []
TARGET = []


def _days(window):
    first = date.fromisoformat(window["first_return_day"])
    return [(first + timedelta(days=i)).isoformat() for i in range(window["days"])]


def steady(config, data):
    CALLED.append(config["id"])
    seed = sum(ord(c) for c in config["id"]) * 7919 + (config["seed"] or 0)
    drift = (seed % 13) / 13000
    x = seed
    out = []
    for day in _days(config["window"]):
        x = (1103515245 * x + 12345) % 2147483648
        out.append((day, (x / 2147483648 - 0.5) * 0.02 + drift))
    return out


def partial(config, data):
    return steady(config, data)[:100]


def outside(config, data):
    return [("2026-01-01", 0.01)]


def fails(config, data):
    CALLED.append(config["id"])
    raise RuntimeError("synthetic failure")


def opens_file(config, data):
    CALLED.append(config["id"])
    dataclasses.sys.modules["io"].open(__file__).read()
    return steady(config, data)


def writes_file(config, data):
    CALLED.append(config["id"])
    dataclasses.sys.modules["io"].open(TARGET[0], "w").write("leak")
    return steady(config, data)


def opens_socket(config, data):
    CALLED.append(config["id"])
    dataclasses.sys.modules["socket"].socket()
    return steady(config, data)


def records_env(config, data):
    SEEN_ENV.append(dict(dataclasses.sys.modules["os"].environ))
    return steady(config, data)


def exits(config, data):
    CALLED.append(config["id"])
    dataclasses.sys.modules["os"]._exit(137)


def loads(config, data):
    from datetime import datetime, timezone

    utc = timezone.utc
    rows = data.load("spot/klines/XUSDT/1d", datetime(2025, 12, 1, tzinfo=utc),
                     datetime(2025, 12, 5, tzinfo=utc)).rows
    CALLED.append(len(rows))
    return steady(config, data)
'''


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


class World:
    """A bare remote, a working clone with a committed registration, manifest,
    key record and strategy module, ``prereg-v1`` on the remote, and (unless
    ``genesis=False``) a genesis entry."""

    def __init__(self, tmp: Path, genesis: bool = True) -> None:
        self.tmp = tmp
        self.remote = tmp / "remote.git"
        self.repo = tmp / "work"
        git(tmp, "init", "--quiet", "--bare", "-b", "main", str(self.remote))
        git(tmp, "clone", "--quiet", str(self.remote), str(self.repo))
        self.store = ArchiveStore(tmp / "mirror")
        series = [make_series(self.store, "spot/klines/XUSDT/1d", "2025-11", "2025-12")]
        manifest = dumps(build(self.store, DEVELOPMENT, series))
        self.manifest_sha256 = sha256_hex(manifest)
        self.write(".gitignore", "data/\n*.py[cod]\n__pycache__/\n.env\n")
        self.write("docs/PREREGISTRATION.md", REAL_PREREG_MD.read_bytes())
        self.write("docs/preregistration.json", synthetic_registration_bytes(self.manifest_sha256))
        self.write(MANIFEST, manifest)
        self.write(KEY_RECORD, '{"kdf": "scrypt", "synthetic": true}\n')
        self.module_name = f"synthetic_strategy_{next(_counter)}"
        self.module_path = f"strategies/h1/{self.module_name}.py"
        self.write(self.module_path, STRATEGY_SOURCE)
        self.write("research/harness/README", "harness paths\n")
        self.write("strategies/h2/README", "H2 strategy paths\n")
        self.commit("code, registration and manifest")
        self.push()
        git(self.repo, "tag", "-a", "prereg-v1", "-m", "synthetic registration")
        git(self.repo, "push", "--quiet", "origin", "refs/tags/prereg-v1")
        self.prereg_commit = git(self.repo, "rev-parse", "HEAD")
        self.ledger = GitLedger(self.repo)
        spec = importlib.util.spec_from_file_location(
            self.module_name, self.repo / self.module_path
        )
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[self.module_name] = self.module
        spec.loader.exec_module(self.module)
        if genesis:
            self.write_genesis()

    def write_genesis(self) -> Entry:
        return write_genesis(
            self.ledger, tree_paths=TREE_PATHS, key_record=(self.prereg_commit, KEY_RECORD)
        )

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

    def strategy(self, function: str = "steady", parameters=ALL_PARAMETERS, defaults=None):
        return Strategy(
            module=self.module_name,
            run=getattr(self.module, function),
            parameters=frozenset(parameters),
            defaults=dict(defaults or {}),
        )

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

    def close(self) -> None:
        sys.modules.pop(self.module_name, None)


# --- a ledger built in memory, for the pure tests --------------------------------------

FAKE_COMMIT = "c" * 40


class Book:
    """Entries chained in memory against the real registration's windows."""

    def __init__(self, registration_bytes: bytes | None = None, tree_paths=None) -> None:
        data = registration_bytes or synthetic_registration_bytes("d" * 64)
        self.registration = Registration.from_bytes(data)
        self.entries: list[Entry] = []
        self.files: dict[str, bytes] = {}
        self.tree_paths = tree_paths or TREE_PATHS
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
        }

    def add(self, body: dict, time: str = "2026-11-20T00:00:00Z") -> Entry:
        last = self.entries[-1] if self.entries else None
        full = {**body, "seq": len(self.entries), "prev": last.hash if last else "0" * 64,
                "time": time}
        entry = make_entry(full)
        self.entries.append(entry)
        return entry

    def open(self, cid: str, **kwargs) -> Entry:
        return self.add(open_body(self.registration, cid, tree_paths=self.tree_paths, **kwargs))

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


def open_body(registration: Registration, cid: str, *, seed=None, cost=None, rerun=None,
              reserve_reason=None, trees=None, strategy_defaults=None, label=None,
              commit: str = FAKE_COMMIT, manifest_sha256: str = "d" * 64,
              tree_paths=None) -> dict:
    """An open entry's body as the harness would write it, without running anything."""
    extra = {"seed": seed, "sensitivity_total_cost_bps_per_side": cost, "rerun": rerun,
             "reserve_reason": reserve_reason, "label": label}
    request = {"configuration": cid, **{k: v for k, v in extra.items() if v is not None}}
    strategy = Strategy("synthetic", lambda c, d: [], ALL_PARAMETERS, strategy_defaults or {})
    resolved = registration.resolve(request, strategy)
    paths = [p for group in (tree_paths or TREE_PATHS).values() for p in group]
    return {
        "type": OPEN,
        "kind": resolved.kind,
        "hypothesis": resolved.hypothesis,
        "configuration_id": resolved.configuration_id,
        "configuration": resolved.configuration,
        "configuration_sha256": resolved.configuration_sha256,
        "commit": commit,
        "trees": trees or {p: "1" * 40 for p in sorted(paths)},
        "manifest_sha256": manifest_sha256,
        "cost_basis": resolved.cost_basis,
        "window": resolved.window.as_dict(),
        "label": resolved.label,
        "rerun": resolved.rerun,
        "reserve_reason": resolved.reserve_reason,
        "strategy": "synthetic",
        "environment": {"python": "3.12.0", "implementation": "CPython", "platform": "linux"},
    }


def lcg_returns(seed: int, n: int, drift: float = 0.0, scale: float = 0.02) -> list[float]:
    """Deterministic synthetic daily returns."""
    x = seed
    out = []
    for _ in range(n):
        x = (1103515245 * x + 12345) % 2147483648
        out.append((x / 2147483648 - 0.5) * scale + drift)
    return out
