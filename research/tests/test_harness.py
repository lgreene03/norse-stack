"""The harness: every run is ledgered before it computes, or refused (G1.5).

Each test builds a temporary bare remote and clone (ledger_fixtures.World); the
strategies are synthetic and read no market data. Threat model T1, T2, T3, T8, T11,
T12, T13, T15, T17, T18 and T24, section 15, item 11 of PREREGISTRATION.md, and the
security review's H1, H2, M1 to M5, M9, L1, L4, L5, L8, L11 and L13.
"""

import ast
import inspect
import json
import os
import py_compile
import socket  # noqa: F401  (imported so a strategy can reach it through sys.modules)
import subprocess
import sys
import time
import types
from datetime import UTC, datetime

import pytest
from conftest import clear_overrides
from ledger_fixtures import RESEARCH, Book, World, git, issued, lcg_returns
from norse_research import gate, gitledger, harness, ledger
from norse_research.gitledger import GitLedger, LedgerOutOfStep, LedgerWriteError, RemoteNotPinned
from norse_research.harness import RunRefused, UnmanifestedData, admit, disallowed_imports
from norse_research.ledger import LEDGER_PATH, parse_ledger
from norse_research.loader import DatasetLoader, OutOfSampleUnledgered, inside_ledger_entry
from norse_research.manifest import Manifest
from norse_research.registration import RunNotRegistered
from norse_research.rules import check_run_rules

N_H1 = 2802
FIX = {"defect": "synthetic defect", "section": "6"}
_fixes = iter(range(10**6))


def request(cid="H1-01", **extra):
    return {"configuration": cid, **extra}


def run(world, cid="H1-01", behaviour="steady", **extra):
    world.mode(behaviour, world.registration.hypothesis_of(cid) if cid[:2] in ("H1", "H2")
               else ("H1" if cid.endswith("H1") else "H2"))
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
    assert opened.body["strategy"] == f"{world.paths['H1']}:run"


# --- T1: every run counts --------------------------------------------------------------


def test_rerun_of_an_identical_configuration_adds_a_trial(world):
    first = run(world, "H1-01")
    second = run(world, "H1-01", rerun=fix(world))
    opened = trials(remote_entries(world))
    assert [e.hash for e in opened] == [first.open_entry.hash, second.open_entry.hash]
    assert opened[0].body["configuration_sha256"] == opened[1].body["configuration_sha256"]
    assert ledger.trial_count(remote_entries(world)) == 2
    assert opened[1].body["rerun"]["defect"] == "synthetic defect"


def test_open_entry_without_a_result_counts_as_a_trial(world):
    # The run is killed inside the strategy: a real process exit, so no result entry
    # can be written. Its open entry is already on the remote and counts.
    script = f"""
import sys
sys.path[:0] = [{str(RESEARCH)!r}]
from norse_research import harness
module = harness.load_strategy_module({str(world.repo)!r}, {world.paths["H1"]!r})
module.MODE[:] = ["exits"]
harness.run({str(world.repo)!r}, {{"configuration": "H1-01"}})
"""
    env = {k: v for k, v in os.environ.items()
           if not (harness.ENV_REFUSED.match(k) and k not in harness.ENV_ALLOWED)}
    proc = subprocess.run([sys.executable, "-I", "-c", script], env=env, capture_output=True)
    assert proc.returncode == 137, proc.stderr.decode()[-2000:]
    entries = remote_entries(world)
    assert entries[-1].type == "open" and entries[-1].body["configuration_id"] == "H1-01"
    assert ledger.trial_count(entries) == 1
    # The next run starts cleanly and counts as the second trial.
    world.ledger.sync()
    run(world, "H1-01", rerun=fix(world))
    assert ledger.trial_count(remote_entries(world)) == 2


# --- T2, T18: the loader's speed bump and classification by window --------------------


def test_loader_returns_out_of_sample_rows_only_inside_an_open_trial(world):
    manifest = Manifest.load(world.repo / "research/manifests/binance-vision-development.json")
    loader = DatasetLoader(manifest, world.store)
    start, end = datetime(2025, 12, 1, tzinfo=UTC), datetime(2025, 12, 5, tzinfo=UTC)
    with pytest.raises(OutOfSampleUnledgered, match="open trial ledger entry"):
        loader.load("spot/klines/XUSDT/1d", start, end)
    record = run(world, "H1-01", "loads")
    assert record.status == "completed"
    assert world.module().CALLED[-2:] == [4, "H1-01"]  # four rows loaded, then computed
    with pytest.raises(OutOfSampleUnledgered):
        loader.load("spot/klines/XUSDT/1d", start, end)
    with inside_ledger_entry(record.open_entry.hash):
        assert len(loader.load("spot/klines/XUSDT/1d", start, end).rows) == 4


def test_a_run_covering_an_out_of_sample_window_is_a_trial_whatever_its_label(world):
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
    record = run(world, "H1-01", "outside", rerun=fix(world))
    assert record.status == "failed" and "outside the registered window" in record.error
    assert ledger.trial_count(remote_entries(world)) == len(labels) + 1


# --- T3, T8: write-ahead, on the pinned remote -----------------------------------------


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
    assert world.module().CALLED == ["H1-01"]


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

    module = world.mode("writes_file")
    target = world.tmp / "written-by-the-strategy"
    module.TARGET[:] = [str(target)]
    monkeypatch.setattr(gitledger.Git, "run", broken)
    with pytest.raises(LedgerWriteError):
        harness.run(world.repo, request("H1-01"), store=world.store)
    assert module.CALLED == []
    assert capsys.readouterr().out == ""
    assert not target.exists()
    assert world.remote_ledger() == before_remote
    assert world.ledger.local_head() == before_local
    assert not list(world.repo.glob("ledger*"))


def test_a_push_the_remote_rejects_refuses_the_run(world):
    hook = world.remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'rejected by policy' >&2\nexit 1\n")
    hook.chmod(0o755)
    before = world.ledger.local_head()
    with pytest.raises(LedgerWriteError, match="could not be pushed"):
        run(world, "H1-01")
    assert world.module().CALLED == []
    assert world.ledger.local_head() == before


def test_an_entry_the_remote_does_not_keep_refuses_the_run(world):
    # The push reports success but the remote's ledger head does not hold the entry
    # afterwards (here a hook moves it back): the strategy must not run (review H1).
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
    assert world.module().CALLED == []
    assert world.ledger.local_head() == before


def test_run_is_refused_unless_the_remote_is_the_pinned_one(world, monkeypatch):
    # A clone of the working clone: its origin is not the pinned remote (probe 2).
    scratch = world.tmp / "scratch-clone"
    git(world.tmp, "clone", "--quiet", str(world.repo), str(scratch))
    GitLedger(scratch).sync()
    before = remote_entries(world)
    with pytest.raises(RemoteNotPinned, match="not the ledger's pinned remote"):
        harness.run(scratch, request("H1-01"))
    # A mirror reached through a URL rewrite, in the repository's own configuration.
    mirror = world.tmp / "mirror.git"
    git(world.tmp, "clone", "--quiet", "--mirror", str(world.remote), str(mirror))
    git(world.repo, "config", f"url.{mirror}.insteadOf", str(world.remote))
    with pytest.raises(RemoteNotPinned, match="URL rewriting"):
        run(world, "H1-01")
    git(world.repo, "config", "--unset", f"url.{mirror}.insteadOf")
    # A push URL split from the fetch URL.
    git(world.repo, "config", "remote.origin.pushurl", str(mirror))
    with pytest.raises(RemoteNotPinned, match="not the ledger's pinned remote"):
        run(world, "H1-01")
    git(world.repo, "config", "--unset", "remote.origin.pushurl")
    # The same rewrite through the environment, with no file at all (probe 2b).
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{mirror}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(world.remote))
    with pytest.raises(RunRefused, match="GIT_CONFIG_COUNT"):
        run(world, "H1-01")
    for key in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
        monkeypatch.delenv(key)
    assert remote_entries(world) == before
    assert world.module().CALLED == []
    assert run(world, "H1-01").status == "completed"  # and the pinned remote still works


def test_a_run_admitted_on_a_ledger_that_moved_is_refused(world, monkeypatch):
    # Another session in the same repository runs H1-01 between this run's admission
    # and its open entry (the review's race probe).
    original = GitLedger.append
    state = {"inner": None}

    def append(self, body, files=None, **kwargs):
        if body.get("type") == "open" and state["inner"] is None:
            state["inner"] = "running"
            state["inner"] = harness.run(world.repo, request("H1-01"), store=world.store)
        return original(self, body, files, **kwargs)

    monkeypatch.setattr(GitLedger, "append", append)
    world.mode("steady")
    with pytest.raises(LedgerOutOfStep, match=r"moved .* after this run was admitted"):
        harness.run(world.repo, request("H1-01"), store=world.store)
    assert state["inner"].status == "completed"
    opened = trials(remote_entries(world))
    assert [e.body["configuration_id"] for e in opened] == ["H1-01"]
    world.ledger.verify()


# --- T7: determinism -------------------------------------------------------------------


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
    strategy = harness.load_strategy(world.ledger.git, world.repo, "HEAD", snapshot.genesis,
                                     reg, "H1-01")
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
            # Every clock the package reads goes through ledger.utc_now, looked up at
            # call time (review L1); datetime itself is replaced in that module too.
            fake = types.SimpleNamespace(now=lambda tz=None, moment=moment: moment)
            m.setattr(ledger, "datetime", fake)
            m.setattr(time, "time", lambda epoch=epoch: epoch)
            m.setattr(time, "time_ns", lambda epoch=epoch: int(epoch * 1e9))
            m.setenv("GIT_COMMITTER_DATE", moment.isoformat())
            m.setenv("GIT_AUTHOR_DATE", moment.isoformat())
            assert _decisions(world) == honest
            # A write under the false clock still decides nothing differently, and
            # its (informative) timestamp never goes backwards.
            record = run(world, "B-H1" if not times else "B-H2")
            times.append(record.open_entry.body["time"])
            assert ledger.utc_now() == moment.strftime(ledger.TIME_FORMAT)
    assert times[0] >= remote_entries(world)[2].body["time"]  # never backwards
    assert times[1] == "2026-12-09T00:00:00Z"  # the false clock reached the write


# --- T12, T14, T20: only the registered, committed manifest ---------------------------


def test_harness_refuses_a_manifest_not_committed_at_head(world):
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
    assert copy.exists() and world.module().CALLED == []
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
    assert world.module().CALLED == []


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
        "g = run.__globals__\nh = getattr(math, 'pi')\nb = __builtins__\n"
    )
    problems = disallowed_imports(bad, "norse_research.strategies.example")
    assert [p.split(":")[0] for p in problems] == [
        "line 1", "line 3", "line 4", "line 5", "line 6", "line 7", "line 9", "line 10",
        "line 11", "line 12", "line 13",
    ]
    # The harness parses every module under the hypothesis's strategy paths before
    # every run, a helper the strategy never imports included (review L4).
    world.write("strategies/h1/helper.py", "import subprocess\n")
    world.commit("a helper that imports subprocess")
    world.push()
    with pytest.raises(RunRefused, match=r"strategies/h1/helper\.py reaches outside"):
        run(world)
    assert world.module().CALLED == []


def test_strategy_run_cannot_open_files_or_sockets(world):
    target = world.tmp / "written-by-the-strategy"
    world.module().TARGET[:] = [str(target)]
    (world.tmp / "written-by-the-strategy.read").write_text("a file outside the repository\n")
    for i, behaviour in enumerate(("opens_file", "writes_file", "opens_socket")):
        extra = {"rerun": fix(world)} if i else {}
        world.module().TARGET[:] = [str(target)]
        record = run(world, "H1-01", behaviour, **extra)
        assert record.status == "failed"
        assert "StrategyIOBlocked" in record.error
        assert record.result_entry.body["result"] == {"error": record.error}
    assert not target.exists()
    entries = remote_entries(world)
    assert ledger.trial_count(entries) == 3  # every failed run is ledgered and counts
    assert [e.body["status"] for e in entries if e.type == "result"] == ["failed"] * 3


def test_failure_messages_carry_no_local_paths(world):
    record = run(world, "H1-01", "fails")
    assert record.status == "failed"
    assert "/Users/someone" not in record.error and "<path>" in record.error
    assert str(world.tmp) not in json.dumps(record.result_entry.body)


# --- T15: the tree, the environment, the code and the commit --------------------------


def test_modified_tracked_file_is_refused(world):
    world.write("research/norse_research/strategies/__init__.py", "# edited, not committed\n")
    with pytest.raises(RunRefused, match="dirty"):
        run(world)
    assert world.module().CALLED == []


def test_untracked_file_is_refused(world):
    world.write("some/deep/folder/notes.txt", "untracked\n")
    with pytest.raises(RunRefused, match=r"dirty .*some/deep/folder/notes\.txt"):
        run(world)
    assert world.module().CALLED == []


def test_index_flags_cannot_hide_a_modified_file(world):
    module_path = world.paths["H1"]
    for flag, path in (("--skip-worktree", module_path),
                       ("--assume-unchanged", "research/norse_research/strategies/__init__.py")):
        git(world.repo, "update-index", flag, path)
        target = world.repo / path
        original = target.read_bytes()
        target.write_bytes(original + b"\n# modified after the commit, never committed\n")
        assert git(world.repo, "status", "--porcelain") == ""  # git itself says clean
        with pytest.raises(RunRefused, match="assume-unchanged or skip-worktree"):
            harness.run(world.repo, request("H1-01"), store=world.store)
        target.write_bytes(original)
        git(world.repo, "update-index", flag.replace("--", "--no-"), path)
    assert world.module().CALLED == []


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
    assert world.module().CALLED == []


def test_code_that_runs_is_the_code_at_head(world, monkeypatch):
    # A package file in the repository that is not byte for byte the code under test:
    # the code that would run is not the code the entry would record (review M3).
    path = world.repo / "research/norse_research/periods.py"
    path.write_bytes(path.read_bytes() + b"\n# a committed change the running code lacks\n")
    world.commit("a package change")
    world.push()
    with pytest.raises(RunRefused, match=r"norse_research\.periods \(not research/norse_research"):
        run(world)
    git(world.repo, "revert", "--no-edit", "HEAD")
    world.push()
    # A function from outside the repository, relabelled as the pinned strategy, is
    # never what runs: the harness executes the committed file itself (probe 5).
    def foreign(config, data):
        raise AssertionError("foreign code ran")

    foreign.__module__ = world.module().__name__
    impostor = types.ModuleType(world.module().__name__)
    impostor.run = foreign
    monkeypatch.setitem(sys.modules, world.module().__name__, impostor)
    record = harness.run(world.repo, request("H1-01"), store=world.store)
    assert record.status == "completed" and world.module().CALLED == ["H1-01"]
    assert "strategy" not in inspect.signature(harness.run).parameters


def test_environment_overrides_are_refused(world, monkeypatch):
    refused = ["RISK_MAX_DRAWDOWN_PCT", "NORSE_LIVE_TESTS", "PYTHONPATH", "PYTHONHASHSEED",
               "PYTHONHOME", "PYTHONSTARTUP", "GIT_DIR", "GIT_INDEX_FILE",
               "GIT_OBJECT_DIRECTORY", "GIT_REPLACE_REF_BASE", "GIT_CONFIG_COUNT"]
    for name in refused:
        with monkeypatch.context() as m:
            m.setenv(name, "1" if name != "PYTHONPATH" else str(world.tmp))
            with pytest.raises(RunRefused, match=name):
                harness.run(world.repo, request(), store=world.store)
    assert world.module().CALLED == []
    # NORSE_DATASETS_DIR only says where the verified files are: allowed, and like
    # every other variable it never reaches the strategy.
    monkeypatch.setenv("NORSE_DATASETS_DIR", str(world.tmp / "mirror"))
    run(world, "H1-01", "records_env")
    assert world.module().SEEN_ENV == [{}]
    assert os.environ["NORSE_DATASETS_DIR"] == str(world.tmp / "mirror")


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
    # On another branch of the remote is not enough: branches can be deleted (M9).
    git(world.repo, "push", "--quiet", "origin", "HEAD:refs/heads/scratch")
    with pytest.raises(RunRefused, match="is not on origin's main"):
        run(world)
    world.push()
    assert run(world).status == "completed"


# --- T17, H2: the grid, the budget and runs outside N ---------------------------------


def test_configuration_outside_the_registered_grid_is_refused(world):
    for cid in ("H1-09", "H3-01", "B-H3", "holdout"):
        with pytest.raises(RunNotRegistered):
            harness.run(world.repo, request(cid), store=world.store)
    with pytest.raises(RunNotRegistered, match="unknown request keys"):
        run(world, "H1-01", lookbacks_days=[10, 30, 90])
    with pytest.raises(RunNotRegistered, match="seed"):
        run(world, "C-H1", seed=100)  # outside the registered 0 to 99
    with pytest.raises(RunNotRegistered, match="sensitivity cost"):
        run(world, "H1-01", sensitivity_total_cost_bps_per_side=12)
    assert world.module().CALLED == []
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
    assert {e.body["strategy"] for e in opened} == {f"{world.paths['H1']}:run"}
    assert ledger.trial_count(remote_entries(world)) == 0


def test_benchmark_with_a_non_benchmark_module_is_refused(tmp_path, monkeypatch):
    clear_overrides(monkeypatch)
    # H1's module says it implements the grid only, yet genesis pins B-H1 and C-H1 to it.
    grid_only = {"H1": {f"H1-0{i}" for i in range(1, 9)}}
    world = World(tmp_path, configurations=grid_only)
    try:
        with pytest.raises(RunRefused, match="does not implement B-H1"):
            run(world, "B-H1")
        with pytest.raises(RunRefused, match="does not implement C-H1"):
            run(world, "C-H1", seed=3)
        assert run(world, "H1-01").status == "completed"
    finally:
        world.close()


def full_book_for(h: str, own: int, reserve: int = 0) -> Book:
    """A ledger in memory with ``own`` of a hypothesis's own entries and ``reserve``
    reserve entries used, every registered configuration run first."""
    book = Book()
    order = book.registration.order[h]
    record = {**FIX, "fix_commit": "9" * 40}
    for i in range(own):
        cid = order[i % len(order)]
        book.open(cid, rerun=record if i >= len(order) else None)
    for _ in range(reserve):
        book.open(order[0], rerun=record, reserve_reason="harness defect found late")
    return book


def _admit(book: Book, cid: str, **extra):
    from ledger_fixtures import ALL_PARAMETERS
    from norse_research.harness import Strategy

    snapshot = book.snapshot()
    strategy = Strategy("synthetic", lambda c, d: [], ALL_PARAMETERS, {})
    admit(snapshot, book.registration, book.registration.resolve(request(cid, **extra), strategy),
          None)


def test_trial_beyond_the_registered_budget_is_refused():
    record = {**FIX, "fix_commit": "9" * 40}
    _admit(full_book_for("H1", 11), "H1-04", rerun=record)  # the twelfth: admitted
    with pytest.raises(RunRefused, match="needs a recorded reason"):
        _admit(full_book_for("H1", 12), "H1-04", rerun=record)
    _admit(full_book_for("H1", 12, reserve=5), "H1-04", rerun=record, reserve_reason="defect")
    with pytest.raises(RunRefused, match="budget is exhausted"):
        _admit(full_book_for("H1", 12, reserve=6), "H1-04", rerun=record, reserve_reason="defect")
    _admit(full_book_for("H1", 12, reserve=6), "H2-01")  # H2's own budget is untouched


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
    # In memory: each rule broken once, after the fact.
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
    # In git: an entry appended by hand, past the harness, fails verification, and no
    # run starts after it.
    run(world, "H1-01")
    from ledger_fixtures import open_body

    world.ledger.append(open_body(world.registration, "H1-01"))
    with pytest.raises(ledger.VerificationError, match="breaks the run rules"):
        world.ledger.verify()
    with pytest.raises(ledger.VerificationError):
        run(world, "H1-02")


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
    clear_overrides(monkeypatch)
    bare = World(tmp_path, genesis=False)
    try:
        with pytest.raises(RunRefused, match="no genesis entry"):
            harness.run(bare.repo, request(), store=bare.store)
        from ledger_fixtures import open_body

        with pytest.raises(ledger.VerificationError, match="precedes the genesis entry"):
            bare.ledger.append(open_body(bare.registration, "B-H1"))
        bare.write_genesis()
        genesis = bare.ledger.snapshot(bare.ledger.local_head()).genesis
        forged = json.loads(genesis.line)
        forged["preregistration"]["files"]["docs/preregistration.json"] = "0" * 64
        with pytest.raises(ledger.VerificationError, match="does not match the genesis entry"):
            gitledger.load_registration(bare.ledger.git, ledger.make_entry(forged))
        assert bare.module().CALLED == []
    finally:
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
    assert world.module().CALLED == []


def test_sensitivity_run_of_a_non_candidate_is_refused():
    from ledger_fixtures import ALL_PARAMETERS
    from norse_research.harness import Strategy

    book = Book()
    _complete_grid(book, "H1", "H1-03")
    frozen = issued(book, frozen=True)
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
    with pytest.raises(RunRefused, match="frozen"):
        admit(book.snapshot(), book.registration,
              book.registration.resolve(request("B-H1"), strategy), frozen)


def test_a_frozen_ledger_refuses_trials_through_the_harness(world):
    run(world, "B-H1")
    gate.freeze(world.ledger, "M2: the ledger freeze", allow_incomplete=True)
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
    assert _calls("inside_ledger_entry") == ["harness.py"]  # review L5


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
