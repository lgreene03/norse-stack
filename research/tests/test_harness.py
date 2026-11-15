"""The harness: every run is ledgered before it computes, or refused (G1.5).

Each test builds a temporary bare remote and clone (ledger_fixtures.World); the
strategies are synthetic and read no market data, and each runs in a fresh
interpreter, so a test picks a strategy's behaviour by which function genesis pins.
Threat model T1, T2, T3, T8, T11, T12, T13, T15, T17, T18 and T24, section 15, item 11
of PREREGISTRATION.md, and the security reviews' findings named beside each test.
"""

import ast
import json
import os
import py_compile
import signal
import subprocess
import sys
import time
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import clear_overrides
from ledger_fixtures import RESEARCH, Book, World, git, issued, lcg_returns, open_body
from norse_research import gate, gitledger, harness, ledger
from norse_research.environment import EnvironmentRefused
from norse_research.gitledger import (
    FREEZE_ANCHOR,
    GitLedger,
    LedgerOutOfStep,
    LedgerWriteError,
    RemoteNotPinned,
)
from norse_research.harness import RunRefused, UnmanifestedData, admit, disallowed_imports
from norse_research.ledger import LEDGER_PATH, parse_ledger
from norse_research.loader import DatasetLoader, OutOfSampleUnledgered, inside_ledger_entry
from norse_research.manifest import Manifest
from norse_research.registration import RunNotRegistered
from norse_research.rules import check_run_rules, run_of, run_rule_error

N_H1 = 2802
FIX = {"defect": "synthetic defect", "section": "6"}
_fixes = iter(range(10**6))


def request(cid="H1-01", **extra):
    return {"configuration": cid, **extra}


def run(world, cid="H1-01", **extra):
    return harness.run(world.repo, request(cid, **extra), store=world.store)


def fix(world):
    """A rerun record naming a commit newer than every run so far (review L8)."""
    world.write(f"fixes/{next(_fixes)}.txt", "a fix\n")
    commit = world.commit("a fix")
    world.push()
    return {**FIX, "fix_commit": commit}


def remote_entries(world):
    return parse_ledger(world.remote_ledger())


def trials(entries):
    return [e for e in entries if e.type == "open" and e.body["kind"] == "trial"]


def a_world(tmp_path, monkeypatch, **kwargs) -> World:
    clear_overrides(monkeypatch)
    return World(tmp_path, **kwargs)


@pytest.fixture
def calls(monkeypatch):
    """Every strategy process the harness starts, recorded."""
    started = []
    original = harness._run_strategy

    def recorded(*args, **kwargs):
        started.append(args[2]["function"])
        return original(*args, **kwargs)

    monkeypatch.setattr(harness, "_run_strategy", recorded)
    return started


@pytest.fixture
def no_source_checks(monkeypatch):
    """Turn the source checks off, to test the runner's tripwire behind them."""
    monkeypatch.setattr(harness, "check_strategy_sources", lambda *a, **k: None)
    monkeypatch.setattr(gitledger, "check_strategy_sources", lambda *a, **k: None)


# --- the fields of an entry ------------------------------------------------------------


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
    assert opened.body["strategy"] == world.pin("H1-01")
    assert set(opened.body["trees"]) == {"harness", "H1", "H2"}
    assert opened.body["environment"]["git"].startswith("git version")


# --- T1: every run counts --------------------------------------------------------------


def test_rerun_of_an_identical_configuration_adds_a_trial(world):
    first = run(world, "H1-01")
    second = run(world, "H1-01", rerun=fix(world))
    opened = trials(remote_entries(world))
    assert [e.hash for e in opened] == [first.open_entry.hash, second.open_entry.hash]
    assert opened[0].body["configuration_sha256"] == opened[1].body["configuration_sha256"]
    assert ledger.trial_count(remote_entries(world)) == 2
    assert opened[1].body["rerun"]["defect"] == "synthetic defect"


def test_open_entry_without_a_result_counts_as_a_trial(tmp_path, monkeypatch):
    # The harness is killed while its strategy runs: no result entry can be written,
    # and the open entry already on the remote counts.
    world = a_world(tmp_path, monkeypatch, behaviours={"H1-01": "spins"})
    try:
        script = (
            f"import sys; sys.path[:0] = [{str(RESEARCH)!r}]\n"
            "from norse_research import harness\n"
            f"harness.run({str(world.repo)!r}, {{'configuration': 'H1-01'}})\n"
        )
        env = {k: v for k, v in os.environ.items()
               if not (harness.ENV_REFUSED.match(k) and k not in harness.ENV_ALLOWED)}
        proc = subprocess.Popen([sys.executable, "-I", "-c", script], env=env,
                                start_new_session=True, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        try:
            for _ in range(600):
                if b'"type":"open"' in world.remote_ledger():
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("the open entry never reached the remote")
        finally:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        entries = remote_entries(world)
        assert entries[-1].type == "open" and entries[-1].body["configuration_id"] == "H1-01"
        assert ledger.trial_count(entries) == 1
    finally:
        world.close()


def test_a_strategy_process_that_exits_is_ledgered_as_failed(tmp_path, monkeypatch,
                                                              no_source_checks):
    world = a_world(tmp_path, monkeypatch, behaviours={"H1-01": "exits"}, evasive=True)
    try:
        record = run(world, "H1-01")
        assert record.status == "failed" and "exited with 137" in record.error
        assert ledger.trial_count(remote_entries(world)) == 1
    finally:
        world.close()


# --- T2, T18: the loader's speed bump and classification by window --------------------


def test_loader_returns_out_of_sample_rows_only_inside_an_open_trial(tmp_path, monkeypatch):
    world = a_world(tmp_path, monkeypatch, behaviours={"H1-01": "loads"})
    try:
        manifest = Manifest.load(world.repo / "research/manifests/binance-vision-development.json")
        loader = DatasetLoader(manifest, world.store)
        start, end = datetime(2025, 12, 1, tzinfo=UTC), datetime(2025, 12, 5, tzinfo=UTC)
        with pytest.raises(OutOfSampleUnledgered, match="open trial ledger entry"):
            loader.load("spot/klines/XUSDT/1d", start, end)
        record = run(world, "H1-01")
        assert record.status == "completed", record.error  # four rows, inside the entry
        with pytest.raises(OutOfSampleUnledgered):
            loader.load("spot/klines/XUSDT/1d", start, end)
        with inside_ledger_entry(record.open_entry.hash):
            assert len(loader.load("spot/klines/XUSDT/1d", start, end).rows) == 4
    finally:
        world.close()


def test_a_run_covering_an_out_of_sample_window_is_a_trial_whatever_its_label(
    tmp_path, monkeypatch
):
    world = a_world(tmp_path, monkeypatch, behaviours={"H1-02": "outside"})
    try:
        labels = ["in-sample", "smoke", "debug"]
        for i, label in enumerate(labels):
            extra = {"label": label, **({"rerun": fix(world)} if i else {})}
            record = run(world, "H1-01", **extra)
            assert record.open_entry.body["kind"] == "trial"
            assert record.open_entry.body["label"] == label
            assert record.result_entry.body["result"]["covers_window"] is True
        assert ledger.trial_count(remote_entries(world)) == len(labels)
        hashes = {e.body["configuration_sha256"] for e in trials(remote_entries(world))}
        assert len(hashes) == 1  # the label never enters the configuration hash
        record = run(world, "H1-02", label="debug")
        assert record.status == "failed" and "outside the registered window" in record.error
        assert ledger.trial_count(remote_entries(world)) == len(labels) + 1
    finally:
        world.close()


# --- T3, T8: write-ahead, on the pinned remote -----------------------------------------


def test_open_entry_is_pushed_before_the_strategy_runs(world, monkeypatch):
    seen = []
    original = harness._run_strategy

    def watched(*args, **kwargs):
        remote = parse_ledger(world.remote_ledger())
        seen.append((remote[-1].hash, remote[-1].type, args[6]))
        return original(*args, **kwargs)

    monkeypatch.setattr(harness, "_run_strategy", watched)
    record = run(world, "H1-01")
    assert seen == [(record.open_entry.hash, "open", record.open_entry.hash)]


@pytest.mark.parametrize("failing", ["hash-object", "commit-tree", "update-ref", "push"])
def test_strategy_is_not_invoked_when_the_open_entry_cannot_be_written(
    world, monkeypatch, capsys, calls, failing
):
    before_local = world.ledger.local_head()
    before_remote = world.remote_ledger()
    original = gitledger.Git.run

    def broken(self, *args, **kwargs):
        if args and args[0] == failing:
            raise gitledger.GitError(f"git {failing} failed: injected")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(gitledger.Git, "run", broken)
    with pytest.raises(LedgerWriteError):
        run(world, "H1-01")
    assert calls == []
    assert capsys.readouterr().out == ""
    assert world.remote_ledger() == before_remote
    assert world.ledger.local_head() == before_local
    assert not list(world.repo.glob("ledger*"))


def test_a_push_the_remote_rejects_refuses_the_run(world, calls):
    hook = world.remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'rejected by policy' >&2\nexit 1\n")
    hook.chmod(0o755)
    before = world.ledger.local_head()
    with pytest.raises(LedgerWriteError, match="could not be pushed"):
        run(world, "H1-01")
    assert calls == []
    assert world.ledger.local_head() == before


def test_an_entry_the_remote_does_not_keep_refuses_the_run(world, calls):
    hook = world.remote / "hooks" / "post-receive"
    hook.write_text(
        "#!/bin/sh\nwhile read old new ref; do\n"
        '  if [ "$ref" = refs/heads/ledger ]; then git update-ref "$ref" "$old"; fi\n'
        "done\nexit 0\n"
    )
    hook.chmod(0o755)
    before = world.ledger.local_head()
    with pytest.raises(LedgerWriteError, match="does not hold the pushed entry"):
        run(world, "H1-01")
    assert calls == []
    assert world.ledger.local_head() == before


def test_run_is_refused_unless_the_remote_is_the_pinned_one(world, monkeypatch, calls):
    scratch = world.tmp / "scratch-clone"
    git(world.tmp, "clone", "--quiet", str(world.repo), str(scratch))
    GitLedger(scratch).sync()
    before = remote_entries(world)
    with pytest.raises(RemoteNotPinned, match="not the ledger's pinned remote"):
        harness.run(scratch, request("H1-01"))
    mirror = world.tmp / "mirror.git"
    git(world.tmp, "clone", "--quiet", "--mirror", str(world.remote), str(mirror))
    git(world.repo, "config", f"url.{mirror}.insteadOf", str(world.remote))
    with pytest.raises(RemoteNotPinned, match="URL rewriting"):
        run(world, "H1-01")
    git(world.repo, "config", "--unset", f"url.{mirror}.insteadOf")
    git(world.repo, "config", "remote.origin.pushurl", str(mirror))
    with pytest.raises(RemoteNotPinned, match="not the ledger's pinned remote"):
        run(world, "H1-01")
    git(world.repo, "config", "--unset", "remote.origin.pushurl")
    # The same URL, but a transport program or proxy that goes elsewhere (review N6).
    for key, value in (("remote.origin.receivepack", f"git-receive-pack {mirror} #"),
                       ("remote.origin.uploadpack", f"git-upload-pack {mirror} #"),
                       ("http.proxy", "http://127.0.0.1:9"),
                       ("core.sshCommand", "ssh -o ProxyCommand=nc")):
        git(world.repo, "config", key, value)
        with pytest.raises(RemoteNotPinned, match="transport override"):
            run(world, "H1-01")
        git(world.repo, "config", "--unset", key)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{mirror}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(world.remote))
    with pytest.raises(RunRefused, match="GIT_CONFIG_COUNT"):
        run(world, "H1-01")
    for key in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
        monkeypatch.delenv(key)
    assert remote_entries(world) == before
    assert calls == []
    assert run(world, "H1-01").status == "completed"


def test_a_run_admitted_on_a_ledger_that_moved_is_refused(world, monkeypatch):
    original = GitLedger.append
    state = {"inner": None}

    def append(self, body, files=None, **kwargs):
        if body.get("type") == "open" and state["inner"] is None:
            state["inner"] = "running"
            state["inner"] = harness.run(world.repo, request("H1-01"), store=world.store)
        return original(self, body, files, **kwargs)

    monkeypatch.setattr(GitLedger, "append", append)
    with pytest.raises(LedgerOutOfStep, match=r"moved .* after this run was admitted"):
        run(world, "H1-01")
    assert state["inner"].status == "completed"
    assert [e.body["configuration_id"] for e in trials(remote_entries(world))] == ["H1-01"]
    world.ledger.verify()


def test_a_trial_admitted_before_the_freeze_cannot_land_after_it(world, monkeypatch, calls):
    # Another session freezes the ledger between this run's admission and its open
    # entry. The freeze does not move the ledger head, so only a check under the append
    # lock catches it (review N2).
    original = GitLedger.append
    state = {"done": False}

    def append(self, body, files=None, **kwargs):
        if body.get("type") == "open" and not state["done"]:
            state["done"] = True
            gate.freeze(GitLedger(world.repo), "M2 by another session", allow_incomplete=True)
        return original(self, body, files, **kwargs)

    monkeypatch.setattr(GitLedger, "append", append)
    with pytest.raises(LedgerOutOfStep, match="frozen"):
        run(world, "H1-01")
    assert calls == []
    frozen = gate.gate_ledger(world.ledger)
    assert frozen.frozen and gate.trial_count(frozen) == 0
    assert ledger.trial_count(remote_entries(world)) == 0


# --- T7: determinism, and nothing carried between runs ---------------------------------


def test_identical_inputs_give_identical_result_hash(world):
    first = run(world, "H1-01")
    second = run(world, "H1-01", rerun=fix(world))
    assert first.open_entry.body["configuration_sha256"] == (
        second.open_entry.body["configuration_sha256"]
    )
    assert first.open_entry.body["manifest_sha256"] == second.open_entry.body["manifest_sha256"]
    assert first.result_entry.body["result_sha256"] == second.result_entry.body["result_sha256"]
    other = run(world, "H1-02")
    assert other.result_entry.body["result_sha256"] != first.result_entry.body["result_sha256"]


def test_strategy_code_runs_only_in_a_fresh_process_from_head(tmp_path, monkeypatch):
    # Review N1: no caller can patch what a strategy runs, through its module state,
    # its function or its declarations; nothing carries from one run to the next.
    world = a_world(tmp_path, monkeypatch, behaviours={"H1-01": "counter"})
    try:
        assert not hasattr(harness, "load_strategy_module")
        first = run(world, "H1-01")
        name = harness.module_name_for(world.paths["H1"])
        assert name not in sys.modules  # the harness never imported the strategy
        impostor = types.ModuleType(name)
        impostor.counter = lambda config, data: [("2018-05-01", 0.004)]
        impostor.PARAMETERS = ["extra_lookback"]
        impostor.DEFAULTS = {"extra_lookback": 3}
        monkeypatch.setitem(sys.modules, name, impostor)
        second = run(world, "H1-01", rerun=fix(world))
        assert second.result_entry.body["result_sha256"] == first.result_entry.body["result_sha256"]
        # The counter in the module's state started from zero again: a fresh process.
        assert "extra_lookback" not in second.open_entry.body["configuration"]["parameters"]
    finally:
        world.close()


def test_a_forged_bytecode_file_never_runs(world, monkeypatch):
    # A .pyc beside the strategy, forged with its source's timestamp and size, would be
    # what a normal import runs (review L12). The runner executes committed source only.
    honest = run(world, "H1-01")
    source = world.repo / world.paths["H1"]
    forged_source = world.tmp / "forged.py"
    forged_source.write_text(
        source.read_text().replace("(x / 2147483648 - 0.5) * 0.02 + drift", "0.004")
    )
    cache = Path(sys.implementation.cache_tag)
    pyc = source.parent / "__pycache__" / f"{source.stem}.{cache}.pyc"
    pyc.parent.mkdir(exist_ok=True)
    py_compile.compile(str(forged_source), cfile=str(pyc), doraise=True)
    stat = source.stat()
    data = bytearray(pyc.read_bytes())
    data[8:12] = int(stat.st_mtime).to_bytes(4, "little")
    data[12:16] = (stat.st_size & 0xFFFFFFFF).to_bytes(4, "little")
    pyc.write_bytes(bytes(data))
    assert git(world.repo, "status", "--porcelain") == ""  # ignored: __pycache__
    sys.path.insert(0, str(source.parent))
    try:
        forged = __import__(source.stem)  # a normal import runs the forgery
        assert forged.run({"id": "H1-01", "seed": None, "window": {
            "first_return_day": "2018-05-01", "days": 1}}, None) == [("2018-05-01", 0.004)]
    finally:
        sys.path.remove(str(source.parent))
        sys.modules.pop(source.stem, None)
    again = run(world, "H1-01", rerun=fix(world))
    assert again.result_entry.body["result_sha256"] == honest.result_entry.body["result_sha256"]


# --- T11: trust never reads the clock -------------------------------------------------


def _decisions(world):
    out = {}
    try:
        verified = world.ledger.verify()
        out["verify"] = "ok"
    except ledger.VerificationError as exc:
        out["verify"] = str(exc)
        return out
    snapshot, reg = verified.snapshot, verified.registration
    strategy = harness.load_strategy(world.ledger.git, "HEAD", snapshot.genesis, reg, "H1-01")
    rerun = {**FIX, "fix_commit": git(world.repo, "rev-parse", "HEAD")}
    for req in (request("H1-02"), request("H1-04"), request("H1-01"),
                request("H1-01", rerun=rerun),
                request("H1-01", sensitivity_total_cost_bps_per_side=7.5)):
        try:
            admit(snapshot, reg, reg.resolve(req, strategy), None)
            out[json.dumps(req, sort_keys=True)] = "admitted"
        except RunRefused as exc:
            out[json.dumps(req, sort_keys=True)] = str(exc)
    return out


def test_trust_decisions_do_not_read_the_clock(world, monkeypatch):
    run(world, "H1-01")
    honest = _decisions(world)
    assert honest["verify"] == "ok"
    times = []
    for moment in (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 12, 9, tzinfo=UTC)):
        epoch = moment.timestamp()
        with monkeypatch.context() as m:
            fake = types.SimpleNamespace(now=lambda tz=None, moment=moment: moment)
            m.setattr(ledger, "datetime", fake)
            m.setattr(time, "time", lambda epoch=epoch: epoch)
            m.setattr(time, "time_ns", lambda epoch=epoch: int(epoch * 1e9))
            m.setenv("GIT_COMMITTER_DATE", moment.isoformat())
            m.setenv("GIT_AUTHOR_DATE", moment.isoformat())
            assert _decisions(world) == honest
            record = run(world, "B-H1" if not times else "B-H2")
            times.append(record.open_entry.body["time"])
            assert ledger.utc_now() == moment.strftime(ledger.TIME_FORMAT)
    assert times[0] >= remote_entries(world)[2].body["time"]  # never backwards
    assert times[1] == "2026-12-09T00:00:00Z"  # the false clock reached the write


def test_trust_code_reads_no_clock():
    # Review L1: the only clock read is ledger.utc_now, for informative timestamps.
    clocks = {("datetime", "now"), ("datetime", "utcnow"), ("datetime", "today"),
              ("date", "today"), ("time", "time"), ("time", "time_ns"),
              ("time", "monotonic"), ("time", "perf_counter"), ("time", "localtime"),
              ("time", "gmtime"), ("time", "ctime")}
    modules = ("rules", "harness", "gate", "gitledger", "sources", "runner", "registration",
               "environment", "canonical", "loader", "ledger")
    found = [
        f"{name}.py:{node.lineno}"
        for name in modules
        for node in ast.walk(ast.parse((RESEARCH / "norse_research" / f"{name}.py").read_text()))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and (node.func.value.id, node.func.attr) in clocks
    ]
    assert found == ["ledger.py:" + str(_utc_now_line())]


def _utc_now_line() -> int:
    tree = ast.parse((RESEARCH / "norse_research" / "ledger.py").read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "utc_now")
    call = next(n for n in ast.walk(function) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "now")
    return call.lineno


# --- T12, T14, T20: only the registered, committed manifest ---------------------------


def test_harness_refuses_a_manifest_not_committed_at_head(world, calls):
    committed = world.repo / "research/manifests/binance-vision-development.json"
    copy = world.write("data/manifest.json", committed.read_bytes())  # ignored: tree clean
    assert git(world.repo, "status", "--porcelain") == ""
    with pytest.raises(UnmanifestedData, match="not committed at HEAD"):
        harness.run(world.repo, request(), manifest_path="data/manifest.json")
    outside = world.tmp / "elsewhere.json"
    outside.write_bytes(committed.read_bytes())
    with pytest.raises(UnmanifestedData, match="outside the repository"):
        harness.run(world.repo, request(), manifest_path=outside)
    committed.write_bytes(committed.read_bytes().replace(b'"licence"', b'"licence" '))
    with pytest.raises(UnmanifestedData, match="differs from the file committed at HEAD"):
        harness.run(world.repo, request())
    assert copy.exists() and calls == []
    assert ledger.trial_count(remote_entries(world)) == 0


def test_harness_refuses_a_manifest_other_than_the_registered_one(world, calls):
    path = world.repo / "research/manifests/binance-vision-development.json"
    doc = json.loads(path.read_text())
    doc["datasets"]["spot/klines/XUSDT/1d"]["rows"] += 1  # rebuilt from altered files
    from norse_research.manifest import dumps

    path.write_bytes(dumps(doc))
    world.commit("a rebuilt manifest")
    world.push()
    with pytest.raises(UnmanifestedData, match="is not the development manifest the genesis"):
        run(world)
    assert calls == []


# --- T13: the strategy's reach ---------------------------------------------------------


def test_strategy_modules_import_only_the_allowlist(world, calls):
    modules = harness.strategy_modules()
    assert ("norse_research.strategies", harness.STRATEGIES_DIR / "__init__.py") in modules
    for name, path in modules:
        assert disallowed_imports(path.read_text(), name, path.name == "__init__.py",
                                  [harness.STRATEGIES_PATH]) == [], name
    bad = (
        "import os\nimport math\nfrom pathlib import Path\nfrom norse_research import store\n"
        "from norse_research.coverage import read_rows\nimport urllib.request\n"
        "from .. import manifest\nfrom . import h1\nx = open('f')\neval('1')\n"
        "g = run.__globals__\nh = getattr(math, 'pi')\nb = __builtins__\n"
        "s = dataclasses.sys\nfrom operator import attrgetter\nm = math.modules\n"
    )
    problems = disallowed_imports(bad, "norse_research.strategies.example",
                                  allowed_paths=["research/norse_research/strategies/example"])
    assert sorted({int(p.split(":")[0].split()[1]) for p in problems}) == [
        1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16,
    ]
    # A strategy imports from the repository only inside its own hypothesis's paths
    # (review N12): a helper in the other hypothesis's paths is refused.
    assert disallowed_imports("import strategies.h2.helper\n", "strategies.h1.x",
                              allowed_paths=["strategies/h1"]) != []
    assert disallowed_imports("import strategies.h1.helper\n", "strategies.h1.x",
                              allowed_paths=["strategies/h1"]) == []
    # Every module under the hypothesis's paths is parsed before each run (L4).
    world.write("strategies/h1/helper.py", "import subprocess\n")
    world.commit("a helper that imports subprocess")
    world.push()
    with pytest.raises(RunRefused, match=r"strategies/h1/helper\.py reaches outside"):
        run(world)
    assert calls == []


def test_committed_strategy_code_never_runs_before_its_checks(world, calls):
    # Review N4: the pinned module's top-level code ran, and only then was refused.
    marker = world.tmp / "top-level-marker"
    world.write(world.paths["H1"], world.source("H1", top_level=(
        f"import os\nos.mkdir({str(marker)!r})\n\n")))
    world.commit("top-level code")
    world.push()
    before = remote_entries(world)
    with pytest.raises(RunRefused, match="outside the strategy allowlist"):
        run(world)
    assert not marker.exists() and calls == []
    assert remote_entries(world) == before
    # Declarations must be literals read from the text; nothing is executed to find them.
    world.write(world.paths["H1"], world.source("H1").replace(
        "PARAMETERS = [", "PARAMETERS = list(CONFIGURATIONS) + ["))
    world.commit("computed declarations")
    world.push()
    with pytest.raises(RunRefused, match="PARAMETERS must be a literal"):
        run(world)
    assert calls == []


def test_strategy_run_cannot_open_files_or_sockets(tmp_path, monkeypatch, no_source_checks):
    world = a_world(tmp_path, monkeypatch, evasive=True, behaviours={
        "H1-01": "opens_file", "H1-02": "writes_file", "H1-03": "opens_socket"})
    try:
        Path(world.target + ".read").write_text("a file outside the repository\n")
        for cid in ("H1-01", "H1-02", "H1-03"):
            record = run(world, cid)
            assert record.status == "failed"
            assert "StrategyIOBlocked" in record.error, record.error
            assert record.result_entry.body["result"] == {"error": record.error}
        assert not Path(world.target).exists()
        entries = remote_entries(world)
        assert ledger.trial_count(entries) == 3  # every failed run is ledgered and counts
        assert [e.body["status"] for e in entries if e.type == "result"] == ["failed"] * 3
    finally:
        world.close()


def test_a_strategy_cannot_turn_the_tripwire_off(tmp_path, monkeypatch, no_source_checks):
    # Review N9: a committed strategy set the harness's flag through sys.modules and
    # read a file outside the loader. The runner has no flag; nothing turns it off.
    world = a_world(tmp_path, monkeypatch, evasive=True, behaviours={"H1-01": "flips_flag"})
    try:
        Path(world.target + ".read").write_text("read from outside the loader\n")
        record = run(world, "H1-01")
        assert record.status == "failed" and "StrategyIOBlocked" in record.error
    finally:
        world.close()


def test_failure_messages_carry_no_local_paths(tmp_path, monkeypatch):
    world = a_world(tmp_path, monkeypatch, behaviours={"H1-01": "fails"})
    try:
        record = run(world, "H1-01")
        assert record.status == "failed"
        text = json.dumps(record.result_entry.body)
        for leak in ("/Users/someone", "secret", "Mac 4T", "4T/", str(world.tmp)):
            assert leak not in text, leak
        assert "<path>" in record.error and "<volume>" in record.error
        assert str(Path.home()) not in harness.public_error(OSError(f"{Path.home()}/a b.csv"))
    finally:
        world.close()


# --- T15: the tree, the environment, the code and the commit --------------------------


def test_modified_tracked_file_is_refused(world, calls):
    world.write("research/norse_research/strategies/__init__.py", "# edited, not committed\n")
    with pytest.raises(RunRefused, match="dirty"):
        run(world)
    assert calls == []


def test_untracked_file_is_refused(world, calls):
    world.write("some/deep/folder/notes.txt", "untracked\n")
    with pytest.raises(RunRefused, match=r"dirty .*some/deep/folder/notes\.txt"):
        run(world)
    assert calls == []


def test_index_flags_cannot_hide_a_modified_file(world, calls):
    for flag, path in (("--skip-worktree", world.paths["H1"]),
                       ("--assume-unchanged", "research/norse_research/strategies/__init__.py")):
        git(world.repo, "update-index", flag, path)
        target = world.repo / path
        original = target.read_bytes()
        target.write_bytes(original + b"\n# modified after the commit, never committed\n")
        assert git(world.repo, "status", "--porcelain") == ""  # git itself says clean
        with pytest.raises(RunRefused, match="assume-unchanged or skip-worktree"):
            run(world)
        target.write_bytes(original)
        git(world.repo, "update-index", flag.replace("--", "--no-"), path)
    assert calls == []


def test_ignored_sourceless_module_is_refused(world, calls):
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
    assert calls == []


def test_code_that_runs_is_the_code_at_head(world, calls):
    path = world.repo / "research/norse_research/periods.py"
    path.write_bytes(path.read_bytes() + b"\n# a committed change the running code lacks\n")
    world.commit("a package change")
    world.push()
    with pytest.raises(RunRefused, match=r"norse_research\.periods \(not research/norse_research"):
        run(world)
    assert calls == []


def test_environment_overrides_are_refused(tmp_path, monkeypatch, no_source_checks):
    world = a_world(tmp_path, monkeypatch, evasive=True, behaviours={"H1-01": "records_env"})
    try:
        refused = ["RISK_MAX_DRAWDOWN_PCT", "NORSE_LIVE_TESTS", "PYTHONPATH", "PYTHONHASHSEED",
                   "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONOPTIMIZE", "GIT_DIR",
                   "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_REPLACE_REF_BASE",
                   "GIT_CONFIG_COUNT"]
        for name in refused:
            with monkeypatch.context() as m:
                m.setenv(name, "1" if name != "PYTHONPATH" else str(world.tmp))
                with pytest.raises(RunRefused, match=name):
                    run(world)
        # A git inside the repository, first on the path (review N10).
        fake = world.write("data/bin/git", "#!/bin/sh\nexec /usr/bin/env -i git \"$@\"\n")
        fake.chmod(0o755)
        with monkeypatch.context() as m:
            m.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")
            with pytest.raises(RunRefused, match="inside the repository"):
                run(world)
        # The genesis step, the tags and the freeze refuse them too.
        with monkeypatch.context() as m:
            m.setenv("RISK_MAX_DRAWDOWN_PCT", "1")
            with pytest.raises(EnvironmentRefused):
                world.ledger.tag_milestone("ledger-m1", "M1")
            with pytest.raises(EnvironmentRefused):
                gate.freeze(world.ledger, "M2", allow_incomplete=True)
            with pytest.raises(EnvironmentRefused):
                world.write_genesis()
        # NORSE_DATASETS_DIR is allowed, and no variable reaches the strategy.
        monkeypatch.setenv("NORSE_DATASETS_DIR", str(world.tmp / "mirror"))
        record = run(world, "H1-01")
        count, overrides = record.error.split("environment ", 1)[1].split(" ", 1)
        assert overrides == "[]" and int(count) <= 2  # macOS may add its own one or two
        assert os.environ["NORSE_DATASETS_DIR"] == str(world.tmp / "mirror")
    finally:
        world.close()


def test_replace_refs_are_refused(world):
    head = git(world.repo, "rev-parse", "HEAD")
    tree = git(world.repo, "rev-parse", "HEAD^{tree}")
    other = git(world.repo, "commit-tree", tree, "-m", "a substitute")
    git(world.repo, "update-ref", f"refs/replace/{head}", other)
    with pytest.raises(RemoteNotPinned, match="refs/replace"):
        run(world)
    git(world.repo, "update-ref", "-d", f"refs/replace/{head}")
    assert run(world).status == "completed"


def test_run_at_a_commit_not_on_the_remote_is_refused(world):
    world.write("research/notes.txt", "a local change\n")
    head = world.commit("not pushed")
    with pytest.raises(RunRefused, match=f"HEAD {head} is not on origin's main"):
        run(world)
    git(world.repo, "push", "--quiet", "origin", "HEAD:refs/heads/scratch")
    with pytest.raises(RunRefused, match="is not on origin's main"):
        run(world)
    world.push()
    assert run(world).status == "completed"


# --- T17, H2, N13: the grid, the budget and runs outside N -----------------------------


def test_configuration_outside_the_registered_grid_is_refused(world, calls):
    for cid in ("H1-09", "H3-01", "B-H3", "holdout"):
        with pytest.raises(RunNotRegistered):
            run(world, cid)
    with pytest.raises(RunNotRegistered, match="unknown request keys"):
        run(world, "H1-01", lookbacks_days=[10, 30, 90])
    with pytest.raises(RunNotRegistered, match="seed"):
        run(world, "C-H1", seed=100)  # outside the registered 0 to 99
    with pytest.raises(RunNotRegistered, match="sensitivity cost"):
        run(world, "H1-01", sensitivity_total_cost_bps_per_side=12)
    assert calls == []
    assert len(remote_entries(world)) == 1  # the genesis entry alone


def test_benchmark_and_control_runs_are_pinned_and_bounded(world):
    assert run(world, "B-H1").status == "completed"
    with pytest.raises(RunRefused, match="B-H1 has run before"):
        run(world, "B-H1")
    assert run(world, "B-H1", rerun=fix(world)).status == "completed"
    assert run(world, "C-H1", seed=0).status == "completed"
    with pytest.raises(RunRefused, match="C-H1 with seed 0 has run before"):
        run(world, "C-H1", seed=0)
    assert run(world, "C-H1", seed=1).status == "completed"
    opened = [e for e in remote_entries(world) if e.type == "open"]
    assert {e.body["strategy"] for e in opened} == {world.pin("B-H1")}
    assert ledger.trial_count(remote_entries(world)) == 0


def test_benchmark_with_a_non_benchmark_module_is_refused(tmp_path, monkeypatch):
    grid_only = {"H1": {f"H1-0{i}" for i in range(1, 9)}}
    world = a_world(tmp_path, monkeypatch, genesis=False, configurations=grid_only)
    try:
        with pytest.raises(ledger.LedgerError, match="does not declare B-H1"):
            world.write_genesis()
    finally:
        world.close()
    second = tmp_path / "second"
    second.mkdir()
    world = World(second)
    try:
        # After genesis, a module that stops declaring an id cannot run it.
        world.write(world.paths["H1"], world.source("H1", {"H1": {"H1-01"}}))
        world.commit("the H1 module drops the benchmark")
        world.push()
        with pytest.raises(RunRefused, match="does not implement B-H1"):
            run(world, "B-H1")
        assert run(world, "H1-01").status == "completed"
    finally:
        world.close()


def full_book_for(h: str, own: int, reserve: int = 0) -> Book:
    book = Book()
    order = book.registration.order[h]
    record = {**FIX, "fix_commit": "9" * 40}
    for i in range(own):
        cid = order[i % len(order)]
        book.open(cid, rerun=record if i >= len(order) else None)
    for _ in range(reserve):
        book.open(order[0], rerun=record, reserve_reason="harness defect found late")
    return book


def _admit(book: Book, cid: str, frozen=None, **extra):
    from ledger_fixtures import ALL_PARAMETERS
    from norse_research.harness import Strategy

    snapshot = book.snapshot()
    strategy = Strategy("synthetic", None, ALL_PARAMETERS, {})
    admit(snapshot, book.registration, book.registration.resolve(request(cid, **extra), strategy),
          frozen)


def test_trial_beyond_the_registered_budget_is_refused():
    record = {**FIX, "fix_commit": "9" * 40}
    _admit(full_book_for("H1", 11), "H1-04", rerun=record)
    with pytest.raises(RunRefused, match="needs a recorded reason"):
        _admit(full_book_for("H1", 12), "H1-04", rerun=record)
    _admit(full_book_for("H1", 12, reserve=5), "H1-04", rerun=record, reserve_reason="defect")
    with pytest.raises(RunRefused, match="budget is exhausted"):
        _admit(full_book_for("H1", 12, reserve=6), "H1-04", rerun=record, reserve_reason="defect")
    _admit(full_book_for("H1", 12, reserve=6), "H2-01")


def test_reserve_trial_requires_a_recorded_reason():
    record = {**FIX, "fix_commit": "9" * 40}
    book = full_book_for("H2", 12)
    with pytest.raises(RunRefused, match="needs a recorded reason"):
        _admit(book, "H2-03", rerun=record)
    _admit(book, "H2-03", rerun=record, reserve_reason="the fill rule departed from section 5")
    with pytest.raises(RunNotRegistered, match="non-empty"):
        _admit(book, "H2-03", rerun=record, reserve_reason="  ")
    with pytest.raises(RunRefused, match="only after"):
        _admit(full_book_for("H2", 6), "H2-01", rerun=record, reserve_reason="early")


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


def test_verification_replays_the_run_rules(world):
    record = {**FIX, "fix_commit": "9" * 40}
    cases = []
    twice = Book()
    twice.open("H1-01")
    twice.open("H1-01")
    cases.append((twice, "H1-01 has run before"))
    out_of_order = Book()
    out_of_order.open("H1-02")
    cases.append((out_of_order, "registered order"))
    over = full_book_for("H1", 12)
    over.open("H1-01", rerun=record)
    cases.append((over, "needs a recorded reason"))
    benchmark = Book()
    benchmark.open("B-H2")
    benchmark.open("B-H2")
    cases.append((benchmark, "B-H2 has run before"))
    for book, message in cases:
        with pytest.raises(ledger.VerificationError, match=message):
            check_run_rules(book.snapshot().entries, book.registration)
    run(world, "H1-01")
    world.ledger.append(world.open_body("H1-01"))
    with pytest.raises(ledger.VerificationError, match="breaks the run rules"):
        world.ledger.verify()
    with pytest.raises(ledger.VerificationError):
        run(world, "H1-02")


def _hand_append(world, body):
    """Append an entry by plumbing alone, past every check of GitLedger.append."""
    ledger_ = world.ledger
    head = ledger_.local_head()
    data = ledger_.git.blob(head, LEDGER_PATH)
    entries = parse_ledger(data)
    full = {**body, "seq": len(entries), "prev": entries[-1].hash,
            "time": entries[-1].body["time"]}
    line = ledger.make_entry(full).line
    commit = ledger_._commit(head, data + line.encode() + b"\n", {}, "by hand")
    git(world.repo, "update-ref", "refs/heads/ledger", commit)
    git(world.repo, "push", "--quiet", "origin", f"{commit}:refs/heads/ledger")


def test_verification_refuses_entries_the_harness_would_never_write(tmp_path, monkeypatch):
    # Review N7: each open entry must be exactly what the registration resolves it to,
    # in its place relative to the freeze, with a rerun's fix new since the run it repeats.
    from norse_research.canonical import value_hash

    cases = []
    cases.append(("is not what the registration resolves", lambda w: {
        **w.open_body("H1-01"), "kind": "benchmark",
        "configuration": {**w.open_body("H1-01")["configuration"], "kind": "benchmark"},
        "configuration_sha256": value_hash(
            {**w.open_body("H1-01")["configuration"], "kind": "benchmark"}),
    }))
    cases.append(("sensitivity run before the freeze", lambda w: w.open_body("H1-01", cost=7.5)))
    cases.append(("not the one genesis pins", lambda w: {
        **w.open_body("B-H1"), "strategy": "strategies/h1/other.py:run",
        "configuration": {**w.open_body("B-H1")["configuration"],
                          "strategy": "strategies/h1/other.py:run"},
        "configuration_sha256": value_hash({**w.open_body("B-H1")["configuration"],
                                            "strategy": "strategies/h1/other.py:run"}),
    }))
    for i, (message, make) in enumerate(cases):
        folder = tmp_path / f"case{i}"
        folder.mkdir()
        world = a_world(folder, monkeypatch)
        try:
            _hand_append(world, make(world))
            with pytest.raises(ledger.VerificationError, match=message):
                world.ledger.verify()
        finally:
            world.close()
    # A trial written past the freeze, by hand.
    folder = tmp_path / "frozen"
    folder.mkdir()
    world = a_world(folder, monkeypatch)
    try:
        gate.freeze(world.ledger, "M2", allow_incomplete=True)
        _hand_append(world, world.open_body("H1-01"))
        with pytest.raises(ledger.VerificationError, match="trial run after the freeze"):
            world.ledger.verify()
    finally:
        world.close()
    # A rerun naming the fix commit the run it repeats already had.
    folder = tmp_path / "old-fix"
    folder.mkdir()
    world = a_world(folder, monkeypatch)
    try:
        first = run(world, "H1-01")
        old = {**FIX, "fix_commit": first.open_entry.body["commit"]}
        _hand_append(world, world.open_body("H1-01", rerun=old))
        with pytest.raises(ledger.VerificationError, match="already in the code of entry"):
            world.ledger.verify()
    finally:
        world.close()


def test_a_rerun_must_name_a_fix_newer_than_the_run_it_repeats(world):
    first = run(world, "H1-01")
    old = {**FIX, "fix_commit": first.open_entry.body["commit"]}
    with pytest.raises(RunRefused, match="already in the code of the run it repeats"):
        run(world, "H1-01", rerun=old)
    with pytest.raises(RunRefused, match="in the code that runs"):
        run(world, "H1-01", rerun={**FIX, "fix_commit": "9" * 40})
    assert run(world, "H1-01", rerun=fix(world)).status == "completed"


# --- T24: no run without a genesis naming the registration ----------------------------


def test_trials_require_a_genesis_entry_naming_the_preregistration(tmp_path, monkeypatch):
    bare = a_world(tmp_path, monkeypatch, genesis=False)
    try:
        with pytest.raises(RunRefused, match="no genesis entry"):
            run(bare)
        with pytest.raises(ledger.VerificationError, match="precedes the genesis entry"):
            bare.ledger.append(open_body(bare.registration, "B-H1"))
        bare.write_genesis()
        genesis = bare.ledger.snapshot(bare.ledger.local_head()).genesis
        forged = json.loads(genesis.line)
        forged["preregistration"]["files"]["docs/preregistration.json"] = "0" * 64
        with pytest.raises(ledger.VerificationError, match="does not match the genesis entry"):
            gitledger.load_registration(bare.ledger.git, ledger.make_entry(forged))
    finally:
        bare.close()


# --- section 15, item 11 and review N13: sensitivity runs ------------------------------


def _complete_grid(book: Book, h: str, best: str) -> None:
    for i, cid in enumerate(book.registration.order[h]):
        n = book.registration.windows[h].days
        book.run(cid, lcg_returns(100 + i, n, drift=0.002 if cid == best else 0.0))


def test_sensitivity_run_before_the_freeze_is_refused(world, calls):
    with pytest.raises(RunRefused, match="only after the ledger freeze"):
        run(world, "H1-01", sensitivity_total_cost_bps_per_side=7.5)
    book = Book()
    _complete_grid(book, "H1", "H1-03")
    with pytest.raises(RunRefused, match="only after the ledger freeze"):
        _admit(book, "H1-03", sensitivity_total_cost_bps_per_side=15)
    assert calls == []


def test_sensitivity_run_of_a_non_candidate_is_refused():
    book = Book()
    _complete_grid(book, "H1", "H1-03")
    frozen = issued(book, frozen=True)
    _admit(book, "H1-03", frozen, sensitivity_total_cost_bps_per_side=10)
    for cid in ("H1-01", "H1-05", "H2-01"):
        with pytest.raises(RunRefused, match="candidate"):
            _admit(book, cid, frozen, sensitivity_total_cost_bps_per_side=10)
    with pytest.raises(RunRefused, match="frozen"):
        _admit(book, "B-H1", frozen)


def test_sensitivity_runs_are_bounded():
    # One run per registered cost; another only with a recorded defect (review N13).
    book = Book()
    _complete_grid(book, "H1", "H1-03")
    opened = book.open("H1-03", cost=7.5)
    prior = book.snapshot().entries
    again = run_of(opened)
    assert "has run before" in run_rule_error(prior, book.registration, again)
    other_cost = {**again, "costs": {"fee_bps_per_side": 13, "slippage_bps_per_side": 2}}
    assert run_rule_error(prior, book.registration, other_cost) is None
    with_fix = {**again, "rerun": {**FIX, "fix_commit": "9" * 40}}
    assert run_rule_error(prior, book.registration, with_fix) is None


def test_a_frozen_ledger_refuses_trials_through_the_harness(world):
    run(world, "B-H1")
    gate.freeze(world.ledger, "M2: the ledger freeze", allow_incomplete=True)
    assert world.ledger.verify().freeze is not None
    assert any(n.startswith(FREEZE_ANCHOR) for n in world.ledger.milestone_tags())
    with pytest.raises(RunRefused, match="frozen"):
        run(world, "H1-01")
    with pytest.raises(RunRefused, match="not H1's candidate"):
        run(world, "H1-01", sensitivity_total_cost_bps_per_side=7.5)


# --- the package's own boundaries ------------------------------------------------------


def _calls(name: str) -> list[str]:
    found = []
    for path in sorted((RESEARCH / "norse_research").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call):
                func = node.func
                called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if called == name:
                    found.append(path.name)
    return found


def test_the_harness_exposes_no_network_submission_surface():
    servers = {"http.server", "socketserver", "xmlrpc.server", "asyncio", "wsgiref",
               "multiprocessing.connection", "socket"}
    for path in sorted((RESEARCH / "norse_research").rglob("*.py")):
        imported = set()
        for node in ast.walk(ast.parse(path.read_text())):
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


def test_only_the_harness_opens_a_ledger_entry_for_the_loader():
    assert _calls("inside_ledger_entry") == ["runner.py"]  # review L5: the runner, in its process


def test_only_the_gate_issues_registered_ledgers():
    assert set(_calls("_issue")) == {"gate.py"}  # review M8
    assert _calls("RegisteredLedger") == ["gate.py"]


def test_the_gate_never_calls_the_floored_deflated_sharpe():
    assert _calls("deflated_sharpe") == []  # review L13: only deflated_sharpe_ratio


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
