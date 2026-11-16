"""The holdout seal: the key record, the one unseal, the re-fetch and the holdout run (G1.6).

Each test that needs a ledger builds a temporary bare remote and clone
(ledger_fixtures.World), writes every H1 configuration's run and B-H1's by plumbing,
with H1-03 the one configuration that passes development, and tags the freeze. The
holdout's files are synthetic and sit in a separate folder standing in for the public
archive; the fetcher serves them from there, and nothing here touches the network, the
real repository, its ledger or its tags. The passphrase is ``TEST_PASSPHRASE``, which
exists only in these tests. Threat model T20 to T25 (docs/research/ledger-threat-model.md),
section 11 of docs/PREREGISTRATION.md and decisions D4 and D17.
"""

import ast
import copy
import io
import json
import re
import subprocess
import zipfile
from datetime import UTC, datetime

import pytest
from conftest import clear_overrides
from ledger_fixtures import (
    KEY_RECORD,
    KEY_RECORD_DOC,
    REPO,
    RESEARCH,
    TEST_PASSPHRASE,
    World,
    git,
    grid_world,
)
from norse_research import gate, gitledger, harness, ledger, seal
from norse_research.coverage import read_rows
from norse_research.formats import DatasetSpec
from norse_research.gitledger import LedgerOutOfStep, LedgerWriteError, check_key_record
from norse_research.ledger import LEDGER_PATH, VerificationError, parse_ledger, unseal_of
from norse_research.loader import DatasetLoader, inside_ledger_entry
from norse_research.manifest import Manifest, read_series_file
from norse_research.periods import (
    HOLDOUT_END_MS,
    HOLDOUT_START_MS,
    HoldoutSealed,
    inside_unsealed_run,
)
from norse_research.registration import RunNotRegistered
from norse_research.seal import NoTerminal, SealRefused
from norse_research.store import ArchiveStore
from synthetic_store import make_series

CANDIDATE = "H1-03"
OTHER_KEY = "another passphrase, one an agent might know"


# --- helpers ---------------------------------------------------------------------------


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    """A frozen ledger whose holdout candidate is H1-03."""
    clear_overrides(monkeypatch)
    world = grid_world(tmp_path)
    yield world
    world.close()


def a_grid_world(tmp_path, monkeypatch, **kwargs) -> World:
    clear_overrides(monkeypatch)
    return grid_world(tmp_path, **kwargs)


def entries_of(world):
    return world.ledger.snapshot(world.ledger.local_head()).entries


def remote_entries(world):
    return parse_ledger(world.remote_ledger())


def genesis_of(world):
    return world.ledger.snapshot(world.ledger.local_head()).genesis


def trial(world, cid):
    snapshot = world.ledger.snapshot(world.ledger.local_head())
    opened = [e for e in snapshot.opens("trial") if e.body["configuration_id"] == cid][-1]
    return opened, snapshot.result_of(opened)


def request_for(world, cid=CANDIDATE):
    return {
        "preregistration": copy.deepcopy(genesis_of(world).body["preregistration"]),
        "configuration_sha256": trial(world, cid)[0].body["configuration_sha256"],
    }


def key(passphrase=TEST_PASSPHRASE):
    """A passphrase reader for tests, recording each time it is asked."""
    calls = []

    def read(prompt):
        calls.append(prompt)
        return passphrase

    read.calls = calls
    return read


def unseal(world, request=None, read=None, fetch=True, calls=None):
    return seal.unseal(
        world.repo, request or request_for(world), read_key=read or key(), store=world.store,
        fetch=world.fetcher(calls) if fetch else None,
    )


def holdout_run(world, sha=None):
    sha = sha or trial(world, CANDIDATE)[0].body["configuration_sha256"]
    return harness.run(world.repo, {"configuration": "holdout", "candidate_sha256": sha},
                       store=world.store)


def unseal_body(world, cid=CANDIDATE, **changes):
    """An unseal entry's body as the seal would write it, by hand."""
    genesis = genesis_of(world)
    opened, closed = trial(world, cid)
    return {
        "type": "unseal",
        "preregistration": genesis.body["preregistration"],
        "configuration_id": cid,
        "hypothesis": opened.body["hypothesis"],
        "configuration_sha256": opened.body["configuration_sha256"],
        "candidate": {"open": opened.hash, "result": closed.hash},
        "freeze": world.ledger.verify().freeze or "0" * 40,
        "holdout_manifest_sha256": genesis.body["manifests"]["holdout"],
        "key_record_sha256": genesis.body["key_record"]["sha256"],
        **changes,
    }


def hand_append(world, body):
    """Append one entry by plumbing alone, past every check of GitLedger.append."""
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


def holdout_open_body(world, candidate_cid=CANDIDATE, configuration=None):
    """A holdout run's open entry as the harness would write it, by hand."""
    genesis = genesis_of(world)
    opened, _ = trial(world, candidate_cid)
    reg = world.registration
    resolved = reg.resolve_holdout(opened.body, genesis.body["manifests"]["holdout"])
    config = configuration or resolved.configuration
    from norse_research.canonical import value_hash

    body = dict(opened.body)
    body.update({
        "kind": "holdout",
        "configuration": config,
        "configuration_sha256": value_hash(config),
        "window": config["window"],
        "commit": git(world.repo, "rev-parse", "HEAD"),
    })
    return body


# --- the key record (item 1 of the brief; T25) ---------------------------------------


def test_the_test_key_record_is_derived_from_the_test_passphrase():
    assert seal.derive(TEST_PASSPHRASE, KEY_RECORD_DOC) == KEY_RECORD_DOC["derived_key"]
    seal.check_key(KEY_RECORD_DOC, TEST_PASSPHRASE)


class _Terminal(io.StringIO):
    def isatty(self):
        return True


def test_key_record_command_writes_a_record_the_genesis_step_accepts(tmp_path, monkeypatch,
                                                                      capsys):
    clear_overrides(monkeypatch)
    passphrase = "a synthetic six word passphrase for this test"
    typed = []
    monkeypatch.setattr("sys.stdin", _Terminal())
    monkeypatch.setattr(seal.getpass, "getpass",
                        lambda prompt: typed.append(prompt) or passphrase)
    out = tmp_path / "keys" / "holdout-key-record.json"
    assert seal.main(["key-record", "--out", str(out)]) == 0
    assert len(typed) == 2  # asked twice
    shown = capsys.readouterr()
    assert passphrase not in shown.out + shown.err
    data = out.read_bytes()
    record = json.loads(data)
    assert set(record) == gitledger.KEY_RECORD_KEYS
    assert (record["kdf"], record["n"], record["r"], record["p"], record["dklen"]) == (
        "scrypt", 2**17, 8, 1, 32)
    assert len(bytes.fromhex(record["salt"])) == 16
    assert passphrase not in data.decode()
    check_key_record(data)  # the validator the genesis step applies
    seal.check_key(record, passphrase)
    with pytest.raises(SealRefused, match="never replaced"):
        seal.main(["key-record", "--out", str(out)])
    # The genesis step itself accepts it, committed on main, and pins its hash.
    world = World(tmp_path / "w", genesis=False)
    try:
        world.write("research/keys/owner-record.json", data)
        commit = world.commit("the owner's key record")
        world.push()
        entry = world.write_genesis(key_record=(commit, "research/keys/owner-record.json"))
        assert entry.body["key_record"]["sha256"] == seal.sha256_hex(data)
    finally:
        world.close()


def test_key_record_command_refuses_differing_or_short_passphrases(tmp_path):
    answers = iter(["a synthetic six word passphrase here", "a different one entirely, typed"])
    with pytest.raises(SealRefused, match="differ"):
        seal.write_key_record(tmp_path / "a.json", read=lambda prompt: next(answers))
    with pytest.raises(SealRefused, match="shorter than 20"):
        seal.write_key_record(tmp_path / "b.json", read=lambda prompt: "short words")
    assert not list(tmp_path.iterdir())


def test_key_record_uses_scrypt_with_the_registered_parameters():
    # T25: a fast hash, or scrypt below n 2^17, r 8, p 1 or a 16-byte salt, is refused.
    good = seal.make_key_record(TEST_PASSPHRASE, salt=bytes(range(16)))
    check_key_record(seal.key_record_bytes(good))
    assert {k: good[k] for k in ("kdf", "n", "r", "p", "dklen")} == {
        "kdf": "scrypt", "n": 2**17, "r": 8, "p": 1, "dklen": 32}
    weak = [
        {**good, "kdf": "sha256"},
        {**good, "n": 2**16},
        {**good, "n": 3 * 2**16},
        {**good, "r": 4},
        {**good, "p": 2},
        {**good, "salt": "ab" * 15},
        {**good, "dklen": 16, "derived_key": good["derived_key"][:32]},
        {**good, "sha256": "0" * 64},  # no other hash of the passphrase
    ]
    for record in weak:
        with pytest.raises(ledger.LedgerError, match="breaks its contract"):
            check_key_record(seal.key_record_bytes(record))
    fast = {"kdf": "sha256", "salt": "ab" * 16,
            "hash": seal.sha256_hex(bytes.fromhex("ab" * 16) + TEST_PASSPHRASE.encode())}
    with pytest.raises(ledger.LedgerError, match="breaks its contract"):
        check_key_record(json.dumps(fast).encode())


def test_key_is_read_only_from_an_interactive_terminal(monkeypatch):
    # No option, environment variable or file supplies the key, and without a terminal
    # the unseal is refused (T25).
    monkeypatch.setenv("HOLDOUT_PASSPHRASE", TEST_PASSPHRASE)
    monkeypatch.setattr(seal.getpass, "getpass", lambda prompt: TEST_PASSPHRASE)
    monkeypatch.setattr("sys.stdin", io.StringIO(TEST_PASSPHRASE + "\n"))
    with pytest.raises(NoTerminal, match="interactive terminal"):
        seal.read_passphrase("Holdout passphrase: ")
    monkeypatch.setattr("sys.stdin", None)
    with pytest.raises(NoTerminal):
        seal.read_passphrase("Holdout passphrase: ")
    monkeypatch.setattr("sys.stdin", _Terminal())
    assert seal.read_passphrase("Holdout passphrase: ") == TEST_PASSPHRASE
    # No command-line option can carry it.
    source = (RESEARCH / "norse_research" / "seal.py").read_text()
    options = set(re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"', source))
    assert options == {"--repo", "--remote", "--out", "--commit", "--tag",
                       "--configuration-sha256"}
    # Nothing in the module reads the environment or a file for it.
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"getenv", "read_text", "readline"}, node.func.attr
            if node.func.attr == "get" and isinstance(node.func.value, ast.Attribute):
                assert node.func.value.attr != "environ"
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
            assert node.value.attr != "environ"


def test_the_unseal_asks_the_terminal_and_refuses_without_one(frozen, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(TEST_PASSPHRASE + "\n"))
    before = frozen.remote_ledger()
    with pytest.raises(NoTerminal):
        seal.unseal(frozen.repo, request_for(frozen), store=frozen.store, fetch=frozen.fetcher())
    assert frozen.remote_ledger() == before


# --- T20: the periods, and no early unseal --------------------------------------------


def test_period_constants_match_the_genesis_entry(world, monkeypatch):
    before = world.remote_ledger()
    for name, value in (("HOLDOUT_START", datetime(2025, 7, 1, tzinfo=UTC)),
                        ("LAST_DEVELOPMENT_MONTH", "2026-06")):
        with monkeypatch.context() as m:
            m.setattr(gitledger, name, value)
            with pytest.raises(harness.RunRefused, match="differs from the genesis entry"):
                harness.run(world.repo, {"configuration": "H1-01"}, store=world.store)
    assert world.remote_ledger() == before


def test_early_unseal_is_refused(tmp_path, monkeypatch):
    # "Early" is before the freeze anchor exists (section 11: the candidate is chosen on
    # the frozen ledger). The key is never asked for, nothing is written or fetched.
    world = a_grid_world(tmp_path, monkeypatch, freeze=False)
    try:
        read, fetched = key(), []
        before = world.remote_ledger()
        with pytest.raises(SealRefused, match="early unseal"):
            unseal(world, read=read, calls=fetched)
        assert read.calls == [] and fetched == [] and world.remote_ledger() == before
        with pytest.raises(LedgerOutOfStep, match="only after the ledger freeze"):
            world.ledger.append(unseal_body(world))
        hand_append(world, unseal_body(world))
        with pytest.raises(VerificationError, match="unseal before the freeze anchor"):
            world.ledger.verify()
    finally:
        world.close()


def test_the_unseal_check_does_not_read_the_clock(frozen, monkeypatch):
    # T11, G1.6's half: "early" is the freeze anchor, never a date, so a clock set
    # before the pre-registration or after 2026-12-08 decides exactly as the true one.
    import time
    import types

    def refusals():
        out = []
        for request in (request_for(frozen, "H1-01"), {**request_for(frozen), "extra": 1}):
            try:
                unseal(frozen, request, read=key())
            except SealRefused as exc:
                out.append(str(exc))
        return out

    honest = refusals()
    assert len(honest) == 2
    for moment in (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 12, 9, tzinfo=UTC)):
        epoch = moment.timestamp()
        with monkeypatch.context() as m:
            m.setattr(ledger, "datetime", types.SimpleNamespace(now=lambda tz=None, t=moment: t))
            m.setattr(time, "time", lambda t=epoch: t)
            m.setenv("GIT_COMMITTER_DATE", moment.isoformat())
            assert refusals() == honest
    assert unseal(frozen).type == "unseal"


# --- T21: the package's readers and the committed files -------------------------------


def test_package_readers_refuse_holdout_months_without_the_audit_flag(tmp_path):
    store = ArchiveStore(tmp_path)
    spec, _ = make_series(store, "spot/klines/XUSDT/1d", "2025-12", "2026-01")
    key_ = spec.key("2026-01")
    with pytest.raises(HoldoutSealed):
        store.read(key_)
    with pytest.raises(HoldoutSealed):
        read_series_file(store, spec, "2026-01")
    with pytest.raises(HoldoutSealed):
        read_rows(store, spec, "2026-01")
    with pytest.raises(HoldoutSealed):
        store.get(key_, spec.max_bytes())
    # Development months are unaffected; the manifest tools' flag and the unsealed run
    # are the only ways in.
    assert len(read_rows(store, spec, "2025-12")) == 31
    assert store.read(key_, holdout_audit=True)[1]
    assert len(read_series_file(store, spec, "2026-01", holdout_audit=True)[0]) == 31
    with inside_unsealed_run("e" * 64):
        assert len(read_rows(store, spec, "2026-01")) == 31
    with pytest.raises(HoldoutSealed):
        read_rows(store, spec, "2026-01")


def _keyword_callers(name: str, value=True) -> list[str]:
    return sorted(
        path.name
        for path in (RESEARCH / "norse_research").rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.keyword) and node.arg == name
        and not (isinstance(node.value, ast.Constant) and node.value.value is False)
        and not (isinstance(node.value, ast.Name) and node.value.id == name)
    )


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


def test_only_manifest_tools_pass_the_audit_flag():
    assert _keyword_callers("holdout_audit") == ["manifest.py", "manifest.py"]  # build, verify
    tree = ast.parse((RESEARCH / "norse_research" / "manifest.py").read_text())
    passing = {
        function.name
        for function in tree.body if isinstance(function, ast.FunctionDef)
        for node in ast.walk(function)
        if isinstance(node, ast.keyword) and node.arg == "holdout_audit"
        and isinstance(node.value, ast.Constant) and node.value.value is True
    }
    assert passing == {"series_entry", "verify"}
    # Only the runner, in its own process, enters the unsealed run (as for the ledger
    # entry, review L5); only the store and the seal's re-fetch write mirror files.
    assert _calls("inside_unsealed_run") == ["runner.py"]
    assert set(_calls("_atomic_write")) == {"store.py", "seal.py"}


def test_no_committed_fixture_holds_holdout_rows():
    # A row in this package is an archive or REST record keyed by its epoch time in
    # milliseconds or microseconds. Manifests and coverage files hold month names and
    # first and last bar times as text, never a row.
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", "research/tests/fixtures", "research/coverage",
         "research/manifests", "research/tests"],
        cwd=REPO, capture_output=True, check=True,
    ).stdout.split(b"\0")
    paths = [REPO / p.decode() for p in listed if p and not p.endswith(b".py")]
    assert len(paths) >= 10
    epoch = re.compile(r"(?<![0-9A-Za-z])(\d{13}|\d{16})(?![0-9A-Za-z])")
    found, rows = [], 0
    for path in paths:
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as archive:
                texts = [archive.read(n).decode() for n in archive.namelist()]
        else:
            texts = [path.read_bytes().decode("utf-8", errors="replace")]
        for text in texts:
            for match in epoch.finditer(text):
                value = int(match.group(1))
                ms = value // 1000 if len(match.group(1)) == 16 else value
                rows += 1
                if HOLDOUT_START_MS <= ms < HOLDOUT_END_MS:
                    found.append(f"{path.name}: {ms}")
    assert rows > 300  # the fixtures' rows were seen, so the scan reads them
    assert found == []


def test_the_registered_holdout_manifest_is_the_frozen_one():
    from norse_research.manifest import HOLDOUT_MANIFEST_SHA256
    from norse_research.registration import Registration

    reg = Registration.from_bytes((REPO / "docs/preregistration.json").read_bytes())
    committed = (REPO / seal.HOLDOUT_MANIFEST).read_bytes()
    assert reg.holdout_manifest_sha256 == HOLDOUT_MANIFEST_SHA256 == seal.sha256_hex(committed)
    assert reg.holdout_window.days == 304
    assert reg.holdout_run_window("H1").days == 2802 + 304
    assert reg.holdout_run_window("H2").first_return_day == "2020-01-08"


# --- T22: one candidate, chosen by the rule, run once --------------------------------


def test_unseal_must_name_the_candidate_selected_by_the_registered_rule(frozen):
    chosen = gate.holdout_candidate(gate.gate_ledger(frozen.ledger))
    assert chosen.configuration_id == CANDIDATE
    read = key()
    for other in ("H1-01", "H1-08"):
        with pytest.raises(SealRefused, match=f"registered rule selects {CANDIDATE}"):
            unseal(frozen, request_for(frozen, other), read=read)
    assert read.calls == [] and unseal_of(remote_entries(frozen)) is None
    # A hand-written unseal naming another configuration fails verification.
    hand_append(frozen, unseal_body(frozen, "H1-01"))
    with pytest.raises(VerificationError, match=f"registered rule selects {CANDIDATE}"):
        frozen.ledger.verify()


def test_the_unseal_names_the_candidate_and_is_on_the_remote(frozen):
    fetched = []
    entry = unseal(frozen, calls=fetched)
    opened, closed = trial(frozen, CANDIDATE)
    genesis = genesis_of(frozen)
    assert entry.body["configuration_id"] == CANDIDATE
    assert entry.body["configuration_sha256"] == opened.body["configuration_sha256"]
    assert entry.body["candidate"] == {"open": opened.hash, "result": closed.hash}
    assert entry.body["preregistration"] == genesis.body["preregistration"]
    assert entry.body["freeze"] == frozen.ledger.verify().freeze
    assert unseal_of(remote_entries(frozen)).hash == entry.hash
    assert frozen.ledger.verify().snapshot.trial_count() == 8  # the unseal is no trial
    # D17: the holdout files were fetched again and verified, each with its CHECKSUM.
    assert sorted(fetched) == sorted(
        f"{DatasetSpec.from_name('spot/klines/XUSDT/1d').key(m)}{suffix}"
        for m in ("2026-01", "2026-02") for suffix in (".CHECKSUM", "")
    )
    assert frozen.holdout_files_in_mirror() == [
        "XUSDT-1d-2026-01.zip", "XUSDT-1d-2026-01.zip.CHECKSUM",
        "XUSDT-1d-2026-02.zip", "XUSDT-1d-2026-02.zip.CHECKSUM",
    ]


def test_holdout_run_reads_holdout_rows_once_and_spends_the_seal(frozen, monkeypatch):
    entry = unseal(frozen)
    calls = []
    original = harness._run_strategy

    def watched(*args, **kwargs):
        held = {e.hash: e for e in remote_entries(frozen)}
        calls.append((kwargs.get("unsealed"), entry.hash in held, args[6] in held,
                      args[5]["window"]["last_return_day"]))
        return original(*args, **kwargs)

    monkeypatch.setattr(harness, "_run_strategy", watched)
    record = holdout_run(frozen)
    assert record.status == "completed", record.error
    # First the development window, without the unseal; then the holdout run, unsealed;
    # the remote held the unseal entry and the run's open entry at each call (T23).
    assert calls == [
        (None, True, True, "2025-12-31"),
        ((seal.HOLDOUT_MANIFEST, entry.hash), True, True, "2026-10-31"),
    ]
    opened = record.open_entry.body
    assert opened["kind"] == "holdout" and opened["configuration_id"] == CANDIDATE
    assert opened["configuration"]["candidate_sha256"] == entry.body["configuration_sha256"]
    summary = record.result_entry.body["result"]
    assert (summary["development_days"], summary["holdout_days"]) == (2802, 304)
    snapshot = frozen.ledger.verify().snapshot
    series = snapshot.returns_of(record.result_entry)
    committed = snapshot.returns_of(trial(frozen, CANDIDATE)[1])
    assert series[:2802] == committed and series[-1][0] == "2026-10-31"
    assert snapshot.trial_count() == 8  # the holdout run is outside N


def test_holdout_run_of_another_configuration_is_refused(frozen, monkeypatch):
    unseal(frozen)
    started = []
    monkeypatch.setattr(harness, "_run_strategy", lambda *a, **k: started.append(a) or [])
    for other in ("H1-01", "H1-05"):
        with pytest.raises(harness.RunRefused, match="alone"):
            holdout_run(frozen, trial(frozen, other)[0].body["configuration_sha256"])
    with pytest.raises(RunNotRegistered, match="holds exactly"):
        harness.run(frozen.repo, {"configuration": "holdout"}, store=frozen.store)
    assert started == []
    # A hand-written holdout run of another configuration fails verification.
    hand_append(frozen, holdout_open_body(frozen, "H1-01"))
    with pytest.raises(VerificationError, match="H1-03's configuration"):
        frozen.ledger.verify()


def test_second_holdout_result_after_one_unseal_is_refused(frozen, monkeypatch):
    unseal(frozen)
    original = harness._run_strategy
    crashes = [1]

    def crash_once(*args, **kwargs):
        if crashes:
            crashes.pop()
            raise RuntimeError("synthetic crash before any result")
        return original(*args, **kwargs)

    monkeypatch.setattr(harness, "_run_strategy", crash_once)
    first = holdout_run(frozen)
    assert first.status == "failed"  # ledgered; the seal is not spent
    second = holdout_run(frozen)  # a retry, same unseal and configuration (item 6)
    assert second.status == "completed", second.error
    with pytest.raises(harness.RunRefused, match="spends the seal"):
        holdout_run(frozen)
    attempts = [e for e in remote_entries(frozen) if e.type == "open" and e.body["kind"] == "holdout"]
    assert len(attempts) == 2
    # Verification refuses a third holdout run written by hand.
    hand_append(frozen, holdout_open_body(frozen))
    with pytest.raises(VerificationError, match="spends the seal"):
        frozen.ledger.verify()


def test_no_holdout_return_is_computed_unless_the_development_run_reproduces(
    tmp_path, monkeypatch
):
    for i, behaviour in enumerate(("peeks", "drifts_late")):
        world = a_grid_world(tmp_path / str(i), monkeypatch, behaviours={CANDIDATE: behaviour})
        try:
            unseal(world)
            record = holdout_run(world)
            assert record.status == "failed"
            if behaviour == "peeks":  # it read 2026 in its development run: refused there
                assert "HoldoutSealed" in record.error
            else:  # its development part changed once 2026 was in the window
                assert "differ from the candidate's committed series" in record.error
            assert record.result_entry.body["result_sha256"] is None
        finally:
            world.close()


def test_a_holdout_run_at_code_changed_outside_data_loading_is_refused(frozen):
    # Section 11, item 6: the candidate's commit plus only data loading or the seal.
    unseal(frozen)
    candidate_commit = trial(frozen, CANDIDATE)[0].body["commit"]
    head = git(frozen.repo, "rev-parse", "HEAD")
    paths = ["research/norse_research", "strategies/h1"]
    assert gitledger.holdout_commit_error(frozen.ledger.git, candidate_commit, head, paths) is None
    frozen.write("research/norse_research/notes.txt", "a change outside data loading\n")
    frozen.commit("a change outside data loading and the seal")
    frozen.push()
    with pytest.raises(harness.RunRefused, match="outside data loading and the seal"):
        holdout_run(frozen)
    head = git(frozen.repo, "rev-parse", "HEAD")
    problem = gitledger.holdout_commit_error(frozen.ledger.git, candidate_commit, head, paths)
    assert "notes.txt" in problem
    # A change to data loading or the seal alone is allowed.
    allowed = gitledger.SEAL_AND_DATA_LOADING
    assert "research/norse_research/loader.py" in allowed
    assert "research/norse_research/harness.py" not in allowed


def test_no_candidate_means_no_unseal(tmp_path, monkeypatch):
    world = a_grid_world(tmp_path, monkeypatch, skilled=None)
    try:
        frozen_ledger = gate.gate_ledger(world.ledger)
        assert [gate.development(frozen_ledger, h) for h in ("H1", "H2")][1] is None
        assert not gate.development(frozen_ledger, "H1").passes
        assert gate.holdout_candidate(frozen_ledger) is None
        read = key()
        with pytest.raises(SealRefused, match="no holdout candidate"):
            unseal(world, read=read)
        assert read.calls == [] and unseal_of(remote_entries(world)) is None
        hand_append(world, unseal_body(world, "H1-01"))
        with pytest.raises(VerificationError, match="no hypothesis passes development"):
            world.ledger.verify()
    finally:
        world.close()


# --- T23: once, on the remote, before any holdout row ----------------------------------


def test_second_unseal_is_refused(frozen):
    first = unseal(frozen)
    read = key()
    with pytest.raises(SealRefused, match="second unseal"):
        unseal(frozen, read=read)
    assert read.calls == []
    assert [e.hash for e in remote_entries(frozen) if e.type == "unseal"] == [first.hash]
    with pytest.raises(VerificationError, match="second unseal"):
        frozen.ledger.append(unseal_body(frozen))
    hand_append(frozen, unseal_body(frozen))
    with pytest.raises(VerificationError, match="second unseal"):
        frozen.ledger.verify()


def test_unseal_on_a_ledger_behind_the_remote_is_refused(frozen):
    request = request_for(frozen)
    head = frozen.ledger.local_head()
    git(frozen.repo, "update-ref", "refs/heads/ledger", f"{head}^")
    read = key()
    with pytest.raises(LedgerOutOfStep, match="behind"):
        unseal(frozen, request, read=read)
    assert read.calls == []
    git(frozen.repo, "update-ref", "refs/heads/ledger", head)
    # Another clone unseals; this one, now behind it, cannot unseal again (T23).
    other = frozen.clone("other")
    seal.unseal(other.git.repo, request, read_key=key(), store=None, fetch=None)
    with pytest.raises(LedgerOutOfStep, match="behind"):
        unseal(frozen, request, read=read)
    assert read.calls == []


def test_holdout_rows_require_the_unseal_entry_on_the_remote(frozen):
    with pytest.raises(SealRefused, match="does not hold the unseal entry"):
        seal.require_unseal_on_remote(frozen.ledger, "e" * 64)
    before = git(frozen.remote, "rev-parse", "refs/heads/ledger")
    entry = unseal(frozen)
    seal.require_unseal_on_remote(frozen.ledger, entry.hash)
    # The loader returns holdout rows only inside the unsealed run.
    manifest = Manifest.load(frozen.repo / "research/manifests/binance-vision-development.json")
    holdout = Manifest.load(frozen.repo / seal.HOLDOUT_MANIFEST)
    loader = DatasetLoader(manifest, frozen.store, holdout=holdout)
    start, end = datetime(2025, 12, 30, tzinfo=UTC), datetime(2026, 1, 3, tzinfo=UTC)
    with inside_ledger_entry("f" * 64):
        # The loader refuses before any file is read; the store would refuse too.
        with pytest.raises(HoldoutSealed, match="rows load only inside the holdout run"):
            loader.load("spot/klines/XUSDT/1d", start, end)
        with inside_unsealed_run(entry.hash):
            assert len(loader.load("spot/klines/XUSDT/1d", start, end).rows) == 4
    with pytest.raises(HoldoutSealed, match="rows load only inside the holdout run"):
        DatasetLoader(manifest, frozen.store).load("spot/klines/XUSDT/1d", start, end)
    # A remote that lost the unseal entry (rewritten, T10) seals the holdout again.
    git(frozen.remote, "update-ref", "refs/heads/ledger", before)
    with pytest.raises(SealRefused, match="does not hold the unseal entry"):
        seal.require_unseal_on_remote(frozen.ledger, entry.hash)
    with pytest.raises(LedgerOutOfStep):
        holdout_run(frozen)


@pytest.mark.parametrize("failing", ["commit-tree", "push"])
def test_unseal_is_written_to_the_ledger_before_any_holdout_row_is_returned(
    frozen, monkeypatch, failing
):
    before = frozen.remote_ledger()
    original = gitledger.Git.run

    def broken(self, *args, **kwargs):
        if args and args[0] == failing:
            raise gitledger.GitError(f"git {failing} failed: injected")
        return original(self, *args, **kwargs)

    fetched = []
    with monkeypatch.context() as m:
        m.setattr(gitledger.Git, "run", broken)
        with pytest.raises(LedgerWriteError):
            unseal(frozen, calls=fetched)
    assert fetched == [] and frozen.holdout_files_in_mirror() == []
    assert frozen.remote_ledger() == before
    with pytest.raises(harness.RunRefused, match="needs the unseal entry"):
        holdout_run(frozen)
    # When it is written, the remote holds it before the first file is fetched.
    seen = []
    fetch = frozen.fetcher()

    def watching(url, max_bytes):
        seen.append(any(e.type == "unseal" for e in remote_entries(frozen)))
        return fetch(url, max_bytes)

    seal.unseal(frozen.repo, request_for(frozen), read_key=key(), store=frozen.store,
                fetch=watching)
    assert seen and all(seen)


def test_a_holdout_file_that_differs_from_the_manifest_stops_the_refetch(frozen):
    spec = DatasetSpec.from_name("spot/klines/XUSDT/1d")
    path = frozen.archive.path(spec.key("2026-02"))
    checksum = path.with_name(path.name + ".CHECKSUM")
    offline = {"": path.read_bytes(), ".CHECKSUM": checksum.read_bytes()}  # the owner's copy
    data = bytearray(offline[""])
    data[-30] ^= 1  # the archive re-published it: a new file with its own CHECKSUM
    path.write_bytes(bytes(data))
    checksum.write_text(f"{seal.sha256_hex(bytes(data))}  {path.name}\n")
    with pytest.raises(SealRefused, match="not the one the frozen holdout manifest pins"):
        unseal(frozen)
    entry = unseal_of(remote_entries(frozen))
    assert entry is not None  # the unseal stands; the run cannot start
    with pytest.raises(harness.RunRefused, match="do not all match"):
        holdout_run(frozen)
    # The owner's offline copy restores the file; fetch-holdout then verifies and goes on.
    target = frozen.store.path(spec.key("2026-02"))
    target.parent.mkdir(parents=True, exist_ok=True)
    for suffix, content in offline.items():
        target.with_name(target.name + suffix).write_bytes(content)
    assert seal.fetch_holdout(frozen.repo, store=frozen.store, fetch=frozen.fetcher()) == 2
    assert holdout_run(frozen).status == "completed"


# --- T24 and T25: the registration and the key --------------------------------------


def test_unseal_naming_another_preregistration_hash_is_refused(frozen):
    read = key()
    for change in (
        lambda p: p["files"].update({"docs/preregistration.json": "0" * 64}),
        lambda p: p["files"].update({"docs/PREREGISTRATION.md": "1" * 64}),
        lambda p: p.update({"commit": "a" * 40}),
        lambda p: p.update({"tag": "prereg-v2"}),
    ):
        request = request_for(frozen)
        change(request["preregistration"])
        with pytest.raises(SealRefused, match="pre-registration other than"):
            unseal(frozen, request, read=read)
    assert read.calls == []
    named = seal.preregistration_at(frozen.ledger.git, "prereg-v1")
    assert named == genesis_of(frozen).body["preregistration"]
    other = unseal_body(frozen)
    other["preregistration"] = {**other["preregistration"], "commit": "a" * 40}
    hand_append(frozen, other)
    with pytest.raises(VerificationError, match="pre-registration other than"):
        frozen.ledger.verify()


def test_unseal_without_the_key_is_refused(frozen):
    before = frozen.remote_ledger()
    for read in (key(""), key(None)):
        with pytest.raises(SealRefused, match="no key"):
            unseal(frozen, read=read)
        assert read.calls  # asked, after every other check passed

    def no_terminal(prompt):
        raise NoTerminal("no terminal")

    with pytest.raises(NoTerminal):
        unseal(frozen, read=no_terminal)
    assert frozen.remote_ledger() == before and frozen.holdout_files_in_mirror() == []


def test_unseal_with_a_wrong_key_is_refused(frozen):
    before = frozen.remote_ledger()
    for wrong in (OTHER_KEY, TEST_PASSPHRASE + " ", TEST_PASSPHRASE.upper()):
        with pytest.raises(SealRefused, match="does not verify"):
            unseal(frozen, read=key(wrong))
    assert frozen.remote_ledger() == before
    assert unseal(frozen).type == "unseal"


def test_key_record_is_read_from_the_genesis_preregistration_not_the_working_tree(frozen):
    # A key record for a passphrase someone else knows, committed on main where the
    # pinned one was, changes nothing: the seal reads the one genesis pins (T25).
    substitute = seal.make_key_record(OTHER_KEY, salt=bytes(16))
    frozen.write(KEY_RECORD, seal.key_record_bytes(substitute))
    frozen.commit("a substituted key record")
    frozen.push()
    with pytest.raises(SealRefused, match="does not verify"):
        unseal(frozen, read=key(OTHER_KEY))
    entry = unseal(frozen)
    assert entry.body["key_record_sha256"] == genesis_of(frozen).body["key_record"]["sha256"]
    record = seal.key_record_of(frozen.ledger.git, genesis_of(frozen))
    assert record == KEY_RECORD_DOC
