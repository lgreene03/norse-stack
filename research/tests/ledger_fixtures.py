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

For the seal's tests (G1.6) the world also holds a synthetic holdout manifest, whose
files sit in a separate "archive" folder standing in for data.binance.vision and never
in the mirror until the unseal fetches them; the registration names that manifest's
hash. The key record is derived from ``TEST_PASSPHRASE``, a passphrase that exists
only in these tests and is never the owner's; ``sign_as_owner`` signs an unseal written
by hand with the one-time key it derives (decision D27). Each world's registration is
synthetic, so its genesis names the world's own file hashes rather than the ones the code
pins (``gitledger.PREREGISTRATION_SHA256``; security review of #65, L-9).
"""

from __future__ import annotations

import functools
import hashlib
import itertools
import json
import subprocess
import sys
from pathlib import Path

from norse_research import lamport
from norse_research.binance_archive import DOWNLOAD_URL, ArchiveNotFound
from norse_research.canonical import sha256_hex
from norse_research.gitledger import (
    PREREGISTRATION_TAG,
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
    LEDGER_PATH,
    OPEN,
    RESULT,
    Entry,
    LedgerSnapshot,
    encode_returns,
    make_entry,
    parse_ledger,
    returns_path,
)
from norse_research.manifest import build, dumps
from norse_research.periods import DEVELOPMENT, HOLDOUT
from norse_research.registration import Registration
from norse_research.store import MIRROR_DIR, ArchiveStore
from synthetic_store import make_series

RESEARCH = Path(__file__).resolve().parent.parent
REPO = RESEARCH.parent
PACKAGE = RESEARCH / "norse_research"
REAL_PREREG_JSON = REPO / "docs" / "preregistration.json"
REAL_PREREG_MD = REPO / "docs" / "PREREGISTRATION.md"
MANIFEST = "research/manifests/binance-vision-development.json"
HOLDOUT_MANIFEST = "research/manifests/binance-vision-holdout.json"
KEY_RECORD = "research/keys/holdout-key-record.json"
# Exists only in these tests; never the owner's passphrase.
TEST_PASSPHRASE = "synthetic test passphrase, never the owner key"
# The committed sample record for TEST_PASSPHRASE, in the format the owner's record
# uses, so CI's secret scan reads that format on every pull request. test_seal checks
# it against a scrypt computed here with hashlib alone.
KEY_RECORD_SAMPLE = Path(__file__).resolve().parent / "fixtures" / "key-record-test-passphrase-only.json"
KEY_RECORD_TEXT = KEY_RECORD_SAMPLE.read_text()
KEY_RECORD_DOC = json.loads(KEY_RECORD_TEXT)
TREE_PATHS = {"harness": ["research/norse_research"], "H1": ["strategies/h1"],
              "H2": ["strategies/h2"]}
# A signature of the right shape that verifies against nothing, for the ledgers built in
# memory, whose replay never checks signatures (that needs git and the key record).
SHAPED_SIGNATURE = {"preimages": ["ab" * 32] * lamport.BITS,
                    "complements": ["cd" * 32] * lamport.BITS}


@functools.lru_cache(maxsize=4)
def seed_for(passphrase: str = TEST_PASSPHRASE) -> bytes:
    """The one-time signing seed ``passphrase`` derives under the test record's salt."""
    output = hashlib.scrypt(passphrase.encode(), salt=bytes.fromhex(KEY_RECORD_DOC["salt"]),
                            n=KEY_RECORD_DOC["n"], r=KEY_RECORD_DOC["r"], p=KEY_RECORD_DOC["p"],
                            dklen=KEY_RECORD_DOC["dklen"], maxmem=256 * 1024 * 1024)
    return lamport.seed_of(output)


def sign_as_owner(message: bytes, passphrase: str = TEST_PASSPHRASE) -> dict:
    """The signature the seal would make of ``message`` with ``passphrase``'s key."""
    return lamport.sign(seed_for(passphrase), message)

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


def crosses_gap(config, data):
    # Crosses the bar the gap tests leave out on 2025-12-10; the series' coverage, from
    # the manifest, is one run, since that gap is inside a pinned month.
    utc = timezone.utc
    rows = data.load("spot/klines/XUSDT/1d", datetime(2025, 12, 8, tzinfo=utc),
                     datetime(2025, 12, 13, tzinfo=utc), accept_recorded_gaps=True).rows
    if len(rows) != 4:
        raise AssertionError(f"loaded {len(rows)} rows across the gap, not 4")
    first = int(datetime(2025, 11, 1, tzinfo=utc).timestamp()) * 1000
    last = int(datetime(2025, 12, 31, tzinfo=utc).timestamp()) * 1000
    if data.coverage("spot/klines/XUSDT/1d") != [(first, last)]:
        raise AssertionError("coverage is not the manifest's one run")
    if data.list_series() != ("spot/klines/XUSDT/1d",):
        raise AssertionError("the listed series are not the manifest's")
    return run(config, data)


COUNT = [0]


def counter(config, data):
    COUNT[0] += 1  # module state: in a fresh process every run starts from 0
    return [(day, value + COUNT[0] / 1000) for day, value in run(config, data)]


def _series(config, drift):
    x = sum(ord(c) for c in config["id"]) * 7919 + 17
    out = []
    for day in _days(config["window"]):
        x = (1103515245 * x + 12345) % 2147483648
        out.append((day, (x / 2147483648 - 0.5) * 0.04 + drift))
    return out


def noise(config, data):
    return _series(config, 0.0)


def market(config, data):
    return _series(config, 0.0)


def skilled(config, data):
    # Day by day from the window's start, so a longer window keeps the same prefix.
    if config["window"]["last_return_day"] > "2025-12-31":
        utc = timezone.utc
        rows = data.load("spot/klines/XUSDT/1d", datetime(2025, 12, 30, tzinfo=utc),
                         datetime(2026, 1, 3, tzinfo=utc)).rows
        if len(rows) != 4:
            raise AssertionError(f"loaded {len(rows)} rows across the boundary, not 4")
    return _series(config, 0.002)


def drifts_late(config, data):
    out = _series(config, 0.002)
    if config["window"]["last_return_day"] > "2025-12-31":
        out[0] = (out[0][0], out[0][1] + 1.0)  # its development part no longer reproduces
    return out


def peeks(config, data):
    utc = timezone.utc
    data.load("spot/klines/XUSDT/1d", datetime(2025, 12, 30, tzinfo=utc),
              datetime(2026, 1, 3, tzinfo=utc))
    return _series(config, 0.002)


def set_order(config, data):
    # Depends on the iteration order of a set of names, so on string hashing.
    order = list({"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT",
                  "BNBUSDT", "TRXUSDT"})
    return _series(config, order.index("SOLUSDT") / 10000)


def loads_across(config, data):
    # Reads across 2025-12-01, the boundary the rehearsal tests put in development.
    utc = timezone.utc
    rows = data.load("spot/klines/XUSDT/1d", datetime(2025, 11, 29, tzinfo=utc),
                     datetime(2025, 12, 3, tzinfo=utc)).rows
    if len(rows) != 4:
        raise AssertionError(f"loaded {len(rows)} rows across the boundary, not 4")
    return _series(config, 0.002)


def leaks_late(config, data):
    if config["window"]["last_return_day"] > "2025-12-31":
        utc = timezone.utc
        rows = data.load("spot/klines/XUSDT/1d", datetime(2025, 12, 30, tzinfo=utc),
                         datetime(2026, 1, 3, tzinfo=utc)).rows
        raise RuntimeError(f"holdout close {rows[-1][4]!r} at {rows[-1][0]}")
    return _series(config, 0.002)
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


def synthetic_registration_bytes(manifest_sha256: str, holdout_sha256: str | None = None) -> bytes:
    doc = json.loads(REAL_PREREG_JSON.read_text())
    doc["data"]["development_manifest"]["sha256"] = manifest_sha256
    if holdout_sha256 is not None:
        doc["data"]["holdout_manifest"]["sha256"] = holdout_sha256
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
    registration, manifest, key record and two strategy modules, the pre-registration
    tag a genesis entry names by default on the remote, and (unless ``genesis=False``) a
    genesis entry and its anchor.

    ``behaviours`` maps a configuration id to the function genesis pins for it;
    ``configurations`` overrides which ids a module declares; ``evasive`` adds the
    functions that try to reach past the tripwire."""

    def __init__(self, tmp: Path, genesis: bool = True, configurations=None,
                 behaviours=None, evasive: bool = False, drop=()) -> None:
        tmp.mkdir(parents=True, exist_ok=True)
        self.tmp = tmp
        self.remote = tmp / "remote.git"
        self.repo = tmp / "work"
        self.target = str(tmp / "written-by-the-strategy")
        git(tmp, "init", "--quiet", "--bare", "-b", "main", str(self.remote))
        git(tmp, "clone", "--quiet", str(self.remote), str(self.repo))
        self.store = ArchiveStore(tmp / "mirror")
        # ``drop`` leaves development rows out, so the manifest records a gap.
        series = [make_series(self.store, "spot/klines/XUSDT/1d", "2025-11", "2025-12", drop)]
        manifest = dumps(build(self.store, DEVELOPMENT, series))
        self.manifest_sha256 = sha256_hex(manifest)
        # The public archive, off the mirror: the holdout's files are fetched from here
        # only after the unseal.
        self.archive = ArchiveStore(tmp / "archive")
        held = [make_series(self.archive, "spot/klines/XUSDT/1d", "2026-01", "2026-02")]
        self.holdout_manifest = dumps(build(self.archive, HOLDOUT, held))
        self.holdout_sha256 = sha256_hex(self.holdout_manifest)
        registration_bytes = synthetic_registration_bytes(self.manifest_sha256, self.holdout_sha256)
        self.registration = Registration.from_bytes(registration_bytes)
        self.registration_sha256 = {"docs/PREREGISTRATION.md": sha256_hex(REAL_PREREG_MD.read_bytes()),
                                    "docs/preregistration.json": sha256_hex(registration_bytes)}
        self.ids = registered_ids(self.registration)
        n = next(_counter)
        self.paths = {"H1": f"strategies/h1/synthetic_h1_{n}.py",
                      "H2": f"strategies/h2/synthetic_h2_{n}.py"}
        self.write(".gitignore", "data/\n*.py[cod]\n__pycache__/\n.env\n")
        self.write("docs/PREREGISTRATION.md", REAL_PREREG_MD.read_bytes())
        self.write("docs/preregistration.json", registration_bytes)
        self.write(MANIFEST, manifest)
        self.write(HOLDOUT_MANIFEST, self.holdout_manifest)
        self.write(KEY_RECORD, KEY_RECORD_TEXT)
        for source in sorted(PACKAGE.rglob("*.py")):
            if "__pycache__" not in source.parts:
                self.write(f"research/norse_research/{source.relative_to(PACKAGE).as_posix()}",
                           source.read_bytes())
        for h, path in self.paths.items():
            self.write(path, self.source(h, configurations, evasive=evasive))
        self.commit("code, registration and manifest")
        self.push()
        git(self.repo, "tag", "-a", PREREGISTRATION_TAG, "-m", "synthetic registration")
        git(self.repo, "push", "--quiet", "origin", f"refs/tags/{PREREGISTRATION_TAG}")
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
                     "key_record": (self.prereg_commit, KEY_RECORD),
                     "registration_sha256": self.registration_sha256, **overrides}
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

    # --- for the seal's tests (G1.6) ---------------------------------------------------

    def returns_of(self, cid: str, configuration: dict) -> list[tuple[str, float]]:
        """What the pinned function computes for ``configuration``, computed here from
        the same committed source, so a run by hand matches a run through the harness."""
        namespace: dict = {}
        exec(compile(self.source(self.ids[cid]), "<synthetic strategy>", "exec"), namespace)
        function = self.pins[cid]["function"]
        if function in ("skilled", "drifts_late", "peeks", "leaks_late", "loads_across"):
            series = namespace["_series"](configuration, 0.002)
        else:
            series = namespace[function](configuration, None)
        return [(day, float(value)) for day, value in series]

    def runs_by_hand(self, cids, fail=()) -> list[tuple[Entry, Entry]]:
        """Open and result entries for ``cids``, exactly as the harness writes them, in
        one ledger commit by plumbing, for speed. A run in ``fail`` has a failed result."""
        head = self.ledger.local_head()
        data = self.ledger.git.blob(head, LEDGER_PATH)
        entries = list(parse_ledger(data))
        when = entries[-1].body["time"]
        files: dict[str, bytes] = {}

        def add(body: dict) -> Entry:
            last = entries[-1]
            entry = make_entry({**body, "seq": len(entries), "prev": last.hash, "time": when})
            entries.append(entry)
            return entry

        pairs = []
        for cid in cids:
            opened = add(self.open_body(cid))
            common = {"type": RESULT, "opens": opened.hash, "open_seq": opened.seq,
                      **{k: opened.body[k] for k in ("configuration_sha256", "commit",
                                                     "manifest_sha256", "cost_basis")}}
            if cid in fail:
                closed = add({**common, "status": FAILED, "result": {"error": "synthetic"},
                              "result_sha256": None, "returns_path": None,
                              "crossed_gaps": []})
            else:
                series = encode_returns(self.returns_of(cid, opened.body["configuration"]))
                digest = sha256_hex(series)
                files[returns_path(digest)] = series
                closed = add({**common, "status": COMPLETED, "result": {"synthetic": True},
                              "result_sha256": digest, "returns_path": returns_path(digest),
                              "crossed_gaps": []})
            pairs.append((opened, closed))
        new = "".join(e.line + "\n" for e in entries).encode("ascii")
        commit = self.ledger._commit(head, new, files, "runs written by hand")
        git(self.repo, "update-ref", "refs/heads/ledger", commit)
        git(self.repo, "push", "--quiet", "origin", f"{commit}:refs/heads/ledger")
        return pairs

    def fetcher(self, calls: list | None = None):
        """A fetcher serving the synthetic archive, recording each key it serves."""

        def fetch(url: str, max_bytes: int) -> bytes:
            key = url[len(DOWNLOAD_URL):]
            if calls is not None:
                calls.append(key)
            path = self.archive.root / MIRROR_DIR / key
            if not path.is_file():
                raise ArchiveNotFound(f"{url}: HTTP 404")
            return path.read_bytes()

        return fetch

    def holdout_files_in_mirror(self) -> list[str]:
        root = self.store.root / MIRROR_DIR
        return sorted(p.name for p in root.rglob("*-2026-*")) if root.exists() else []


def grid_world(tmp: Path, *, skilled: str | None = "H1-03", benchmark: bool = True,
               h2: bool = True, incomplete: bool = False, loader_changed: bool = False,
               loader_line: str = "# as the runs had it", behaviours=None) -> World:
    """A world whose ledger holds every H1 and H2 configuration's selectable run, with
    ``skilled`` the one that passes development (none if None; H2's have no edge), and
    both benchmarks' runs, which DEV-2 needs, so the unseal may come (decision D21: it
    closes development, and only once both grids are complete or their budgets spent).

    ``h2=False`` leaves H2 unrun, so an unseal would stop its grid early.
    ``incomplete`` also leaves the last H1 configuration unrun, so no hypothesis has a
    candidate. ``loader_changed`` runs the grids at a commit whose loader.py differs
    from HEAD's, so the unseal has a data loading diff to accept (security review round
    3, T1), the runs' loader.py carrying ``loader_line`` at its end; HEAD's package is
    still the code under test."""
    registration = Registration.from_bytes(REAL_PREREG_JSON.read_bytes())
    pinned = {cid: "noise" for h in ("H1", "H2") for cid in registration.order[h]}
    pinned["B-H1"] = "market"
    if skilled is not None:
        pinned[skilled] = "skilled"
    world = World(tmp, behaviours={**pinned, **(behaviours or {})})
    loader = "research/norse_research/loader.py"
    if loader_changed:
        world.write(loader, (PACKAGE / "loader.py").read_text() + f"\n{loader_line}\n")
        world.commit("loader: the data loading the runs used")
        world.push()
    order = list(registration.order["H1"][:-1] if incomplete else registration.order["H1"])
    if h2 and not incomplete:
        order += registration.order["H2"]
    world.runs_by_hand([*order, *(["B-H1", "B-H2"] if benchmark else [])])
    if loader_changed:
        world.write(loader, (PACKAGE / "loader.py").read_bytes())
        world.commit("loader: a data loading fix after the runs")
        world.push()
    return world


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
                             "result_sha256": None, "returns_path": None,
                              "crossed_gaps": []})
        h = opened.body["hypothesis"]
        days = days or self.registration.windows[h].day_list()
        data = encode_returns(list(zip(days, values, strict=True)))
        digest = sha256_hex(data)
        self.files[returns_path(digest)] = data
        return self.add({**common, "status": COMPLETED, "result": {"days": len(values)},
                         "result_sha256": digest, "returns_path": returns_path(digest),
                              "crossed_gaps": []})

    def run(self, cid: str, values=None, **kwargs) -> tuple[Entry, Entry]:
        opened = self.open(cid, **kwargs)
        return opened, self.result(opened, values)

    @property
    def data(self) -> bytes:
        return "".join(e.line + "\n" for e in self.entries).encode()

    def snapshot(self) -> LedgerSnapshot:
        return LedgerSnapshot.from_bytes(self.data, self.files)


def issued(book: Book, closed: bool = False):
    """The gate's ledger for an in-memory book, issued as gate_ledger would; ``closed``
    as it is once the unseal entry exists (decision D21)."""
    from norse_research import gate

    return gate._issue(book.snapshot(), book.registration, None, closed)


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
