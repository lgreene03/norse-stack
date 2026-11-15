"""The ledger in git, verified with its anchors (G1.5).

Every test runs against a temporary bare repository standing in for GitHub and a
temporary clone; the real repository, its ledger and its tags are never touched.
Threat model T3, T4, T5, T7, T9, T10 and T24, and the security review's M6, M7, L2,
L3, L9 and L10.
"""

import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import clear_overrides
from ledger_fixtures import KEY_RECORD, TREE_PATHS, World, git, open_body
from norse_research import gate, harness
from norse_research.canonical import sha256_hex
from norse_research.gitledger import (
    GENESIS_TAG,
    GitLedger,
    LedgerBusy,
    LedgerOutOfStep,
    LedgerWriteError,
    write_genesis,
)
from norse_research.ledger import (
    LEDGER_BRANCH,
    LEDGER_PATH,
    LedgerError,
    VerificationError,
    make_entry,
    parse_ledger,
)

_fixes = iter(range(10**6))


def trial(world, cid="H1-01", behaviour="steady", **request):
    world.mode(behaviour, "H2" if cid.startswith("H2") or cid.endswith("H2") else "H1")
    return harness.run(world.repo, {"configuration": cid, **request}, store=world.store)


def recomputed(entries, k, change):
    """The ledger with entry ``k`` changed and every later hash recomputed, so the
    chain on its own verifies again."""
    bodies = [dict(e.body) for e in entries]
    bodies[k] = change(bodies[k])
    out, prev, renamed = [], "0" * 64, {}
    for old, body in zip(entries, bodies, strict=True):
        if body.get("opens") in renamed:  # a careful editor fixes these references too
            body = {**body, "opens": renamed[body["opens"]]}
        entry = make_entry({**body, "prev": prev})
        renamed[old.hash] = entry.hash
        out.append(entry.line)
        prev = entry.hash
    return ("\n".join(out) + "\n").encode()


def commit_on_top(world, data, files=None, push=True, parent=None):
    ledger = world.ledger
    parent = parent or ledger.local_head()
    commit = ledger._commit(parent, data, files or {}, "a hand-made ledger commit")
    git(world.repo, "update-ref", "refs/heads/ledger", commit)
    if push:
        git(world.repo, "push", "--quiet", "origin", f"{commit}:refs/heads/ledger")
    return commit


def test_genesis_pins_the_registration_and_the_ledger_lives_on_its_own_branch(world):
    verified = world.ledger.verify()
    genesis = verified.snapshot.genesis
    prereg = genesis.body["preregistration"]
    assert prereg["tag"] == "prereg-v1" and prereg["commit"] == world.prereg_commit
    for path in ("docs/PREREGISTRATION.md", "docs/preregistration.json"):
        assert prereg["files"][path] == sha256_hex((world.repo / path).read_bytes())
    assert genesis.body["manifests"]["development"] == world.manifest_sha256
    assert genesis.body["key_record"]["sha256"] == sha256_hex((world.repo / KEY_RECORD).read_bytes())
    assert genesis.body["tree_paths"] == TREE_PATHS
    assert genesis.body["remote"] == {"url": str(world.remote), "branch": "ledger",
                                      "code_branch": "main"}
    assert genesis.body["strategies"] == world.pins
    assert verified.head == verified.remote_head
    assert git(world.repo, "ls-tree", "-r", "--name-only", LEDGER_BRANCH) == LEDGER_PATH
    assert git(world.repo, "status", "--porcelain") == ""


def test_genesis_is_anchored_by_the_ledger_genesis_tag(world):
    head = world.ledger.local_head()
    assert world.ledger.milestone_tags() == {GENESIS_TAG: head}
    # Before this tag existed, deleting the branch and writing a new genesis in a fresh
    # clone passed verification (review M7). Now it does not.
    git(world.repo, "push", "--quiet", "origin", f":refs/heads/{LEDGER_BRANCH}")
    fresh_path = world.tmp / "fresh"
    git(world.tmp, "clone", "--quiet", str(world.remote), str(fresh_path))
    fresh = GitLedger(fresh_path)
    with pytest.raises(LedgerError, match=f"{GENESIS_TAG} already anchors a ledger"):
        write_genesis(fresh, tree_paths=TREE_PATHS, strategies=world.pins,
                      key_record=(world.prereg_commit, KEY_RECORD))
    # Written by hand instead, the new genesis fails verification in every clone.
    genesis = world.ledger.snapshot(head).genesis.body
    fresh.append({k: v for k, v in genesis.items() if k not in ("seq", "prev", "time")})
    with pytest.raises(VerificationError, match=f"milestone tag {GENESIS_TAG} .* not an ancestor"):
        fresh.verify()
    # A ledger without the tag at all is refused.
    bare_dir = world.tmp / "untagged"
    bare_dir.mkdir()
    untagged = World(bare_dir, genesis=False)
    try:
        body = dict(world.ledger.snapshot(head).genesis.body)
        body["remote"] = {**body["remote"], "url": str(untagged.remote)}
        body["preregistration"] = {**body["preregistration"], "commit": untagged.prereg_commit}
        body["key_record"] = {**body["key_record"], "commit": untagged.prereg_commit}
        body["strategies"] = untagged.pins
        untagged.ledger.append({k: v for k, v in body.items() if k not in ("seq", "prev", "time")})
        with pytest.raises(VerificationError, match=f"no {GENESIS_TAG} tag"):
            untagged.ledger.verify()
    finally:
        untagged.close()


def test_recomputed_chain_after_an_edit_is_refused_by_history(world):
    trial(world, "H1-01")
    trial(world, "B-H1")
    entries = world.ledger.verify().snapshot.entries
    data = recomputed(entries, 1, lambda body: {**body, "label": "nudged"})
    assert len(parse_ledger(data)) == len(entries)  # the chain alone verifies again
    files = world.ledger.files_at(world.ledger.local_head())
    files.pop(LEDGER_PATH)
    bad = commit_on_top(world, data, files)
    with pytest.raises(VerificationError, match=rf"ledger commit {bad} does not append"):
        world.ledger.verify()


def test_truncated_tail_is_refused_against_the_remote_head(world):
    trial(world, "H1-01")
    head = world.ledger.local_head()
    remote_entries = world.ledger.verify().snapshot.entries
    parent = git(world.repo, "rev-parse", f"{head}~1")  # the ledger without its last entry
    truncated = world.ledger.snapshot(parent)
    assert len(truncated.entries) == len(remote_entries) - 1  # the chain alone verifies
    world.ledger.verify_history(parent)  # and so does its history
    git(world.repo, "update-ref", "refs/heads/ledger", parent)
    with pytest.raises(VerificationError, match=rf"remote ledger head {head} \(entry "
                       rf"{remote_entries[-1].hash}\) is not in the local ledger"):
        world.ledger.verify()


def test_edited_last_entry_is_refused_against_the_remote_head(world):
    trial(world, "H1-01")
    trial(world, "H1-02", "fails")
    head = world.ledger.local_head()
    entries = world.ledger.verify().snapshot.entries
    data = recomputed(entries, len(entries) - 1, lambda b: {**b, "result": {"error": "edited"}})
    commit_on_top(world, data, push=False, parent=git(world.repo, "rev-parse", f"{head}~1"))
    with pytest.raises(VerificationError, match="is not in the local ledger"):
        world.ledger.verify()


def test_run_is_refused_when_the_local_ledger_differs_from_the_remote(world):
    other = world.clone("other")
    assert other.local_head() == world.ledger.local_head()
    trial(world, "B-H1")  # the remote moves on; the other clone is now behind
    with pytest.raises(LedgerOutOfStep, match="behind"):
        harness.run(other.git.repo, {"configuration": "H1-01"})
    with pytest.raises(LedgerOutOfStep, match="behind"):
        other.append(open_body(world.registration, "B-H2"))
    calls_before = list(world.module().CALLED)
    # Ahead: an entry committed locally but never pushed.
    body = open_body(world.registration, "B-H2")
    entries = world.ledger.verify().snapshot.entries
    last = entries[-1]
    entry = make_entry({**body, "seq": len(entries), "prev": last.hash, "time": last.body["time"]})
    data = world.ledger.git.blob(world.ledger.local_head(), LEDGER_PATH) + entry.line.encode() + b"\n"
    commit_on_top(world, data, push=False)
    with pytest.raises(LedgerOutOfStep, match="ahead"):
        trial(world, "H1-01")
    assert world.module().CALLED == calls_before
    fresh_path = world.tmp / "fresh"
    git(world.tmp, "clone", "--quiet", str(world.remote), str(fresh_path))
    fresh = GitLedger(fresh_path)
    with pytest.raises(LedgerOutOfStep, match="no local ledger"):
        fresh.require_in_step()
    fresh.sync()
    assert fresh.require_in_step() == fresh.fetch()


def _race(appenders):
    barrier = threading.Barrier(len(appenders))

    def go(append):
        barrier.wait()
        try:
            return append()
        except (LedgerBusy, LedgerOutOfStep, LedgerWriteError) as refusal:
            return refusal

    with ThreadPoolExecutor(len(appenders)) as pool:
        return list(pool.map(go, appenders))


def test_two_concurrent_appends_cannot_share_a_previous_hash(world):
    reg = world.registration
    before = len(world.ledger.verify().snapshot.entries)
    same = [GitLedger(world.repo), GitLedger(world.repo)]
    bodies = [open_body(reg, "B-H1"), open_body(reg, "B-H2")]
    outcomes = _race([lambda i=i: same[i].append(bodies[i]) for i in range(2)])
    clones = [world.clone("a"), world.clone("b")]
    later = [open_body(reg, "C-H1", seed=0), open_body(reg, "C-H2", seed=0)]
    outcomes += _race([lambda i=i: clones[i].append(later[i]) for i in range(2)])
    written = [o for o in outcomes if not isinstance(o, Exception)]
    assert len(written) >= 2  # at least one writer in each race got through
    remote = parse_ledger(world.remote_ledger())
    assert len(remote) == before + len(written)
    assert len({e.body["prev"] for e in remote}) == len(remote)  # never a fork
    assert {e.hash for e in written} <= {e.hash for e in remote}
    for clone in clones:
        clone.sync()
        clone.verify()


def test_verifier_refuses_a_merge_on_the_ledger_branch(world):
    trial(world, "B-H1")
    head = world.ledger.local_head()
    tree = git(world.repo, "rev-parse", f"{head}^{{tree}}")
    merge = git(world.repo, "commit-tree", tree, "-p", head, "-p", f"{head}~1", "-m", "merge")
    git(world.repo, "update-ref", "refs/heads/ledger", merge)
    git(world.repo, "push", "--quiet", "origin", f"{merge}:refs/heads/ledger")
    with pytest.raises(VerificationError, match="is a merge"):
        world.ledger.verify()


def test_a_milestone_tag_marks_the_ledger_head(world):
    trial(world, "B-H1")
    head = world.ledger.local_head()
    tag_object = world.ledger.tag_milestone("ledger-m1", "M1: the gate")
    assert world.ledger.milestone_tags()["ledger-m1"] == head
    kind = subprocess.run(["git", "--git-dir", str(world.remote), "cat-file", "-t", tag_object],
                          capture_output=True, check=True).stdout.decode().strip()
    assert kind == "tag"
    assert world.ledger.verify().tags["ledger-m1"] == head
    with pytest.raises(LedgerError, match="already exists"):
        world.ledger.tag_milestone("ledger-m1", "again")
    with pytest.raises(ValueError, match="ledger-"):
        world.ledger.tag_milestone("prereg-v9", "not a milestone")
    with pytest.raises(ValueError, match="lower-case"):
        world.ledger.tag_milestone("ledger-M2", "upper case")
    assert git(world.repo, "tag", "--list", "ledger-*") == ""  # the tag lives on the remote


def test_a_stray_ledger_tag_is_reported_not_fatal(world):
    # A ledger-* tag on a code commit, as a careless `git push --tags` would make: the
    # tag ruleset makes it permanent, so it must not stop every run (review M6).
    code = git(world.repo, "rev-parse", "HEAD")
    git(world.repo, "tag", "ledger-test", code)
    git(world.repo, "push", "--quiet", "origin", "refs/tags/ledger-test")
    verified = world.ledger.verify()
    assert verified.ignored_tags == {"ledger-test": code}
    assert "ledger-test" not in verified.tags
    assert trial(world, "B-H1").status == "completed"


def test_an_upper_case_ledger_tag_is_not_ignored(world):
    # ledger-M1 is as permanent as ledger-m1 under the tag ruleset (review L9).
    trial(world, "B-H1")
    head = world.ledger.local_head()
    git(world.repo, "tag", "ledger-M1", head)
    git(world.repo, "push", "--quiet", "origin", "refs/tags/ledger-M1")
    assert world.ledger.verify().tags["ledger-M1"] == head
    _rewrite_from(world, head, "rewritten after the upper-case tag")
    with pytest.raises(VerificationError, match="milestone tag ledger-M1"):
        world.ledger.verify()


def test_the_freeze_tag_needs_the_freeze_command_and_a_complete_grid(world):
    trial(world, "B-H1")
    with pytest.raises(LedgerError, match="irreversible"):
        world.ledger.tag_milestone("ledger-freeze", "too easy")
    with pytest.raises(gate.GateRefused, match="14 registered configurations have no selectable"):
        gate.freeze(world.ledger, "M2")
    assert "ledger-freeze" not in world.ledger.milestone_tags()
    gate.freeze(world.ledger, "M2, two hypotheses unrun", allow_incomplete=True)
    assert "ledger-freeze" in world.ledger.verify().tags
    with pytest.raises(gate.GateRefused, match="already frozen"):
        gate.freeze(world.ledger, "again", allow_incomplete=True)


def _rewrite_from(world, base, change_label):
    """Force push a rewritten ledger to the temporary remote, as an admin could."""
    entries = world.ledger.snapshot(base).entries
    data = recomputed(
        entries, len(entries) - 1, lambda b: {**b, "result": {**b["result"], "note": change_label}}
    )
    files = world.ledger.files_at(base)
    files.pop(LEDGER_PATH)
    parent = git(world.repo, "rev-parse", f"{base}~1")
    commit = world.ledger._commit(parent, data, files, "rewritten")
    git(world.repo, "update-ref", "refs/heads/ledger", commit)
    git(world.repo, "push", "--quiet", "--force", "origin", f"{commit}:refs/heads/ledger")
    return commit


def test_every_milestone_tag_is_an_ancestor_of_the_ledger_head(world):
    trial(world, "B-H1")
    world.ledger.tag_milestone("ledger-m1", "M1")
    world.ledger.verify()
    _rewrite_from(world, world.ledger.local_head(), "rewritten after the milestone")
    with pytest.raises(VerificationError, match=r"milestone tag ledger-m1 .* is not an ancestor"):
        world.ledger.verify()


def test_ledger_at_each_milestone_tag_is_a_prefix_of_the_current_ledger(world):
    trial(world, "B-H1")
    world.ledger.tag_milestone("ledger-m1", "M1")
    entries = world.ledger.verify().snapshot.entries
    data = recomputed(entries, 1, lambda b: {**b, "label": "edited after the milestone"})
    files = world.ledger.files_at(world.ledger.local_head())
    files.pop(LEDGER_PATH)
    commit_on_top(world, data, files)  # a fast-forward: the tag stays an ancestor
    with pytest.raises(VerificationError, match="ledger at milestone tag ledger-m1 is not a prefix"):
        world.ledger.verify()


def test_verifier_flags_a_tag_that_no_longer_matches_the_genesis_entry(world):
    world.ledger.verify()
    world.write("docs/preregistration.json", b'{"schema": "a different grid"}\n')
    moved_to = world.commit("a different registration")
    world.push()
    git(world.repo, "tag", "-f", "-a", "prereg-v1", "-m", "moved", moved_to)
    git(world.repo, "push", "--quiet", "--force", "origin", "refs/tags/prereg-v1")
    with pytest.raises(VerificationError, match=rf"tag prereg-v1 points at {moved_to}"):
        world.ledger.verify()
    from norse_research.gitledger import load_registration

    genesis = world.ledger.snapshot(world.ledger.local_head()).genesis
    assert load_registration(world.ledger.git, genesis).development_manifest_sha256 == (
        world.manifest_sha256
    )


def test_entry_result_hash_matches_the_committed_return_series(world):
    record = trial(world, "H1-01")
    head = world.ledger.local_head()
    digest = record.result_entry.body["result_sha256"]
    committed = world.ledger.git.blob(head, f"ledger/returns/{digest}.csv")
    assert sha256_hex(committed) == digest
    assert committed.count(b"\n") == 1 + 2802
    assert record.result_entry.body["result"]["days"] == 2802
    files = world.ledger.files_at(head)
    data = files.pop(LEDGER_PATH)
    files[f"ledger/returns/{digest}.csv"] = committed.replace(b"\n2025-12-31,", b"\n2025-12-31,1")
    commit_on_top(world, data, files)
    with pytest.raises(VerificationError, match="does not match its hash"):
        world.ledger.verify()


def test_a_path_with_a_newline_cannot_change_what_is_read(world):
    # The review's probe: a path holding a newline and an object id used to shift
    # every later blob in a batch read, so snapshot() saw a forged ledger (L2).
    trial(world, "H1-01", "fails")
    head = world.ledger.local_head()
    real = world.ledger.git.blob(head, LEDGER_PATH)
    forged = git(world.repo, "hash-object", "-w", "--stdin", input=real.split(b"\n", 1)[0] + b"\n")
    odd = f"ledger/returns/a\n{forged}"
    index = world.tmp / "odd-index"
    env = {"GIT_INDEX_FILE": str(index)}
    world.ledger.git.run("read-tree", head, env=env)
    blob = git(world.repo, "hash-object", "-w", "--stdin", input=b"x")
    world.ledger.git.run("update-index", "--add", "--cacheinfo", f"100644,{blob},{odd}", env=env)
    tree = world.ledger.git.text("write-tree", env=env)
    commit = git(world.repo, "commit-tree", tree, "-p", head, "-m", "odd path")
    files = world.ledger.files_at(commit)
    assert files[LEDGER_PATH] == real
    assert files[odd] == b"x"
    with pytest.raises(VerificationError, match="files other than the ledger"):
        world.ledger.snapshot(commit)


def test_a_remote_name_cannot_inject_an_option(world):
    for name in ("--upload-pack=touch pwned", "-o", "origin;rm", ""):
        with pytest.raises(ValueError, match="not a remote name"):
            GitLedger(world.repo, name)
    with pytest.raises(ValueError, match="not a remote name"):
        harness.run(world.repo, {"configuration": "H1-01"}, remote="--upload-pack=x")
    assert not (world.repo / "pwned").exists()


def test_write_genesis_validates_its_inputs(tmp_path, monkeypatch):
    clear_overrides(monkeypatch)
    world = World(tmp_path, genesis=False)
    try:
        good = {"tree_paths": TREE_PATHS, "strategies": world.pins,
                "key_record": (world.prereg_commit, KEY_RECORD)}
        world.write("research/keys/weak.json", json.dumps(
            {"kdf": "sha256", "n": 1, "r": 8, "p": 1, "salt": "ab"}) + "\n")
        weak = world.commit("a weak key record")
        world.push()
        world.write("research/keys/unpushed.json", (world.repo / KEY_RECORD).read_text())
        unpushed = world.commit("a key record not on main")
        git(world.repo, "reset", "--quiet", "--hard", "HEAD~1")
        cases = [
            ({"key_record": (weak, "research/keys/weak.json")}, "scrypt with n of at least"),
            ({"key_record": (unpushed, "research/keys/unpushed.json")}, "not on the remote's main"),
            ({"tree_paths": {**TREE_PATHS, "H2": ["strategies/missing"]}}, "is not on the remote"),
            ({"tree_paths": {**TREE_PATHS, "harness": ["strategies"]}}, "overlaps"),
            ({"strategies": {k: v for k, v in world.pins.items() if k != "B-H2"}},
             "must pin exactly"),
            ({"strategies": {**world.pins, "B-H1": world.pins["H2-01"]}},
             "outside H1's tree paths"),
        ]
        for change, message in cases:
            with pytest.raises(LedgerError, match=message):
                write_genesis(world.ledger, **{**good, **change})
        assert world.ledger.local_head() is None  # nothing was written
        write_genesis(world.ledger, **good)
        assert world.ledger.verify().tags[GENESIS_TAG] == world.ledger.local_head()
    finally:
        world.close()


def test_write_genesis_is_refused_once_the_registration_is_closed(world):
    world.write("docs/PREREGISTRATION.md", b"# a second version\n")
    world.commit("prereg-v2")
    world.push()
    git(world.repo, "tag", "-a", "prereg-v2", "-m", "second version")
    git(world.repo, "push", "--quiet", "origin", "refs/tags/prereg-v2")
    args = {"tree_paths": TREE_PATHS, "strategies": world.pins,
            "key_record": (world.prereg_commit, KEY_RECORD)}
    write_genesis(world.ledger, **args, tag="prereg-v2")
    assert world.ledger.verify().snapshot.genesis.body["preregistration"]["tag"] == "prereg-v2"
    trial(world, "B-H1")  # the first entry of any other kind closes the registration
    git(world.repo, "tag", "-a", "prereg-v3", "-m", "third version")
    git(world.repo, "push", "--quiet", "origin", "refs/tags/prereg-v3")
    with pytest.raises(LedgerError, match="registration is closed"):
        write_genesis(world.ledger, **args, tag="prereg-v3")
    genesis = dict(world.ledger.snapshot(world.ledger.local_head()).entries[0].body)
    genesis["preregistration"] = {**genesis["preregistration"], "tag": "prereg-v3"}
    with pytest.raises(VerificationError, match="closed the registration"):
        world.ledger.append({k: v for k, v in genesis.items() if k not in ("seq", "prev", "time")})


def test_the_command_line_verifies_syncs_tags_and_writes_genesis(world, tmp_path, capsys):
    from norse_research.gitledger import main

    trial(world, "B-H1")
    repo = ["--repo", str(world.repo)]
    assert main([*repo, "verify"]) == 0
    out = capsys.readouterr().out
    assert "ledger verified: 3 entries" in out and "0 trial entries" in out
    assert f"milestone tags: {GENESIS_TAG}" in out
    assert main([*repo, "tag", "ledger-m1", "--message", "M1: the gate"]) == 0
    assert "ledger-m1: tag object" in capsys.readouterr().out
    assert main([*repo, "verify"]) == 0
    assert f"milestone tags: {GENESIS_TAG}, ledger-m1" in capsys.readouterr().out
    assert main([*repo, "sync"]) == 0
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    second = World(second_dir, genesis=False)
    pins = tmp_path / "pins.json"
    pins.write_text(json.dumps(second.pins))
    try:
        assert main([
            "--repo", str(second.repo), "genesis",
            "--key-record", f"{second.prereg_commit}:{KEY_RECORD}",
            "--strategies", str(pins),
            "--harness", *TREE_PATHS["harness"], "--h1", *TREE_PATHS["H1"],
            "--h2", *TREE_PATHS["H2"],
        ]) == 0
        assert f"genesis entry 0 written and tagged {GENESIS_TAG}" in capsys.readouterr().out
        assert second.ledger.verify().snapshot.genesis.body["tree_paths"] == TREE_PATHS
    finally:
        second.close()
