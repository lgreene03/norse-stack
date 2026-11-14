"""The harness: every run is ledgered before it computes, or refused (G1.5).

Each test builds a temporary bare remote and clone (ledger_fixtures.World); the
strategies are synthetic and read no market data. Threat model T1, T2, T3, T8, T11,
T12, T13, T15, T17, T18 and T24, and section 15, item 11 of PREREGISTRATION.md.
"""

import ast
import json
import os
import py_compile
import socket  # noqa: F401  (imported so a strategy can reach it through sys.modules)
import subprocess
import sys
import time
from datetime import UTC, datetime

import pytest
from ledger_fixtures import RESEARCH, Book, git, lcg_returns
from norse_research import gitledger, harness, ledger
from norse_research.gate import RegisteredLedger
from norse_research.gitledger import GitLedger, LedgerWriteError
from norse_research.harness import RunRefused, UnmanifestedData, admit, disallowed_imports
from norse_research.ledger import LEDGER_PATH, parse_ledger
from norse_research.loader import DatasetLoader, OutOfSampleUnledgered, inside_ledger_entry
from norse_research.manifest import Manifest
from norse_research.registration import RunNotRegistered

N_H1 = 2802
FIX = {"defect": "synthetic defect", "section": "6"}


def request(cid="H1-01", **extra):
    return {"configuration": cid, **extra}


def run(world, cid="H1-01", function="steady", strategy=None, **extra):
    return harness.run(
        world.repo, strategy or world.strategy(function), request(cid, **extra), store=world.store
    )


def rerun_record(world):
    return {**FIX, "fix_commit": git(world.repo, "rev-parse", "HEAD")}


def remote_entries(world):
    return parse_ledger(world.remote_ledger())


def trials(entries):
    return [e for e in entries if e.type == "open" and e.body["kind"] == "trial"]


# --- T1: every run counts --------------------------------------------------------------


def test_rerun_of_an_identical_configuration_adds_a_trial(world):
    first = run(world, "H1-01")
    second = run(world, "H1-01", rerun=rerun_record(world))
    entries = remote_entries(world)
    opened = trials(entries)
    assert [e.hash for e in opened] == [first.open_entry.hash, second.open_entry.hash]
    assert opened[0].body["configuration_sha256"] == opened[1].body["configuration_sha256"]
    assert ledger.trial_count(entries) == 2
    assert opened[1].body["rerun"]["defect"] == "synthetic defect"


def test_every_run_appends_an_entry_holding_the_fields_g15_names(world):
    record = run(world, "H1-01")
    entries = remote_entries(world)
    opened, closed = entries[-2], entries[-1]
    assert (opened.hash, closed.hash) == (record.open_entry.hash, record.result_entry.hash)
    head = git(world.repo, "rev-parse", "HEAD")
    for entry, previous in ((opened, entries[-3]), (closed, opened)):
        body = entry.body
        assert body["prev"] == previous.hash
        assert body["commit"] == head
        assert body["manifest_sha256"] == world.manifest_sha256
        assert body["cost_basis"] == {"fee_bps_per_side": 10, "slippage_bps_per_side": 2}
        assert len(body["configuration_sha256"]) == 64
        assert body["time"].endswith("Z")
    summary = closed.body["result"]
    assert summary["days"] == N_H1 and summary["covers_window"] is True
    assert {"mean", "sd", "sharpe", "first_day", "last_day"} <= set(summary)
    assert closed.body["configuration_sha256"] == opened.body["configuration_sha256"]


def test_open_entry_without_a_result_counts_as_a_trial(world):
    # The run is killed inside the strategy: a real process exit, so no result entry
    # can be written. Its open entry is already on the remote and counts.
    script = f"""
import importlib.util, sys
sys.path[:0] = [{str(RESEARCH)!r}]
from norse_research import harness
spec = importlib.util.spec_from_file_location({world.module_name!r}, {str(world.repo / world.module_path)!r})
module = importlib.util.module_from_spec(spec)
sys.modules[{world.module_name!r}] = module
spec.loader.exec_module(module)
strategy = harness.Strategy({world.module_name!r}, module.exits, frozenset({sorted(world.strategy().parameters)!r}))
harness.run({str(world.repo)!r}, strategy, {{"configuration": "H1-01"}})
"""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("NORSE_", "RISK_"))}
    proc = subprocess.run([sys.executable, "-I", "-c", script], env=env, capture_output=True)
    assert proc.returncode == 137, proc.stderr.decode()
    entries = remote_entries(world)
    assert entries[-1].type == "open" and entries[-1].body["configuration_id"] == "H1-01"
    assert ledger.trial_count(entries) == 1
    # The next run starts cleanly and counts as the second trial.
    world.ledger.sync()
    run(world, "H1-01", rerun=rerun_record(world))
    assert ledger.trial_count(remote_entries(world)) == 2


# --- T2, T18: the loader's speed bump and classification by window --------------------


def test_loader_returns_out_of_sample_rows_only_inside_an_open_trial(world):
    manifest = Manifest.load(world.repo / "research/manifests/binance-vision-development.json")
    loader = DatasetLoader(manifest, world.store)
    start, end = datetime(2025, 12, 1, tzinfo=UTC), datetime(2025, 12, 5, tzinfo=UTC)
    with pytest.raises(OutOfSampleUnledgered, match="open trial ledger entry"):
        loader.load("spot/klines/XUSDT/1d", start, end)
    # Inside a ledgered run the same rows load.
    record = run(world, "H1-01", "loads")
    assert record.status == "completed"
    assert world.module.CALLED[-2:] == [4, "H1-01"]  # four rows loaded, then computed
    # Outside it again, refused again.
    with pytest.raises(OutOfSampleUnledgered):
        loader.load("spot/klines/XUSDT/1d", start, end)
    with inside_ledger_entry(record.open_entry.hash):
        assert len(loader.load("spot/klines/XUSDT/1d", start, end).rows) == 4


def test_a_run_covering_an_out_of_sample_window_is_a_trial_whatever_its_label(world):
    labels = ["in-sample", "smoke", "debug"]
    for i, label in enumerate(labels):
        extra = {"label": label}
        if i:
            extra["rerun"] = rerun_record(world)
        record = run(world, "H1-01", **extra)
        assert record.open_entry.body["kind"] == "trial"
        assert record.open_entry.body["label"] == label
        assert record.result_entry.body["result"]["covers_window"] is True
    assert ledger.trial_count(remote_entries(world)) == len(labels)
    # The label never enters the configuration hash.
    hashes = {e.body["configuration_sha256"] for e in trials(remote_entries(world))}
    assert len(hashes) == 1
    # No run produces returns outside its registered window: it fails, and counts.
    record = run(world, "H1-01", "outside", rerun=rerun_record(world))
    assert record.status == "failed" and "outside the registered window" in record.error
    assert ledger.trial_count(remote_entries(world)) == len(labels) + 1


# --- T3, T8: write-ahead ---------------------------------------------------------------


def test_open_entry_is_pushed_before_the_strategy_runs(world, monkeypatch):
    seen = []
    original = harness._call_strategy

    def watched(strategy, configuration, data, entry):
        remote = parse_ledger(world.remote_ledger())
        seen.append((remote[-1].hash, remote[-1].type, entry))
        return original(strategy, configuration, data, entry)

    monkeypatch.setattr(harness, "_call_strategy", watched)
    record = run(world, "H1-01")
    assert seen == [(record.open_entry.hash, "open", record.open_entry.hash)]
    assert world.module.CALLED == ["H1-01"]


@pytest.mark.parametrize("failing", ["hash-object", "commit-tree", "update-ref", "push"])
def test_strategy_is_not_invoked_when_the_open_entry_cannot_be_written(
    world, monkeypatch, capsys, failing
):
    before_local = world.ledger.local_head()
    before_remote = world.remote_ledger()
    original = gitledger.Git.run

    def broken(self, *args, **kwargs):
        if args and args[0] == failing:
            raise gitledger.GitError(f"git {failing} failed: injected")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(gitledger.Git, "run", broken)
    target = world.tmp / "written-by-the-strategy"
    world.module.TARGET.append(str(target))
    with pytest.raises(LedgerWriteError):
        run(world, "H1-01", "writes_file")
    assert world.module.CALLED == []
    assert capsys.readouterr().out == ""
    assert not target.exists()
    assert world.remote_ledger() == before_remote
    assert world.ledger.local_head() == before_local
    assert not list((world.repo).glob("ledger*"))


def test_a_push_the_remote_rejects_refuses_the_run(world):
    hook = world.remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'rejected by policy' >&2\nexit 1\n")
    hook.chmod(0o755)
    before = world.ledger.local_head()
    with pytest.raises(LedgerWriteError, match="could not be pushed"):
        run(world, "H1-01")
    assert world.module.CALLED == []
    assert world.ledger.local_head() == before


# --- T7: determinism -------------------------------------------------------------------


def test_identical_inputs_give_identical_result_hash(world):
    first = run(world, "H1-01")
    second = run(world, "H1-01", rerun=rerun_record(world))
    assert first.open_entry.body["commit"] == second.open_entry.body["commit"]
    assert first.open_entry.body["configuration_sha256"] == (
        second.open_entry.body["configuration_sha256"]
    )
    assert first.open_entry.body["manifest_sha256"] == second.open_entry.body["manifest_sha256"]
    assert first.result_entry.body["result_sha256"] == second.result_entry.body["result_sha256"]
    other = run(world, "H1-02")
    assert other.result_entry.body["result_sha256"] != first.result_entry.body["result_sha256"]


# --- T11: trust never reads the clock -------------------------------------------------


def _decisions(world):
    out = {}
    try:
        world.ledger.verify()
        out["verify"] = "ok"
    except ledger.VerificationError as exc:
        out["verify"] = str(exc)
    snapshot = world.ledger.verify().snapshot
    reg = gitledger.load_registration(world.ledger.git, snapshot.genesis)
    for cid, extra in (("H1-02", {}), ("H1-04", {}), ("H1-01", {}),
                       ("H1-01", {"rerun": rerun_record(world)}), ("H1-01", {"cost": 7.5})):
        req = {"configuration": cid}
        if "rerun" in extra:
            req["rerun"] = extra["rerun"]
        if "cost" in extra:
            req["sensitivity_total_cost_bps_per_side"] = extra["cost"]
        try:
            admit(snapshot, reg, reg.resolve(req, world.strategy()), None)
            out[json.dumps(req, sort_keys=True)] = "admitted"
        except RunRefused as exc:
            out[json.dumps(req, sort_keys=True)] = str(exc)
    return out


def test_trust_decisions_do_not_read_the_clock(world, monkeypatch):
    run(world, "H1-01")
    honest = _decisions(world)
    assert honest["verify"] == "ok"
    for moment in (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 12, 9, tzinfo=UTC)):
        epoch = moment.timestamp()
        with monkeypatch.context() as m:
            m.setattr(time, "time", lambda epoch=epoch: epoch)
            m.setattr(time, "time_ns", lambda epoch=epoch: int(epoch * 1e9))
            m.setattr(ledger, "utc_now", lambda moment=moment: moment.strftime(ledger.TIME_FORMAT))
            m.setenv("GIT_COMMITTER_DATE", moment.isoformat())
            m.setenv("GIT_AUTHOR_DATE", moment.isoformat())
            assert _decisions(world) == honest
    # The (informative) timestamps never go backwards, whatever the clock says.
    early = GitLedger(world.repo, clock=lambda: "2020-01-01T00:00:00Z")
    entry = early.append(_benchmark_body(world))
    assert entry.body["time"] >= remote_entries(world)[-2].body["time"]


def _benchmark_body(world):
    from ledger_fixtures import open_body
    from norse_research.registration import Registration

    reg = Registration.from_bytes((world.repo / "docs/preregistration.json").read_bytes())
    return open_body(reg, "B-H1")


# --- T12, T14, T20: only the registered, committed manifest ---------------------------


def test_harness_refuses_a_manifest_not_committed_at_head(world):
    committed = world.repo / "research/manifests/binance-vision-development.json"
    # An untracked copy, in an ignored folder so that the tree stays clean.
    copy = world.write("data/manifest.json", committed.read_bytes())
    assert git(world.repo, "status", "--porcelain") == ""
    with pytest.raises(UnmanifestedData, match="not committed at HEAD"):
        harness.run(world.repo, world.strategy(), request(), manifest_path="data/manifest.json")
    # Outside the repository altogether.
    outside = world.tmp / "elsewhere.json"
    outside.write_bytes(committed.read_bytes())
    with pytest.raises(UnmanifestedData, match="outside the repository"):
        harness.run(world.repo, world.strategy(), request(), manifest_path=outside)
    # The committed file changed in place.
    committed.write_bytes(committed.read_bytes().replace(b'"licence"', b'"licence" '))
    with pytest.raises(UnmanifestedData, match="differs from the file committed at HEAD"):
        harness.run(world.repo, world.strategy(), request())
    assert copy.exists() and world.module.CALLED == []
    assert ledger.trial_count(remote_entries(world)) == 0


def test_harness_refuses_a_manifest_other_than_the_registered_one(world):
    path = world.repo / "research/manifests/binance-vision-development.json"
    doc = json.loads(path.read_text())
    doc["datasets"]["spot/klines/XUSDT/1d"]["rows"] += 1  # rebuilt from altered files
    from norse_research.manifest import dumps

    path.write_bytes(dumps(doc))
    world.commit("a rebuilt manifest")
    world.push()
    with pytest.raises(UnmanifestedData, match="is not the development manifest the genesis"):
        run(world)
    assert world.module.CALLED == []


# --- T13: the strategy's reach ---------------------------------------------------------


def test_strategy_modules_import_only_the_allowlist(world):
    modules = harness.strategy_modules()
    assert ("norse_research.strategies", harness.STRATEGIES_DIR / "__init__.py") in modules
    for name, path in modules:
        assert disallowed_imports(path.read_text(), name, path.name == "__init__.py") == [], name
    bad = (
        "import os\nimport math\nfrom pathlib import Path\nfrom norse_research import store\n"
        "from norse_research.coverage import read_rows\nimport urllib.request\n"
        "from .. import manifest\nfrom . import h1\nx = open('f')\neval('1')\n"
    )
    problems = disallowed_imports(bad, "norse_research.strategies.example")
    assert [p.split(":")[0] for p in problems] == [
        "line 1", "line 3", "line 4", "line 5", "line 6", "line 7", "line 9", "line 10"
    ]
    # The harness parses the strategy's own module before every run.
    world.write(world.module_path, (world.repo / world.module_path).read_text()
                + "\nimport subprocess\n")
    world.commit("a strategy that imports subprocess")
    world.push()
    with pytest.raises(RunRefused, match="outside the allowlist"):
        run(world)


def test_strategy_run_cannot_open_files_or_sockets(world):
    target = world.tmp / "written-by-the-strategy"
    world.module.TARGET.append(str(target))
    for i, function in enumerate(("opens_file", "writes_file", "opens_socket")):
        extra = {"rerun": rerun_record(world)} if i else {}
        record = run(world, "H1-01", function, **extra)
        assert record.status == "failed"
        assert "StrategyIOBlocked" in record.error
        assert record.result_entry.body["result"] == {"error": record.error}
    assert not target.exists()
    assert world.module.CALLED == ["H1-01"] * 3
    entries = remote_entries(world)
    assert ledger.trial_count(entries) == 3  # every failed run is ledgered and counts
    assert [e.body["status"] for e in entries if e.type == "result"] == ["failed"] * 3
    # After the run the harness's own process is unaffected.
    assert (world.repo / ".gitignore").read_text()


# --- T15: the tree, the environment and the commit -------------------------------------


def test_modified_tracked_file_is_refused(world):
    world.write("research/harness/README", "edited, not committed\n")
    with pytest.raises(RunRefused, match="dirty"):
        run(world)
    assert world.module.CALLED == []


def test_untracked_file_is_refused(world):
    world.write("some/deep/folder/notes.txt", "untracked\n")
    with pytest.raises(RunRefused, match=r"dirty .*some/deep/folder/notes\.txt"):
        run(world)
    assert world.module.CALLED == []


def test_ignored_sourceless_module_is_refused(world):
    source = world.write("research/sourceless_helper.py", "VALUE = 1\n")
    compiled = world.repo / "research" / "sourceless_helper.pyc"
    py_compile.compile(str(source), cfile=str(compiled), doraise=True)
    source.unlink()
    assert git(world.repo, "status", "--porcelain") == ""  # ignored: *.py[cod]
    sys.path.insert(0, str(world.repo / "research"))
    try:
        import sourceless_helper  # noqa: F401

        with pytest.raises(RunRefused, match=r"sourceless_helper \(research/sourceless_helper\.py"):
            run(world)
    finally:
        sys.path.remove(str(world.repo / "research"))
        sys.modules.pop("sourceless_helper", None)
    assert world.module.CALLED == []


def test_environment_overrides_are_refused(world, monkeypatch):
    for name in ("RISK_MAX_DRAWDOWN_PCT", "NORSE_LIVE_TESTS"):
        with monkeypatch.context() as m:
            m.setenv(name, "1")
            with pytest.raises(RunRefused, match=name):
                run(world)
    assert world.module.CALLED == []
    # NORSE_DATASETS_DIR only says where the verified files are: allowed, and like
    # every other variable it never reaches the strategy.
    monkeypatch.setenv("NORSE_DATASETS_DIR", str(world.tmp / "mirror"))
    run(world, "H1-01", "records_env")
    assert world.module.SEEN_ENV == [{}]
    assert os.environ["NORSE_DATASETS_DIR"] == str(world.tmp / "mirror")


def test_run_at_a_commit_not_on_the_remote_is_refused(world):
    world.write("research/harness/README", "a local change\n")
    head = world.commit("not pushed")
    with pytest.raises(RunRefused, match=f"HEAD {head} is not on origin"):
        run(world)
    world.push()
    assert run(world).status == "completed"


# --- T17: the grid and the budget ------------------------------------------------------


def test_configuration_outside_the_registered_grid_is_refused(world):
    for cid in ("H1-09", "H3-01", "B-H3", "holdout"):
        with pytest.raises(RunNotRegistered):
            run(world, cid)
    with pytest.raises(RunNotRegistered, match="unknown request keys"):
        run(world, "H1-01", lookbacks_days=[10, 30, 90])
    with pytest.raises(RunNotRegistered, match="seed"):
        run(world, "C-H1", seed=100)  # outside the registered 0 to 99
    with pytest.raises(RunNotRegistered, match="sensitivity cost"):
        run(world, "H1-01", sensitivity_total_cost_bps_per_side=12)
    assert world.module.CALLED == []
    assert len(remote_entries(world)) == 1  # the genesis entry alone


def full_book_for(h: str, own: int, reserve: int = 0) -> Book:
    """A ledger in memory with ``own`` of a hypothesis's own entries and ``reserve``
    reserve entries used, every registered configuration run first."""
    book = Book()
    order = book.registration.order[h]
    fix = {**FIX, "fix_commit": "9" * 40}
    for i in range(own):
        cid = order[i % len(order)]
        book.open(cid, rerun=fix if i >= len(order) else None)
    for _ in range(reserve):
        book.open(order[0], rerun=fix, reserve_reason="harness defect found late")
    return book


def _admit(book: Book, cid: str, **extra):
    from ledger_fixtures import ALL_PARAMETERS
    from norse_research.harness import Strategy

    snapshot = book.snapshot()
    strategy = Strategy("synthetic", lambda c, d: [], ALL_PARAMETERS, {})
    admit(snapshot, book.registration, book.registration.resolve(request(cid, **extra), strategy),
          None)


def test_trial_beyond_the_registered_budget_is_refused():
    fix = {**FIX, "fix_commit": "9" * 40}
    _admit(full_book_for("H1", 11), "H1-04", rerun=fix)  # the twelfth: admitted
    with pytest.raises(RunRefused, match="needs a recorded reason"):
        _admit(full_book_for("H1", 12), "H1-04", rerun=fix)
    _admit(full_book_for("H1", 12, reserve=5), "H1-04", rerun=fix, reserve_reason="defect")
    with pytest.raises(RunRefused, match="budget is exhausted"):
        _admit(full_book_for("H1", 12, reserve=6), "H1-04", rerun=fix, reserve_reason="defect")
    # H2's own budget is untouched by H1's.
    _admit(full_book_for("H1", 12, reserve=6), "H2-01")


def test_reserve_trial_requires_a_recorded_reason():
    fix = {**FIX, "fix_commit": "9" * 40}
    book = full_book_for("H2", 12)
    with pytest.raises(RunRefused, match="needs a recorded reason"):
        _admit(book, "H2-03", rerun=fix)
    _admit(book, "H2-03", rerun=fix, reserve_reason="the fill rule departed from section 5")
    with pytest.raises(RunNotRegistered, match="non-empty"):
        _admit(book, "H2-03", rerun=fix, reserve_reason="  ")
    # The reserve is for reruns only, and only once the hypothesis's own are used.
    with pytest.raises(RunRefused, match="only after"):
        _admit(full_book_for("H2", 6), "H2-01", rerun=fix, reserve_reason="early")


def test_registered_order_and_rerun_records_are_enforced():
    book = Book()
    with pytest.raises(RunRefused, match=r"\['H1-01', 'H1-02'\] first"):
        _admit(book, "H1-03")
    book.open("H1-01")
    with pytest.raises(RunRefused, match="rerun records its defect"):
        _admit(book, "H1-01")
    with pytest.raises(RunRefused, match="not a rerun"):
        _admit(book, "H1-02", rerun={**FIX, "fix_commit": "9" * 40})
    with pytest.raises(RunNotRegistered, match="full commit"):
        _admit(book, "H1-01", rerun={**FIX, "fix_commit": "abc1234"})


# --- T24: no run without a genesis naming the registration ----------------------------


def test_trials_require_a_genesis_entry_naming_the_preregistration(tmp_path, monkeypatch):
    from ledger_fixtures import World

    for key in list(os.environ):
        if key.startswith(("NORSE_", "RISK_")):
            monkeypatch.delenv(key)
    bare = World(tmp_path, genesis=False)
    with pytest.raises(RunRefused, match="no genesis entry"):
        harness.run(bare.repo, bare.strategy(), request(), store=bare.store)
    # Without a genesis entry no other entry can even be appended.
    with pytest.raises(ledger.VerificationError, match="precedes the genesis entry"):
        bare.ledger.append(_benchmark_body(bare))
    # A genesis whose file hashes do not match the commit it pins is refused.
    bare.write_genesis()
    genesis = bare.ledger.snapshot(bare.ledger.local_head()).genesis
    forged = json.loads(genesis.line)
    forged["preregistration"]["files"]["docs/preregistration.json"] = "0" * 64
    with pytest.raises(ledger.VerificationError, match="does not match the genesis entry"):
        gitledger.load_registration(bare.ledger.git, ledger.make_entry(forged))
    assert bare.module.CALLED == []
    bare.close()


# --- section 15, item 11: sensitivity runs ---------------------------------------------


def _complete_grid(book: Book, h: str, best: str) -> None:
    for i, cid in enumerate(book.registration.order[h]):
        n = book.registration.windows[h].days
        book.run(cid, lcg_returns(100 + i, n, drift=0.002 if cid == best else 0.0))


def test_sensitivity_run_before_the_freeze_is_refused(world):
    with pytest.raises(RunRefused, match="only after the ledger freeze"):
        run(world, "H1-01", sensitivity_total_cost_bps_per_side=7.5)
    book = Book()
    _complete_grid(book, "H1", "H1-03")
    with pytest.raises(RunRefused, match="only after the ledger freeze"):
        _admit(book, "H1-03", sensitivity_total_cost_bps_per_side=15)
    assert world.module.CALLED == []


def test_sensitivity_run_of_a_non_candidate_is_refused():
    from ledger_fixtures import ALL_PARAMETERS
    from norse_research.harness import Strategy

    book = Book()
    _complete_grid(book, "H1", "H1-03")
    frozen = RegisteredLedger(book.snapshot(), book.registration)
    strategy = Strategy("synthetic", lambda c, d: [], ALL_PARAMETERS, {})

    def sensitivity(cid):
        resolved = book.registration.resolve(
            request(cid, sensitivity_total_cost_bps_per_side=10), strategy
        )
        admit(book.snapshot(), book.registration, resolved, frozen)

    sensitivity("H1-03")  # the candidate: admitted after the freeze
    for cid in ("H1-01", "H1-05", "H2-01"):  # H2 has no candidate at all
        with pytest.raises(RunRefused, match="candidate"):
            sensitivity(cid)
    # After the freeze nothing else runs.
    with pytest.raises(RunRefused, match="frozen"):
        admit(book.snapshot(), book.registration,
              book.registration.resolve(request("B-H1"), strategy), frozen)


def test_a_frozen_ledger_refuses_trials_through_the_harness(world):
    run(world, "B-H1")
    world.ledger.tag_milestone(gitledger.FREEZE_TAG, "M2: the ledger freeze")
    with pytest.raises(RunRefused, match="frozen"):
        run(world, "H1-01")
    with pytest.raises(RunRefused, match="not H1's candidate"):
        run(world, "H1-01", sensitivity_total_cost_bps_per_side=7.5)


# --- the harness exposes no submission surface (T26) -----------------------------------


def test_the_harness_exposes_no_network_submission_surface():
    servers = {"http.server", "socketserver", "xmlrpc.server", "asyncio", "wsgiref",
               "multiprocessing.connection", "socket"}
    package = RESEARCH / "norse_research"
    for path in sorted(package.rglob("*.py")):
        source = path.read_text()
        imported = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not imported & servers, f"{path.name} imports {imported & servers}"


def test_only_the_rest_comparison_passes_the_integrity_audit_flag():
    callers = [
        path.name
        for path in sorted((RESEARCH / "norse_research").rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.keyword) and node.arg == "integrity_audit"
    ]
    assert callers == ["datasets.py"]


def test_out_of_sample_start_is_the_registered_one():
    from norse_research.periods import OUT_OF_SAMPLE_START
    from norse_research.registration import Registration

    reg = Registration.from_bytes((RESEARCH.parent / "docs/preregistration.json").read_bytes())
    assert OUT_OF_SAMPLE_START.date().isoformat() == reg.out_of_sample_start == "2018-05-01"


def test_the_ledger_never_reaches_the_working_tree(world):
    run(world, "H1-01")
    assert not (world.repo / LEDGER_PATH).exists()
    assert git(world.repo, "status", "--porcelain") == ""
    assert git(world.repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"

