"""The holdout seal: the key record, the one unseal, the re-fetch and the holdout run (G1.6).

Each test that needs a ledger builds a temporary bare remote and clone
(ledger_fixtures.World), writes every H1 configuration's run and B-H1's by plumbing,
with H1-03 the one configuration that passes development; there is no freeze (decision
D21), since the unseal closes development. The
holdout's files are synthetic and sit in a separate folder standing in for the public
archive; the fetcher serves them from there, and nothing here touches the network, the
real repository, its ledger or its tags. The passphrase is ``TEST_PASSPHRASE``, which
exists only in these tests. Threat model T20 to T25 (docs/research/ledger-threat-model.md),
section 11 of docs/PREREGISTRATION.md and decisions D4 and D17.
"""

import ast
import copy
import functools
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import clear_overrides
from ledger_fixtures import (
    KEY_RECORD,
    KEY_RECORD_DOC,
    KEY_RECORD_TEXT,
    REPO,
    RESEARCH,
    SHAPED_SIGNATURE,
    TEST_PASSPHRASE,
    Book,
    World,
    git,
    grid_world,
    lcg_returns,
    seed_for,
    sign_as_owner,
)
from norse_research import (
    environment,
    gate,
    gitledger,
    harness,
    lamport,
    ledger,
    relaunch,
    rules,
    seal,
)
from norse_research.coverage import read_rows
from norse_research.environment import EnvironmentRefused
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
from norse_research.rules import check_run_rules
from norse_research.seal import NoTerminal, SealRefused
from norse_research.store import ArchiveStore
from synthetic_store import make_series

CANDIDATE = "H1-03"
OTHER_KEY = "another passphrase, one an agent might know"


# --- helpers ---------------------------------------------------------------------------


@pytest.fixture
def ready(tmp_path, monkeypatch):
    """A ledger ready for the unseal, whose holdout candidate is H1-03."""
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


def accepted_diff(world, cid=CANDIDATE):
    """The hash of the data loading and seal diff from ``cid``'s run to HEAD (T1)."""
    head = git(world.repo, "rev-parse", "HEAD")
    return gitledger.seal_changes(world.ledger.git, trial(world, cid)[0].body["commit"], head)[2]


def request_for(world, cid=CANDIDATE):
    """The owner's request, pinning HEAD and the ledger head as seal candidate prints them
    (review of #70, M-4), and the candidate's commit as GitHub shows it (round 2, H-4)."""
    return {
        "preregistration": copy.deepcopy(genesis_of(world).body["preregistration"]),
        "configuration_sha256": trial(world, cid)[0].body["configuration_sha256"],
        "accepted_diff": accepted_diff(world, cid),
        "unseal_commit": git(world.repo, "rev-parse", "HEAD"),
        "ledger_head": world.ledger.local_head(),
        "candidate_commit": trial(world, cid)[0].body["commit"],
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
    """The unseal, then, with ``fetch``, the re-fetch at HEAD as the owner runs it next
    (``seal fetch-holdout``; review of #70, H-3)."""
    entry = seal.unseal(world.repo, request or request_for(world), read_key=read or key())
    if fetch:
        seal.fetch_holdout(world.repo, store=world.store, fetch=world.fetcher(calls))
    return entry


def holdout_run(world, sha=None):
    sha = sha or trial(world, CANDIDATE)[0].body["configuration_sha256"]
    return harness.run(world.repo, {"configuration": "holdout", "candidate_sha256": sha},
                       store=world.store)


@functools.lru_cache(maxsize=4)
def output_of(passphrase=TEST_PASSPHRASE):
    """The scrypt output of ``passphrase`` under the test record's salt, as hex: the key
    the seal derives its signing key from, never written anywhere (D27)."""
    return seal.derive(passphrase, KEY_RECORD_DOC)


def unseal_body(world, cid=CANDIDATE, **changes):
    """An unseal entry's body as the seal would write it, by hand, unsigned: the append's
    ``sign``, ``append_unseal`` or ``hand_append`` signs it unless ``changes`` gives a
    signature."""
    genesis = genesis_of(world)
    opened, closed = trial(world, cid)
    return {
        "type": "unseal",
        "preregistration": genesis.body["preregistration"],
        "configuration_id": cid,
        "hypothesis": opened.body["hypothesis"],
        "configuration_sha256": opened.body["configuration_sha256"],
        "candidate": {"open": opened.hash, "result": closed.hash},
        "accepted_diff": accepted_diff(world, cid),
        "holdout_manifest_sha256": genesis.body["manifests"]["holdout"],
        "key_record_sha256": genesis.body["key_record"]["sha256"],
        "commit": git(world.repo, "rev-parse", "HEAD"),
        **changes,
    }


def append_unseal(world, body, passphrase=TEST_PASSPHRASE):
    """The public append of an unseal, signed with ``passphrase``'s key as the seal signs."""
    return world.ledger.append(body, sign=lambda message: sign_as_owner(message, passphrase))


def resigned(body, passphrase=TEST_PASSPHRASE):
    """``body``, a whole entry's, signed again over its bytes as they now stand, so a test
    of a later layer is refused by that layer, not by the signature."""
    return {**body, "signature": sign_as_owner(ledger.signed_bytes(body), passphrase)}


def hand_append(world, body):
    """Append one entry by plumbing alone, past every check of GitLedger.append. An
    unseal without a signature is signed with the test key, as the seal would sign it."""
    ledger_ = world.ledger
    head = ledger_.local_head()
    data = ledger_.git.blob(head, LEDGER_PATH)
    entries = parse_ledger(data)
    full = {**body, "seq": len(entries), "prev": entries[-1].hash,
            "time": entries[-1].body["time"]}
    if full["type"] == "unseal" and "signature" not in full:
        full = resigned(full)
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


def test_the_committed_sample_record_commits_to_the_test_passphrase():
    # Computed here with hashlib and hmac alone, independently of norse_research.seal and
    # lamport, from the scheme lamport's docstring states (decision D27).
    output = hashlib.scrypt(TEST_PASSPHRASE.encode(), salt=bytes.fromhex("ab" * 16),
                            n=2**17, r=8, p=1, dklen=32, maxmem=256 * 1024 * 1024)
    assert KEY_RECORD_DOC["verifier_sha256"] == hashlib.sha256(output).hexdigest()
    domain = b"norse-stack/holdout-unseal/lamport-sha256/v1/"
    seed = hmac.new(output, domain + b"seed", hashlib.sha256).digest()
    public = hashlib.sha256(domain + b"public")
    for i in range(256):
        for bit in (0, 1):
            secret = hmac.new(seed, domain + b"secret" + i.to_bytes(2, "big") + bytes([bit]),
                              hashlib.sha256).digest()
            public.update(hashlib.sha256(domain + b"leaf" + secret).digest())
    assert KEY_RECORD_DOC["signer_sha256"] == public.hexdigest()
    # The record holds hashes of what the scrypt output derives, never the output (review
    # H1; review of #65, H-2), nor the seed.
    assert output.hex() not in KEY_RECORD_TEXT and seed.hex() not in KEY_RECORD_TEXT
    assert set(KEY_RECORD_DOC) == gitledger.KEY_RECORD_KEYS
    check_key_record(KEY_RECORD_TEXT.encode())
    assert seal.check_key(KEY_RECORD_DOC, TEST_PASSPHRASE) == output.hex() == output_of()
    assert seal.signing_seed(output_of()) == seed == seed_for()


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
    output = seal.check_key(record, passphrase)
    assert record["verifier_sha256"] == hashlib.sha256(bytes.fromhex(output)).hexdigest()
    assert record["signer_sha256"] == lamport.signer_sha256(seal.signing_seed(output))
    assert passphrase not in data.decode() and output not in data.decode()
    assert seal.sha256_hex(data) in shown.out  # for the owner to keep beside the passphrase
    check_key_record(data)  # the validator the genesis step applies
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
    # Review L6: six words as well as 20 characters, whatever separates them.
    for weak in ("correct horse battery staple", "averyveryverylongsingleword-and-two"):
        with pytest.raises(SealRefused, match="fewer than 6 words"):
            seal.write_key_record(tmp_path / "c.json", read=lambda prompt, w=weak: w)
    assert seal.passphrase_problem("plinth-ochre-gravel-fable-tundra-quill") is None
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
        {**good, "dklen": 16},
        {**good, "verifier_sha256": good["verifier_sha256"].upper()},
        {**good, "verifier_sha256": good["verifier_sha256"][:40]},
        {**good, "signer_sha256": good["signer_sha256"].upper()},
        {**good, "signer_sha256": good["signer_sha256"][:40]},
        {k: v for k, v in good.items() if k != "signer_sha256"},  # D27's commitment
        {**good, "sha256": "0" * 64},  # no other hash of the passphrase
        # The scrypt output itself is never in the record, nor anywhere else (D27).
        {**good, "derived_key": seal.derive(TEST_PASSPHRASE, good)},
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
                       "--configuration-sha256", "--accept-diff", "--boundary",
                       "--unseal-commit", "--ledger-head", "--candidate-commit"}
    # Nothing in the module reads the environment or a file for it.
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"getenv", "read_text", "readline"}, node.func.attr
            if node.func.attr == "get" and isinstance(node.func.value, ast.Attribute):
                assert node.func.value.attr != "environ"
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
            assert node.value.attr != "environ"


def test_the_unseal_asks_the_terminal_and_refuses_without_one(ready, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(TEST_PASSPHRASE + "\n"))
    before = ready.remote_ledger()
    with pytest.raises(NoTerminal):
        seal.unseal(ready.repo, request_for(ready))
    assert ready.remote_ledger() == before


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
    # "Early" is before each hypothesis has a selectable run of every registered
    # configuration or has exhausted its own entries and the reserve (section 11, items
    # 3 and 4; decision D21). Here H1's grid is complete and H1-03 passes development, but
    # H2 has not run: an unseal now would stop H2's grid early (G3.1 open question 10).
    # The key is never asked for, nothing is written or fetched; the append and replay
    # refuse the entry too.
    world = a_grid_world(tmp_path, monkeypatch, h2=False)
    try:
        issued_ledger = gate.gate_ledger(world.ledger)
        assert gate.holdout_candidate(issued_ledger).configuration_id == CANDIDATE
        assert "H2 has neither" in gate.unseal_early_error(issued_ledger)
        read, fetched = key(), []
        before = world.remote_ledger()
        with pytest.raises(SealRefused, match="early unseal: H2 has neither"):
            unseal(world, read=read, calls=fetched)
        assert read.calls == [] and fetched == [] and world.remote_ledger() == before
        with pytest.raises(VerificationError, match="early unseal"):
            append_unseal(world, unseal_body(world))
        assert world.remote_ledger() == before
        hand_append(world, unseal_body(world))
        with pytest.raises(VerificationError, match="early unseal"):
            world.ledger.verify()
    finally:
        world.close()


def test_the_unseal_check_does_not_read_the_clock(ready, monkeypatch):
    # T11, G1.6's half: "early" is what the ledger holds, never a date, so a clock set
    # before the pre-registration or after 2026-12-08 decides exactly as the true one.
    import time
    import types

    def refusals():
        out = []
        for request in (request_for(ready, "H1-01"), {**request_for(ready), "extra": 1}):
            try:
                unseal(ready, request, read=key())
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
    assert unseal(ready).type == "unseal"


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


@pytest.mark.parametrize("suffix", ["/", "/.", "//", "/./"])
def test_a_holdout_key_with_a_trailing_separator_is_refused(tmp_path, suffix):
    # Probe S1 (security review L1): the store decided "holdout month" by a suffix match,
    # which a trailing separator defeated. A key not in normal form is now refused.
    store = ArchiveStore(tmp_path)
    spec, _ = make_series(store, "spot/klines/XUSDT/1d", "2025-12", "2026-01")
    for key_ in (spec.key("2026-01") + suffix, "data/./" + spec.key("2026-01")[5:],
                 spec.key("2026-01").replace("/", "//", 1)):
        with pytest.raises((ValueError, PermissionError)):
            store.read(key_)
        with pytest.raises(ValueError, match="unsafe archive key"):
            store.path(key_)
    assert len(store.read(spec.key("2025-12"))[0]) > 0


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


def test_unseal_must_name_the_candidate_selected_by_the_registered_rule(ready):
    chosen = gate.holdout_candidate(gate.gate_ledger(ready.ledger))
    assert chosen.configuration_id == CANDIDATE
    read = key()
    for other in ("H1-01", "H1-08"):
        with pytest.raises(SealRefused, match=f"registered rule selects {CANDIDATE}"):
            unseal(ready, request_for(ready, other), read=read)
    assert read.calls == [] and unseal_of(remote_entries(ready)) is None
    # A hand-written unseal naming another configuration fails verification.
    hand_append(ready, unseal_body(ready, "H1-01"))
    with pytest.raises(VerificationError, match=f"registered rule selects {CANDIDATE}"):
        ready.ledger.verify()


def test_the_unseal_names_the_candidate_and_is_on_the_remote(ready):
    fetched = []
    entry = unseal(ready, calls=fetched)
    opened, closed = trial(ready, CANDIDATE)
    genesis = genesis_of(ready)
    assert entry.body["configuration_id"] == CANDIDATE
    assert entry.body["configuration_sha256"] == opened.body["configuration_sha256"]
    assert entry.body["candidate"] == {"open": opened.hash, "result": closed.hash}
    assert entry.body["preregistration"] == genesis.body["preregistration"]
    assert entry.body["accepted_diff"] == hashlib.sha256(b"").hexdigest()  # nothing changed
    assert unseal_of(remote_entries(ready)).hash == entry.hash
    assert ready.ledger.verify().snapshot.trial_count() == 14  # the unseal is no trial
    # D17: the holdout files were fetched again and verified, each with its CHECKSUM.
    assert sorted(fetched) == sorted(
        f"{DatasetSpec.from_name('spot/klines/XUSDT/1d').key(m)}{suffix}"
        for m in ("2026-01", "2026-02") for suffix in (".CHECKSUM", "")
    )
    assert ready.holdout_files_in_mirror() == [
        "XUSDT-1d-2026-01.zip", "XUSDT-1d-2026-01.zip.CHECKSUM",
        "XUSDT-1d-2026-02.zip", "XUSDT-1d-2026-02.zip.CHECKSUM",
    ]


def test_holdout_run_reads_holdout_rows_once_and_spends_the_seal(ready, monkeypatch):
    entry = unseal(ready)
    calls = []
    original = harness._run_strategy

    def watched(*args, **kwargs):
        remote = remote_entries(ready)
        held = {e.hash: e for e in remote}
        calls.append((kwargs.get("unsealed"), entry.hash in held, args[6] in held,
                      args[5]["window"]["last_return_day"], remote[-1].type))
        return original(*args, **kwargs)

    monkeypatch.setattr(harness, "_run_strategy", watched)
    record = holdout_run(ready)
    assert record.status == "completed", record.error
    # First the development window, without the unseal; then the holdout run, unsealed;
    # the remote held the unseal entry and the run's open entry at each call (T23), and
    # the attempt entry before the unsealed one (security review H2).
    assert calls == [
        (None, True, True, "2025-12-31", "open"),
        ((seal.HOLDOUT_MANIFEST, entry.hash), True, True, "2026-10-31", "attempt"),
    ]
    attempts = [e for e in remote_entries(ready) if e.type in ("attempt", "attempt_close")]
    assert [e.type for e in attempts] == ["attempt", "attempt_close"]
    assert attempts[1].body["outcome"] == "returned"
    head = git(ready.repo, "rev-parse", "HEAD")
    assert entry.body["commit"] == head == attempts[0].body["commit"]  # round 2, N1
    opened = record.open_entry.body
    assert opened["kind"] == "holdout" and opened["configuration_id"] == CANDIDATE
    assert opened["configuration"]["candidate_sha256"] == entry.body["configuration_sha256"]
    summary = record.result_entry.body["result"]
    assert (summary["development_days"], summary["holdout_days"]) == (2802, 304)
    snapshot = ready.ledger.verify().snapshot
    series = snapshot.returns_of(record.result_entry)
    committed = snapshot.returns_of(trial(ready, CANDIDATE)[1])
    assert series[:2802] == committed and series[-1][0] == "2026-10-31"
    assert snapshot.trial_count() == 14  # the holdout run is outside N


def test_holdout_run_of_another_configuration_is_refused(ready, monkeypatch):
    unseal(ready)
    started = []
    monkeypatch.setattr(harness, "_run_strategy", lambda *a, **k: started.append(a) or [])
    for other in ("H1-01", "H1-05"):
        with pytest.raises(harness.RunRefused, match="alone"):
            holdout_run(ready, trial(ready, other)[0].body["configuration_sha256"])
    with pytest.raises(RunNotRegistered, match="holds exactly"):
        harness.run(ready.repo, {"configuration": "holdout"}, store=ready.store)
    assert started == []
    # A hand-written holdout run of another configuration fails verification.
    hand_append(ready, holdout_open_body(ready, "H1-01"))
    with pytest.raises(VerificationError, match="H1-03's configuration"):
        ready.ledger.verify()


def test_second_holdout_result_after_one_unseal_is_refused(ready, monkeypatch):
    unseal(ready)
    original = harness._run_strategy
    crashes = [1]

    def crash_once(*args, **kwargs):
        if crashes:
            crashes.pop()
            raise RuntimeError("synthetic crash before any result")
        return original(*args, **kwargs)

    monkeypatch.setattr(harness, "_run_strategy", crash_once)
    first = holdout_run(ready)
    assert first.status == "failed"  # ledgered; the seal is not spent
    second = holdout_run(ready)  # a retry, same unseal and configuration (item 6)
    assert second.status == "completed", second.error
    with pytest.raises(harness.RunRefused, match="spends the seal"):
        holdout_run(ready)
    attempts = [e for e in remote_entries(ready) if e.type == "open" and e.body["kind"] == "holdout"]
    assert len(attempts) == 2
    # Verification refuses a third holdout run written by hand.
    hand_append(ready, holdout_open_body(ready))
    with pytest.raises(VerificationError, match="spends the seal"):
        ready.ledger.verify()


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


def test_a_holdout_run_at_code_changed_outside_data_loading_is_refused(ready):
    # Section 11, item 6, applied once, at the unseal (security review round 2, N1): the
    # unseal is refused at a commit that changed code outside data loading or the seal,
    # and every holdout run after it is at exactly the commit it records.
    candidate_commit = trial(ready, CANDIDATE)[0].body["commit"]
    unsealed_at = git(ready.repo, "rev-parse", "HEAD")
    paths = ["research/norse_research", "strategies/h1"]
    assert gitledger.holdout_commit_error(
        ready.ledger.git, candidate_commit, unsealed_at, paths) is None
    ready.write("research/norse_research/notes.txt", "a change outside data loading\n")
    ready.commit("a change outside data loading and the seal")
    ready.push()
    read = key()
    with pytest.raises(SealRefused, match="outside data loading and the seal"):
        unseal(ready, read=read)
    assert read.calls == [] and unseal_of(remote_entries(ready)) is None
    head = git(ready.repo, "rev-parse", "HEAD")
    git(ready.repo, "checkout", "--quiet", unsealed_at)
    entry = unseal(ready)
    assert entry.body["commit"] == unsealed_at
    git(ready.repo, "checkout", "--quiet", head)
    with pytest.raises(harness.RunRefused, match="every holdout run is at the unseal's commit"):
        holdout_run(ready)
    problem = gitledger.holdout_commit_error(ready.ledger.git, candidate_commit, head, paths)
    assert "notes.txt" in problem
    # Replay checks the unseal's commit against the candidate's, too: an unseal entry
    # naming the changed commit fails it.
    verified = ready.ledger.verify()
    forged = ledger.LedgerSnapshot(
        tuple(e if e.type != "unseal" else ledger.make_entry(resigned({**e.body, "commit": head}))
              for e in verified.snapshot.entries),
        verified.snapshot.series_files,
    )
    with pytest.raises(VerificationError, match="outside data loading and the seal"):
        ready.ledger._check_unseal(forged, verified.registration)
    # Security review L3: the runner exports the whole package, so the diff covers it even
    # when the genesis tree paths list less of it.
    narrow = gitledger.holdout_commit_error(ready.ledger.git, candidate_commit, head,
                                           ["strategies/h1"])
    assert "notes.txt" in narrow
    # A change to data loading or the seal alone is allowed.
    allowed = gitledger.SEAL_AND_DATA_LOADING
    assert "research/norse_research/loader.py" in allowed
    assert "research/norse_research/harness.py" not in allowed
    # The quant role's ruling on #70: the verification of the owner's signature stays the
    # code the trials ran under, so lamport.py stays out; so does the seal's launcher,
    # which runs before the candidate's code (review of #70, H-3). Widening the set has
    # to change this test.
    for frozen in ("lamport", "relaunch", "environment", "__init__", "gitledger", "ledger"):
        assert f"research/norse_research/{frozen}.py" not in allowed


def test_the_unseal_shows_the_data_loading_diff_and_needs_its_hash(tmp_path, monkeypatch):
    # Security review round 3, T1 (the reviewer's probe): a data loading change made
    # after the candidate's run, before the unseal, is allowed by item 6's file rule but
    # must be reviewed. The unseal prints the files, their diff and, at a terminal, the
    # hash of their diff, and refuses, before the key is asked for, unless the request
    # accepts exactly that hash; the entry records it and replay recomputes it.
    world = a_grid_world(tmp_path, monkeypatch, loader_changed=True)
    monkeypatch.setattr("sys.stdin", _Terminal())  # the owner's own terminal (I-7)
    try:
        candidate_commit = trial(world, CANDIDATE)[0].body["commit"]
        head = git(world.repo, "rev-parse", "HEAD")
        files, stat, digest = gitledger.seal_changes(world.ledger.git, candidate_commit, head)
        assert files == ["research/norse_research/loader.py"] and "loader.py" in stat
        assert digest != hashlib.sha256(b"").hexdigest()
        read, before = key(), world.remote_ledger()
        for wrong in (None, hashlib.sha256(b"").hexdigest(), "0" * 64):
            with pytest.raises(SealRefused, match=rf"(?s)loader\.py.*--accept-diff {digest}"):
                unseal(world, {**request_for(world), "accepted_diff": wrong}, read=read)
        assert read.calls == [] and world.remote_ledger() == before
        with pytest.raises(VerificationError, match="round 3, T1"):
            append_unseal(world, unseal_body(world, accepted_diff="0" * 64))
        assert world.remote_ledger() == before
        entry = unseal(world, {**request_for(world), "accepted_diff": digest}, fetch=False)
        assert entry.body["accepted_diff"] == digest and entry.body["commit"] == head
        verified = world.ledger.verify()
        forged = ledger.LedgerSnapshot(
            tuple(e if e.type != "unseal" else ledger.make_entry(
                resigned({**e.body, "accepted_diff": hashlib.sha256(b"").hexdigest()}))
                for e in verified.snapshot.entries),
            verified.snapshot.series_files,
        )
        with pytest.raises(VerificationError, match="round 3, T1"):
            world.ledger._check_unseal(forged, verified.registration)
    finally:
        world.close()


PRE_PUSH_HOOK = """#!/bin/sh
echo ran >> "MARKER"
while read lref lsha rref rsha; do
  if [ "$rref" = "refs/heads/ledger" ]; then
    line=$(git cat-file blob "$lsha:ledger/trials.jsonl" | tail -n 1)
    case "$line" in *'"type":"unseal"'*) printf '%s\\n' "$line" > "CAPTURE"; exit 1;; esac
  fi
done
exit 0
"""


def test_a_pre_push_hook_never_runs_in_the_owners_unseal(ready):
    # Security review of #65, H-1 (the reviewer's probe P3): a pre-push hook in the
    # clone's untracked .git/hooks, which leaves git status clean, copied the unseal
    # out of the push and failed it, so an agent could unseal with the stolen proof at a
    # commit of its own. Every ledger git command now runs with hooks off.
    marker, capture = ready.tmp / "hook-ran", ready.tmp / "captured-unseal.jsonl"
    hook = ready.repo / ".git" / "hooks" / "pre-push"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(PRE_PUSH_HOOK.replace("MARKER", str(marker)).replace("CAPTURE", str(capture)))
    hook.chmod(0o755)
    assert git(ready.repo, "status", "--porcelain") == ""
    git(ready.repo, "push", "--quiet", "origin", "HEAD:refs/heads/hook-check")
    assert marker.read_text() == "ran\n"  # an ordinary push in this clone runs the hook
    entry = unseal(ready, fetch=False)
    assert marker.read_text() == "ran\n" and not capture.exists()  # the unseal's did not
    assert unseal_of(remote_entries(ready)).hash == entry.hash


def test_a_local_textconv_cannot_hide_a_line_of_the_diff_to_accept(tmp_path, monkeypatch):
    # Security review of #65, M-1 (probe P4): untracked .git/info/attributes and a
    # textconv in .git/config hid a loader line from the owner's git diff, which the
    # refusal told the owner to run. A textconv in the clone's own configuration is now
    # refused outright (H-1's class); one in the owner's global configuration, which no
    # ledger command reads (M-3) but the clone's own git diff does, still cannot hide the
    # line, since the diff is rendered from the blobs.
    hidden = "_REBOUND = __import__('json').dumps  # hidden"
    world = a_grid_world(tmp_path, monkeypatch, loader_changed=True, loader_line=hidden)
    monkeypatch.setattr("sys.stdin", _Terminal())  # the owner's own terminal (I-7)
    try:
        (world.repo / ".git" / "info").mkdir(exist_ok=True)
        (world.repo / ".git" / "info" / "attributes").write_text("*.py diff=quiet\n")
        git(world.repo, "config", "diff.quiet.textconv", "grep -v hidden")
        read = key()
        with pytest.raises(gitledger.RemoteNotPinned, match=r"diff\.quiet\.textconv \(local\)"):
            unseal(world, {**request_for(world), "accepted_diff": None}, read=read)
        assert read.calls == []
        git(world.repo, "config", "--unset", "diff.quiet.textconv")
        owner = world.tmp / "owner-global.cfg"
        owner.write_text('[diff "quiet"]\n\ttextconv = grep -v hidden\n')
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(owner))
        assert git(world.repo, "status", "--porcelain") == ""
        candidate_commit = trial(world, CANDIDATE)[0].body["commit"]
        head = git(world.repo, "rev-parse", "HEAD")
        porcelain = git(world.repo, "diff", candidate_commit, head)
        assert "hidden" not in porcelain  # the clone's own git diff hides the line
        files, diff, digest = gitledger.seal_changes(world.ledger.git, candidate_commit, head)
        assert files == ["research/norse_research/loader.py"] and f"+{hidden}" in diff
        with pytest.raises(SealRefused) as refused:
            unseal(world, {**request_for(world), "accepted_diff": None})
        message = str(refused.value)
        assert message.index(f"+{hidden}") < message.index(f"--accept-diff {digest}")
        assert "git diff" not in message
        # Nor can a character hide a line on the terminal: control and format characters
        # are written as escapes, and so are tabs (L-10).
        assert gitledger._shown("ok\r# shown\x1b[8m‮\t") == "ok\\r# shown\\x1b[8m\\u202e\\t"
    finally:
        world.close()


def test_a_credential_helper_in_the_clone_refuses_the_unseal(ready):
    # Security review of #65, H-1's class: a credential helper in the clone's own
    # configuration would run inside the unseal's push, with the unseal's objects in
    # reach. It refuses the unseal before the passphrase is asked for; once it is gone,
    # the unseal is written and pushed as before.
    request = request_for(ready)
    for scope in ("--local", "--worktree"):
        if scope == "--worktree":
            git(ready.repo, "config", "extensions.worktreeConfig", "true")
        git(ready.repo, "config", scope, "credential.helper", "store")
        read = key()
        with pytest.raises(gitledger.RemoteNotPinned, match=r"credential\.helper \("):
            unseal(ready, request, read=read)
        assert read.calls == [] and unseal_of(remote_entries(ready)) is None
        git(ready.repo, "config", scope, "--unset", "credential.helper")
    entry = unseal(ready, request, fetch=False)
    assert unseal_of(remote_entries(ready)).hash == entry.hash


def test_the_unseal_refuses_a_credential_program_in_its_environment(ready, monkeypatch):
    # Security review of #65, H-1's class: GIT_ASKPASS and SSH_ASKPASS are programs git
    # runs for a credential inside the unseal's push, so the unseal refuses them; GIT_SSH,
    # GIT_SSH_COMMAND, GIT_EXEC_PATH and GIT_CONFIG_PARAMETERS are refused for every
    # ledger write. Each is refused before the passphrase is asked for.
    request = request_for(ready)
    for name, refused in (("GIT_ASKPASS", SealRefused), ("SSH_ASKPASS", SealRefused),
                          ("GIT_SSH", EnvironmentRefused),
                          ("GIT_SSH_COMMAND", EnvironmentRefused),
                          ("GIT_EXEC_PATH", EnvironmentRefused),
                          ("GIT_CONFIG_PARAMETERS", EnvironmentRefused)):
        monkeypatch.setenv(name, "/bin/echo")
        read = key()
        with pytest.raises(refused, match=name):
            unseal(ready, request, read=read)
        monkeypatch.delenv(name)
        assert read.calls == [] and unseal_of(remote_entries(ready)) is None


def test_a_gitattributes_change_since_the_candidate_refuses_the_unseal(ready):
    # Security review of #65, M-1 (probe P4b): a committed .gitattributes marking the
    # package binary sat outside item 6's paths, so git showed the owner "Binary files
    # differ". A change to any .gitattributes file, the root's or a nested one, now
    # refuses the unseal, before the key is asked for, and fails replay.
    candidate_commit = trial(ready, CANDIDATE)[0].body["commit"]
    for path in (".gitattributes", "research/.gitattributes"):
        ready.write(path, "norse_research/*.py -diff\nresearch/norse_research/*.py -diff\n")
        head = ready.commit(f"attributes at {path}")
        ready.push()
        paths = genesis_of(ready).body["tree_paths"]
        problem = gitledger.holdout_commit_error(
            ready.ledger.git, candidate_commit, head, [*paths["harness"], *paths["H1"]]
        )
        assert path in problem
        read = key()
        with pytest.raises(SealRefused, match=re.escape(path)):
            unseal(ready, read=read)
        assert read.calls == [] and unseal_of(remote_entries(ready)) is None
        git(ready.repo, "rm", "--quiet", path)
        ready.commit(f"remove {path}")
        ready.push()


def test_the_candidate_command_prints_neither_the_diff_nor_its_hash(tmp_path, monkeypatch, capsys):
    # Security review of #65, L-2: the key-free candidate command printed the hash to
    # accept without the diff it covers. It now lists the files and points to the unseal,
    # whose refusal shows the full diff and only then the hash.
    world = a_grid_world(tmp_path, monkeypatch, loader_changed=True)
    try:
        digest = accepted_diff(world)
        opened = trial(world, CANDIDATE)[0]
        candidate = opened.body["commit"]
        assert seal.main(["--repo", str(world.repo), "candidate",
                          "--candidate-commit", candidate]) == 0
        out = capsys.readouterr().out
        printed = json.loads(out[out.index("{\n"):])
        named = printed["holdout_candidate"]
        assert named["seal_files_changed_since"] == ["research/norse_research/loader.py"]
        assert "without --accept-diff" in named["diff_to_accept"]
        assert digest not in out and "accept_diff_at_head" not in out
        # The two pins the unseal needs (review of #70, M-4), the candidate's commit whose
        # code ran this (H-3, H-4), and the git every ledger command runs (L-6).
        head = git(world.repo, "rev-parse", "HEAD")
        assert named["unseal_commit"] == head and named["ledger_head"] == world.ledger.local_head()
        assert named["candidate_commit"] == candidate != head
        assert named["candidate_open_entry"] == opened.hash
        assert printed["git"] == environment.GIT_PROGRAM
        # The owner's procedure, filled in: check C on GitHub, export C's code with plain
        # git, run the unseal from there (review of #70, round 2).
        steps = "\n".join(named["unseal_procedure"])
        assert f"check that {candidate} is the commit of {CANDIDATE}'s open entry" in steps
        assert (f"{environment.GIT_PROGRAM} -C {world.repo} archive {candidate} research | "
                f'tar -x -C "$folder"') in steps
        assert (f"-m norse_research.seal --repo {world.repo} unseal --candidate-commit "
                f"{candidate}") in steps
        assert f"--unseal-commit {head} --ledger-head {named['ledger_head']}" in steps
        # Another commit is refused, naming the one the rule selects (H-4).
        with pytest.raises(SealRefused, match=f"selectable run, open entry {opened.hash}"):
            seal.main(["--repo", str(world.repo), "candidate", "--candidate-commit", head])
        with pytest.raises(SystemExit):
            seal.main(["--repo", str(world.repo), "candidate"])  # the pin is required
    finally:
        world.close()


def test_without_a_terminal_the_unseal_shows_the_diff_but_not_its_hash(tmp_path, monkeypatch):
    # Security review of #65, I-7: an agent could run the unseal without --accept-diff,
    # which needs no key, and read the hash to hand the owner a ready-made command line.
    # Without an interactive terminal, the test the passphrase prompt applies, the refusal
    # prints the diff and not the hash; at the owner's terminal it prints both.
    world = a_grid_world(tmp_path, monkeypatch, loader_changed=True)
    try:
        digest, request = accepted_diff(world), {**request_for(world), "accepted_diff": None}
        read = key()
        monkeypatch.setattr("sys.stdin", io.StringIO(""))  # an agent's shell: no terminal
        with pytest.raises(SealRefused) as refused:
            unseal(world, request, read=read)
        message = str(refused.value)
        assert "+# as the runs had it" in message and digest not in message
        assert "interactive terminal" in message and "I-7" in message
        monkeypatch.setattr("sys.stdin", _Terminal())
        with pytest.raises(SealRefused, match=rf"(?s)\+# as the runs had it.*--accept-diff {digest}"):
            unseal(world, request, read=read)
        assert read.calls == [] and unseal_of(remote_entries(world)) is None
    finally:
        world.close()


def test_a_padded_line_in_the_diff_to_accept_is_wrapped_and_flagged(tmp_path, monkeypatch):
    # Security review of #65, L-10: a loader line padded with 400 spaces kept its rebinding
    # past the right edge of a pager that cuts long lines. Every line wider than
    # DIFF_WIDTH is now flagged, then shown in pieces that each end in a visible mark, and
    # a tab is written as an escape, so its width on the screen is its length.
    padded = "_SEAL_OK = True" + " " * 400 + "; __import__('json').dumps = repr"
    world = a_grid_world(tmp_path, monkeypatch, loader_changed=True, loader_line=padded)
    try:
        candidate_commit = trial(world, CANDIDATE)[0].body["commit"]
        head = git(world.repo, "rev-parse", "HEAD")
        _, diff, _ = gitledger.seal_changes(world.ledger.git, candidate_commit, head)
        lines = diff.splitlines()
        content = [line for line in lines if line[:1] in ("+", "-", " ", "@", "!")]
        assert max(len(line) for line in content) <= gitledger.DIFF_WIDTH + 2
        flagged = next(i for i, line in enumerate(lines) if line.startswith("! the next line"))
        assert f"is {1 + len(padded)} characters long" in lines[flagged]
        pieces = lines[flagged + 1 : flagged + 6]
        assert all(p.startswith("+") and p.endswith(gitledger.WRAP_MARK) for p in pieces)
        assert "".join(p[1:-1] for p in pieces) == padded
        assert "dumps = repr" in pieces[-1] and "_SEAL_OK" not in "".join(lines[flagged + 6:])
        assert gitledger._fitted("+short") == ["+short"]
    finally:
        world.close()


def test_replay_checks_the_accepted_diff_hash_without_rendering_it(tmp_path, monkeypatch):
    # Security review of #65, L-7: replay rendered the whole data loading diff on every
    # verify, which took 45 to 65 seconds for a large table. Replay, the append's check,
    # an accepted unseal and the candidate command now hash the raw record only; only the
    # unseal's refusal renders the diff.
    world = a_grid_world(tmp_path, monkeypatch, loader_changed=True)
    try:
        request = request_for(world)

        def no_rendering(*args, **kwargs):
            raise AssertionError("the diff was rendered")

        monkeypatch.setattr(gitledger, "render_diff", no_rendering)
        monkeypatch.setattr(seal, "render_diff", no_rendering)
        entry = unseal(world, request, fetch=False)
        assert entry.body["accepted_diff"] == request["accepted_diff"]
        assert unseal_of(world.ledger.verify().snapshot.entries).hash == entry.hash
        assert seal.main(["--repo", str(world.repo), "candidate", "--candidate-commit",
                          request["candidate_commit"]]) == 0
    finally:
        world.close()


def test_a_global_filter_never_runs_in_the_unseal(ready, monkeypatch):
    # Security review of #65, M-3 (probe N2): a filter in the owner's global configuration,
    # which an agent can write, named by an untracked .git/info/attributes, ran inside the
    # unseal's clean-tree check before the passphrase was asked for, and nothing refused
    # it. Every ledger git command now runs without the global or system configuration, so
    # the clone's own git still runs the filter and no ledger command does.
    marker = ready.tmp / "filter-ran"
    program = ready.tmp / "clean.sh"
    program.write_text(f"#!/bin/sh\necho ran >> {marker}\ncat\n")
    program.chmod(0o755)
    owner = ready.tmp / "owner-global.cfg"
    owner.write_text(f'[filter "x"]\n\tclean = {program}\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(owner))
    (ready.repo / ".git" / "info").mkdir(exist_ok=True)
    (ready.repo / ".git" / "info" / "attributes").write_text("*.py filter=x\n")
    loader = ready.repo / "research" / "norse_research" / "loader.py"
    os.utime(loader, (946684800, 946684800))  # stat-dirty, so status reads it again
    git(ready.repo, "status", "--porcelain")
    assert marker.exists()  # the clone's own git runs it
    marker.unlink()
    os.utime(loader, (946684801, 946684801))
    ready.ledger.check_repository()
    asked = []

    def read(prompt):
        asked.append(marker.exists())
        return TEST_PASSPHRASE

    entry = unseal(ready, read=read, fetch=False)
    assert asked == [False] and not marker.exists()  # no ledger command ran it
    assert unseal_of(remote_entries(ready)).hash == entry.hash


def test_the_clean_tree_check_reads_no_ignore_file_under_home(ready, monkeypatch):
    # Review of #70, I-8 (probe): HOME is passed to git, so the default ignore file under
    # it, ~/.config/git/ignore, hid an untracked module from the clean-tree check. That
    # check now reads neither it nor the attributes file beside it.
    home = ready.tmp / "home"
    (home / ".config" / "git").mkdir(parents=True)
    (home / ".config" / "git" / "ignore").write_text("stray_module.py\n")
    monkeypatch.setenv("HOME", str(home))
    # The ledger's git gets HOME but never XDG_CONFIG_HOME, so this is the file it reads
    # by default (GitHub's runners set XDG_CONFIG_HOME, which moves the fixtures' own).
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    (ready.repo / "research" / "stray_module.py").write_text("X = 1\n")
    plain = ready.ledger.git.text("status", "--porcelain", "--untracked-files=all")
    assert "stray_module.py" not in plain  # the ledger's own git, unqualified, hides it
    with pytest.raises(harness.RunRefused, match=r"dirty.*stray_module\.py"):
        harness.check_clean_tree(ready.ledger.git)


def test_the_unseal_command_names_the_genesis_tag_and_asks_for_the_diff(ready, monkeypatch):
    # The command line's --tag defaults to the tag the genesis entry names (D21 moved the
    # default to prereg-v2, and a ledger begun under another tag must still unseal), and
    # without --accept-diff it prints, at a terminal, the hash to accept and refuses (T1;
    # I-7).
    monkeypatch.setattr("sys.stdin", _Terminal())

    class NoNetwork:
        def __call__(self, url, max_bytes):
            raise AssertionError("no network in tests")

        def close(self):
            pass

    monkeypatch.setattr(seal, "HttpFetcher", NoNetwork)
    monkeypatch.setenv("NORSE_DATASETS_DIR", str(ready.store.root))
    before = ready.remote_ledger()
    sha = trial(ready, CANDIDATE)[0].body["configuration_sha256"]
    command = ["--repo", str(ready.repo), "unseal", "--commit", ready.prereg_commit,
               "--configuration-sha256", sha,
               "--candidate-commit", trial(ready, CANDIDATE)[0].body["commit"],
               "--unseal-commit", git(ready.repo, "rev-parse", "HEAD"),
               "--ledger-head", ready.ledger.local_head()]
    with pytest.raises(SealRefused, match=f"--accept-diff {accepted_diff(ready)}"):
        seal.main(command)
    with pytest.raises(SealRefused, match="pre-registration other than"):
        seal.main([*command, "--tag", "prereg-v1"])
    # The pins are required, and spelt out in full (review of #70, M-4 and round 2, H-4).
    for missing in ("--candidate-commit", "--unseal-commit", "--ledger-head"):
        index = command.index(missing)
        with pytest.raises(SystemExit):
            seal.main(command[:index] + command[index + 2:])
    with pytest.raises(SystemExit):
        seal.main([*command[:-2], "--ledger", command[-1]])  # no abbreviation
    assert ready.remote_ledger() == before and ready.holdout_files_in_mirror() == []


def test_no_candidate_means_no_unseal(tmp_path, monkeypatch):
    world = a_grid_world(tmp_path, monkeypatch, skilled=None)
    try:
        issued_ledger = gate.gate_ledger(world.ledger)
        judged = [gate.development(issued_ledger, h) for h in ("H1", "H2")]
        assert all(j is not None and not j.passes for j in judged)  # both grids, no pass
        assert gate.unseal_early_error(issued_ledger) is None
        assert gate.holdout_candidate(issued_ledger) is None
        read = key()
        with pytest.raises(SealRefused, match="no holdout candidate"):
            unseal(world, read=read)
        assert read.calls == [] and unseal_of(remote_entries(world)) is None
        hand_append(world, unseal_body(world, "H1-01"))
        with pytest.raises(VerificationError, match="no hypothesis passes development"):
            world.ledger.verify()
    finally:
        world.close()


# --- decision D21: no freeze; the unseal closes development ----------------------------


def test_the_unseal_closes_development(ready):
    # After the unseal no trial, benchmark or control runs, through the harness, the
    # public append or by hand, and the gate computes N, V and the candidate on the
    # entries before it. The candidates' sensitivity runs still may, outside N.
    current = gate.gate_ledger(ready.ledger)
    assert not current.closed and current.commit == ready.ledger.local_head()
    entry = unseal(ready, fetch=False)
    for cid in ("H2-01", "B-H1"):
        with pytest.raises(harness.RunRefused, match="closed development"):
            harness.run(ready.repo, {"configuration": cid}, store=ready.store)
    assert rules.after_unseal_error(remote_entries(ready), "control") is not None
    before = ready.remote_ledger()
    with pytest.raises(LedgerOutOfStep, match="closed development"):
        ready.ledger.append(ready.open_body("H2-01"))
    assert ready.remote_ledger() == before
    with pytest.raises(harness.RunRefused, match="not H1's candidate"):
        harness.run(ready.repo, {"configuration": "H1-01",
                                 "sensitivity_total_cost_bps_per_side": 7.5}, store=ready.store)
    record = harness.run(ready.repo, {"configuration": CANDIDATE,
                                      "sensitivity_total_cost_bps_per_side": 7.5},
                         store=ready.store)
    assert record.status == "completed", record.error
    closed = gate.gate_ledger(ready.ledger)
    assert closed.closed and closed.commit is None and len(closed.snapshot.entries) == entry.seq
    assert closed.snapshot.data == current.snapshot.data
    assert gate.trial_count(closed) == 14
    assert gate.holdout_candidate(closed).configuration_id == CANDIDATE
    hand_append(ready, ready.open_body("H2-01"))
    with pytest.raises(VerificationError, match="trial run after the unseal"):
        ready.ledger.verify()


def test_a_trial_admitted_before_the_unseal_cannot_land_after_it(ready, monkeypatch):
    # Another session unseals between this run's admission (a control run, since both
    # grids are complete) and its open entry. The unseal moves the ledger head, so the
    # append's check of the admitted head refuses (M2); the rule is also checked under
    # the append lock. The freeze's race, which needed follow-up N2b because the freeze
    # did not move the head, cannot arise.
    original = gitledger.GitLedger.append
    state = {"done": False}

    def append(self, body, files=None, **kwargs):
        if body.get("type") == "open" and not state["done"]:
            state["done"] = True
            unseal(ready, fetch=False)
        return original(self, body, files, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(gitledger.GitLedger, "append", append)
        with pytest.raises(LedgerOutOfStep, match="after this run was admitted"):
            harness.run(ready.repo, {"configuration": "C-H2", "seed": 0}, store=ready.store)
    assert state["done"] and unseal_of(remote_entries(ready)) is not None
    with pytest.raises(LedgerOutOfStep, match="closed development"):
        ready.ledger.append(ready.open_body("H2-01"))
    assert ledger.trial_count(remote_entries(ready)) == 14
    assert not [e for e in remote_entries(ready) if e.type == "open" and e.body["kind"] == "control"]
    ready.ledger.verify()


# --- T23: once, on the remote, before any holdout row ----------------------------------


def test_second_unseal_is_refused(ready):
    first = unseal(ready)
    read = key()
    with pytest.raises(SealRefused, match="second unseal"):
        unseal(ready, read=read)
    assert read.calls == []
    assert [e.hash for e in remote_entries(ready) if e.type == "unseal"] == [first.hash]
    with pytest.raises(VerificationError, match="second unseal"):
        append_unseal(ready, unseal_body(ready))
    hand_append(ready, unseal_body(ready))
    with pytest.raises(VerificationError, match="second unseal"):
        ready.ledger.verify()


def test_unseal_on_a_ledger_behind_the_remote_is_refused(ready):
    request = request_for(ready)
    head = ready.ledger.local_head()
    git(ready.repo, "update-ref", "refs/heads/ledger", f"{head}^")
    read = key()
    with pytest.raises(LedgerOutOfStep, match="behind"):
        unseal(ready, request, read=read)
    assert read.calls == []
    git(ready.repo, "update-ref", "refs/heads/ledger", head)
    # Another clone unseals; this one, now behind it, cannot unseal again (T23).
    other = ready.clone("other")
    seal.unseal(other.git.repo, request, read_key=key())
    with pytest.raises(LedgerOutOfStep, match="behind"):
        unseal(ready, request, read=read)
    assert read.calls == []


def test_holdout_rows_require_the_unseal_entry_on_the_remote(ready):
    with pytest.raises(SealRefused, match="does not hold the unseal entry"):
        seal.require_unseal_on_remote(ready.ledger, "e" * 64)
    before = git(ready.remote, "rev-parse", "refs/heads/ledger")
    entry = unseal(ready)
    seal.require_unseal_on_remote(ready.ledger, entry.hash)
    # The loader returns holdout rows only inside the unsealed run.
    manifest = Manifest.load(ready.repo / "research/manifests/binance-vision-development.json")
    holdout = Manifest.load(ready.repo / seal.HOLDOUT_MANIFEST)
    loader = DatasetLoader(manifest, ready.store, holdout=holdout)
    start, end = datetime(2025, 12, 30, tzinfo=UTC), datetime(2026, 1, 3, tzinfo=UTC)
    with inside_ledger_entry("f" * 64):
        # The loader refuses before any file is read; the store would refuse too.
        with pytest.raises(HoldoutSealed, match="rows load only inside the holdout run"):
            loader.load("spot/klines/XUSDT/1d", start, end)
        with inside_unsealed_run(entry.hash):
            assert len(loader.load("spot/klines/XUSDT/1d", start, end).rows) == 4
    with pytest.raises(HoldoutSealed, match="rows load only inside the holdout run"):
        DatasetLoader(manifest, ready.store).load("spot/klines/XUSDT/1d", start, end)
    # A remote that lost the unseal entry (rewritten, T10) seals the holdout again.
    git(ready.remote, "update-ref", "refs/heads/ledger", before)
    with pytest.raises(SealRefused, match="does not hold the unseal entry"):
        seal.require_unseal_on_remote(ready.ledger, entry.hash)
    with pytest.raises(LedgerOutOfStep):
        holdout_run(ready)


@pytest.mark.parametrize("failing", ["commit-tree", "push"])
def test_unseal_is_written_to_the_ledger_before_any_holdout_row_is_returned(
    ready, monkeypatch, failing
):
    before = ready.remote_ledger()
    original = gitledger.Git.run

    def broken(self, *args, **kwargs):
        if args and args[0] == failing:
            raise gitledger.GitError(f"git {failing} failed: injected")
        return original(self, *args, **kwargs)

    fetched = []
    with monkeypatch.context() as m:
        m.setattr(gitledger.Git, "run", broken)
        with pytest.raises(LedgerWriteError):
            unseal(ready, calls=fetched)
    assert fetched == [] and ready.holdout_files_in_mirror() == []
    assert ready.remote_ledger() == before
    with pytest.raises(harness.RunRefused, match="needs the unseal entry"):
        holdout_run(ready)
    # When it is written, the remote holds it before the first file is fetched: the
    # re-fetch is a separate step after the unseal, at HEAD (review of #70, H-3).
    seen = []
    fetch = ready.fetcher()

    def watching(url, max_bytes):
        seen.append(any(e.type == "unseal" for e in remote_entries(ready)))
        return fetch(url, max_bytes)

    seal.unseal(ready.repo, request_for(ready), read_key=key())
    assert ready.holdout_files_in_mirror() == []  # the unseal itself fetches nothing
    seal.fetch_holdout(ready.repo, store=ready.store, fetch=watching)
    assert seen and all(seen)


def test_a_holdout_file_that_differs_from_the_manifest_stops_the_refetch(ready):
    spec = DatasetSpec.from_name("spot/klines/XUSDT/1d")
    path = ready.archive.path(spec.key("2026-02"))
    checksum = path.with_name(path.name + ".CHECKSUM")
    offline = {"": path.read_bytes(), ".CHECKSUM": checksum.read_bytes()}  # the owner's copy
    data = bytearray(offline[""])
    data[-30] ^= 1  # the archive re-published it: a new file with its own CHECKSUM
    path.write_bytes(bytes(data))
    checksum.write_text(f"{seal.sha256_hex(bytes(data))}  {path.name}\n")
    with pytest.raises(SealRefused, match="not the one the frozen holdout manifest pins"):
        unseal(ready)
    entry = unseal_of(remote_entries(ready))
    assert entry is not None  # the unseal stands; the run cannot start
    with pytest.raises(harness.RunRefused, match="do not all match"):
        holdout_run(ready)
    # The owner's offline copy restores the file; fetch-holdout then verifies and goes on.
    target = ready.store.path(spec.key("2026-02"))
    target.parent.mkdir(parents=True, exist_ok=True)
    for suffix, content in offline.items():
        target.with_name(target.name + suffix).write_bytes(content)
    assert seal.fetch_holdout(ready.repo, store=ready.store, fetch=ready.fetcher()) == 2
    assert holdout_run(ready).status == "completed"


# --- T24 and T25: the registration and the key --------------------------------------


def test_unseal_naming_another_preregistration_hash_is_refused(ready):
    read = key()
    for change in (
        lambda p: p["files"].update({"docs/preregistration.json": "0" * 64}),
        lambda p: p["files"].update({"docs/PREREGISTRATION.md": "1" * 64}),
        lambda p: p.update({"commit": "a" * 40}),
        lambda p: p.update({"tag": "prereg-v1"}),
    ):
        request = request_for(ready)
        change(request["preregistration"])
        with pytest.raises(SealRefused, match="pre-registration other than"):
            unseal(ready, request, read=read)
    assert read.calls == []
    tag = gitledger.PREREGISTRATION_TAG
    named = seal.preregistration_at(ready.ledger.git, tag, tag)
    assert named == genesis_of(ready).body["preregistration"]
    other = unseal_body(ready)
    other["preregistration"] = {**other["preregistration"], "commit": "a" * 40}
    hand_append(ready, other)
    with pytest.raises(VerificationError, match="pre-registration other than"):
        ready.ledger.verify()


def test_unseal_without_the_key_is_refused(ready):
    before = ready.remote_ledger()
    for read in (key(""), key(None)):
        with pytest.raises(SealRefused, match="no key"):
            unseal(ready, read=read)
        assert read.calls  # asked, after every other check passed

    def no_terminal(prompt):
        raise NoTerminal("no terminal")

    with pytest.raises(NoTerminal):
        unseal(ready, read=no_terminal)
    assert ready.remote_ledger() == before and ready.holdout_files_in_mirror() == []


def test_unseal_with_a_wrong_key_is_refused(ready):
    before = ready.remote_ledger()
    for wrong in (OTHER_KEY, TEST_PASSPHRASE + " ", TEST_PASSPHRASE.upper()):
        with pytest.raises(SealRefused, match="does not verify"):
            unseal(ready, read=key(wrong))
    assert ready.remote_ledger() == before
    assert unseal(ready).type == "unseal"


def test_key_record_is_read_from_the_genesis_preregistration_not_the_working_tree(ready):
    # A key record for a passphrase someone else knows, committed on main where the
    # pinned one was, changes nothing: the seal reads the one genesis pins (T25).
    substitute = seal.make_key_record(OTHER_KEY, salt=bytes(16))
    ready.write(KEY_RECORD, seal.key_record_bytes(substitute))
    ready.commit("a substituted key record")
    ready.push()
    with pytest.raises(SealRefused, match="does not verify"):
        unseal(ready, read=key(OTHER_KEY))
    entry = unseal(ready)
    assert entry.body["key_record_sha256"] == genesis_of(ready).body["key_record"]["sha256"]
    record = seal.key_record_of(ready.ledger.git, genesis_of(ready))
    assert record == KEY_RECORD_DOC


def test_an_unseal_written_without_the_key_is_refused_by_the_ledger(ready):
    # Security review H1 (probe K1), with D27's signature: the key is on the path that
    # opens the holdout. The public append refuses an unseal with no signature, a
    # misshapen one, or one by another passphrase's key, and replay refuses one written
    # past append by plumbing, so neither the re-fetch nor the holdout run can start.
    before = ready.remote_ledger()
    with pytest.raises(VerificationError, match="unseal entry holds"):
        ready.ledger.append(unseal_body(ready))
    short = {"preimages": SHAPED_SIGNATURE["preimages"][:-1],
             "complements": SHAPED_SIGNATURE["complements"]}
    for signature in (short, {**SHAPED_SIGNATURE, "proof": output_of()}, output_of()):
        with pytest.raises(VerificationError, match="signature"):
            ready.ledger.append(unseal_body(ready, signature=signature))
    with pytest.raises(VerificationError, match="does not verify"):
        append_unseal(ready, unseal_body(ready), passphrase=OTHER_KEY)
    assert ready.remote_ledger() == before
    hand_append(ready, unseal_body(ready, signature=SHAPED_SIGNATURE))  # shaped, not signed
    with pytest.raises(VerificationError, match="does not verify"):
        ready.ledger.verify()
    with pytest.raises(VerificationError, match="does not verify"):
        seal.fetch_holdout(ready.repo, store=ready.store, fetch=ready.fetcher())
    with pytest.raises(VerificationError, match="does not verify"):
        holdout_run(ready)
    assert ready.holdout_files_in_mirror() == []


def test_the_unseal_entry_is_signed_by_the_key_the_record_commits_to(ready):
    # Decision D27: the entry carries a one-time signature over its own bytes, which
    # verifies against the record's signer_sha256, and reveals nothing of the key: neither
    # the scrypt output nor the seed is in the entry or the ledger.
    entry = unseal(ready, fetch=False)
    assert "proof" not in entry.body
    assert lamport.signature_error(KEY_RECORD_DOC["signer_sha256"], ledger.signed_bytes(entry.body),
                                   entry.body["signature"]) is None
    data = ready.remote_ledger().decode()
    assert output_of() not in data and seed_for().hex() not in data
    assert entry.body["time"] == remote_entries(ready)[entry.seq - 1].body["time"]
    ready.ledger.verify()


def test_a_stolen_unseal_signature_authorises_only_its_own_entry(ready, monkeypatch):
    # Security review of #65, H-2 (probe N1): a same-user process read the unseal out of
    # the temporary object directory while it was pushed and failed the push; an agent
    # then appended its own unseal through the public append, at a commit nobody
    # reviewed, with the stolen proof, and verify() passed. Here the theft and the failure
    # are made deterministic: the push's own objects are read and the push is killed.
    # Since D27 what is stolen is a signature over the owner's entry: the agent's unseal is
    # refused, and the one thing the copy can still do is land the very entry the owner
    # reviewed and signed, at the owner's commit.
    original = gitledger.Git.run
    stolen = []

    def steal_then_fail(self, *args, **kwargs):
        objects = kwargs.get("env") or {}
        if args and args[0] == "push" and "GIT_OBJECT_DIRECTORY" in objects:
            commit = args[-1].split(":", 1)[0]
            data = original(self, "cat-file", "blob", f"{commit}:{LEDGER_PATH}", env=objects)
            stolen.append(json.loads(data.decode().splitlines()[-1]))
            raise gitledger.GitError("git push failed: killed by another process")
        return original(self, *args, **kwargs)

    owners_head = git(ready.repo, "rev-parse", "HEAD")
    with monkeypatch.context() as m:
        m.setattr(gitledger.Git, "run", steal_then_fail)
        with pytest.raises(LedgerWriteError, match="could not be pushed"):
            unseal(ready, fetch=False)
    assert unseal_of(remote_entries(ready)) is None
    (copy,) = stolen
    assert copy["type"] == "unseal" and copy["commit"] == owners_head and "proof" not in copy
    # The agent commits a data loading change nobody reviewed and unseals there with the
    # stolen signature, as the probe did with the stolen proof.
    loader = "research/norse_research/loader.py"
    ready.write(loader, (ready.repo / loader).read_text() + "\n_REWRITE_2026 = True\n")
    head = ready.commit("loader: not reviewed by anyone")
    ready.push()
    body = {k: v for k, v in copy.items() if k not in ("seq", "prev", "time")}
    before = ready.remote_ledger()
    with pytest.raises(VerificationError, match="does not verify"):
        ready.ledger.append({**body, "commit": head, "accepted_diff": accepted_diff(ready)})
    with pytest.raises(VerificationError, match="does not verify"):
        ready.ledger.append({**body, "commit": head})
    assert ready.remote_ledger() == before
    # The copy unchanged is the owner's own entry: it lands recording the commit the owner
    # reviewed, where alone the holdout can run (rules), and replay accepts it.
    landed = ready.ledger.append(body)
    assert landed.line == ledger.make_entry(copy).line and landed.body["commit"] == owners_head
    assert unseal_of(ready.ledger.verify().snapshot.entries).hash == landed.hash


def test_a_forged_or_moved_unseal_signature_is_refused(ready):
    # Decision D27: a signature of random values, or the owner's own signature moved onto
    # another body, or onto the same body at another place in the chain, is refused by the
    # append before anything is pushed, and by replay when written past it.
    before = ready.remote_ledger()
    forged = {"preimages": [secrets.token_hex(32) for _ in range(lamport.BITS)],
              "complements": [secrets.token_hex(32) for _ in range(lamport.BITS)]}
    with pytest.raises(VerificationError, match="does not verify"):
        ready.ledger.append(unseal_body(ready, signature=forged))
    entries = remote_entries(ready)
    owners = {**unseal_body(ready), "seq": len(entries), "prev": entries[-1].hash,
              "time": entries[-1].body["time"]}
    signature = sign_as_owner(ledger.signed_bytes(owners))
    assert lamport.signature_error(KEY_RECORD_DOC["signer_sha256"], ledger.signed_bytes(owners),
                                   signature) is None
    for change in ({"accepted_diff": "0" * 64}, {"commit": "1" * 40},
                   unseal_body(ready, "H1-01")):  # another candidate, whole
        moved = {**unseal_body(ready), **change, "signature": signature}
        with pytest.raises(VerificationError, match="does not verify"):
            ready.ledger.append(moved)
    elsewhere = sign_as_owner(ledger.signed_bytes({**owners, "prev": "3" * 64, "seq": 1}))
    with pytest.raises(VerificationError, match="does not verify"):
        ready.ledger.append(unseal_body(ready, signature=elsewhere))
    assert ready.remote_ledger() == before
    hand_append(ready, unseal_body(ready, signature=signature, accepted_diff="0" * 64))
    with pytest.raises(VerificationError, match="does not verify"):
        ready.ledger.verify()
    with pytest.raises(VerificationError, match="does not verify"):
        holdout_run(ready)


def test_a_key_record_whose_signer_does_not_match_is_refused(tmp_path, monkeypatch):
    # Decision D27: a record whose signer_sha256 is not the one its passphrase derives
    # (here the test passphrase's verifier beside another passphrase's signer) meets the
    # contract, which cannot tell, but check-key and the unseal refuse it before anything
    # is signed, and an unseal signed with the test key fails replay under it.
    mixed = {**KEY_RECORD_DOC, "signer_sha256": seal.make_key_record(
        OTHER_KEY, salt=bytes.fromhex(KEY_RECORD_DOC["salt"]))["signer_sha256"]}
    check_key_record(seal.key_record_bytes(mixed))
    with pytest.raises(SealRefused, match="does not derive its signer_sha256"):
        seal.check_key(mixed, TEST_PASSPHRASE)
    clear_overrides(monkeypatch)
    world = World(tmp_path, genesis=False)
    try:
        world.write("research/keys/mixed.json", seal.key_record_bytes(mixed))
        commit = world.commit("a record whose signer the passphrase does not derive")
        world.push()
        world.write_genesis(key_record=(commit, "research/keys/mixed.json"))
        with pytest.raises(SealRefused, match="does not derive its signer_sha256"):
            seal.check_genesis_key(world.repo, read_key=key())
        body = resigned({"type": "unseal", "seq": 1, "prev": "4" * 64,
                         "time": "2026-11-22T00:00:00Z", "commit": "5" * 40})
        problem = gitledger.unseal_signature_error(world.ledger.git, genesis_of(world), body)
        assert problem is not None and "does not verify" in problem
        assert lamport.signature_error(KEY_RECORD_DOC["signer_sha256"], ledger.signed_bytes(body),
                                       body["signature"]) is None  # the honest record's
    finally:
        world.close()


def test_check_key_verifies_the_genesis_record_and_writes_nothing(tmp_path, monkeypatch, capsys):
    # Security review M2: the owner checks the record genesis pinned straight after
    # genesis, so a record substituted there is found at once, not at the unseal.
    clear_overrides(monkeypatch)
    world = World(tmp_path / "honest")
    try:
        before = world.remote_ledger()
        pinned = genesis_of(world).body["key_record"]["sha256"]
        # Review of #70, L-11: the genesis entry pins this world's synthetic registration,
        # not the one the code pins, so check-key refuses it, after the key verified.
        with pytest.raises(SealRefused, match="is not the registration this code implements"):
            seal.check_genesis_key(world.repo, read_key=key())
        monkeypatch.setattr(gitledger, "PREREGISTRATION_SHA256", world.registration_sha256)
        assert seal.check_genesis_key(world.repo, read_key=key()) == pinned
        with pytest.raises(SealRefused, match="does not verify"):
            seal.check_genesis_key(world.repo, read_key=key(OTHER_KEY))
        monkeypatch.setattr("sys.stdin", _Terminal())
        monkeypatch.setattr(seal.getpass, "getpass", lambda prompt: TEST_PASSPHRASE)
        assert seal.main(["--repo", str(world.repo), "check-key"]) == 0
        shown = capsys.readouterr().out
        assert pinned in shown and world.registration_sha256["docs/preregistration.json"] in shown
        assert world.remote_ledger() == before
    finally:
        world.close()
    # Genesis pinned a record for a passphrase someone else chose: check-key says so.
    world = World(tmp_path / "substituted", genesis=False)
    try:
        world.write("research/keys/other.json", seal.key_record_bytes(
            seal.make_key_record(OTHER_KEY, salt=bytes(16))))
        commit = world.commit("a record the owner never made")
        world.push()
        world.write_genesis(key_record=(commit, "research/keys/other.json"))
        with pytest.raises(SealRefused, match="does not verify"):
            seal.check_genesis_key(world.repo, read_key=key())
    finally:
        world.close()


# --- review of #70: the candidate's code, the pins, the diff -------------------------

# Appended to HEAD's seal.py by an agent's commit, which item 6 allows: the reviewer's
# H-3 probe. It rebinds two of the module's own functions at import, so the review hides
# seal.py's own hunk and the passphrase is copied to a file, and it leaves a mark if it is
# ever imported.
HOSTILE_SEAL = '''

# --- a data loading tidy-up (the hostile part) ---
Path(os.environ["TMPDIR"], "hostile-seal-imported").write_text("imported")
_real_diff_to_accept = diff_to_accept
_real_read_passphrase = read_passphrase


def diff_to_accept(candidate, head, files, diff, digest, *, show_hash):
    kept, skipping = [], False
    for line in diff.split("\\n"):
        if line.startswith("research/norse_research/") and ", mode " in line:
            skipping = line.startswith("research/norse_research/seal.py")
        if not skipping:
            kept.append(line)
    files = [f for f in files if not f.endswith("/seal.py")]
    return _real_diff_to_accept(candidate, head, files, "\\n".join(kept), digest, show_hash=True)


def read_passphrase(prompt):
    typed = _real_read_passphrase(prompt)
    Path(os.environ["TMPDIR"], "tapped-passphrase").write_text(typed)
    return typed
'''


def _seal_process(world, arguments, tmpdir, stdin):
    """``python -m norse_research.seal`` run as the owner runs it, from the clone at HEAD."""
    env = {**os.environ, "TMPDIR": str(tmpdir)}
    return subprocess.Popen(
        [sys.executable, "-m", "norse_research.seal", "--repo", str(world.repo), *arguments],
        cwd=world.repo / "research", env=env, stdin=stdin, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, start_new_session=True,
    )


def _read_until(stream, needle: bytes, timeout: float) -> bytes:
    import select
    import time

    seen, end = b"", time.monotonic() + timeout
    while needle not in seen:
        left = end - time.monotonic()
        if left <= 0 or not select.select([stream], [], [], left)[0]:
            raise AssertionError(f"no {needle!r} within {timeout} s; saw {seen[-2000:]!r}")
        chunk = os.read(stream.fileno(), 65536)
        if not chunk:
            raise AssertionError(f"the process ended before {needle!r}; saw {seen[-2000:]!r}")
        seen += chunk
    return seen


def test_heads_seal_never_renders_the_review_or_reads_the_passphrase(ready, tmp_path):
    # Review of #70, H-3 (the reviewer's probe, made a refusing test): a seal.py change at
    # HEAD, which item 6 allows, left itself out of the diff it rendered, printed the true
    # hash with no terminal, and wrote the typed passphrase to a file. The command now runs
    # the candidate's code, exported from the candidate's commit, never HEAD's: the hostile
    # seal.py is never imported, the review shows its own hunk, the hash stays hidden
    # without a terminal, and the passphrase is read by the candidate's code.
    opened, _ = trial(ready, CANDIDATE)
    seal_path = ready.repo / "research" / "norse_research" / "seal.py"
    source = seal_path.read_text()
    marker = '\nif __name__ == "__main__":'
    assert source.count(marker) == 1
    seal_path.write_text(source.replace(marker, HOSTILE_SEAL + "\n" + marker))
    head = ready.commit("seal: tidy the data loading messages")
    ready.push()
    files, digest = gitledger.seal_digest(ready.ledger.git, opened.body["commit"], head)
    assert files == ["research/norse_research/seal.py"]
    tmpdir = tmp_path / "T"
    tmpdir.mkdir()
    base = ["unseal", "--commit", ready.prereg_commit,
            "--configuration-sha256", opened.body["configuration_sha256"],
            "--candidate-commit", opened.body["commit"],
            "--unseal-commit", head, "--ledger-head", ready.ledger.local_head()]

    # The review, with no terminal (an agent's shell, or the owner's).
    proc = _seal_process(ready, base, tmpdir, subprocess.DEVNULL)
    out, err = proc.communicate(timeout=600)
    shown = (out + err).decode()
    assert proc.returncode != 0 and "SealRefused: " in shown, shown[-3000:]
    assert f"seal: running with the code of commit {opened.body['commit']}" in shown
    message = shown.split("SealRefused: ", 1)[1]
    assert "research/norse_research/seal.py: M" in message
    assert "+def read_passphrase(prompt):" in message and "tapped-passphrase" in message
    assert digest not in message and "interactive terminal" in message
    assert not (tmpdir / "hostile-seal-imported").exists()

    # The unseal with that hash, the passphrase typed at a terminal once it is asked for.
    master, slave = os.openpty()
    proc = _seal_process(ready, [*base, "--accept-diff", digest], tmpdir, slave)
    os.close(slave)
    try:
        _read_until(proc.stderr, b"Holdout passphrase", timeout=600)
        os.write(master, (TEST_PASSPHRASE + "\n").encode())
        out, err = proc.communicate(timeout=600)
    finally:
        os.close(master)
    assert proc.returncode == 0, err.decode()[-3000:]
    assert b"unseal entry" in out and b"written and on the remote" in out
    assert not (tmpdir / "tapped-passphrase").exists()
    assert not (tmpdir / "hostile-seal-imported").exists()
    entry = unseal_of(remote_entries(ready))
    assert entry is not None and entry.body["commit"] == head
    assert entry.body["accepted_diff"] == digest
    ready.ledger.verify()
    assert not list(tmpdir.glob("norse-seal-at-candidate-*"))  # the export is removed


def _fail_the_push(world, monkeypatch, request):
    """The owner's unseal, its push failed by another process (probe S1, made
    deterministic)."""
    original = gitledger.Git.run

    def no_push(self, *args, **kwargs):
        if args and args[0] == "push" and "GIT_OBJECT_DIRECTORY" in (kwargs.get("env") or {}):
            raise gitledger.GitError("git push failed: the agent's process stopped it")
        return original(self, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(gitledger.Git, "run", no_push)
        with pytest.raises(LedgerWriteError, match="could not be pushed"):
            unseal(world, request, fetch=False)
    assert unseal_of(remote_entries(world)) is None


def test_a_retry_after_the_ledger_or_head_moved_is_refused_not_signed(ready, monkeypatch):
    # Review of #70, M-4 (probes S4 and S4b): between a failure it caused and the owner's
    # retry, a process of the owner's user moved the ledger or HEAD, so the identical
    # command signed other bytes; five such signatures forged an unseal at the agent's own
    # commit. The owner now pins both, and a retry signs the same bytes or nothing.
    request = request_for(ready)  # the owner's command line, the same every time
    signed = []
    real_sign = lamport.sign

    def recording(seed, message):
        signed.append(message)
        return real_sign(seed, message)

    monkeypatch.setattr(lamport, "sign", recording)
    _fail_the_push(ready, monkeypatch, request)
    assert len(signed) == 1
    # S4: the agent appends a control run before the owner's retry.
    record = harness.run(ready.repo, {"configuration": "C-H2", "seed": 0}, store=ready.store)
    assert record.status == "completed", record.error
    read = key()
    with pytest.raises(SealRefused, match=r"(?s)the ledger's head is .*M-4"):
        unseal(ready, request, read=read, fetch=False)
    assert len(signed) == 1 and read.calls == []
    # The owner may move a pin once, ever: two signatures in all leave about 2^126 work.
    request = {**request, "ledger_head": ready.ledger.local_head()}
    _fail_the_push(ready, monkeypatch, request)
    assert len(signed) == 2
    # S4b: the agent moves HEAD instead, by a document-only commit on main, which leaves
    # the accepted diff unchanged.
    ready.write("docs/agent-note.md", "a note\n")
    ready.commit("docs: a note")
    ready.push()
    assert accepted_diff(ready) == request["accepted_diff"]
    with pytest.raises(SealRefused, match=r"(?s)HEAD is .*M-4"):
        unseal(ready, request, read=read, fetch=False)
    assert len(signed) == 2 and read.calls == []
    assert unseal_of(remote_entries(ready)) is None


def test_non_ascii_in_the_diff_to_accept_is_escaped_and_flagged(tmp_path, monkeypatch):
    # Review of #70, L-10: a homoglyph, a Cyrillic o in "json", was printable and shown as
    # is. Every non-ASCII character is now written as its escape, and the line is flagged,
    # so it cannot pass for the letter it imitates, nor for a literal escape in the code.
    cyrillic_o = chr(0x043E)
    homoglyph = f"_REBOUND = __import__('js{cyrillic_o}n').dumps"
    literal = "_TEXT = 'js\\u043en'  # a literal escape, not a homoglyph"
    world = a_grid_world(tmp_path, monkeypatch, loader_changed=True,
                         loader_line=f"{homoglyph}\n{literal}")
    try:
        candidate_commit = trial(world, CANDIDATE)[0].body["commit"]
        head = git(world.repo, "rev-parse", "HEAD")
        _, diff, _ = gitledger.seal_changes(world.ledger.git, candidate_commit, head)
        assert diff.isascii() and cyrillic_o not in diff
        lines = diff.splitlines()
        shown = lines.index("+_REBOUND = __import__('js\\u043en').dumps")
        assert lines[shown - 1] == gitledger.ESCAPE_FLAG
        plain = next(i for i, line in enumerate(lines) if line.startswith("+_TEXT = "))
        assert lines[plain - 1] != gitledger.ESCAPE_FLAG  # the source's own backslash
        assert gitledger._shown(chr(0xE9) + chr(0x4E2D)) == "\\xe9\\u4e2d"
    finally:
        world.close()


def test_the_launcher_runs_the_named_commit_or_refuses(ready, tmp_path):
    # Review of #70, H-3 and round 2, H-4: the launcher takes the candidate's commit from
    # the command line alone, checks it against the remote's ledger, fetched with the
    # pinned git, lets the command run in place only when the package on disk is that
    # commit's, and otherwise writes that commit's package from git's objects.
    opened, _ = trial(ready, CANDIDATE)
    sha, commit = opened.body["configuration_sha256"], opened.body["commit"]
    argv = [sys.executable, "-s", "-m", "norse_research.seal", "--repo", str(ready.repo),
            "unseal", "--configuration-sha256", sha, "--commit", "c",
            "--candidate-commit", commit]
    arguments = relaunch.seal_arguments(argv)
    assert relaunch.parse(arguments) == ("unseal", {
        "--repo": str(ready.repo), "--configuration-sha256": sha, "--commit": "c",
        "--candidate-commit": commit})
    assert relaunch.seal_arguments([sys.executable, "-m", "pytest"]) is None
    entries = relaunch.remote_ledger_entries(ready.repo, "origin")
    assert relaunch.trial_commit(entries, sha) == commit
    assert relaunch.trial_commit(entries, "0" * 64) is None
    assert commit in {body["commit"] for body in relaunch.completed_trials(entries)}
    assert relaunch.package_matches(ready.repo, commit)
    relaunch.maybe_relaunch(argv)  # the package here is the candidate's: in place
    for named in (argv[:-2], [*argv[:-1], commit[:12]]):  # missing, or abbreviated
        with pytest.raises(relaunch.RelaunchRefused, match="needs --candidate-commit"):
            relaunch.maybe_relaunch(named)
    with pytest.raises(relaunch.RelaunchRefused, match="has no completed trial"):
        relaunch.maybe_relaunch([*argv[:8], "0" * 64, *argv[9:]])
    with pytest.raises(relaunch.RelaunchRefused, match="not a remote name"):
        relaunch.maybe_relaunch([*argv, "--remote", str(ready.remote)])
    exported = tmp_path / "exported"
    relaunch.export_package(ready.repo, commit, exported)
    package = exported / "research" / "norse_research"
    assert relaunch.package_matches(ready.repo, commit, package)
    (package / "seal.py").write_text((package / "seal.py").read_text() + "\n# changed\n")
    assert not relaunch.package_matches(ready.repo, commit, package)
    (package / "seal.py").unlink()
    assert not relaunch.package_matches(ready.repo, commit, package)


def forge_local_trial(world, opened, closed, commit) -> str:
    """The reviewer's round 2 H-4 probe: a completed trial of ``opened``'s configuration at
    ``commit``, appended by plumbing to the clone's own ledger refs alone, never pushed."""
    ledger_ = world.ledger
    head = ledger_.local_head()
    data = ledger_.git.blob(head, LEDGER_PATH)
    entries = parse_ledger(data)
    time = entries[-1].body["time"]
    fake_open = ledger.make_entry({**opened.body, "commit": commit, "seq": len(entries),
                                   "prev": entries[-1].hash, "time": time})
    fake_result = ledger.make_entry({**closed.body, "commit": commit, "opens": fake_open.hash,
                                     "open_seq": len(entries), "seq": len(entries) + 1,
                                     "prev": fake_open.hash, "time": time})
    lines = f"{fake_open.line}\n{fake_result.line}\n".encode()
    forged = ledger_._commit(head, data + lines, {}, "forged")
    for ref in ("refs/heads/ledger", "refs/remotes/origin/ledger"):
        git(world.repo, "update-ref", ref, forged)
    return forged


def test_a_forged_local_ledger_never_chooses_the_code_that_runs(ready, tmp_path):
    # Review of #70, round 2, H-4 (the reviewer's probe, made a refusing test): the
    # launcher took the candidate's commit from the clone's own ledger ref, which any
    # process of the owner's user can rewrite. An agent committed a hostile seal.py, left
    # it unpushed, appended to the local ledger a completed trial of the candidate's
    # configuration at that commit, and the launcher ran the hostile code in place. The
    # commit now comes from the command line, as the owner reads it on GitHub, and is
    # checked against the remote's ledger, fetched with the pinned git: the hostile commit
    # is refused before any of its code is imported, and the honest one runs its own
    # code, which refuses the forged ledger before any passphrase is asked for.
    opened, closed = trial(ready, CANDIDATE)
    honest = opened.body["commit"]
    ledger_head = ready.ledger.local_head()
    seal_path = ready.repo / "research" / "norse_research" / "seal.py"
    source = seal_path.read_text()
    marker = '\nif __name__ == "__main__":'
    assert source.count(marker) == 1
    seal_path.write_text(source.replace(marker, HOSTILE_SEAL + "\n" + marker))
    hostile = ready.commit("seal: tidy the data loading messages")  # never pushed
    forged = forge_local_trial(ready, opened, closed, hostile)
    assert relaunch.trial_commit(
        [(e.hash, e.body) for e in parse_ledger(ready.ledger.git.blob(forged, LEDGER_PATH))],
        opened.body["configuration_sha256"]) == hostile  # what the old launcher ran
    tmpdir = tmp_path / "T"
    tmpdir.mkdir()
    unseal_base = ["unseal", "--commit", ready.prereg_commit,
                   "--configuration-sha256", opened.body["configuration_sha256"],
                   "--unseal-commit", hostile, "--ledger-head", ledger_head]

    def run(arguments):
        proc = _seal_process(ready, arguments, tmpdir, subprocess.DEVNULL)
        out, err = proc.communicate(timeout=600)
        return proc.returncode, (out + err).decode()

    for arguments in ([*unseal_base, "--candidate-commit", hostile],
                      ["candidate", "--candidate-commit", hostile], unseal_base, ["candidate"]):
        code, shown = run(arguments)
        assert code != 0 and "round 2, H-4" in shown, shown[-3000:]
        assert "running with the code of commit" not in shown
        assert not (tmpdir / "hostile-seal-imported").exists()
    # The launcher's fetch replaced the forged remote-tracking ref with the remote's ledger.
    assert git(ready.repo, "rev-parse", "refs/remotes/origin/ledger") == ledger_head
    code, shown = run([*unseal_base, "--candidate-commit", honest])
    assert code != 0 and f"seal: running with the code of commit {honest}" in shown, shown[-3000:]
    assert "the local ledger is ahead of the remote" in shown
    assert not (tmpdir / "hostile-seal-imported").exists()
    assert not (tmpdir / "tapped-passphrase").exists()
    assert unseal_of(remote_entries(ready)) is None


def test_the_owners_procedure_runs_the_candidates_own_code(ready, tmp_path, capsys, request):
    # Review of #70, round 2, the launcher residual: run from the clone, the launcher is
    # HEAD's code, which a change after the candidate's run could rewrite. The owner's
    # procedure, which seal candidate prints filled in, exports the candidate's code with
    # plain git into a new folder and runs the unseal there, so the launcher that runs is
    # the candidate's, and it runs in place. Its steps run here as printed, against a HEAD
    # whose seal.py is hostile.
    opened, _ = trial(ready, CANDIDATE)
    commit = opened.body["commit"]
    seal_path = ready.repo / "research" / "norse_research" / "seal.py"
    source = seal_path.read_text()
    marker = '\nif __name__ == "__main__":'
    seal_path.write_text(source.replace(marker, HOSTILE_SEAL + "\n" + marker))
    head = ready.commit("seal: tidy the data loading messages")
    ready.push()
    assert seal.main(["--repo", str(ready.repo), "candidate", "--candidate-commit", commit]) == 0
    out = capsys.readouterr().out
    steps = json.loads(out[out.index("{\n"):])["holdout_candidate"]["unseal_procedure"]
    assert [step.split(". ", 1)[0] for step in steps] == [str(n) for n in range(1, 8)]
    assert "ledger/trials.jsonl on the ledger branch of" in steps[0] and commit in steps[0]
    commands = [step.split(". ", 1)[1] for step in steps[1:5]]
    script = "\n".join(["set -e", *commands[:3], "printf 'export: %s\\n' \"$folder\"",
                        commands[3]])
    assert f"--unseal-commit {head} " in script and "--accept-diff" not in script
    tmpdir = tmp_path / "T"
    tmpdir.mkdir()
    proc = subprocess.run(["/bin/sh", "-c", script], env={**os.environ, "TMPDIR": str(tmpdir)},
                          stdin=subprocess.DEVNULL, capture_output=True, timeout=600,
                          check=False)
    shown = (proc.stdout + proc.stderr).decode()
    # macOS's mktemp -d ignores TMPDIR, so the folder is found from the script's output.
    exported = re.search(r"^export: (.+)$", proc.stdout.decode(), re.MULTILINE)
    assert exported is not None, shown[-3000:]
    folder = Path(exported.group(1))
    request.addfinalizer(lambda: shutil.rmtree(folder, ignore_errors=True))
    # The candidate's own launcher ran it in place: no second export, no HEAD code.
    assert proc.returncode != 0 and "SealRefused: " in shown, shown[-3000:]
    assert "running with the code of commit" not in shown
    message = shown.split("SealRefused: ", 1)[1]
    assert "research/norse_research/seal.py: M" in message and "hostile-seal-imported" in message
    assert "interactive terminal" in message
    assert not (tmpdir / "hostile-seal-imported").exists()
    assert relaunch.package_matches(ready.repo, commit, folder / "research" / "norse_research")


# --- security review H2, M1, L7: holdout attempts, one at a time ---------------------

DRIVER = r'''
import json, sys
sys.path.insert(0, sys.argv[1] + "/research")
from pathlib import Path
from norse_research import harness
from norse_research.store import ArchiveStore
try:
    record = harness.run(Path(sys.argv[1]), {"configuration": "holdout",
                         "candidate_sha256": sys.argv[3]}, store=ArchiveStore(Path(sys.argv[2])))
    print(json.dumps({"status": record.status}))
except harness.RunRefused as exc:
    print(json.dumps({"refused": str(exc)}))
'''


def run_at_head_in_a_fresh_process(world, tmp_path):
    """A holdout run in a new process importing the code at HEAD, as a real retry after
    a code change would be (the test process holds the code it started with)."""
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER)
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME") or k.startswith("GIT_")}
    sha = trial(world, CANDIDATE)[0].body["configuration_sha256"]
    proc = subprocess.run(
        [sys.executable, "-I", "-B", str(driver), str(world.repo), str(world.store.root), sha],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), check=False,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def commit_a_data_loading_change(world):
    """A commit touching only loader.py, which item 6's file rule allows."""
    loader = world.repo / "research/norse_research/loader.py"
    loader.write_text(loader.read_text() + "\n# a data loading change\n")
    world.commit("loader: a data loading change")
    world.push()


def test_a_failed_attempt_then_a_data_loading_change_cannot_run_the_holdout(
    ready, monkeypatch, tmp_path
):
    # Probes R1 and F2 (security review round 2, N1): the holdout series was computed
    # and seen, then the attempt failed. A run at a commit with a data loading change is
    # refused, whatever the attempt reported; at the unseal's commit the retry is
    # allowed and gives the same series.
    entry = unseal(ready)
    seen = []

    def peek_then_fail(returned, window, committed):
        seen.append([(str(d), float(v)) for d, v in returned])
        raise RuntimeError("stand-in for any failure after the holdout returns were computed")

    with monkeypatch.context() as m:
        m.setattr(harness, "_holdout_summary", peek_then_fail)
        first = holdout_run(ready)
    assert first.status == "failed" and harness.WITHHELD in first.error
    closes = [e for e in remote_entries(ready) if e.type == "attempt_close"]
    assert [c.body["outcome"] for c in closes] == ["returned"]
    commit_a_data_loading_change(ready)
    out = run_at_head_in_a_fresh_process(ready, tmp_path)
    assert "every holdout run is at the unseal's commit" in out["refused"]
    assert entry.body["commit"] in out["refused"]
    git(ready.repo, "checkout", "--quiet", entry.body["commit"])
    second = holdout_run(ready)
    assert second.status == "completed", second.error
    assert ready.ledger.verify().snapshot.returns_of(second.result_entry) == seen[0]


def test_a_close_the_caller_writes_unlocks_nothing(ready, monkeypatch, tmp_path):
    # Probe F1: the process stops after the unsealed run returned, and the caller writes
    # the attempt's close itself. It is a valid close, and changes nothing: a run at a
    # data loading change is still refused.
    unseal(ready)
    original = gitledger.GitLedger.append

    def die_at_the_close(self, body, files=None, **kwargs):
        if body.get("type") == "attempt_close":
            raise SystemExit("stand-in for a kill between the unsealed run and its close")
        return original(self, body, files, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(gitledger.GitLedger, "append", die_at_the_close)
        with pytest.raises(SystemExit):
            holdout_run(ready)
    (attempt,) = [e for e in remote_entries(ready) if e.type == "attempt"]
    ready.ledger.append({"type": "attempt_close", "attempt": attempt.hash,
                          "attempt_seq": attempt.seq, "outcome": "failed"})
    commit_a_data_loading_change(ready)
    out = run_at_head_in_a_fresh_process(ready, tmp_path)
    assert "every holdout run is at the unseal's commit" in out["refused"]
    ready.ledger.verify()


def test_an_off_ledger_peek_then_a_rewriting_first_attempt_is_refused(ready, tmp_path):
    # Probe D2: after the unseal the holdout files are on disk and the runner can be
    # called directly, leaving no entry (T21's residual). The rewrite that would follow
    # needs a code change, and the first ledgered run at it is refused.
    entry = unseal(ready)
    genesis = genesis_of(ready)
    opened = trial(ready, CANDIDATE)[0]
    config = ready.registration.resolve_holdout(
        opened.body, genesis.body["manifests"]["holdout"]).configuration
    before = ready.remote_ledger()
    peeked = harness._run_strategy(
        ready.ledger.git, entry.body["commit"], genesis.body["strategies"][CANDIDATE],
        genesis.body["tree_paths"]["H1"], "research/manifests/binance-vision-development.json",
        config, "e" * 64, ready.store, unsealed=(seal.HOLDOUT_MANIFEST, entry.hash),
    ).returns
    assert len(peeked) == 2802 + 304 and ready.remote_ledger() == before
    commit_a_data_loading_change(ready)
    out = run_at_head_in_a_fresh_process(ready, tmp_path)
    assert "every holdout run is at the unseal's commit" in out["refused"]
    assert not [e for e in remote_entries(ready) if e.type == "open" and e.body["kind"] == "holdout"]


def test_a_killed_attempt_is_closed_as_abandoned_by_the_next_run(ready, monkeypatch):
    # Probe R1b: the process dies after the unsealed run returned, before the attempt's
    # close; the next run, at the unseal's commit, closes it as abandoned and goes on.
    entry = unseal(ready)
    original = gitledger.GitLedger.append

    def die_before_the_close(self, body, files=None, **kwargs):
        if body.get("type") == "attempt_close":
            raise SystemExit("stand-in for SIGKILL after the unsealed run returned")
        return original(self, body, files, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(gitledger.GitLedger, "append", die_before_the_close)
        with pytest.raises(SystemExit):
            holdout_run(ready)
    entries = remote_entries(ready)
    (attempt,) = [e for e in entries if e.type == "attempt"]
    assert not any(e.type in ("attempt_close", "result") for e in entries[attempt.seq:])
    ready.ledger.verify()
    assert "unseal's commit" in rules.holdout_commit_rule(entries, "0" * 40)
    assert rules.holdout_commit_rule(entries, entry.body["commit"]) is None
    again = holdout_run(ready)
    assert again.status == "completed", again.error
    closes = [e for e in remote_entries(ready) if e.type == "attempt_close"]
    assert (closes[0].body["attempt"], closes[0].body["outcome"]) == (attempt.hash, "abandoned")
    ready.ledger.verify()


def test_the_holdout_reaches_its_unsealed_stage_at_most_three_times(ready, monkeypatch):
    # Section 11, item 6, implemented conservatively (rules.HOLDOUT_ATTEMPTS): three
    # holdout runs that reach the unsealed stage (an attempt entry each) and fail, then no
    # more, at the harness and on replay (security review round 3, C1).
    unseal(ready)
    original = harness._run_strategy

    def crash_once_unsealed(*args, **kwargs):
        if kwargs.get("unsealed"):
            raise RuntimeError("a crash inside the unsealed run")
        return original(*args, **kwargs)

    monkeypatch.setattr(harness, "_run_strategy", crash_once_unsealed)
    for _ in range(rules.HOLDOUT_ATTEMPTS):
        assert holdout_run(ready).status == "failed"
    assert [e.type for e in remote_entries(ready)].count("attempt") == 3
    with pytest.raises(harness.RunRefused, match="unsealed stage 3 times"):
        holdout_run(ready)
    hand_append(ready, holdout_open_body(ready))
    with pytest.raises(VerificationError, match="unsealed stage 3 times"):
        ready.ledger.verify()


def test_holdout_runs_that_fail_before_the_unsealed_stage_do_not_count(ready, monkeypatch):
    # Security review round 3, C1 (the reviewer's probe): runs that fail before their
    # attempt entry, here in the development reproduction, read nothing of the holdout,
    # so more of them than the cap allows leave the holdout run still possible.
    unseal(ready)

    def crash(*args, **kwargs):
        raise RuntimeError("a crash before the unsealed stage")

    with monkeypatch.context() as m:
        m.setattr(harness, "_run_strategy", crash)
        for _ in range(rules.HOLDOUT_ATTEMPTS + 1):
            assert holdout_run(ready).status == "failed"
    assert "attempt" not in [e.type for e in remote_entries(ready)]
    record = holdout_run(ready)
    assert record.status == "completed", record.error
    ready.ledger.verify()


def test_a_second_holdout_run_while_one_is_in_flight_is_refused(ready, monkeypatch):
    # Probe R2 and security review M1: on one clone the holdout lock refuses a second run
    # while the first is in flight, so only one attempt and one result exist.
    unseal(ready)
    original = harness._run_strategy
    inner = []

    def start_another(*args, **kwargs):
        if not inner:
            try:
                inner.append(holdout_run(ready).status)
            except harness.RunRefused as exc:
                inner.append(str(exc))
        return original(*args, **kwargs)

    monkeypatch.setattr(harness, "_run_strategy", start_another)
    outer = holdout_run(ready)
    assert outer.status == "completed", outer.error
    assert "another holdout run is in progress" in inner[0]
    entries = remote_entries(ready)
    assert [e.type for e in entries].count("attempt") == 1
    assert sum(1 for e in entries if e.type == "result" and e.body["status"] == "completed"
               and e.body["configuration_sha256"] == outer.open_entry.body[
                   "configuration_sha256"]) == 1
    ready.ledger.verify()


def test_an_attempt_from_another_clone_ends_one_in_flight_which_cannot_complete(
    ready, monkeypatch
):
    # Security review M1 across clones: the lock is per clone, so the ledger keeps
    # attempts one at a time. A run on another clone first closes the open attempt as
    # abandoned (which binds it to the same commit); the first run cannot then close its
    # attempt or complete. One completed result, and the ledger verifies.
    unseal(ready)
    original = harness._run_strategy
    other = []

    def another_clone_runs_meanwhile(*args, **kwargs):
        if kwargs.get("unsealed") and not other:
            other.append(None)
            clone = ready.clone("other")
            other[0] = harness.run(clone.git.repo, {"configuration": "holdout",
                                   "candidate_sha256": request_for(ready)["configuration_sha256"]},
                                   store=ready.store)
        return original(*args, **kwargs)

    monkeypatch.setattr(harness, "_run_strategy", another_clone_runs_meanwhile)
    first = holdout_run(ready)
    assert other[0].status == "completed", other[0].error
    assert first.status == "failed" and "closed twice" in first.error
    entries = remote_entries(ready)
    completed = [e for e in entries if e.type == "result" and e.body["status"] == "completed"
                 and e.body["opens"] in {o.hash for o in entries
                                         if o.type == "open" and o.body["kind"] == "holdout"}]
    assert len(completed) == 1
    assert [e.body["outcome"] for e in entries if e.type == "attempt_close"] == [
        "abandoned", "returned"]
    ready.ledger.verify()


def test_a_failed_unsealed_run_publishes_no_text_of_its_own(tmp_path, monkeypatch):
    # Security review L7: once the strategy has the unseal, a failure's message may carry
    # holdout values, so the ledger records only its type.
    world = a_grid_world(tmp_path, monkeypatch, behaviours={CANDIDATE: "leaks_late"})
    try:
        unseal(world)
        record = holdout_run(world)
        assert record.status == "failed"
        assert record.error == f"RuntimeError: {harness.WITHHELD}"
        assert "holdout close" not in world.remote_ledger().decode()
        (closed,) = [e for e in remote_entries(world) if e.type == "attempt_close"]
        assert closed.body["outcome"] == "failed"
    finally:
        world.close()


def test_the_attempt_rules_replay_without_git():
    # The rules the ledger and replay hold holdout runs and attempts to, on a ledger
    # built in memory: the unseal's commit only, at most three runs reaching the unsealed
    # stage (any number failing before it; round 3, C1), one attempt at a time, never
    # closed twice, and a completed result needs its own returned attempt.
    book = Book()
    days = book.registration.windows["H1"].days
    for i, cid in enumerate(book.registration.order["H1"]):
        book.run(cid, lcg_returns(i, days))
    genesis = book.entries[0]
    opened = next(e for e in book.entries if e.type == "open"
                  and e.body["configuration_id"] == CANDIDATE)
    closed = next(e for e in book.entries if e.type == "result" and e.body["opens"] == opened.hash)
    sealed_at = "1" * 40
    book.add({"type": "unseal", "preregistration": genesis.body["preregistration"],
              "configuration_id": CANDIDATE, "hypothesis": "H1",
              "configuration_sha256": opened.body["configuration_sha256"],
              "candidate": {"open": opened.hash, "result": closed.hash},
              "accepted_diff": hashlib.sha256(b"").hexdigest(),
              "holdout_manifest_sha256": genesis.body["manifests"]["holdout"],
              "key_record_sha256": genesis.body["key_record"]["sha256"],
              "signature": SHAPED_SIGNATURE, "commit": sealed_at})
    resolved = book.registration.resolve_holdout(opened.body, genesis.body["manifests"]["holdout"])

    def holdout_open(on, commit=sealed_at):
        return on.add({**opened.body, "kind": "holdout", "configuration": resolved.configuration,
                       "configuration_sha256": resolved.configuration_sha256,
                       "window": resolved.window.as_dict(), "commit": commit})

    def attempt(on, run):
        return on.add({"type": "attempt", "opens": run.hash, "open_seq": run.seq,
                       "commit": run.body["commit"]})

    def close(on, tried, outcome="failed"):
        return on.add({"type": "attempt_close", "attempt": tried.hash, "attempt_seq": tried.seq,
                       "outcome": outcome})

    def replay(on):
        check_run_rules(on.snapshot().entries, on.registration)

    first = holdout_open(book)
    tried = attempt(book, first)
    close(book, tried)
    replay(book)
    assert "unseal's commit" in rules.holdout_commit_rule(book.entries, "2" * 40)
    elsewhere = copy.deepcopy(book)
    holdout_open(elsewhere, "2" * 40)
    with pytest.raises(VerificationError, match="unseal's commit"):
        replay(elsewhere)
    unattempted_runs = copy.deepcopy(book)
    for _ in range(rules.HOLDOUT_ATTEMPTS + 2):
        holdout_open(unattempted_runs)  # failed before the unsealed stage: not counted
    replay(unattempted_runs)
    assert rules.HOLDOUT_ATTEMPTS == 3
    capped = copy.deepcopy(book)  # the first run's attempt is the first
    close(capped, attempt(capped, holdout_open(capped)))  # the second
    run_a, run_b = holdout_open(capped), holdout_open(capped)
    close(capped, attempt(capped, run_a))  # the third
    replay(capped)
    after_cap = copy.deepcopy(capped)
    holdout_open(after_cap)
    with pytest.raises(VerificationError, match="unsealed stage 3 times"):
        replay(after_cap)
    attempt(capped, run_b)  # a fourth, on a run opened before the cap was reached
    with pytest.raises(VerificationError, match="unsealed stage 3 times"):
        replay(capped)
    cases = []
    overlapping = copy.deepcopy(book)
    second = holdout_open(overlapping)
    attempt(overlapping, second)
    third = holdout_open(overlapping)
    attempt(overlapping, third)
    cases.append((overlapping, "attempts run one at a time"))
    twice = copy.deepcopy(book)
    close(twice, tried)
    cases.append((twice, "closed twice"))
    unattempted = copy.deepcopy(book)
    run = holdout_open(unattempted)
    unattempted.result(run, [0.0] * resolved.window.days, days=resolved.window.day_list())
    cases.append((unattempted, "needs its own attempt"))
    for ledger_, message in cases:
        with pytest.raises(VerificationError, match=message):
            ledger_.snapshot()


def test_append_refuses_the_holdout_entries_replay_would_reject(ready):
    # Security review round 2, N4: an entry the public append accepts never fails
    # verification afterwards, so a hand-written one cannot break the ledger for good.
    unseal(ready, fetch=False)
    before = ready.remote_ledger()
    elsewhere = {**holdout_open_body(ready), "commit": "0" * 40}
    with pytest.raises(VerificationError, match="unseal's commit"):
        ready.ledger.append(elsewhere)
    assert ready.remote_ledger() == before
    # Runs that never reach the unsealed stage are not counted (round 3, C1).
    runs = [ready.ledger.append(holdout_open_body(ready))
            for _ in range(rules.HOLDOUT_ATTEMPTS + 1)]
    with pytest.raises(VerificationError, match="commit is not its holdout run's"):
        ready.ledger.append({"type": "attempt", "opens": runs[-1].hash,
                              "open_seq": runs[-1].seq, "commit": "0" * 40})
    for run in runs[:-1]:  # three reach the unsealed stage and fail
        tried = ready.ledger.append({"type": "attempt", "opens": run.hash, "open_seq": run.seq,
                                     "commit": run.body["commit"]})
        ready.ledger.append({"type": "attempt_close", "attempt": tried.hash,
                             "attempt_seq": tried.seq, "outcome": "failed"})
    before = ready.remote_ledger()
    with pytest.raises(VerificationError, match="unsealed stage 3 times"):
        ready.ledger.append(holdout_open_body(ready))
    with pytest.raises(VerificationError, match="unsealed stage 3 times"):
        ready.ledger.append({"type": "attempt", "opens": runs[-1].hash,
                             "open_seq": runs[-1].seq, "commit": runs[-1].body["commit"]})
    assert ready.remote_ledger() == before
    ready.ledger.verify()


# --- the rehearsal (security review round 2, the coordinator's item 4) ------------------


def test_the_rehearsal_runs_the_holdout_path_on_development_data_and_writes_nothing(
    tmp_path, monkeypatch, capsys
):
    world = a_grid_world(tmp_path, monkeypatch, behaviours={CANDIDATE: "loads_across"})
    try:
        before = world.remote_ledger()
        seen = []
        original = harness._run_strategy

        def watched(*args, **kwargs):
            seen.append((kwargs.get("rehearsal"), kwargs.get("unsealed")))
            return original(*args, **kwargs)

        with monkeypatch.context() as m:
            m.setattr(harness, "_run_strategy", watched)
            done = harness.rehearse_holdout(world.repo, store=world.store, boundary="2025-12")
        assert (done["configuration_id"], done["boundary"], done["days"]) == (CANDIDATE, "2025-12",
                                                                              2802)
        assert done["commit"] == git(world.repo, "rev-parse", "HEAD")
        assert seen == [("2025-12", None)]  # the rehearsal path, never the real unseal
        monkeypatch.setenv("NORSE_DATASETS_DIR", str(world.store.root))
        assert seal.main(["--repo", str(world.repo), "rehearse", "--boundary", "2025-12"]) == 0
        assert "rehearsal passed" in capsys.readouterr().out
        assert world.remote_ledger() == before and world.holdout_files_in_mirror() == []
        # It fails loudly on a data loading error: a development file no longer verifies.
        spec = DatasetSpec.from_name("spot/klines/XUSDT/1d")
        path = world.store.path(spec.key("2025-12"))
        damaged = bytearray(path.read_bytes())
        damaged[-30] ^= 1
        path.write_bytes(bytes(damaged))
        with pytest.raises(harness.RunRefused, match="the rehearsal failed"):
            harness.rehearse_holdout(world.repo, store=world.store, boundary="2025-12")
        assert world.remote_ledger() == before
    finally:
        world.close()


def test_the_rehearsal_fails_loudly_on_a_seal_error_and_needs_a_candidate(tmp_path, monkeypatch):
    # A candidate that reads 2026 in its development run is stopped by the seal in the
    # rehearsal, as it would be in the holdout run's reproduction step.
    world = a_grid_world(tmp_path / "peeks", monkeypatch, behaviours={CANDIDATE: "peeks"})
    try:
        with pytest.raises(harness.RunRefused, match="HoldoutSealed"):
            harness.rehearse_holdout(world.repo, store=world.store, boundary="2025-12")
    finally:
        world.close()
    world = a_grid_world(tmp_path / "incomplete", monkeypatch, incomplete=True)
    try:
        with pytest.raises(harness.RunRefused, match="no holdout candidate"):
            harness.rehearse_holdout(world.repo, store=world.store)
    finally:
        world.close()


def test_a_failed_unseal_push_leaves_no_signature_in_the_clone(ready, monkeypatch, capsys):
    # Probe P2 (security review round 2, N2): a push that fails must not strand the
    # owner's unseal in a commit, a ref or a reflog of the clone. Since D27 the retry also
    # signs exactly the bytes the failed one signed, so it reveals nothing new of the
    # one-time key, and the scrypt output is never written anywhere. The failure, and a
    # line printed before the push, name the entry's hash, so an unseal entry with that
    # hash on the remote can be recognised as the owner's (review of #70, I-10).
    reflog = ["reflog", "show", "--format=%H %gs", "refs/heads/ledger"]
    before = git(ready.repo, *reflog)
    original = gitledger.Git.run
    signed = []
    real_sign = lamport.sign

    def recording(seed, message):
        signed.append(message)
        return real_sign(seed, message)

    def no_push(self, *args, **kwargs):
        if args and args[0] == "push":
            raise gitledger.GitError("git push failed: injected")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(lamport, "sign", recording)
    with monkeypatch.context() as m:
        m.setattr(gitledger.Git, "run", no_push)
        with pytest.raises(LedgerWriteError, match="nothing of it is left in this clone") as failed:
            unseal(ready, fetch=False)
    assert unseal_of(remote_entries(ready)) is None and len(signed) == 1
    first_preimage = real_sign(seed_for(), signed[0])["preimages"][0].encode()
    printed = capsys.readouterr().err

    def every_object():
        return subprocess.run(["git", "cat-file", "--batch-all-objects", "--batch"],
                              cwd=ready.repo, capture_output=True, check=True).stdout

    assert first_preimage not in every_object()
    assert git(ready.repo, *reflog) == before
    assert "unseal" not in git(ready.repo, "log", "-g", "--all", "--format=%gs %s")
    # When the push succeeds, the entry is public, and the search above finds it; the
    # retry signed the very bytes the failed attempt signed.
    entry = unseal(ready, fetch=False)
    assert signed[1] == signed[0] == ledger.signed_bytes(entry.body)
    message = str(failed.value)
    assert entry.hash in message and "seal fetch-holdout" in message and "M-4" in message
    assert f"signed the unseal entry {entry.hash}" in printed
    assert first_preimage in every_object()
    assert output_of().encode() not in every_object()
    assert git(ready.repo, "rev-parse", "refs/heads/ledger") == git(
        ready.remote, "rev-parse", "refs/heads/ledger")
    ready.ledger.verify()
    assert entry.type == "unseal"


def test_the_strategy_process_gives_the_same_series_every_time(tmp_path, monkeypatch):
    # Security review round 2, N3: under -I string hashing was randomised per process, so
    # a strategy that iterates a set of names gave a different series on each run of the
    # same commit. The process now runs with PYTHONHASHSEED=0 and nothing else.
    clear_overrides(monkeypatch)
    world = World(tmp_path, behaviours={"H1-01": "set_order"})
    try:
        genesis = genesis_of(world)
        config = world.open_body("H1-01")["configuration"]

        def run_once():
            return harness._run_strategy(
                world.ledger.git, git(world.repo, "rev-parse", "HEAD"),
                genesis.body["strategies"]["H1-01"], genesis.body["tree_paths"]["H1"],
                "research/manifests/binance-vision-development.json", config, "e" * 64, None,
            )

        assert harness.STRATEGY_ENVIRONMENT == {"PYTHONHASHSEED": "0"}
        first = run_once()
        assert all(run_once() == first for _ in range(2))
        # Without the fixed seed the same commit gives different series.
        monkeypatch.setattr(harness, "STRATEGY_ENVIRONMENT", {})
        assert len({json.dumps(run_once()) for _ in range(6)}) > 1
    finally:
        world.close()
