"""The ledger in git, verified with its anchors (G1.5).

Every test runs against a temporary bare repository standing in for GitHub and a
temporary clone; the real repository, its ledger and its tags are never touched.
Threat model T3, T4, T5, T7, T9, T10 and T24.
"""

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from ledger_fixtures import KEY_RECORD, TREE_PATHS, git, open_body
from norse_research import harness
from norse_research.canonical import sha256_hex
from norse_research.gitledger import (
    LEDGER_BRANCH,
    LedgerBusy,
    LedgerOutOfStep,
    LedgerWriteError,
    write_genesis,
)
from norse_research.ledger import (
    LEDGER_PATH,
    LedgerError,
    VerificationError,
    make_entry,
    parse_ledger,
)
from norse_research.registration import Registration


def trial(world, cid="H1-01", function="steady", **request):
    return harness.run(
        world.repo, world.strategy(function), {"configuration": cid, **request}, store=world.store
    )


def rerun(world, cid="H1-01", function="steady"):
    fix = git(world.repo, "rev-parse", "HEAD")
    record = {"defect": "synthetic defect", "fix_commit": fix, "section": "6"}
    return trial(world, cid, function, rerun=record)


def registration(world) -> Registration:
    return Registration.from_bytes((world.repo / "docs/preregistration.json").read_bytes())


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
    assert verified.head == verified.remote_head
    # The ledger branch holds only the ledger; main is untouched and the tree clean.
    assert git(world.repo, "ls-tree", "-r", "--name-only", LEDGER_BRANCH) == LEDGER_PATH
    assert git(world.repo, "status", "--porcelain") == ""


def test_recomputed_chain_after_an_edit_is_refused_by_history(world):
    trial(world, "H1-01")
    trial(world, "B-H1")
    entries = world.ledger.verify().snapshot.entries

    def relabel(body):
        return {**body, "label": "nudged"}

    data = recomputed(entries, 1, relabel)
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
    # Rewritten locally as a fresh append to the commit before the real last entry.
    commit_on_top(world, data, push=False, parent=git(world.repo, "rev-parse", f"{head}~1"))
    with pytest.raises(VerificationError, match="is not in the local ledger"):
        world.ledger.verify()


def test_run_is_refused_when_the_local_ledger_differs_from_the_remote(world):
    other = world.clone("other")
    assert other.local_head() == world.ledger.local_head()
    trial(world, "B-H1")  # the remote moves on; the other clone is now behind
    with pytest.raises(LedgerOutOfStep, match="behind"):
        harness.run(other.git.repo, world.strategy(), {"configuration": "H1-01"})
    # The append itself refuses too, under its lock, whoever calls it.
    with pytest.raises(LedgerOutOfStep, match="behind"):
        other.append(open_body(registration(world), "B-H2"))
    calls_before = list(world.module.CALLED)
    # Ahead: an entry committed locally but never pushed.
    body = open_body(registration(world), "B-H2")
    entries = world.ledger.verify().snapshot.entries
    last = entries[-1]
    entry = make_entry({**body, "seq": len(entries), "prev": last.hash, "time": last.body["time"]})
    data = world.ledger.git.blob(world.ledger.local_head(), LEDGER_PATH) + entry.line.encode() + b"\n"
    commit_on_top(world, data, push=False)
    with pytest.raises(LedgerOutOfStep, match="ahead"):
        trial(world, "H1-01")
    assert world.module.CALLED == calls_before
    # A fresh clone has no local ledger until it syncs from the remote.
    fresh_path = world.tmp / "fresh"
    git(world.tmp, "clone", "--quiet", str(world.remote), str(fresh_path))
    from norse_research.gitledger import GitLedger

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
    reg = registration(world)
    before = len(world.ledger.verify().snapshot.entries)
    # Two writers in one repository: the lock and the compare-and-swap.
    from norse_research.gitledger import GitLedger

    same = [GitLedger(world.repo), GitLedger(world.repo)]
    bodies = [open_body(reg, "B-H1"), open_body(reg, "B-H2")]
    outcomes = _race([lambda i=i: same[i].append(bodies[i]) for i in range(2)])
    # Two writers in two clones: the remote refuses the second push.
    clones = [world.clone("a"), world.clone("b")]
    outcomes += _race([lambda i=i: clones[i].append(bodies[i]) for i in range(2)])
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
    assert world.ledger.milestone_tags() == {"ledger-m1": head}
    kind = subprocess.run(["git", "--git-dir", str(world.remote), "cat-file", "-t", tag_object],
                          capture_output=True, check=True).stdout.decode().strip()
    assert kind == "tag"
    assert world.ledger.verify().tags == {"ledger-m1": head}
    with pytest.raises(LedgerError, match="already exists"):
        world.ledger.tag_milestone("ledger-m1", "again")
    with pytest.raises(ValueError, match="ledger-"):
        world.ledger.tag_milestone("prereg-v9", "not a milestone")
    # No local tag was created: the tag lives on the remote.
    assert git(world.repo, "tag", "--list", "ledger-*") == ""


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
    # The harness itself never trusts the tag: the registration it reads is the one
    # at the commit the genesis entry pins.
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
    # A return series altered after the fact no longer matches its entry.
    files = world.ledger.files_at(head)
    data = files.pop(LEDGER_PATH)
    path = f"ledger/returns/{digest}.csv"
    files[path] = committed.replace(b"\n2025-12-31,", b"\n2025-12-31,1")
    commit_on_top(world, data + b"", files)
    with pytest.raises(VerificationError, match="does not match its hash"):
        world.ledger.verify()


def test_write_genesis_is_refused_once_the_registration_is_closed(world):
    # A superseding registration before any other entry: a new tag, a new genesis.
    world.write("docs/PREREGISTRATION.md", b"# a second version\n")
    world.commit("prereg-v2")
    world.push()
    git(world.repo, "tag", "-a", "prereg-v2", "-m", "second version")
    git(world.repo, "push", "--quiet", "origin", "refs/tags/prereg-v2")
    commit = git(world.repo, "rev-parse", "HEAD")
    write_genesis(world.ledger, tree_paths=TREE_PATHS, key_record=(commit, KEY_RECORD),
                  tag="prereg-v2")
    assert world.ledger.verify().snapshot.genesis.body["preregistration"]["tag"] == "prereg-v2"
    trial(world, "B-H1")  # the first entry of any other kind closes the registration
    git(world.repo, "tag", "-a", "prereg-v3", "-m", "third version")
    git(world.repo, "push", "--quiet", "origin", "refs/tags/prereg-v3")
    with pytest.raises(LedgerError, match="registration is closed"):
        write_genesis(world.ledger, tree_paths=TREE_PATHS, key_record=(commit, KEY_RECORD),
                      tag="prereg-v3")
    genesis = dict(world.ledger.snapshot(world.ledger.local_head()).entries[0].body)
    genesis["preregistration"] = {**genesis["preregistration"], "tag": "prereg-v3"}
    with pytest.raises(VerificationError, match="closed the registration"):
        world.ledger.append({k: v for k, v in genesis.items() if k not in ("seq", "prev", "time")})


def test_the_command_line_verifies_syncs_tags_and_writes_genesis(world, tmp_path, capsys):
    from ledger_fixtures import World
    from norse_research.gitledger import main

    trial(world, "B-H1")
    repo = ["--repo", str(world.repo)]
    assert main([*repo, "verify"]) == 0
    out = capsys.readouterr().out
    assert "ledger verified: 3 entries" in out and "0 trial entries" in out
    assert "milestone tags: none" in out
    assert main([*repo, "tag", "ledger-m1", "--message", "M1: the gate"]) == 0
    assert "ledger-m1: tag object" in capsys.readouterr().out
    assert main([*repo, "verify"]) == 0
    assert "milestone tags: ledger-m1" in capsys.readouterr().out
    assert main([*repo, "sync"]) == 0
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    second = World(second_dir, genesis=False)
    try:
        assert main([
            "--repo", str(second.repo), "genesis",
            "--key-record", f"{second.prereg_commit}:{KEY_RECORD}",
            "--harness", *TREE_PATHS["harness"], "--h1", *TREE_PATHS["H1"],
            "--h2", *TREE_PATHS["H2"],
        ]) == 0
        assert "genesis entry 0 written" in capsys.readouterr().out
        assert second.ledger.verify().snapshot.genesis.body["tree_paths"] == TREE_PATHS
    finally:
        second.close()
