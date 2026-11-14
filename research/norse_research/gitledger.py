"""The trial ledger in git: appends, anchored verification and milestone tags (G1.5).

**Where it lives.** The ledger is the file ``ledger/trials.jsonl`` on its own branch,
``ledger``, beside the committed return series under ``ledger/returns/``. It is not
on main because main needs the ``research`` and ``validate`` checks and a pull
request (decision D16), which would refuse the harness's push per entry; the
threat model foresaw this ("put the ledger on its own branch"). The branch needs its
own ruleset (no deletion, no force push), which only the owner can add.

**Appending.** An append never touches the working tree, so it cannot dirty it. It
takes a lock in the repository's git directory, fetches the remote ledger ref, builds
the new commit with git's plumbing (a blob, a private index, a tree, a commit),
moves the local ref with a compare-and-swap, and pushes without force, so a second
writer's push is refused as non-fast-forward (T9). If the push fails the local ref is
put back and the append raises: the caller has computed nothing yet (T8).

**Verifying.** The chain alone proves only that it agrees with itself. ``verify``
adds the three anchors of the threat model's principle 1: along the ledger ref's
first-parent history every committed version of the file is a byte prefix of the
next and every committed return series is never changed (T4, T7); the remote's
ledger head is in the local file (T5); and every ``ledger-*`` milestone tag is an
ancestor of the head whose ledger is a prefix of the current one (T10). It also flags
a pre-registration tag that no longer points where the genesis entry says (T24).

Nothing here reads the clock to decide anything; it is read only for timestamps,
which never decrease along the chain (T11).
"""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .canonical import sha256_hex
from .ledger import (
    FORMAT,
    GENESIS,
    LEDGER_PATH,
    TIME_FORMAT,
    ZERO_HASH,
    Entry,
    LedgerError,
    LedgerSnapshot,
    VerificationError,
    make_entry,
    parse_ledger,
    utc_now,
)
from .periods import HOLDOUT_START, LAST_DEVELOPMENT_MONTH, OUT_OF_SAMPLE_START
from .registration import (
    HYPOTHESES,
    PREREGISTRATION_FILES,
    PREREGISTRATION_JSON,
    TRIAL,
    Registration,
)
from .store import REPO_ROOT

LEDGER_BRANCH = "ledger"
FREEZE_TAG = "ledger-freeze"
MILESTONE_TAG = re.compile(r"ledger-[a-z0-9][a-z0-9.-]*")
PREREGISTRATION_TAG = "prereg-v1"
LOCK_NAME = "norse-ledger.lock"
LEDGER_NAME = "Norse trial ledger"
LEDGER_EMAIL = "ledger@norse-stack.invalid"
IDENTITY = {
    "GIT_AUTHOR_NAME": LEDGER_NAME,
    "GIT_AUTHOR_EMAIL": LEDGER_EMAIL,
    "GIT_COMMITTER_NAME": LEDGER_NAME,
    "GIT_COMMITTER_EMAIL": LEDGER_EMAIL,
}
_RETURNS_FILE = re.compile(r"ledger/returns/[0-9a-f]{64}\.csv")


class GitError(LedgerError):
    """A git command failed."""


class LedgerWriteError(LedgerError):
    """An append failed. Nothing was published and the local ref is as it was."""


class LedgerOutOfStep(LedgerError, PermissionError):
    """The local ledger head is not the remote's (T3)."""


class LedgerBusy(LedgerError, PermissionError):
    """Another process holds the ledger lock (T9)."""


class Git:
    """A git repository, driven through the command line git runs."""

    def __init__(self, repo: Path) -> None:
        self.repo = Path(repo)

    def call(self, *args: str, input: bytes | None = None, env: Mapping | None = None):
        full = None if env is None else {**os.environ, **env}
        return subprocess.run(
            ["git", *args], cwd=self.repo, input=input, capture_output=True, env=full, check=False
        )

    def run(self, *args: str, input: bytes | None = None, env: Mapping | None = None) -> bytes:
        proc = self.call(*args, input=input, env=env)
        if proc.returncode:
            message = proc.stderr.decode(errors="replace").strip()[:800]
            raise GitError(f"git {args[0]} failed: {message}")
        return proc.stdout

    def text(self, *args: str, input: bytes | None = None, env: Mapping | None = None) -> str:
        return self.run(*args, input=input, env=env).decode().strip()

    def try_text(self, *args: str) -> str | None:
        proc = self.call(*args)
        return proc.stdout.decode().strip() if proc.returncode == 0 else None

    def is_ancestor(self, older: str, newer: str) -> bool:
        proc = self.call("merge-base", "--is-ancestor", older, newer)
        if proc.returncode in (0, 1):
            return proc.returncode == 0
        raise GitError(f"git merge-base failed: {proc.stderr.decode(errors='replace').strip()}")

    def blob(self, commit: str, path: str) -> bytes | None:
        proc = self.call("cat-file", "blob", f"{commit}:{path}")
        return proc.stdout if proc.returncode == 0 else None

    def blobs(self, specs: Sequence[str]) -> list[bytes | None]:
        """Many ``<rev>:<path>`` blobs in one ``cat-file --batch``."""
        if not specs:
            return []
        out = self.run("cat-file", "--batch", input=("\n".join(specs) + "\n").encode())
        found: list[bytes | None] = []
        pos = 0
        for _ in specs:
            end = out.index(b"\n", pos)
            header = out[pos:end].split()
            pos = end + 1
            if header[-1] == b"missing":
                found.append(None)
                continue
            size = int(header[2])
            found.append(out[pos : pos + size])
            pos += size + 1
        return found

    def git_dir(self) -> Path:
        common = self.text("rev-parse", "--git-common-dir")
        return (self.repo / common).resolve()


@dataclass(frozen=True)
class Verified:
    snapshot: LedgerSnapshot
    head: str | None
    remote_head: str | None
    tags: dict[str, str]  # milestone tag -> the ledger commit it marks


class GitLedger:
    def __init__(
        self,
        repo: Path,
        remote: str = "origin",
        branch: str = LEDGER_BRANCH,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.git = Git(repo)
        self.remote = remote
        self.branch = branch
        self.ref = f"refs/heads/{branch}"
        self.tracking = f"refs/remotes/{remote}/{branch}"
        self.tag_namespace = f"refs/norse-ledger/{remote}/tags"
        self.clock = clock

    # --- refs -----------------------------------------------------------------------

    def local_head(self) -> str | None:
        return self.git.try_text("rev-parse", "--verify", "--quiet", self.ref)

    def fetch(self) -> str | None:
        """Fetch the remote ledger ref; return the remote's head, or None if it has none."""
        listed = self.git.text("ls-remote", self.remote, self.ref)
        if not listed:
            return None
        self.git.run("fetch", "--no-tags", "--quiet", self.remote, f"+{self.ref}:{self.tracking}")
        return self.git.text("rev-parse", self.tracking)

    def _describe(self, local: str | None, remote: str | None) -> str:
        if remote is None:
            return "the remote has no ledger"
        if local is None:
            return "there is no local ledger (sync it from the remote first)"
        if self.git.is_ancestor(local, remote):
            return "the local ledger is behind the remote"
        if self.git.is_ancestor(remote, local):
            return "the local ledger is ahead of the remote (an entry was not pushed)"
        return "the local and remote ledgers have diverged"

    def require_in_step(self) -> str | None:
        """The ledger head, after checking the local head equals the remote's (T3)."""
        remote = self.fetch()
        local = self.local_head()
        if local != remote:
            raise LedgerOutOfStep(
                f"{self._describe(local, remote)}: local {local}, remote {remote}; "
                "a run starts only from the remote's ledger"
            )
        return local

    def sync(self) -> str | None:
        """Fast-forward the local ledger to the remote's; never moves it backwards."""
        with self._lock():
            remote = self.fetch()
            local = self.local_head()
            if remote is None or local == remote:
                return local
            if local is not None and not self.git.is_ancestor(local, remote):
                raise LedgerOutOfStep(self._describe(local, remote))
            self.git.run("update-ref", self.ref, remote, local or "")
            return remote

    @contextmanager
    def _lock(self) -> Iterator[None]:
        path = self.git.git_dir() / LOCK_NAME
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise LedgerBusy("another process is appending to the ledger") from None
            yield
        finally:
            os.close(fd)

    # --- reading --------------------------------------------------------------------

    def files_at(self, commit: str) -> dict[str, bytes]:
        listing = self.git.run("ls-tree", "-r", "-z", "--full-tree", commit)
        paths = [item.split(b"\t", 1)[1].decode() for item in listing.split(b"\0") if item]
        blobs = self.git.blobs([f"{commit}:{p}" for p in paths])
        return {p: b for p, b in zip(paths, blobs, strict=True) if b is not None}

    def snapshot(self, commit: str | None) -> LedgerSnapshot:
        """The verified chain and its return series at ``commit`` (no anchors)."""
        if commit is None:
            return LedgerSnapshot(())
        files = self.files_at(commit)
        data = files.pop(LEDGER_PATH, None)
        if data is None:
            raise VerificationError(f"{commit} holds no {LEDGER_PATH}")
        stray = [p for p in files if not _RETURNS_FILE.fullmatch(p)]
        if stray:
            raise VerificationError(f"{commit} holds files other than the ledger: {stray}")
        return LedgerSnapshot.from_bytes(data, files, commit)

    # --- appending ------------------------------------------------------------------

    def append(
        self,
        body: Mapping,
        files: Mapping[str, bytes] | None = None,
        *,
        require_in_step: bool = True,
        attempts: int = 1,
    ) -> Entry:
        """Append one entry, commit it and push it; return the entry as written.

        ``require_in_step`` refuses unless the local head is the remote's; without
        it (for a result entry, whose open entry is already published) the local
        ledger is first fast-forwarded to the remote's.
        """
        for attempt in range(attempts):
            try:
                return self._append_once(body, files or {}, require_in_step)
            except LedgerWriteError:
                if attempt == attempts - 1:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    def _append_once(self, body: Mapping, files: Mapping[str, bytes], in_step: bool) -> Entry:
        with self._lock():
            remote = self.fetch()
            local = self.local_head()
            if local != remote:
                behind = remote is not None and (
                    local is None or self.git.is_ancestor(local, remote)
                )
                if in_step or not behind:
                    raise LedgerOutOfStep(self._describe(local, remote))
                self.git.run("update-ref", self.ref, remote, local or "")
                local = remote
            data = self.git.blob(local, LEDGER_PATH) if local else b""
            if data is None:
                raise VerificationError(f"{local} holds no {LEDGER_PATH}")
            entries = parse_ledger(data)
            last = entries[-1] if entries else None
            full = dict(body)
            full["seq"] = len(entries)
            full["prev"] = last.hash if last else ZERO_HASH
            full["time"] = max(self.clock(), last.body["time"]) if last else self.clock()
            entry = make_entry(full)
            new_data = data + entry.line.encode("ascii") + b"\n"
            parse_ledger(new_data)  # the ledger as it will be must verify
            try:
                commit = self._commit(local, new_data, files, _message(entry))
                self.git.run("update-ref", "-m", "ledger append", self.ref, commit, local or "")
            except GitError as exc:
                raise LedgerWriteError(f"the entry could not be committed: {exc}") from None
            try:
                self.git.run("push", "--quiet", self.remote, f"{commit}:{self.ref}")
            except GitError as exc:
                if local:
                    self.git.run("update-ref", self.ref, local, commit)
                else:
                    self.git.run("update-ref", "-d", self.ref, commit)
                raise LedgerWriteError(f"the entry could not be pushed: {exc}") from None
            return entry

    def _commit(
        self, parent: str | None, data: bytes, files: Mapping[str, bytes], message: str
    ) -> str:
        with tempfile.TemporaryDirectory(prefix="norse-ledger-index-") as tmp:
            env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
            if parent:
                self.git.run("read-tree", parent, env=env)
            else:
                self.git.run("read-tree", "--empty", env=env)
            for path, content in [(LEDGER_PATH, data), *sorted(files.items())]:
                if path != LEDGER_PATH and not _RETURNS_FILE.fullmatch(path):
                    raise LedgerWriteError(f"{path} is not a return series path")
                blob = self.git.text("hash-object", "-w", "--stdin", input=content)
                self.git.run(
                    "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env
                )
            tree = self.git.text("write-tree", env=env)
        args = ["commit-tree", tree, "-m", message]
        if parent:
            args += ["-p", parent]
        return self.git.text(*args, env=IDENTITY)

    # --- verification ---------------------------------------------------------------

    def verify_history(self, head: str) -> list[str]:
        """Check that ``head``'s first-parent history only ever appended. Returns the
        commits, oldest first. Names the first commit that edited, deleted or
        reordered anything (T4, T5, T6), merged (T9) or changed a return series (T7)."""
        commits = []
        for line in self.git.text(
            "rev-list", "--first-parent", "--reverse", "--parents", head
        ).splitlines():
            commit, *parents = line.split()
            if len(parents) > 1:
                raise VerificationError(f"ledger commit {commit} is a merge; the ledger is linear")
            commits.append(commit)
        changes: dict[str, list[tuple[str, str]]] = {}
        current = None
        log = self.git.text(
            "log", "--first-parent", "--reverse", "--format=commit %H", "--raw",
            "--no-renames", "--no-abbrev", "--root", head,
        )
        for line in log.splitlines():
            if line.startswith("commit "):
                current = line.split()[1]
                changes[current] = []
            elif line.startswith(":") and current is not None:
                meta, path = line.split("\t", 1)
                changes[current].append((meta.split()[-1], path))
        previous = b""
        contents = self.git.blobs([f"{c}:{LEDGER_PATH}" for c in commits])
        for index, (commit, data) in enumerate(zip(commits, contents, strict=True)):
            for status, path in changes.get(commit, []):
                if path == LEDGER_PATH and status == ("A" if index == 0 else "M"):
                    continue
                if _RETURNS_FILE.fullmatch(path) and status == "A":
                    continue
                raise VerificationError(
                    f"ledger commit {commit} changes {path} ({status}); the ledger branch "
                    "only appends"
                )
            if data is None or len(data) <= len(previous) or not data.startswith(previous):
                raise VerificationError(
                    f"ledger commit {commit} does not append to the ledger before it: an "
                    "existing entry was edited, deleted or reordered"
                )
            previous = data
        return commits

    def _remote_refs(self, pattern: str) -> dict[str, str]:
        """``refs/tags/...`` names on the remote matching ``pattern``, each with the
        commit it points to (annotated tags peeled)."""
        out: dict[str, str] = {}
        for line in self.git.text("ls-remote", "--tags", self.remote).splitlines():
            oid, name = line.split("\t")
            peeled = name.endswith("^{}")
            name = name.removesuffix("^{}")
            if re.fullmatch(pattern, name.removeprefix("refs/tags/")) and (
                peeled or name not in out
            ):
                out[name] = oid
        return out

    def milestone_tags(self) -> dict[str, str]:
        """``ledger-*`` tags on the remote, each with its tagged commit."""
        found = self._remote_refs(MILESTONE_TAG.pattern)
        return {name.removeprefix("refs/tags/"): oid for name, oid in found.items()}

    def freeze_commit(self) -> str | None:
        return self.milestone_tags().get(FREEZE_TAG)

    def verify(self, head: str | None = None) -> Verified:
        """The anchored verification of the threat model's principle 1."""
        remote = self.fetch()
        head = self.local_head() if head is None else head
        if head is None:
            if remote is not None:
                raise VerificationError("there is no local ledger to verify; the remote has one")
            return Verified(LedgerSnapshot(()), None, None, {})
        snapshot = self.snapshot(head)
        data = snapshot.data
        if remote is None:
            raise VerificationError("the remote has no ledger ref; it was deleted or never pushed")
        remote_data = self.git.blob(remote, LEDGER_PATH) or b""
        if not self.git.is_ancestor(remote, head) or not data.startswith(remote_data):
            last = remote_data.rstrip(b"\n").rsplit(b"\n", 1)[-1]
            raise VerificationError(
                f"the remote ledger head {remote} (entry {sha256_hex(last)}) is not in the "
                "local ledger: entries were removed or rewritten"
            )
        tags = self.milestone_tags()
        if tags:
            self.git.run(
                "fetch", "--no-tags", "--quiet", self.remote,
                f"+refs/tags/ledger-*:{self.tag_namespace}/ledger-*",
            )
        for name, commit in sorted(tags.items()):
            if not self.git.is_ancestor(commit, head):
                raise VerificationError(
                    f"milestone tag {name} ({commit}) is not an ancestor of the ledger head"
                )
            tagged = self.git.blob(commit, LEDGER_PATH)
            if tagged is None or not data.startswith(tagged):
                raise VerificationError(
                    f"the ledger at milestone tag {name} is not a prefix of the current ledger"
                )
        self.verify_history(head)
        self.verify_preregistration_tag(snapshot)
        return Verified(snapshot, head, remote, tags)

    def verify_preregistration_tag(self, snapshot: LedgerSnapshot) -> None:
        """Flag a pre-registration tag that no longer points where the genesis entry
        says (T24). The harness itself trusts the genesis entry, never the tag."""
        for entry in snapshot.entries:
            if entry.type != GENESIS:
                break
            tag = entry.body["preregistration"]["tag"]
            commit = entry.body["preregistration"]["commit"]
            found = self._remote_refs(re.escape(tag)).get(f"refs/tags/{tag}")
            local = self.git.try_text("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")
            for where, actual in (("remote", found), ("local", local)):
                if where == "remote" and actual is None:
                    raise VerificationError(f"the {where} has no tag {tag}, which genesis names")
                if actual is not None and actual != commit:
                    raise VerificationError(
                        f"the {where} tag {tag} points at {actual}, but entry {entry.seq} "
                        f"pins {commit}: the tag was moved or replaced"
                    )

    # --- milestones -----------------------------------------------------------------

    def tag_milestone(self, name: str, message: str) -> str:
        """Mark the ledger head at a milestone with an annotated ``ledger-*`` tag on
        the remote, after full verification. Returns the tag object's id. The tag
        object is pushed straight to the remote, so a failed push leaves nothing
        behind; an existing tag is never replaced (tags are immutable, D16)."""
        if not MILESTONE_TAG.fullmatch(name):
            raise ValueError(f"{name!r} is not a ledger-* milestone tag name")
        with self._lock():
            head = self.local_head()
            remote = self.fetch()
            if head is None or head != remote:
                raise LedgerOutOfStep(self._describe(head, remote))
            self.verify(head)
            local_tag = self.git.try_text("rev-parse", "--verify", "--quiet", f"refs/tags/{name}")
            if name in self.milestone_tags() or local_tag:
                raise LedgerError(f"tag {name} already exists; milestone tags never move")
            when = datetime.strptime(self.clock(), TIME_FORMAT).replace(tzinfo=UTC)
            content = (
                f"object {head}\ntype commit\ntag {name}\n"
                f"tagger {LEDGER_NAME} <{LEDGER_EMAIL}> {int(when.timestamp())} +0000\n\n"
                f"{message.strip()}\n"
            )
            tag_object = self.git.text("mktag", input=content.encode())
            self.git.run("push", "--quiet", self.remote, f"{tag_object}:refs/tags/{name}")
            return tag_object


def _message(entry: Entry) -> str:
    body = entry.body
    what = body["type"]
    if body["type"] != GENESIS:
        what += f" {body.get('kind') or ''} {body.get('configuration_id') or ''}".rstrip()
    return f"ledger: entry {entry.seq}, {what.strip()}\n\nentry {entry.hash}\n"


# --- the registration through the genesis entry ----------------------------------------


def load_registration(git: Git, genesis: Entry) -> Registration:
    """The pre-registration pinned by ``genesis``, read from the pinned commit's
    tree and checked against the hashes genesis records (never the working tree, a
    branch or a tag: principle 4)."""
    prereg = genesis.body["preregistration"]
    commit = prereg["commit"]
    contents = {}
    for path in PREREGISTRATION_FILES:
        data = git.blob(commit, path)
        if data is None:
            raise VerificationError(f"{path} is not in the pinned commit {commit}")
        if sha256_hex(data) != prereg["files"][path]:
            raise VerificationError(f"{path} at {commit} does not match the genesis entry")
        contents[path] = data
    registration = Registration.from_bytes(contents[PREREGISTRATION_JSON])
    if genesis.body != {**genesis.body, **genesis_fields(registration)}:
        raise VerificationError("the genesis entry disagrees with the registration it pins")
    return registration


def genesis_fields(registration: Registration) -> dict:
    """What the genesis entry copies from the registration, for readers."""
    return {
        "manifests": {
            "development": registration.development_manifest_sha256,
            "holdout": registration.holdout_manifest_sha256,
        },
        "windows": {h: registration.windows[h].as_dict() for h in HYPOTHESES},
        "budgets": {
            **registration.own_budget,
            "reserve": registration.reserve_budget,
            "total": registration.total_budget,
        },
    }


def period_constants() -> dict:
    return {
        "out_of_sample_start": OUT_OF_SAMPLE_START.date().isoformat(),
        "holdout_start": HOLDOUT_START.date().isoformat(),
        "last_development_month": LAST_DEVELOPMENT_MONTH,
    }


def write_genesis(
    ledger: GitLedger,
    *,
    tree_paths: Mapping[str, Sequence[str]],
    key_record: tuple[str, str],
    tag: str = PREREGISTRATION_TAG,
) -> Entry:
    """Append a genesis entry pinning the pre-registration tagged ``tag`` on the
    remote. Refused once any entry of another type exists (section 16).

    ``key_record`` is the (commit, path) of G1.6's holdout key record, which the
    genesis entry pins (section 11, item 4 of PREREGISTRATION.md). ``tree_paths``
    lists the harness paths and each hypothesis's strategy paths whose git trees
    every run records (section 9, run rule 4).
    """
    found = ledger._remote_refs(re.escape(tag)).get(f"refs/tags/{tag}")
    if found is None:
        raise LedgerError(f"the remote has no tag {tag}")
    ledger.git.run("fetch", "--no-tags", "--quiet", ledger.remote, f"refs/tags/{tag}")
    files = {}
    for path in PREREGISTRATION_FILES:
        data = ledger.git.blob(found, path)
        if data is None:
            raise LedgerError(f"{path} is not in {tag} ({found})")
        files[path] = sha256_hex(data)
    registration = Registration.from_bytes(ledger.git.blob(found, PREREGISTRATION_JSON))
    periods = period_constants()
    registered = registration.doc["periods"]
    if (
        periods["holdout_start"] != registered["holdout_start"][:10]
        or periods["last_development_month"] != registered["last_development_month"]
        or periods["out_of_sample_start"] != registration.out_of_sample_start
    ):
        raise LedgerError("norse_research.periods disagrees with the registration")
    key_commit, key_path = key_record
    key_data = ledger.git.blob(key_commit, key_path)
    if key_data is None:
        raise LedgerError(f"no key record at {key_commit}:{key_path}")
    if set(tree_paths) != {"harness", *HYPOTHESES}:
        raise LedgerError("tree_paths lists the harness paths and each hypothesis's paths")
    current = ledger.snapshot(ledger.local_head())
    if any(e.type != GENESIS for e in current.entries):
        raise LedgerError(
            "the registration is closed: an entry follows the genesis entry (section 16)"
        )
    body = {
        "type": GENESIS,
        "format": FORMAT,
        "preregistration": {"tag": tag, "commit": found, "files": files},
        **genesis_fields(registration),
        "periods": periods,
        "tree_paths": {k: list(v) for k, v in tree_paths.items()},
        "key_record": {
            "commit": ledger.git.text("rev-parse", f"{key_commit}^{{commit}}"),
            "path": key_path,
            "sha256": sha256_hex(key_data),
        },
    }
    return ledger.append(body)


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m norse_research.gitledger verify|sync|tag|genesis``.

    ``verify`` runs the anchored verification and prints what it checked; ``sync``
    fast-forwards the local ledger to the remote's; ``tag`` marks the ledger head at
    a milestone; ``genesis`` writes the genesis entry, a separate and deliberate step
    taken once G1.6 has committed the holdout key record.
    """
    parser = argparse.ArgumentParser(description=main.__doc__.split("\n\n")[0])
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--remote", default="origin")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("verify")
    commands.add_parser("sync")
    tag = commands.add_parser("tag")
    tag.add_argument("name")
    tag.add_argument("--message", required=True)
    genesis = commands.add_parser("genesis")
    genesis.add_argument("--tag", default=PREREGISTRATION_TAG)
    genesis.add_argument("--key-record", required=True, help="COMMIT:PATH of G1.6's key record")
    for group in ("harness", *HYPOTHESES):
        genesis.add_argument(f"--{group.lower()}", nargs="+", required=True, metavar="PATH")
    args = parser.parse_args(argv)
    ledger = GitLedger(args.repo, args.remote)
    if args.command == "verify":
        verified = ledger.verify()
        snapshot = verified.snapshot
        print(
            f"ledger verified: {len(snapshot.entries)} entries, head entry {snapshot.head_hash}, "
            f"ledger commit {verified.head} (the remote's {verified.remote_head}), "
            f"{sum(1 for e in snapshot.opens(TRIAL))} trial entries; milestone tags: "
            f"{', '.join(sorted(verified.tags)) or 'none'}"
        )
    elif args.command == "sync":
        print(f"local ledger at {ledger.sync()}")
    elif args.command == "tag":
        print(f"{args.name}: tag object {ledger.tag_milestone(args.name, args.message)}")
    else:
        commit, _, path = args.key_record.partition(":")
        paths = {"harness": args.harness, **{h: getattr(args, h.lower()) for h in HYPOTHESES}}
        entry = write_genesis(ledger, tree_paths=paths, key_record=(commit, path), tag=args.tag)
        print(f"genesis entry {entry.seq} written: {entry.hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
