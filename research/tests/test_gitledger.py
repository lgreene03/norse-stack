"""The ledger in git, verified with its anchors (G1.5).

Every test runs against a temporary bare repository standing in for GitHub and a
temporary clone; the real repository, its ledger and its tags are never touched.
Threat model T3, T4, T5, T7, T9, T10 and T24, and the security review's M6, M7, L2,
L3, L9 and L10.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import clear_overrides
from ledger_fixtures import KEY_RECORD, KEY_RECORD_DOC, REPO, TREE_PATHS, World, git
from norse_research import environment, gate, gitledger, harness
from norse_research.canonical import sha256_hex
from norse_research.gitledger import (
    GENESIS_ANCHOR,
    PREREGISTRATION_TAG,
    GitLedger,
    LedgerBusy,
    LedgerOutOfStep,
    LedgerWriteError,
    check_key_record,
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


def trial(world, cid="H1-01", **request):
    return harness.run(world.repo, {"configuration": cid, **request}, store=world.store)


def anchor(world) -> str:
    first = world.ledger.snapshot(world.ledger.local_head()).entries[0]
    return f"{GENESIS_ANCHOR}{first.hash}"


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
    assert prereg["tag"] == PREREGISTRATION_TAG == "prereg-v2"  # D21's prereg-v2
    assert prereg["commit"] == world.prereg_commit
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
    assert world.ledger.milestone_tags() == {anchor(world): head}
    # Before this tag existed, deleting the branch and writing a new genesis in a fresh
    # clone passed verification (review M7). Now it does not.
    git(world.repo, "push", "--quiet", "origin", f":refs/heads/{LEDGER_BRANCH}")
    fresh_path = world.tmp / "fresh"
    git(world.tmp, "clone", "--quiet", str(world.remote), str(fresh_path))
    fresh = GitLedger(fresh_path, clock=lambda: "2030-01-01T00:00:00Z")
    with pytest.raises(LedgerError, match=f"{GENESIS_ANCHOR}[0-9a-f]+ already anchors a ledger"):
        write_genesis(fresh, tree_paths=TREE_PATHS, strategies=world.pins,
                      key_record=(world.prereg_commit, KEY_RECORD),
                      registration_sha256=world.registration_sha256)
    # Written by hand instead, the new genesis fails verification in every clone.
    genesis = world.ledger.snapshot(head).genesis.body
    fresh.append({k: v for k, v in genesis.items() if k not in ("seq", "prev", "time")},
                 registration_sha256=world.registration_sha256)
    with pytest.raises(VerificationError, match=f"milestone tag {GENESIS_ANCHOR}.* not an ancestor"):
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
        # The body names the first world's registration files, so those are the hashes the
        # append is told to expect; this test is about the anchor, not the content pin.
        untagged.ledger.append({k: v for k, v in body.items() if k not in ("seq", "prev", "time")},
                               registration_sha256=world.registration_sha256)
        with pytest.raises(VerificationError, match=f"no {GENESIS_ANCHOR}[0-9a-f]+ tag"):
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
    trial(world, "H1-02")
    head = world.ledger.local_head()
    entries = world.ledger.verify().snapshot.entries
    data = recomputed(entries, len(entries) - 1,
                      lambda b: {**b, "result": {**b["result"], "sharpe": 9.0}})
    files = world.ledger.files_at(head)
    files.pop(LEDGER_PATH)
    commit_on_top(world, data, files, push=False,
                  parent=git(world.repo, "rev-parse", f"{head}~1"))
    with pytest.raises(VerificationError, match="is not in the local ledger"):
        world.ledger.verify()


def test_run_is_refused_when_the_local_ledger_differs_from_the_remote(world):
    other = world.clone("other")
    assert other.local_head() == world.ledger.local_head()
    trial(world, "B-H1")  # the remote moves on; the other clone is now behind
    with pytest.raises(LedgerOutOfStep, match="behind"):
        harness.run(other.git.repo, {"configuration": "H1-01"})
    with pytest.raises(LedgerOutOfStep, match="behind"):
        other.append(world.open_body("B-H2"))
    # Ahead: an entry committed locally but never pushed.
    body = world.open_body("B-H2")
    entries = world.ledger.verify().snapshot.entries
    last = entries[-1]
    entry = make_entry({**body, "seq": len(entries), "prev": last.hash, "time": last.body["time"]})
    data = world.ledger.git.blob(world.ledger.local_head(), LEDGER_PATH) + entry.line.encode() + b"\n"
    commit_on_top(world, data, push=False)
    with pytest.raises(LedgerOutOfStep, match="ahead"):
        trial(world, "H1-01")
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
    before = len(world.ledger.verify().snapshot.entries)
    same = [GitLedger(world.repo), GitLedger(world.repo)]
    bodies = [world.open_body("B-H1"), world.open_body("B-H2")]
    outcomes = _race([lambda i=i: same[i].append(bodies[i]) for i in range(2)])
    clones = [world.clone("a"), world.clone("b")]
    later = [world.open_body("C-H1", seed=0), world.open_body("C-H2", seed=0)]
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


def test_the_genesis_anchor_is_reserved_and_there_is_no_freeze(world):
    # Decision D21: the ledger freeze is gone, with its command, its anchor and every
    # rule keyed on it; the genesis anchor stays reserved to the genesis step (M6, N3).
    trial(world, "B-H1")
    head_entry = world.ledger.snapshot(world.ledger.local_head()).entries[0].hash
    for name in ("ledger-genesis", f"{GENESIS_ANCHOR}{head_entry}"):
        with pytest.raises(LedgerError, match="reserved name"):
            world.ledger.tag_milestone(name, "too easy")
    assert gitledger.RESERVED == ("ledger-genesis",)
    assert not hasattr(gitledger, "FREEZE_ANCHOR") and not hasattr(gate, "freeze")
    verified = world.ledger.verify()
    assert not hasattr(verified, "freeze") and not gate.gate_ledger(world.ledger).closed


def test_stray_reserved_names_never_block_the_ledger(tmp_path, monkeypatch):
    # Review N3: a stray ledger-genesis, which the tag ruleset makes permanent, once
    # blocked the genesis step for ever. The anchor is named for its content, so a stray
    # can never take its place. (The freeze and its anchor are gone, decision D21.)
    clear_overrides(monkeypatch)
    world = World(tmp_path, genesis=False)
    try:
        code = git(world.repo, "rev-parse", "HEAD")
        strays = {"ledger-freeze": code, "ledger-genesis": code,
                  f"{GENESIS_ANCHOR}{'1' * 64}": code}
        for name, commit in strays.items():
            git(world.repo, "tag", name, commit)
            git(world.repo, "push", "--quiet", "origin", f"refs/tags/{name}")
        world.write_genesis()  # the genesis step is not blocked
        trial(world, "B-H1")
        # A reserved name on a ledger commit that does not match its content is stray too.
        head = world.ledger.local_head()
        git(world.repo, "tag", f"{GENESIS_ANCHOR}{'2' * 64}", head)
        git(world.repo, "push", "--quiet", "origin", f"refs/tags/{GENESIS_ANCHOR}{'2' * 64}")
        verified = world.ledger.verify()
        assert set(verified.ignored_tags) == {*strays, f"{GENESIS_ANCHOR}{'2' * 64}"}
        assert trial(world, "B-H2").status == "completed"  # nothing blocked by a stray
    finally:
        world.close()


def test_a_freeze_shaped_milestone_is_refused(world):
    # Security review of #65, L-4: with the freeze gone, ledger-freeze-<commit> had become
    # an ordinary milestone name, and a ruleset-protected tag is permanent. The name is
    # refused, and one pushed by hand, in any case (I-6), is reported, never read as a
    # milestone.
    trial(world, "B-H1")
    head = world.ledger.local_head()
    with pytest.raises(LedgerError, match="withdrawn freeze anchor"):
        world.ledger.tag_milestone(f"ledger-freeze-{head}", "looks like the old anchor")
    with pytest.raises(ValueError, match="lower-case"):
        world.ledger.tag_milestone(f"ledger-Freeze-{head}", "looks like the old anchor")
    assert f"ledger-freeze-{head}" not in world.ledger.milestone_tags()
    by_hand = (f"ledger-freeze-{head}", f"ledger-Freeze-{head[:12]}")
    for name in by_hand:
        git(world.repo, "tag", name, head)
        git(world.repo, "push", "--quiet", "origin", f"refs/tags/{name}")
    verified = world.ledger.verify()
    for name in by_hand:
        assert name in verified.ignored_tags and name not in verified.tags


def test_a_hooks_path_or_a_file_system_monitor_is_refused(world):
    # Security review of #65, H-1 (probe P3b): core.hooksPath pointed the clone's hooks
    # anywhere and nothing refused it. Every ledger git command runs with hooks and the
    # file system monitor off, and a clone configuring either is refused.
    world.ledger.check_repository()
    for name, value in (("core.hooksPath", str(world.tmp / "elsewhere")),
                        ("core.fsmonitor", str(world.tmp / "monitor.sh")),
                        ("core.fsmonitor", "true")):
        git(world.repo, "config", name, value)
        with pytest.raises(gitledger.RemoteNotPinned, match=rf"(?i){name}.*H-1"):
            world.ledger.check_repository()
        with pytest.raises(gitledger.RemoteNotPinned, match="H-1"):
            trial(world, "B-H1")
        git(world.repo, "config", "--unset", name)
    git(world.repo, "config", "core.fsmonitor", "false")  # switched off is no risk
    world.ledger.check_repository()
    assert gitledger.HARDENED == ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")


def test_repository_configuration_that_runs_a_program_is_refused(world):
    # Security review of #65, H-1's class: a credential helper in the clone's own
    # configuration runs inside the unseal's push, with the unseal's objects in reach, and
    # so does any key that runs a program or redirects a read or a push. Since review of
    # #70, L-5, the local and worktree scopes, and what they include, may hold only the keys
    # REPOSITORY_SCOPE_ALLOWED lists, by the ledger and the harness alike. What git clone
    # writes, a local identity and the worktree switch still pass.
    for name, value in (("user.name", "the owner"), ("user.email", "owner@norse.invalid"),
                        ("core.fsmonitor", "false"), ("extensions.worktreeConfig", "true")):
        git(world.repo, "config", name, value)
    listed = git(world.repo, "config", "--local", "--name-only", "--list").splitlines()
    assert all(gitledger.REPOSITORY_SCOPE_ALLOWED.fullmatch(name) for name in listed), listed
    world.ledger.check_repository()
    for name in ("user.name", "user.email", "core.fsmonitor", "extensions.worktreeConfig"):
        git(world.repo, "config", "--unset", name)
    for name, value in (("credential.helper", "!f() { cat > /dev/null; }; f"),
                        ("credential.https://github.com.helper", "store"),
                        ("core.askPass", "/bin/echo"),
                        ("filter.quiet.clean", "cat"),
                        ("diff.quiet.textconv", "cat"),
                        ("gpg.ssh.program", "/bin/echo"),
                        ("protocol.file.allow", "always"),
                        ("uploadpack.packObjectsHook", "/bin/echo"),
                        ("includeIf.gitdir:/nowhere/.path", "/nowhere"),
                        ("sequence.editor", "/bin/echo"),
                        # Review of #70, L-5 (probe): keys a denylist let through, refused
                        # now that the clone's own scopes are an allowlist.
                        ("http.curloptResolve", "github.com:443:127.0.0.1"),
                        ("fetch.bundleURI", "file:///nowhere"),
                        ("core.attributesFile", "/nowhere/attrs"),
                        ("gpg.ssh.defaultKeyCommand", "/bin/echo"),
                        ("submodule.x.update", "!/bin/echo"),
                        ("alias.status", "!/bin/echo"),
                        ("extensions.partialClone", "elsewhere"),
                        ("remote.elsewhere.promisor", "true")):
        git(world.repo, "config", name, value)
        with pytest.raises(gitledger.RemoteNotPinned, match=rf"(?i){re.escape(name)} \(local\)"):
            world.ledger.check_repository()
        with pytest.raises(gitledger.RemoteNotPinned, match="H-1's class"):
            trial(world, "B-H1")
        git(world.repo, "config", "--unset", name)
    git(world.repo, "config", "extensions.worktreeConfig", "true")
    git(world.repo, "config", "--worktree", "credential.helper", "store")
    with pytest.raises(gitledger.RemoteNotPinned, match=r"credential\.helper \(worktree\)"):
        world.ledger.check_repository()
    git(world.repo, "config", "--worktree", "--unset", "credential.helper")
    included = world.tmp / "included.cfg"
    included.write_text("[credential]\n\thelper = store\n")
    git(world.repo, "config", "include.path", str(included))
    with pytest.raises(gitledger.RemoteNotPinned, match=r"credential\.helper \(local\)"):
        world.ledger.check_repository()
    git(world.repo, "config", "--unset", "include.path")
    world.ledger.check_repository()


def test_every_ledger_git_command_runs_with_pinned_configuration_and_the_push_still_works(
    world, monkeypatch
):
    # Security review of #65, M-3 and L-6: every ledger git command read the owner's global
    # configuration, and all but the remote-facing ones inherited the whole environment,
    # so a program an agent wrote into ~/.gitconfig, or a variable in the shell profile,
    # ran inside or steered the ledger's commands. Each now runs with no global or system
    # configuration, an environment built from a short allowlist with PATH rebuilt around
    # the git found, and, on the commands that reach the remote only, the credential helper
    # reset to the owner's. Global settings that run a program are neither refused nor
    # obeyed, and the ordinary append and push still work.
    marker = world.tmp / "global-program-ran"
    program = world.tmp / "program.sh"
    program.write_text(f"#!/bin/sh\necho ran >> {marker}\ncat\n")
    program.chmod(0o755)
    hooks = world.tmp / "global-hooks"
    hooks.mkdir()
    (hooks / "pre-push").write_text(f"#!/bin/sh\necho ran >> {marker}\nexit 1\n")
    (hooks / "pre-push").chmod(0o755)
    owner = world.tmp / "owner-global.cfg"
    owner.write_text(f'[core]\n\thooksPath = {hooks}\n[filter "x"]\n\tclean = {program}\n'
                     f"[credential]\n\thelper = !{program}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(owner))
    (world.repo / ".git" / "info").mkdir(exist_ok=True)
    (world.repo / ".git" / "info" / "attributes").write_text("* filter=x\n")
    world.ledger.check_repository()  # the global scope is never read, so not refused either
    before = len(parse_ledger(world.remote_ledger()))
    assert trial(world, "B-H1").status == "completed"
    assert len(parse_ledger(world.remote_ledger())) == before + 2
    world.ledger.verify()
    assert not marker.exists()
    # What each command is given, recorded without running it.
    seen = {}

    def record(command, **kwargs):
        seen[command[-1]] = (command, kwargs["env"])
        return subprocess.CompletedProcess(command, 0, b"", b"")

    program_path = environment.GIT_PROGRAM  # never one found on PATH (review of #70, L-6)
    steering = {"HTTPS_PROXY": "http://127.0.0.1:9", "ALL_PROXY": "socks5://127.0.0.1:9",
                "XDG_CONFIG_HOME": str(world.tmp), "SSL_CERT_FILE": str(owner),
                "CURL_CA_BUNDLE": str(owner), "DYLD_INSERT_LIBRARIES": str(program),
                "GIT_ASKPASS": str(program), "SSH_ASKPASS": str(program)}
    remote_facing = ("push", "fetch", "ls-remote")
    local_only = ("cat-file", "diff", "diff-tree", "commit-tree", "status", "hash-object",
                  "remote", "config")
    with monkeypatch.context() as m:
        for name, value in steering.items():
            m.setenv(name, value)
        m.setattr(gitledger.subprocess, "run", record)
        for subcommand in remote_facing + local_only:
            gitledger.Git(world.repo).call(subcommand)
        gitledger.Git(world.repo).call(
            "-c", "core.untrackedCache=false", "fetch",
            env={"GIT_CONFIG_GLOBAL": str(owner), "GIT_INDEX_FILE": "index"})
    allowed = {*environment.PASSED_ENVIRONMENT, "PATH", *environment.PINNED_CONFIGURATION}
    for subcommand, (command, env) in seen.items():
        assert command[0] == program_path and command[1:5] == list(gitledger.HARDENED)
        credentials = command[5:9] == list(gitledger.CREDENTIALS)
        assert credentials == (subcommand in remote_facing or subcommand == "fetch"), subcommand
        assert env["GIT_CONFIG_GLOBAL"] == os.devnull and env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert env["GIT_NO_REPLACE_OBJECTS"] == "1"
        assert set(env) - {"GIT_INDEX_FILE"} <= allowed and not set(env) & set(steering)
        assert env["PATH"].split(os.pathsep)[0] == os.path.dirname(program_path)
        assert env["HOME"] == os.environ["HOME"]
    assert gitledger.CREDENTIALS == ("-c", "credential.helper=", "-c",
                                     "credential.helper=osxkeychain")


def test_the_ledger_never_runs_a_git_found_on_path(world, monkeypatch):
    # Review of #70, L-6 (probe): a wrapper git put first on PATH, as an edit to the owner's
    # shell profile could, ran as every ledger git command of the unseal. The ledger now
    # runs environment.GIT_PROGRAM alone, checked to belong to root with its exec path and
    # its osxkeychain helper, so the wrapper never runs; the ordinary append and push still do.
    bindir = world.tmp / "agent-bin"
    bindir.mkdir()
    marker = world.tmp / "wrapper-ran"
    real = shutil.which("git")
    # It logs only calls carrying the ledger's own first settings, as the reviewer's did;
    # the fixtures' own git (remote_ledger, for one) runs from PATH on purpose.
    (bindir / "git").write_text(
        '#!/bin/sh\nif [ "$2" = core.hooksPath=/dev/null ]; then\n'
        f'  echo "$@" >> {marker}\nfi\nexec {real} "$@"\n'
    )
    (bindir / "git").chmod(0o755)
    before = len(parse_ledger(world.remote_ledger()))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    assert shutil.which("git") == str(bindir / "git")
    assert trial(world, "B-H1").status == "completed"
    world.ledger.verify()
    assert not marker.exists()
    assert len(parse_ledger(world.remote_ledger())) == before + 2
    assert environment.git_program() == environment.GIT_PROGRAM
    # On macOS, the Command Line Tools' git itself, never the /usr/bin/git launcher, whose
    # xcrun cache the owner's user can write (review of #70, round 2).
    assert environment.GIT_PROGRAM == (
        "/Library/Developer/CommandLineTools/usr/bin/git" if sys.platform == "darwin"
        else "/usr/bin/git")
    assert environment.git_version().startswith("git version")


def test_a_git_the_owners_user_can_replace_is_refused(tmp_path):
    # Review of #70, L-6: a git binary, or a folder above it, that belongs to the owner's
    # user is refused, whatever it is called; so is one that does not exist.
    fake = tmp_path / "bin" / "git"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    with pytest.raises(environment.EnvironmentRefused, match="is not root's alone"):
        environment.git_program(str(fake))
    with pytest.raises(environment.EnvironmentRefused, match="does not exist"):
        environment.git_program(str(tmp_path / "nowhere" / "git"))
    with pytest.raises(environment.EnvironmentRefused, match="does not exist"):
        environment.git_program("git")  # never a name to look up on PATH
    environment.git_program()  # GIT_PROGRAM, and everything it runs, is root's


def test_genesis_pins_only_the_newest_preregistration_tag(tmp_path, monkeypatch):
    # Quant review of #65, L7: a genesis could pin prereg-v1, whose cross-trial V and
    # freeze the code no longer implements. It pins only the registration the code
    # implements, and only while the remote holds no newer one, on the genesis step and
    # through the public append alike.
    clear_overrides(monkeypatch)
    world = World(tmp_path, genesis=False)
    try:
        git(world.repo, "tag", "-a", "prereg-v1", "-m", "an older version", world.prereg_commit)
        git(world.repo, "push", "--quiet", "origin", "refs/tags/prereg-v1")
        with pytest.raises(LedgerError, match=r"pins prereg-v2, .* never prereg-v1"):
            world.write_genesis(tag="prereg-v1")
        git(world.repo, "tag", "-a", "prereg-v3", "-m", "a newer version", world.prereg_commit)
        git(world.repo, "push", "--quiet", "origin", "refs/tags/prereg-v3")
        with pytest.raises(LedgerError, match="holds prereg-v3, newer than prereg-v2") as newer:
            world.write_genesis()
        # Security review of #65, L-9: an agent can push a newer tag, so the refusal calls
        # it an incident and never tells the owner to move the code to it.
        assert "is an incident, not a reason to change the code" in str(newer.value)
        assert "update the code" not in str(newer.value)
        assert world.ledger.local_head() is None  # nothing was written
        monkeypatch.setattr(gitledger, "PREREGISTRATION_TAG", "prereg-v3")  # the code moves on
        world.write_genesis(tag="prereg-v3")
        genesis = dict(world.ledger.snapshot(world.ledger.local_head()).entries[0].body)
        older = {**genesis, "preregistration": {**genesis["preregistration"], "tag": "prereg-v2"}}
        with pytest.raises(VerificationError, match="never prereg-v2"):
            world.ledger.append({k: v for k, v in older.items() if k not in ("seq", "prev", "time")})
        assert len(world.ledger.verify().snapshot.entries) == 1
    finally:
        world.close()


def test_genesis_pins_the_registration_by_content_as_well_as_by_tag(tmp_path, monkeypatch):
    # Security review of #65, L-9: genesis bound a tag name to the code, not content. The
    # code now pins the SHA-256 of each pre-registration file too, and they are this
    # checkout's own docs/, so the registration and the code change together or not at all.
    # A tag whose files differ is refused at genesis as an incident.
    registry = REPO / "docs"
    assert gitledger.PREREGISTRATION_SHA256 == {
        f"docs/{name}": sha256_hex((registry / name).read_bytes())
        for name in ("PREREGISTRATION.md", "preregistration.json")
    }
    clear_overrides(monkeypatch)
    world = World(tmp_path, genesis=False)
    try:
        # The World's registration is synthetic, so the code's own pins refuse it.
        with pytest.raises(LedgerError, match="is not the registration this code") as moved:
            write_genesis(world.ledger, tree_paths=TREE_PATHS, strategies=world.pins,
                          key_record=(world.prereg_commit, KEY_RECORD))
        assert "other content for docs/preregistration.json." in str(moved.value)
        assert "is an incident, not a reason to change the code" in str(moved.value)
        other = {**world.registration_sha256, "docs/PREREGISTRATION.md": "6" * 64}
        with pytest.raises(LedgerError, match=r"other content for docs/PREREGISTRATION\.md\."):
            world.write_genesis(registration_sha256=other)
        assert world.ledger.local_head() is None  # nothing was written
        world.write_genesis()  # its own content
        genesis = world.ledger.verify().snapshot.genesis.body
        assert genesis["preregistration"]["files"] == world.registration_sha256
    finally:
        world.close()
    # Review of #70, L-11: the public append applies the pin too, so a genesis written past
    # the genesis step is refused as well.
    other = World(tmp_path / "other", genesis=False)
    try:
        body = {k: v for k, v in genesis.items() if k not in ("seq", "prev", "time")}
        body.update(remote={**body["remote"], "url": str(other.remote)},
                    preregistration={**body["preregistration"], "commit": other.prereg_commit},
                    key_record={**body["key_record"], "commit": other.prereg_commit},
                    strategies=other.pins)
        with pytest.raises(VerificationError, match="is not the registration this code implements"):
            other.ledger.append(body)
        assert other.ledger.local_head() is None
    finally:
        other.close()


def test_a_superseding_genesis_cannot_move_the_pinned_remote(world, monkeypatch):
    # Review N5: a superseding genesis written through a mirror, pinning the mirror.
    monkeypatch.setattr(gitledger, "PREREGISTRATION_TAG", "prereg-v3")  # the newest
    fake = world.tmp / "fake.git"
    git(world.tmp, "clone", "--quiet", "--mirror", str(world.remote), str(fake))
    clone = world.tmp / "via-fake"
    git(world.tmp, "clone", "--quiet", str(fake), str(clone))
    fake_ledger = GitLedger(clone)
    fake_ledger.sync()
    git(clone, "tag", "-a", "prereg-v3", "-m", "a second version", world.prereg_commit)
    git(clone, "push", "--quiet", "origin", "refs/tags/prereg-v3")
    with pytest.raises(VerificationError, match="may not move the pinned remote"):
        write_genesis(fake_ledger, tree_paths=TREE_PATHS, strategies=world.pins,
                      key_record=(world.prereg_commit, KEY_RECORD), tag="prereg-v3",
                      remote_url=str(fake), registration_sha256=world.registration_sha256)
    with pytest.raises(LedgerError, match="pinned remote"):
        harness.run(clone, {"configuration": "H1-01"})


def test_the_key_record_contract_is_exact():
    good = dict(KEY_RECORD_DOC)
    check_key_record(json.dumps(good).encode())
    bad = [
        {**good, "n": 2**17 + 1},  # not a power of two (review N8)
        {**good, "n": 2**16},
        {**good, "r": 4},
        {**good, "p": 2},
        {**good, "salt": "a" * 33},  # odd length
        {**good, "salt": "ab" * 15},  # under 16 bytes
        {**good, "salt": "AB" * 16},  # not lower-case
        {**good, "dklen": 16},
        {**good, "verifier_sha256": "cd" * 31},  # not a full SHA-256
        {**good, "verifier_sha256": "CD" * 32},  # not lower-case
        {**good, "kdf": "sha256"},
        {**good, "n": float(2**17)},
        {**good, "sha256_of_passphrase": "ab" * 32},  # an extra field
        {**good, "derived_key": "cd" * 32},  # the scrypt output is never in the record (H1)
        {k: v for k, v in good.items() if k != "verifier_sha256"},
        {k: v for k, v in good.items() if k != "dklen"},
    ]
    for record in bad:
        with pytest.raises(LedgerError, match="contract"):
            check_key_record(json.dumps(record).encode())
    with pytest.raises(LedgerError, match="not JSON"):
        check_key_record(b"scrypt$17$8$1$salt$hash")


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
    tag = PREREGISTRATION_TAG
    git(world.repo, "tag", "-f", "-a", tag, "-m", "moved", moved_to)
    git(world.repo, "push", "--quiet", "--force", "origin", f"refs/tags/{tag}")
    with pytest.raises(VerificationError, match=rf"tag {tag} points at {moved_to}"):
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
    trial(world, "H1-01")
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
                "key_record": (world.prereg_commit, KEY_RECORD),
                "registration_sha256": world.registration_sha256}
        world.write("research/keys/weak.json", json.dumps(
            {"kdf": "sha256", "n": 1, "r": 8, "p": 1, "salt": "ab"}) + "\n")
        weak = world.commit("a weak key record")
        world.push()
        world.write("research/keys/unpushed.json", (world.repo / KEY_RECORD).read_text())
        unpushed = world.commit("a key record not on main")
        git(world.repo, "reset", "--quiet", "--hard", "HEAD~1")
        world.write("strategies/h2/helper.py", "import subprocess\n")
        bad_helper = world.commit("a helper that imports subprocess")
        world.push()
        cases = [
            ({"key_record": (weak, "research/keys/weak.json")}, "breaks its contract"),
            ({"key_record": (unpushed, "research/keys/unpushed.json")}, "not on the remote's main"),
            ({"tree_paths": {**TREE_PATHS, "H2": ["strategies/missing"]}}, "is not on the remote"),
            ({"tree_paths": {**TREE_PATHS, "H1": ["strategies"]}}, "overlaps"),
            ({"tree_paths": {**TREE_PATHS, "H1": ["research"]}}, "overlaps"),
            ({"tree_paths": {**TREE_PATHS, "harness": ["research/norse_research/sim"]}},
             "must cover all of research/norse_research"),
            ({}, "reaches outside the strategy allowlist"),
            ({"strategies": {k: v for k, v in world.pins.items() if k != "B-H2"}},
             "must pin exactly"),
            ({"strategies": {**world.pins, "B-H1": world.pins["H2-01"]}},
             "outside H1's tree paths"),
        ]
        for change, message in cases:
            with pytest.raises(LedgerError, match=message):
                write_genesis(world.ledger, **{**good, **change})
        assert world.ledger.local_head() is None  # nothing was written
        git(world.repo, "rm", "--quiet", "strategies/h2/helper.py")
        world.commit(f"remove the helper added in {bad_helper}")
        world.push()
        # A harness path may hold the strategy paths: they are left out of its digest.
        nested = {"harness": ["strategies", "research/norse_research"],
                  "H1": ["strategies/h1"], "H2": ["strategies/h2"]}
        write_genesis(world.ledger, **{**good, "tree_paths": nested})
        assert world.ledger.verify().tags[anchor(world)] == world.ledger.local_head()
    finally:
        world.close()


def test_write_genesis_is_refused_once_the_registration_is_closed(world, monkeypatch):
    # Each later tag stands for code that implements it (quant review of #65, L7).
    monkeypatch.setattr(gitledger, "PREREGISTRATION_TAG", "prereg-v3")
    world.write("docs/PREREGISTRATION.md", b"# a second version\n")
    world.commit("prereg-v3")
    world.push()
    git(world.repo, "tag", "-a", "prereg-v3", "-m", "second version")
    git(world.repo, "push", "--quiet", "origin", "refs/tags/prereg-v3")
    second = {**world.registration_sha256,
              "docs/PREREGISTRATION.md": sha256_hex(b"# a second version\n")}
    args = {"tree_paths": TREE_PATHS, "strategies": world.pins,
            "key_record": (world.prereg_commit, KEY_RECORD), "registration_sha256": second}
    write_genesis(world.ledger, **args, tag="prereg-v3")
    assert world.ledger.verify().snapshot.genesis.body["preregistration"]["tag"] == "prereg-v3"
    trial(world, "B-H1")  # the first entry of any other kind closes the registration
    git(world.repo, "tag", "-a", "prereg-v4", "-m", "third version")
    git(world.repo, "push", "--quiet", "origin", "refs/tags/prereg-v4")
    monkeypatch.setattr(gitledger, "PREREGISTRATION_TAG", "prereg-v4")
    with pytest.raises(LedgerError, match="registration is closed"):
        write_genesis(world.ledger, **args, tag="prereg-v4")
    genesis = dict(world.ledger.snapshot(world.ledger.local_head()).entries[0].body)
    genesis["preregistration"] = {**genesis["preregistration"], "tag": "prereg-v4"}
    with pytest.raises(VerificationError, match="closed the registration"):
        world.ledger.append({k: v for k, v in genesis.items() if k not in ("seq", "prev", "time")})


def test_the_command_line_verifies_syncs_tags_and_writes_genesis(world, tmp_path, capsys,
                                                                 monkeypatch):
    from norse_research.gitledger import main

    trial(world, "B-H1")
    repo = ["--repo", str(world.repo)]
    assert main([*repo, "verify"]) == 0
    out = capsys.readouterr().out
    assert "ledger verified: 3 entries" in out and "0 trial entries" in out
    assert f"milestone tags: {anchor(world)}" in out
    assert main([*repo, "tag", "ledger-m1", "--message", "M1: the gate"]) == 0
    assert "ledger-m1: tag object" in capsys.readouterr().out
    assert main([*repo, "verify"]) == 0
    assert f"milestone tags: {anchor(world)}, ledger-m1" in capsys.readouterr().out
    assert main([*repo, "sync"]) == 0
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    second = World(second_dir, genesis=False)
    pins = tmp_path / "pins.json"
    pins.write_text(json.dumps(second.pins))
    # The command pins the code's registration; this world's is synthetic (L-9).
    monkeypatch.setattr(gitledger, "PREREGISTRATION_SHA256", second.registration_sha256)
    try:
        assert main([
            "--repo", str(second.repo), "genesis",
            "--key-record", f"{second.prereg_commit}:{KEY_RECORD}",
            "--strategies", str(pins),
            "--harness", *TREE_PATHS["harness"], "--h1", *TREE_PATHS["H1"],
            "--h2", *TREE_PATHS["H2"],
        ]) == 0
        assert "genesis entry 0 written and anchored" in capsys.readouterr().out
        assert second.ledger.verify().snapshot.genesis.body["tree_paths"] == TREE_PATHS
    finally:
        second.close()


def test_trees_cover_the_package_without_the_strategy_paths(world):
    # Review N12: with strategies inside the package, a file the harness paths list one
    # by one could be added later in no recorded tree. The harness digest now covers its
    # whole paths, less the hypotheses' paths nested in them.
    from norse_research.gitledger import tree_digests

    paths = {"harness": ["research/norse_research"],
             "H1": ["research/norse_research/strategies/h1"],
             "H2": ["research/norse_research/strategies/h2"]}
    world.write("research/norse_research/strategies/h1/trend.py", "X = 1\n")
    world.write("research/norse_research/strategies/h2/carry.py", "X = 1\n")
    before = tree_digests(world.ledger.git, world.commit("strategies"), paths)
    world.write("research/norse_research/strategies/common.py", "SHARED = 1\n")
    after_helper = tree_digests(world.ledger.git, world.commit("a shared helper"), paths)
    assert after_helper["harness"] != before["harness"]
    assert (after_helper["H1"], after_helper["H2"]) == (before["H1"], before["H2"])
    world.write("research/norse_research/strategies/h1/trend.py", "X = 2\n")
    after_h1 = tree_digests(world.ledger.git, world.commit("an H1 change"), paths)
    assert after_h1["H1"] != after_helper["H1"]
    assert (after_h1["harness"], after_h1["H2"]) == (after_helper["harness"], after_helper["H2"])
