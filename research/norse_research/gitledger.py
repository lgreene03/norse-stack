"""The trial ledger in git: appends, anchored verification and milestone tags (G1.5).

**Where it lives.** The ledger is the file ``ledger/trials.jsonl`` on its own branch,
``ledger``, beside the committed return series under ``ledger/returns/``. It is not
on main because main needs the ``research`` and ``validate`` checks and a pull
request (decision D16), which would refuse the harness's push per entry; the
threat model foresaw this. The branch needs its own ruleset (no deletion, no force
push), which only the owner can add.

**Pinned remote.** The genesis entry pins the canonical remote URL, the ledger branch
and the code branch. Before reading trust from the remote, and before every push, the
remote's resolved fetch and push URLs must equal the pinned URL, and no ``insteadOf``
rewrite may be configured; so a run from a scratch clone, a mirror or a redirected
remote is refused rather than ledgered where nobody looks (security review H1). Git
runs with ``GIT_NO_REPLACE_OBJECTS`` set, and any ``refs/replace/`` ref is refused
(M5). Every git command here also runs with hooks and the file system monitor turned
off (``core.hooksPath=/dev/null``, ``core.fsmonitor=false``), and a clone that
configures either in any scope is refused: either would run a program of the clone's
choosing inside the unseal's push, where it could read the proof before the remote
holds it (security review of #65, H-1). The rest of that class is closed the same way:
the clone's own configuration (local and worktree scope, and anything they include) may
set nothing that runs a program or redirects a read or a push, such as a credential
helper, an askpass or editor program, a text conversion or filter, an include, or a
transport override (``REPOSITORY_SCOPE_REFUSED``); and every command except those that
reach the remote, or resolve how they would, runs with ``GIT_CONFIG_NOSYSTEM=1``. The
owner's global and system configuration stay in force for the push, because its
credential helper lives there (holdout-seal.md).

**Appending.** An append never touches the working tree, so it cannot dirty it. It
takes a lock in the repository's git directory, fetches the remote ledger ref, checks
that the ledger is still the one the caller was admitted on (M2), builds the new commit
with git's plumbing (a blob, a private index, a tree, a commit), moves the local ref
with a compare-and-swap, pushes without force, and then reads the remote back: its
ledger head must hold the new commit (H1). A second writer's push is refused as
non-fast-forward (T9). If the push fails the local ref is put back and the append
raises: the caller has computed nothing yet (T8).

**Verifying.** The chain alone proves only that it agrees with itself. ``verify``
adds the anchors of the threat model's principle 1: the remote's ledger head is in the
local file (T5); every ``ledger-*`` tag on a ledger commit is an ancestor of the head
whose ledger is a prefix of the current one, and ``ledger-genesis``, written straight
after the genesis entry, must exist (T10, review M7); along the branch's first-parent
history every committed version of the file is a byte prefix of the next and every
return series is never changed (T4, T7); the pre-registration tag still points where
genesis says (T24); and the run rules of section 9 hold over the whole ledger (M2). A
``ledger-*`` tag on a commit that holds no ledger is reported, not fatal, because the
tag ruleset makes such a tag permanent (M6). There is no ledger freeze (decision D21):
the unseal entry (G1.6) closes development. It must name the holdout candidate the
registered rule selects on the entries before it (``gate.check_unseal_candidate``;
T22), its commit must differ from the candidate's only in data loading or the seal
(section 11, item 6), and the diff it records as accepted must be that diff (security
review round 3, T1); every sensitivity run must re-cost its hypothesis's candidate
(``gate.check_sensitivity_entries``).

Nothing here reads the clock to decide anything; it is read only for timestamps,
which never decrease along the chain (T11).
"""

from __future__ import annotations

import argparse
import difflib
import fcntl
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from . import ledger as _ledger
from .canonical import CanonicalError, loads_strict, sha256_hex
from .environment import check_environment
from .ledger import (
    ATTEMPT,
    CODE_BRANCH,
    FORMAT,
    GENESIS,
    LEDGER_BRANCH,
    LEDGER_PATH,
    OPEN,
    TIME_FORMAT,
    UNSEAL,
    ZERO_HASH,
    Entry,
    LedgerError,
    LedgerSnapshot,
    VerificationError,
    genesis_of,
    make_entry,
    parse_ledger,
    unseal_of,
)
from .periods import HOLDOUT_START, LAST_DEVELOPMENT_MONTH, OUT_OF_SAMPLE_START
from .registration import (
    HOLDOUT,
    HYPOTHESES,
    PREREGISTRATION_FILES,
    PREREGISTRATION_JSON,
    SENSITIVITY,
    TRIAL,
    Registration,
)
from .rules import (
    after_unseal_error,
    check_run_rules,
    holdout_attempt_error,
    holdout_rule_error,
    run_of,
    same_run,
)
from .sources import (
    StrategySourceRefused,
    check_strategy_sources,
    inside,
    strategy_declarations,
)
from .store import REPO_ROOT

# The anchor is named for its content, so a stray tag can never take its name (N3):
# ledger-genesis-<hash of the first genesis entry>. There is no freeze anchor (D21).
GENESIS_ANCHOR = "ledger-genesis-"
RESERVED = ("ledger-genesis",)
# The withdrawn freeze anchor's name stays refused as a milestone, so no permanent tag
# can read as a freeze that no longer exists (security review of #65, L-4).
WITHDRAWN = ("ledger-freeze",)
# Every git command runs with these first: hooks and the file system monitor would run a
# program the clone chooses inside a ledger command (security review of #65, H-1).
HARDENED = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")
_FALSE = frozenset({"false", "no", "off", "0"})
# H-1's class, closed as a class rather than key by key: configuration in the clone itself
# (the local and worktree scopes, and any file they include) that runs a program, or
# redirects what the ledger and the seal read or where they push, is refused. Any of these
# could run inside the unseal's push, which carries the proof. The owner's global and
# system configuration is left alone, because the push's credential helper lives there
# (on the owner's Mac, osxkeychain in the system scope; holdout-seal.md).
REPOSITORY_SCOPES = frozenset({"local", "worktree"})
REPOSITORY_SCOPE_REFUSED = re.compile(
    r"credential\..*"
    r"|core\.(sshcommand|askpass|editor|pager|gitproxy|alternaterefscommand|worktree)"
    r"|sequence\.editor|gpg\.(.+\.)?program|include\.path|includeif\..*"
    r"|url\..+\.(insteadof|pushinsteadof)|remote\..+\.(receivepack|uploadpack|proxy|vcs)"
    r"|protocol\.(.+\.)?allow|http\..*(proxy|ssl).*|ssh\.variant"
    r"|diff\.external|diff\..+\.(textconv|command)|merge\..+\.driver|filter\..*"
    r"|uploadpack\.packobjectshook"
)
# The commands that reach the remote, or resolve how they would, read the system
# configuration, where the owner's credential helper lives; every other ledger git command
# runs with GIT_CONFIG_NOSYSTEM=1, so nothing in the system configuration reaches it.
SYSTEM_SCOPE_COMMANDS = frozenset({"push", "fetch", "ls-remote", "remote", "config"})
MILESTONE_TAG = re.compile(r"ledger-[a-z0-9][a-z0-9.-]*")
SCRYPT_MAXMEM = 256 * 1024 * 1024
# Configuration that would send fetches or pushes elsewhere while the URL still
# matches the pinned one (H1, N6), in any scope.
TRANSPORT_OVERRIDES = (
    r"^url\..*\.(insteadof|pushinsteadof)$",
    r"^remote\..*\.(receivepack|uploadpack|proxy|vcs|pushurl)$",
    r"^core\.(sshcommand|gitproxy)$",
    r"^ssh\.variant$",
    r"^http\..*(proxy|ssl).*$",
)
# The tag a new genesis entry names, the registration this code implements; replay
# trusts the tag genesis records. A genesis may pin no other (quant review of #65, L7).
PREREGISTRATION_TAG = "prereg-v2"
PREREGISTRATION_VERSION = re.compile(r"prereg-v([1-9][0-9]*)")
LOCK_NAME = "norse-ledger.lock"
HOLDOUT_LOCK_NAME = "norse-holdout.lock"
LEDGER_NAME = "Norse trial ledger"
LEDGER_EMAIL = "ledger@norse-stack.invalid"
IDENTITY = {
    "GIT_AUTHOR_NAME": LEDGER_NAME,
    "GIT_AUTHOR_EMAIL": LEDGER_EMAIL,
    "GIT_COMMITTER_NAME": LEDGER_NAME,
    "GIT_COMMITTER_EMAIL": LEDGER_EMAIL,
}
REMOTE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# The holdout key record's contract (T25; G1.6 commits the record; genesis pins it).
KEY_RECORD_KEYS = frozenset({"kdf", "n", "r", "p", "dklen", "salt", "verifier_sha256"})
_RETURNS_FILE = re.compile(r"ledger/returns/[0-9a-f]{64}\.csv")
# Section 11, item 6: a holdout run may be retried "at the commit of its selectable run
# plus only commits that touch data loading or the seal". These are the files of data
# loading and of the seal; any other change to the code a run executes (the harness
# paths and the hypothesis's paths) between the candidate's commit and the holdout
# run's refuses the run and fails verification.
SEAL_AND_DATA_LOADING = frozenset(
    f"research/norse_research/{name}.py"
    for name in ("seal", "loader", "store", "manifest", "formats", "binance_archive", "periods")
)
# Where this package lives in the repository. The runner exports all of it to every run,
# so item 6's diff always covers it, whatever the genesis tree paths list (review L3).
PACKAGE_PATH = "research/norse_research"
# Attribute files change what git shows of a diff, and how a file is checked out, without
# changing a byte of the code, so item 6's rule covers every one in the repository, the
# root's included (security review of #65, M-1).
ATTRIBUTES_PATHSPEC = ":(glob)**/.gitattributes"


class GitError(LedgerError):
    """A git command failed."""


class LedgerWriteError(LedgerError):
    """An append failed. Nothing was published and the local ref is as it was."""


class LedgerOutOfStep(LedgerError, PermissionError):
    """The local ledger head is not the remote's, or moved since admission (T3, M2)."""


class LedgerBusy(LedgerError, PermissionError):
    """Another process holds the ledger lock (T9)."""


class RemoteNotPinned(LedgerError, PermissionError):
    """The remote is not the one the genesis entry pins (H1)."""


def normal_url(url: str) -> str:
    url = url.strip().rstrip("/")
    return url[: -len(".git")] if url.endswith(".git") else url


class Git:
    """A git repository, driven through the command line git runs."""

    def __init__(self, repo: Path) -> None:
        self.repo = Path(repo)

    def call(self, *args: str, input: bytes | None = None, env: Mapping | None = None):
        # Replacement objects could substitute one commit or blob for another in
        # every local check (M5); git never honours them here. Hooks and the file system
        # monitor never run (H-1): a command-line setting overrides every file's. Only the
        # commands that need the owner's credential helper read the system configuration.
        scope = {} if args and args[0] in SYSTEM_SCOPE_COMMANDS else {"GIT_CONFIG_NOSYSTEM": "1"}
        full = {**os.environ, "GIT_NO_REPLACE_OBJECTS": "1", **scope, **(env or {})}
        return subprocess.run(
            ["git", *HARDENED, *args], cwd=self.repo, input=input, capture_output=True,
            env=full, check=False,
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

    def objects(self, oids: Sequence[str]) -> list[bytes | None]:
        """Many objects by id in one ``cat-file --batch``. Ids hold no newline, so a
        path cannot desynchronise the batch (review L2)."""
        if not oids:
            return []
        if any(not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", o) for o in oids):
            raise GitError("cat-file --batch is given object ids only")
        out = self.run("cat-file", "--batch", input=("\n".join(oids) + "\n").encode())
        found: list[bytes | None] = []
        pos = 0
        for _ in oids:
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
    tags: dict[str, str]  # ledger-* tag on a ledger commit -> that commit
    ignored_tags: dict[str, str] = field(default_factory=dict)  # stray ledger-* tags
    registration: Registration | None = None


class GitLedger:
    def __init__(
        self,
        repo: Path,
        remote: str = "origin",
        clock: Callable[[], str] | None = None,
    ) -> None:
        if not REMOTE_NAME.fullmatch(remote):
            raise ValueError(f"{remote!r} is not a remote name")  # review L10
        self.git = Git(repo)
        self.remote = remote
        self.branch = LEDGER_BRANCH  # pinned: no caller chooses another ledger (H1)
        self.ref = f"refs/heads/{LEDGER_BRANCH}"
        self.tracking = f"refs/remotes/{remote}/{LEDGER_BRANCH}"
        self.tag_namespace = f"refs/norse-ledger/{remote}/tags"
        self._clock = clock

    def clock(self) -> str:
        # Looked up at call time, so one clock serves every caller (review L1).
        return self._clock() if self._clock is not None else _ledger.utc_now()

    # --- the remote -----------------------------------------------------------------

    def remote_urls(self) -> tuple[str, list[str]]:
        fetch = self.git.text("ls-remote", "--get-url", self.remote)
        push = self.git.text("remote", "get-url", "--push", "--all", self.remote).splitlines()
        return fetch, push

    def check_repository(self) -> None:
        """No replacement refs, no URL rewriting, and no hooks path or file system
        monitor, in any configuration scope; and in the clone's own configuration (local
        and worktree, with what they include), nothing that runs a program or redirects a
        read or a push (``REPOSITORY_SCOPE_REFUSED``)."""
        if self.git.text("for-each-ref", "--format=%(refname)", "refs/replace/"):
            raise RemoteNotPinned("refs/replace/ refs are refused: they substitute objects")
        found = self.git.try_text(
            "config", "--show-scope", "--get-regexp", r"^core\.(hookspath|fsmonitor)$"
        )
        for line in (found or "").splitlines():
            scope, _, setting = line.partition("\t")
            name, _, value = setting.partition(" ")
            if scope == "command" or (name == "core.fsmonitor" and value.lower() in _FALSE):
                continue  # HARDENED's own settings, or a monitor switched off
            raise RemoteNotPinned(
                f"{name} is configured in the {scope} configuration ({value or 'true'}), which "
                "is refused: a hook or a file system monitor runs a program inside the "
                "ledger's git commands, where it could read the unseal's proof before the "
                "remote holds it (security review of #65, H-1)"
            )
        for pattern in TRANSPORT_OVERRIDES:
            found = self.git.try_text("config", "--get-regexp", pattern)
            if found and pattern.startswith("^remote") and ".pushurl" in found:
                continue  # a push URL is checked against the pin in check_remote
            if found:
                kind = "URL rewriting" if "insteadof" in pattern else "a transport override"
                raise RemoteNotPinned(f"{kind} is configured, which is refused: {found}")
        # Then the rest of H-1's class in the clone's own configuration; the transport
        # overrides above are refused in every scope, so they keep their own message.
        listed = self.git.text("config", "--show-scope", "--name-only", "--list")
        refused = sorted({
            f"{name} ({scope})"
            for scope, _, name in (line.partition("\t") for line in listed.splitlines())
            if scope in REPOSITORY_SCOPES and REPOSITORY_SCOPE_REFUSED.fullmatch(name)
        })
        if refused:
            raise RemoteNotPinned(
                f"the clone's own configuration sets {', '.join(refused)}, which is refused: "
                "each runs a program inside the ledger's git commands, or redirects what they "
                "read or where they push, where it could read the unseal's proof (security "
                "review of #65, H-1's class). Remove it with git config --local --unset, or "
                "--worktree --unset"
            )

    def check_remote(self, pinned: str) -> None:
        """The remote's resolved fetch and push URLs are the pinned one (H1)."""
        self.check_repository()
        fetch, push = self.remote_urls()
        urls = [fetch, *push]
        if not push or any(normal_url(u) != normal_url(pinned) for u in urls):
            raise RemoteNotPinned(
                f"remote {self.remote} resolves to {urls}, not the ledger's pinned remote "
                f"{pinned}; a run is ledgered only on the pinned remote"
            )

    # --- refs -----------------------------------------------------------------------

    def local_head(self) -> str | None:
        return self.git.try_text("rev-parse", "--verify", "--quiet", self.ref)

    def fetch(self) -> str | None:
        """Fetch the remote ledger ref; return the remote's head, or None if it has none."""
        listed = self.git.text("ls-remote", "--", self.remote, self.ref)
        if not listed:
            return None
        self.git.run(
            "fetch", "--no-tags", "--quiet", "--", self.remote, f"+{self.ref}:{self.tracking}"
        )
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
    def _lock(self, name: str = LOCK_NAME, busy: str = "another process is appending to the ledger"
              ) -> Iterator[None]:
        path = self.git.git_dir() / name
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise LedgerBusy(busy) from None
            yield
        finally:
            os.close(fd)

    def holdout_lock(self) -> AbstractContextManager[None]:
        """Held for a whole holdout run, so a second one on this clone is refused while
        the first is in flight (security review M1). The operating system releases it
        when a process dies, so a killed run never blocks a retry. Across clones the
        ledger's rule that attempts run one at a time takes over."""
        return self._lock(HOLDOUT_LOCK_NAME, "another holdout run is in progress on this clone")

    # --- reading --------------------------------------------------------------------

    def files_at(self, commit: str) -> dict[str, bytes]:
        listing = self.git.run("ls-tree", "-r", "-z", "--full-tree", commit)
        items = []
        for item in listing.split(b"\0"):
            if item:
                meta, path = item.split(b"\t", 1)
                items.append((path.decode(errors="replace"), meta.split()[2].decode()))
        blobs = self.git.objects([oid for _, oid in items])
        return {p: b for (p, _), b in zip(items, blobs, strict=True) if b is not None}

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
            raise VerificationError(f"{commit} holds files other than the ledger: {stray!r}")
        return LedgerSnapshot.from_bytes(data, files, commit)

    # --- appending ------------------------------------------------------------------

    def append(
        self,
        body: Mapping,
        files: Mapping[str, bytes] | None = None,
        *,
        require_in_step: bool = True,
        expected_head: str | None = None,
        attempts: int = 1,
    ) -> Entry:
        """Append one entry, commit it, push it and read it back from the remote.

        ``require_in_step`` refuses unless the local head is the remote's; without
        it (for a result entry, whose open entry is already published) the local
        ledger is first fast-forwarded to the remote's. ``expected_head`` refuses
        unless the ledger head is still the one the caller decided on (M2).
        """
        for attempt in range(attempts):
            try:
                return self._append_once(body, files or {}, require_in_step, expected_head)
            except LedgerWriteError:
                if attempt == attempts - 1:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    def _append_once(
        self, body: Mapping, files: Mapping[str, bytes], in_step: bool, expected: str | None
    ) -> Entry:
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
            if expected is not None and local != expected:
                raise LedgerOutOfStep(
                    f"the ledger moved from {expected} to {local} after this run was admitted; "
                    "admit it again"
                )
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
            written = parse_ledger(new_data)  # the ledger as it will be verifies
            if genesis_of(written) is None:
                raise VerificationError("no entry may precede the genesis entry")
            self.check_remote(written[0].body["remote"]["url"])  # the first genesis's (N5)
            if body.get("type") == GENESIS:
                problem = preregistration_tag_error(
                    self, (body.get("preregistration") or {}).get("tag")
                )
                if problem:
                    raise VerificationError(f"the entry is refused: {problem}")
            if body.get("type") == OPEN:
                # The unseal closes development (D21); checked under the lock, against the
                # remote's ledger, since another session may have unsealed meanwhile.
                closed = after_unseal_error(written[:-1], body.get("kind"))
                if closed:
                    raise LedgerOutOfStep(closed)
            if body.get("type") == UNSEAL or (
                body.get("type") == OPEN and body.get("kind") == SENSITIVITY
            ):
                self._check_before_push(written, local)
            # The holdout rules replay enforces, applied before anything is pushed, so no
            # entry written through append can fail verification for good (round 2, N4).
            problem = None
            if body.get("type") == ATTEMPT:
                problem = holdout_attempt_error(written[:-1], body.get("commit"))
            elif body.get("type") == OPEN and body.get("kind") == HOLDOUT:
                problem = holdout_rule_error(written[:-1], run_of(written[-1]))
            if problem:
                raise VerificationError(f"the entry is refused: {problem}")
            if body.get("type") == UNSEAL:
                return self._push_unseal(local, new_data, files, entry)
            try:
                commit = self._commit(local, new_data, files, _message(entry))
                self.git.run("update-ref", "-m", "ledger append", self.ref, commit, local or "")
            except GitError as exc:
                raise LedgerWriteError(f"the entry could not be committed: {exc}") from None
            try:
                self.git.run("push", "--quiet", "--", self.remote, f"{commit}:{self.ref}")
            except GitError as exc:
                if local:
                    self.git.run("update-ref", self.ref, local, commit)
                else:
                    self.git.run("update-ref", "-d", self.ref, commit)
                raise LedgerWriteError(f"the entry could not be pushed: {exc}") from None
            after = self.fetch()
            if after is None or not self.git.is_ancestor(commit, after):
                if local:
                    self.git.run("update-ref", self.ref, local, commit)
                else:
                    self.git.run("update-ref", "-d", self.ref, commit)
                raise LedgerWriteError(
                    f"the remote's ledger head {after} does not hold the pushed entry {commit}"
                )
            return entry

    def _check_before_push(self, written: Sequence[Entry], local: str | None) -> None:
        """What replay checks of an unseal or a sensitivity run, which needs git, the
        registration and the gate, checked before the entry is pushed, so no entry
        written through append fails verification for good (round 2, N4; D21)."""
        series = self.snapshot(local).series_files
        snapshot = LedgerSnapshot(tuple(written), series)
        registration = load_registration(self.git, snapshot.genesis)
        what = "the unseal" if written[-1].type == UNSEAL else "the entry"
        try:
            if written[-1].type == UNSEAL:
                self._check_unseal(snapshot, registration)
            else:
                from .gate import check_sensitivity_entries  # the gate imports this module

                check_sensitivity_entries(snapshot, registration)
        except VerificationError as exc:
            raise VerificationError(f"{what} is refused: {exc}") from None

    def _commit(
        self, parent: str | None, data: bytes, files: Mapping[str, bytes], message: str,
        objects: Mapping[str, str] | None = None,
    ) -> str:
        """The commit appending ``data``, built with git's plumbing. ``objects``, when
        given, sends every new object to another object directory (the unseal's)."""
        objects = dict(objects or {})
        with tempfile.TemporaryDirectory(prefix="norse-ledger-index-") as tmp:
            env = {"GIT_INDEX_FILE": str(Path(tmp) / "index"), **objects}
            if parent:
                self.git.run("read-tree", parent, env=env)
            else:
                self.git.run("read-tree", "--empty", env=env)
            for path, content in [(LEDGER_PATH, data), *sorted(files.items())]:
                if path != LEDGER_PATH and not _RETURNS_FILE.fullmatch(path):
                    raise LedgerWriteError(f"{path} is not a return series path")
                blob = self.git.text("hash-object", "-w", "--stdin", input=content, env=objects)
                self.git.run(
                    "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env
                )
            tree = self.git.text("write-tree", env=env)
        args = ["commit-tree", tree, "-m", message]
        if parent:
            args += ["-p", parent]
        return self.git.text(*args, env={**IDENTITY, **objects})

    def _push_unseal(
        self, local: str | None, data: bytes, files: Mapping[str, bytes], entry: Entry
    ) -> Entry:
        """The unseal's commit, built in a temporary object directory and pushed from
        there; the clone's own object store, refs and reflogs learn of it only once the
        remote holds it. A failed push leaves nothing behind that holds the proof, so no
        one can use it later without the owner (security review round 2, N2)."""
        with tempfile.TemporaryDirectory(prefix="norse-unseal-objects-") as tmp:
            objects = {
                "GIT_OBJECT_DIRECTORY": tmp,
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(self.git.git_dir() / "objects"),
            }
            try:
                commit = self._commit(local, data, files, _message(entry), objects)
                self.git.run("push", "--quiet", "--", self.remote, f"{commit}:{self.ref}",
                             env=objects)
            except GitError as exc:
                raise LedgerWriteError(
                    f"the unseal could not be pushed ({exc}); nothing of it is left in this "
                    "clone, so run the unseal again at once, from the same terminal"
                ) from None
            # Pushed, so public: the loose objects join the clone's own store, which the
            # remote-tracking ref the push may have moved already points into.
            store = self.git.git_dir() / "objects"
            for made in Path(tmp).rglob("*"):
                target = store / made.relative_to(tmp)
                if made.is_file() and not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(made, target)
            after = self.fetch()
        if after is None or not self.git.is_ancestor(commit, after):
            raise LedgerWriteError(
                f"the remote's ledger head {after} does not hold the pushed unseal {commit}"
            )
        self.git.run("update-ref", "-m", "ledger append", self.ref, commit, local or "")
        return entry

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
        changes: dict[str, list[tuple[str, str, str]]] = {c: [] for c in commits}
        current = None
        log = self.git.run(
            "log", "--first-parent", "--reverse", "--format=commit %H", "--raw", "-z",
            "--no-renames", "--no-abbrev", "--root", head,
        ).decode(errors="replace")
        tokens = iter(log.split("\0"))
        for token in tokens:
            token = token.lstrip("\n")
            if token.startswith("commit "):
                current = token.split()[1]
            elif token.startswith(":") and current is not None:
                meta = token.split()
                changes[current].append((meta[4], next(tokens, ""), meta[3]))
        ledger_blobs = []
        for index, commit in enumerate(commits):
            blob = None
            for status, path, oid in changes[commit]:
                if path == LEDGER_PATH and status == ("A" if index == 0 else "M"):
                    blob = oid
                    continue
                if _RETURNS_FILE.fullmatch(path) and status == "A":
                    continue
                raise VerificationError(
                    f"ledger commit {commit} changes {path!r} ({status}); the ledger branch "
                    "only appends"
                )
            if blob is None:
                raise VerificationError(f"ledger commit {commit} appends no entry")
            ledger_blobs.append(blob)
        previous = b""
        for commit, data in zip(commits, self.git.objects(ledger_blobs), strict=True):
            if data is None or len(data) <= len(previous) or not data.startswith(previous):
                raise VerificationError(
                    f"ledger commit {commit} does not append to the ledger before it: an "
                    "existing entry was edited, deleted or reordered"
                )
            previous = data
        return commits

    def _remote_refs(self, prefix: str) -> dict[str, str]:
        """``refs/tags/<prefix>...`` on the remote, each with the commit it points to
        (annotated tags peeled)."""
        out: dict[str, str] = {}
        for line in self.git.text("ls-remote", "--tags", "--", self.remote).splitlines():
            oid, name = line.split("\t")
            peeled = name.endswith("^{}")
            name = name.removesuffix("^{}")
            if name.startswith(f"refs/tags/{prefix}") and (peeled or name not in out):
                out[name] = oid
        return out

    def milestone_tags(self) -> dict[str, str]:
        """Every ``ledger-*`` tag on the remote, whatever its case (review L9), with
        its tagged commit."""
        found = self._remote_refs("ledger-")
        return {name.removeprefix("refs/tags/"): oid for name, oid in found.items()}

    def verify(self, head: str | None = None, *, need_genesis_tag: bool = True) -> Verified:
        """The anchored verification of the threat model's principle 1."""
        self.check_repository()
        remote = self.fetch()
        head = self.local_head() if head is None else head
        if head is None:
            if remote is not None:
                raise VerificationError("there is no local ledger to verify; the remote has one")
            return Verified(LedgerSnapshot(()), None, None, {})
        snapshot = self.snapshot(head)
        genesis = snapshot.genesis
        if genesis is None:
            raise VerificationError("the ledger has no genesis entry")
        self.check_remote(genesis.body["remote"]["url"])
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
        tags, ignored = self._check_tags(head, data)
        anchor = f"{GENESIS_ANCHOR}{snapshot.entries[0].hash}"
        if need_genesis_tag and anchor not in tags:
            raise VerificationError(
                f"the remote has no {anchor} tag; it anchors the genesis entry and is "
                "written straight after it"
            )
        self.verify_history(head)
        self.verify_preregistration_tag(snapshot)
        registration = load_registration(self.git, genesis)
        check_run_rules(snapshot.entries, registration, genesis)
        self._check_rerun_commits(snapshot)
        self._check_unseal(snapshot, registration)
        from .gate import check_sensitivity_entries  # the gate imports this module

        check_sensitivity_entries(snapshot, registration)
        return Verified(snapshot, head, remote, tags, ignored, registration)

    def _check_unseal(self, snapshot: LedgerSnapshot, registration: Registration) -> None:
        """On replay, the unseal's proof verifies, it names the candidate the registered
        rule selects on the entries before it (T22), its commit differs from the
        candidate's only in data loading or the seal (item 6), and the diff it records as
        accepted is that diff (security review round 3, T1)."""
        unseal = unseal_of(snapshot.entries)
        if unseal is None:
            return
        problem = unseal_proof_error(self.git, snapshot.genesis, unseal.body)
        if problem:
            raise VerificationError(f"entry {unseal.seq}: {problem}")
        from .gate import check_unseal_candidate  # the gate imports this module

        before = LedgerSnapshot(snapshot.entries[: unseal.seq], snapshot.series_files)
        check_unseal_candidate(before, registration, unseal)
        # Item 6's rule, applied once: the code at the unseal's commit differs from the
        # candidate's only in data loading or the seal; every holdout run is at exactly
        # that commit, which ``rules`` replays (security review round 2, N1).
        candidate = next(e for e in snapshot.entries if e.hash == unseal.body["candidate"]["open"])
        paths = snapshot.genesis.body["tree_paths"]
        paths = [*paths["harness"], *paths[candidate.body["hypothesis"]]]
        problem = holdout_commit_error(
            self.git, candidate.body["commit"], unseal.body["commit"], paths
        )
        if problem:
            raise VerificationError(f"entry {unseal.seq}: {problem}")
        _, _, digest = seal_changes(self.git, candidate.body["commit"], unseal.body["commit"])
        if digest != unseal.body["accepted_diff"]:
            raise VerificationError(
                f"entry {unseal.seq}: the unseal records accepting diff "
                f"{unseal.body['accepted_diff']}, but the data loading and seal diff from the "
                f"candidate's commit to the unseal's is {digest} (security review round 3, T1)"
            )

    def _check_rerun_commits(self, snapshot: LedgerSnapshot) -> None:
        """On replay, each rerun's fix is in its own code and was not in the code of
        the run it repeats (review L8, N7)."""
        opens = [e for e in snapshot.entries if e.type == OPEN]
        for index, entry in enumerate(opens):
            rerun = entry.body["rerun"]
            if rerun is None:
                continue
            fix, commit = rerun["fix_commit"], entry.body["commit"]
            known = self.git.try_text("cat-file", "-t", fix) == "commit"
            if not known or not self.git.is_ancestor(fix, commit):
                raise VerificationError(
                    f"entry {entry.seq}: its fix commit {fix} is not in the code that ran"
                )
            before = [e for e in opens[:index] if same_run(e, run_of(entry))]
            if before and self.git.is_ancestor(fix, before[-1].body["commit"]):
                raise VerificationError(
                    f"entry {entry.seq}: its fix commit {fix} was already in the code of "
                    f"entry {before[-1].seq}, the run it repeats"
                )

    def _check_tags(self, head: str, data: bytes) -> tuple[dict[str, str], dict[str, str]]:
        tags = self.milestone_tags()
        if tags:
            self.git.run(
                "fetch", "--no-tags", "--quiet", "--", self.remote,
                f"+refs/tags/ledger-*:{self.tag_namespace}/ledger-*",
            )
        checked, ignored = {}, {}
        for name, commit in sorted(tags.items()):
            tagged = self.git.blob(commit, LEDGER_PATH)
            if tagged is None or name.startswith(WITHDRAWN):
                # Not on a ledger commit, or the withdrawn freeze's name: reported, not
                # fatal, and never read as a milestone (M6; L-4).
                ignored[name] = commit
                continue
            if name.startswith(RESERVED):
                first = tagged.split(b"\n", 1)[0]
                if name != f"{GENESIS_ANCHOR}{sha256_hex(first)}":
                    ignored[name] = commit  # a reserved name not matching its content (N3)
                    continue
            if not self.git.is_ancestor(commit, head):
                raise VerificationError(
                    f"milestone tag {name} ({commit}) is not an ancestor of the ledger head"
                )
            if not data.startswith(tagged):
                raise VerificationError(
                    f"the ledger at milestone tag {name} is not a prefix of the current ledger"
                )
            checked[name] = commit
        return checked, ignored

    def verify_preregistration_tag(self, snapshot: LedgerSnapshot) -> None:
        """Flag a pre-registration tag that no longer points where the genesis entry
        says (T24). The harness itself trusts the genesis entry, never the tag."""
        for entry in snapshot.entries:
            if entry.type != GENESIS:
                break
            tag = entry.body["preregistration"]["tag"]
            commit = entry.body["preregistration"]["commit"]
            found = self._remote_refs(tag).get(f"refs/tags/{tag}")
            local = self.git.try_text(
                "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}"
            )
            for where, actual in (("remote", found), ("local", local)):
                if where == "remote" and actual is None:
                    raise VerificationError(f"the {where} has no tag {tag}, which genesis names")
                if actual is not None and actual != commit:
                    raise VerificationError(
                        f"the {where} tag {tag} points at {actual}, but entry {entry.seq} "
                        f"pins {commit}: the tag was moved or replaced"
                    )

    # --- milestones -----------------------------------------------------------------

    def tag_milestone(self, name: str, message: str, *, _anchor: bool = False) -> str:
        """Mark the ledger head at a milestone with an annotated ``ledger-*`` tag on
        the remote, after full verification. Returns the tag object's id. The tag
        object is pushed straight to the remote, so a failed push leaves nothing
        behind; an existing tag is never replaced (tags are immutable, D16). The
        genesis anchor is refused here unless ``_anchor`` is set, which only the
        genesis step does (review M6)."""
        if not MILESTONE_TAG.fullmatch(name):
            raise ValueError(f"{name!r} is not a lower-case ledger-* milestone tag name")
        if name.startswith(WITHDRAWN):
            raise LedgerError(
                f"{name} is refused: ledger-freeze-* was the withdrawn freeze anchor (decision "
                "D21), and a permanent tag of that shape would read as a freeze that does not "
                "exist (security review of #65, L-4)"
            )
        if name.startswith(RESERVED) and not _anchor:
            raise LedgerError(
                f"{name} is a reserved name: the genesis step writes the anchor, which "
                "is irreversible"
            )
        check_environment(os.environ, self.git.repo)
        with self._lock():
            head = self.local_head()
            remote = self.fetch()
            if head is None or head != remote:
                raise LedgerOutOfStep(self._describe(head, remote))
            verified = self.verify(head, need_genesis_tag=not name.startswith(GENESIS_ANCHOR))
            if name.startswith(GENESIS_ANCHOR) and name != (
                f"{GENESIS_ANCHOR}{verified.snapshot.entries[0].hash}"
            ):
                raise LedgerError(f"{name} does not name this ledger's genesis entry")
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
            self.git.run("push", "--quiet", "--", self.remote, f"{tag_object}:refs/tags/{name}")
            return tag_object


def _message(entry: Entry) -> str:
    body = entry.body
    what = body["type"]
    if body["type"] != GENESIS:
        what += f" {body.get('kind') or ''} {body.get('configuration_id') or ''}".rstrip()
    return f"ledger: entry {entry.seq}, {what.strip()}\n\nentry {entry.hash}\n"


def holdout_commit_error(git: Git, candidate: str, commit: str, paths: Sequence[str]) -> str | None:
    """Why a holdout run at ``commit`` is not at its candidate's commit plus only
    changes to data loading or the seal under ``paths`` and the whole package, which the
    runner exports to every run (section 11, item 6; review L3), or None. A change to any
    ``.gitattributes`` file, the root's included, is refused too (M-1)."""
    known = git.try_text("cat-file", "-t", candidate) == "commit"
    if not known or not git.is_ancestor(candidate, commit):
        return f"the holdout run's commit {commit} does not descend from its candidate's {candidate}"
    changed = git.text(
        "diff", "--name-only", "--no-renames", candidate, commit, "--", PACKAGE_PATH, *paths,
        ATTRIBUTES_PATHSPEC,
    ).splitlines()
    other = sorted(p for p in changed if p not in SEAL_AND_DATA_LOADING)
    if other:
        return (
            f"the code changed since the candidate's run outside data loading and the seal "
            f"({other[:5]}); section 11, item 6 allows a holdout run only at the candidate's "
            "commit plus changes to data loading or the seal"
        )
    return None


def seal_changes(git: Git, candidate: str, commit: str) -> tuple[list[str], str, str]:
    """The data loading and seal files changed from the candidate's commit to
    ``commit``, the full diff of those changes for the owner to read, and the SHA-256 of
    the diff's raw record (each changed file's path and its blob ids before and after),
    which names the changed content exactly and, being plumbing, does not depend on
    anyone's diff settings. The owner accepts that hash at the unseal, which records it,
    and replay recomputes it (security review round 3, T1). The diff is rendered here
    from the very blobs the record names (``render_diff``), never by git's porcelain, so
    no attribute, filter or setting of the clone can change what the owner reads (M-1).
    With no change the record is empty and the hash is that of no bytes."""
    seal_files = sorted(SEAL_AND_DATA_LOADING)
    raw = git.run(
        "diff-tree", "-r", "--no-renames", "--no-abbrev", candidate, commit, "--", *seal_files
    )
    files = sorted(
        line.split("\t", 1)[1] for line in raw.decode().splitlines() if "\t" in line
    )
    return files, render_diff(git, raw), hashlib.sha256(raw).hexdigest()


def _shown(text: str) -> str:
    """A line as the owner reads it: every character that is not printable (a control
    character such as a carriage return or an escape, a format character such as a
    bidirectional override, a line or paragraph separator) is written as its Python
    escape, so nothing in a file can hide or rewrite a line on the terminal."""
    return "".join(
        c if c == "\t" or c.isprintable() else c.encode("unicode_escape").decode("ascii")
        for c in text
    )


def render_diff(git: Git, raw: bytes) -> str:
    """A unified diff of every file in ``raw``, a ``diff-tree --raw`` record with full
    blob ids, rendered with ``difflib`` from the blobs it names, read with ``cat-file``,
    which applies no attribute, filter or text conversion. Lines split on newlines only,
    bytes that are not UTF-8 show as escapes, and so does any character ``_shown``
    escapes, so what the owner reads is the content the record's hash binds (M-1)."""
    shown = []
    for line in raw.decode().splitlines():
        meta, _, path = line.partition("\t")
        old_mode, new_mode, old, new, status = meta.lstrip(":").split()
        sides = []
        for oid in (old, new):
            if set(oid) == {"0"}:  # an added or a deleted file has no blob on one side
                sides.append([])
                continue
            (blob,) = git.objects([oid])
            if blob is None:
                raise GitError(f"blob {oid} of {path} is missing")
            sides.append([_shown(x) for x in blob.decode("utf-8", "backslashreplace").split("\n")])
        name = _shown(path)
        shown.append(f"{name}: {status}, mode {old_mode} -> {new_mode}, blob {old} -> {new}")
        shown.extend(difflib.unified_diff(*sides, f"a/{name}", f"b/{name}", lineterm=""))
    return "\n".join(shown)


# --- the registration through the genesis entry ----------------------------------------


def registered_ids(registration: Registration) -> dict[str, str]:
    """Every configuration id a strategy module is pinned for, with its hypothesis."""
    out = {cid: h for h in HYPOTHESES for cid in registration.order[h]}
    for cid, benchmark in registration.benchmarks.items():
        out[cid] = benchmark["window"].rsplit(".", 1)[-1]
    for cid, control in registration.controls.items():
        out[cid] = registration.hypothesis_of(control["base"])
    return out


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
    ids = registered_ids(registration)
    pins = genesis.body["strategies"]
    if set(pins) != set(ids):
        raise VerificationError("the genesis entry does not pin one strategy per registered id")
    paths = genesis.body["tree_paths"]
    for cid, pin in pins.items():
        if not inside(pin["path"], paths[ids[cid]]):
            raise VerificationError(f"{cid}'s strategy is outside {ids[cid]}'s tree paths")
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


@lru_cache(maxsize=8)
def _scrypt_accepts(n: int, r: int, p: int, dklen: int) -> bool:
    try:
        hashlib.scrypt(b"trial", salt=b"\0" * 16, n=n, r=r, p=p, maxmem=SCRYPT_MAXMEM, dklen=dklen)
    except (ValueError, MemoryError):
        return False
    return True


def check_key_record(data: bytes) -> None:
    """The holdout key record, exactly (T25, review N8): a JSON object with the keys
    ``kdf`` ("scrypt"), ``n`` (a power of two, at least 2^17), ``r`` (8), ``p`` (1),
    ``dklen`` (at least 32), ``salt`` (lower-case hex, even length, at least 32
    characters) and ``verifier_sha256`` (the lower-case hex SHA-256 of the scrypt
    output), and nothing else; the parameters must be ones ``hashlib.scrypt`` accepts
    within 256 MiB. G1.6 writes the record; this is the contract genesis holds it to.

    The record is the commitment of a commit-reveal (G1.6 security review H1): it holds
    a hash of the scrypt output, never the output itself, and the unseal entry reveals
    the output as its ``proof``, which the ledger checks against this hash when the
    entry is written and on every replay. Offline guessing still costs one scrypt per
    guess."""
    try:
        record = loads_strict(data)
    except CanonicalError as exc:
        raise LedgerError(f"the key record is not JSON: {exc}") from None
    problem = None
    if not isinstance(record, dict) or set(record) != KEY_RECORD_KEYS:
        problem = f"the keys are exactly {sorted(KEY_RECORD_KEYS)}"
    else:
        n, r, p, dklen = record["n"], record["r"], record["p"], record["dklen"]
        salt, verifier = record["salt"], record["verifier_sha256"]
        integers = all(type(v) is int for v in (n, r, p, dklen))
        if record["kdf"] != "scrypt" or not integers:
            problem = "kdf is scrypt and n, r, p and dklen are integers"
        elif n < 2**17 or n & (n - 1) or r != 8 or p != 1:
            problem = "n is a power of two of at least 2^17, r is 8 and p is 1"
        elif not isinstance(salt, str) or not re.fullmatch(r"(?:[0-9a-f]{2}){16,}", salt):
            problem = "salt is lower-case hex of at least 16 bytes"
        elif dklen < 32:
            problem = "dklen is at least 32"
        elif not isinstance(verifier, str) or not re.fullmatch(r"[0-9a-f]{64}", verifier):
            problem = "verifier_sha256 is a lower-case hex SHA-256"
        elif not _scrypt_accepts(n, r, p, dklen):
            problem = "hashlib.scrypt refuses these parameters within 256 MiB"
    if problem:
        raise LedgerError(f"the key record breaks its contract (threat model T25): {problem}")


def pinned_key_record(git: Git, genesis: Entry) -> dict:
    """The key record the genesis entry pins, read from its commit and checked against
    its SHA-256 and the contract; never the working tree's copy (T25)."""
    pin = genesis.body["key_record"]
    data = git.blob(pin["commit"], pin["path"])
    if data is None:
        raise VerificationError(f"no key record at {pin['commit']}:{pin['path']}, which genesis pins")
    if sha256_hex(data) != pin["sha256"]:
        raise VerificationError("the key record at its pinned commit does not match the genesis entry")
    check_key_record(data)
    return loads_strict(data)


def unseal_proof_error(git: Git, genesis: Entry, body: Mapping) -> str | None:
    """Why an unseal entry's ``proof`` is not the scrypt output whose SHA-256 the key
    record genesis pins, or None (security review H1). Compared in constant time."""
    record = pinned_key_record(git, genesis)
    proof = body.get("proof")
    if not isinstance(proof, str) or not re.fullmatch(rf"[0-9a-f]{{{2 * record['dklen']}}}", proof):
        return "its proof is not the scrypt output of the pinned key record's length"
    if not hmac.compare_digest(sha256_hex(bytes.fromhex(proof)), record["verifier_sha256"]):
        return "its proof does not verify against the key record genesis pins: no owner's key (T25)"
    return None


def tree_digests(git: Git, commit: str, tree_paths: Mapping[str, Sequence[str]]) -> dict:
    """For each group of tree paths, the SHA-256 of the sorted ``<blob id> <path>`` lines
    of every file under its paths at ``commit``, the harness group excluding the
    hypotheses' paths nested in it (run rule 4; review N12). None for an empty group."""
    listing = git.run("ls-tree", "-r", "-z", "--full-tree", commit)
    files = []
    for item in listing.split(b"\0"):
        if item:
            meta, path = item.split(b"\t", 1)
            files.append((path.decode(errors="replace"), meta.split()[2].decode()))
    out = {}
    for group, paths in tree_paths.items():
        others = [p for g, ps in tree_paths.items() if g != group for p in ps]
        lines = sorted(
            f"{oid} {path}\n" for path, oid in files
            if inside(path, paths) and not (group == "harness" and inside(path, others))
        )
        out[group] = sha256_hex("".join(lines).encode()) if lines else None
    return out


def preregistration_tag_error(ledger: GitLedger, tag: object) -> str | None:
    """Why a genesis entry may not pin ``tag``, or None. It pins only the newest
    registration: ``PREREGISTRATION_TAG``, the one this code implements, and only while the
    remote holds no ``prereg-v<N>`` newer than it. So a genesis can pin neither a
    registration the code no longer implements (``prereg-v1``'s cross-trial V and freeze
    under ``prereg-v2``'s gate) nor one a newer registration has superseded (quant review
    of #65, L7)."""
    if tag != PREREGISTRATION_TAG:
        return (
            f"a genesis entry pins {PREREGISTRATION_TAG}, the registration this code "
            f"implements, never {tag} (quant review of #65, L7)"
        )
    mine = int(PREREGISTRATION_VERSION.fullmatch(PREREGISTRATION_TAG).group(1))
    newer = [
        name for name in (n.removeprefix("refs/tags/") for n in ledger._remote_refs("prereg-v"))
        if (found := PREREGISTRATION_VERSION.fullmatch(name)) and int(found.group(1)) > mine
    ]
    if newer:
        return (
            f"the remote holds {', '.join(sorted(newer))}, newer than {tag}, which this code "
            "implements: update the code to the newest registration before writing genesis "
            "(quant review of #65, L7)"
        )
    return None


def write_genesis(
    ledger: GitLedger,
    *,
    tree_paths: Mapping[str, Sequence[str]],
    strategies: Mapping[str, Mapping[str, str]],
    key_record: tuple[str, str],
    tag: str = PREREGISTRATION_TAG,
    remote_url: str | None = None,
) -> Entry:
    """Append a genesis entry pinning the pre-registration tagged ``tag`` on the
    remote, then tag it ``ledger-genesis``. Refused once any entry of another type
    exists (section 16).

    ``remote_url`` is the canonical remote every later run must use (default: the
    remote's resolved URL now). ``key_record`` is the (commit, path) of G1.6's holdout
    key record, on the remote's main (section 11, item 4). ``tree_paths`` lists the
    harness paths and each hypothesis's strategy paths whose files every run
    records (run rule 4); they must exist on main and must not overlap, so the
    harness paths exclude the strategies. ``strategies`` pins, for every registered
    configuration, benchmark and control, the module path and function that runs it,
    inside its hypothesis's paths (review H2).
    """
    git = ledger.git
    check_environment(os.environ, git.repo)
    ledger.check_repository()
    problem = preregistration_tag_error(ledger, tag)
    if problem:
        raise LedgerError(problem)
    pinned = remote_url or git.text("ls-remote", "--get-url", ledger.remote)
    found = ledger._remote_refs(tag).get(f"refs/tags/{tag}")
    if found is None:
        raise LedgerError(f"the remote has no tag {tag}")
    git.run("fetch", "--no-tags", "--quiet", "--", ledger.remote, f"refs/tags/{tag}")
    git.run(
        "fetch", "--no-tags", "--quiet", "--", ledger.remote,
        f"+refs/heads/{CODE_BRANCH}:refs/remotes/{ledger.remote}/{CODE_BRANCH}",
    )
    main = git.text("rev-parse", f"refs/remotes/{ledger.remote}/{CODE_BRANCH}")
    files = {}
    for path in PREREGISTRATION_FILES:
        data = git.blob(found, path)
        if data is None:
            raise LedgerError(f"{path} is not in {tag} ({found})")
        files[path] = sha256_hex(data)
    registration = Registration.from_bytes(git.blob(found, PREREGISTRATION_JSON))
    periods = period_constants()
    registered = registration.doc["periods"]
    if (
        periods["holdout_start"] != registered["holdout_start"][:10]
        or periods["last_development_month"] != registered["last_development_month"]
        or periods["out_of_sample_start"] != registration.out_of_sample_start
    ):
        raise LedgerError("norse_research.periods disagrees with the registration")
    key_commit, key_path = key_record
    key_commit = git.text("rev-parse", f"{key_commit}^{{commit}}")
    if not git.is_ancestor(key_commit, main):
        raise LedgerError(f"the key record's commit {key_commit} is not on the remote's main")
    key_data = git.blob(key_commit, key_path)
    if key_data is None:
        raise LedgerError(f"no key record at {key_commit}:{key_path}")
    check_key_record(key_data)
    if set(tree_paths) != {"harness", *HYPOTHESES}:
        raise LedgerError("tree_paths lists the harness paths and each hypothesis's paths")
    groups = {g: list(p) for g, p in tree_paths.items()}
    if not inside(PACKAGE_PATH, groups["harness"]):
        # Security review round 3: the harness digest must cover the whole package, the
        # simulation engine included (G1.9), less the hypotheses' nested paths, or the
        # engine could change between runs that run rule 4 treats as the same code.
        raise LedgerError(
            f"the harness paths must cover all of {PACKAGE_PATH}, not files of it one by one"
        )
    for group, listed in groups.items():
        for path in listed:
            if git.try_text("rev-parse", "--verify", "--quiet", f"{main}:{path}") is None:
                raise LedgerError(f"tree path {path} ({group}) is not on the remote's main")
    names = sorted(groups)
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            for a in groups[first]:
                for b in groups[second]:
                    if not (inside(a, [b]) or inside(b, [a])):
                        continue
                    # A hypothesis's paths may sit strictly inside the harness's, which
                    # leaves them out of its digest (N12); no other overlap is allowed.
                    harness, other = (a, b) if first == "harness" else (b, a)
                    if "harness" in (first, second) and other != harness and inside(
                        other, [harness]
                    ):
                        continue
                    raise LedgerError(f"tree path {a} ({first}) overlaps {b} ({second})")
    ids = registered_ids(registration)
    if set(strategies) != set(ids):
        raise LedgerError(f"strategies must pin exactly {sorted(ids)}")
    pins = {}
    for cid, pin in strategies.items():
        path, function = pin["path"], pin["function"]
        if not inside(path, groups[ids[cid]]):
            raise LedgerError(f"{cid}'s strategy {path} is outside {ids[cid]}'s tree paths")
        source = git.blob(main, path)
        if source is None:
            raise LedgerError(f"{cid}'s strategy {path} is not on the remote's main")
        try:
            check_strategy_sources(git, main, groups[ids[cid]])
            configurations, _, _ = strategy_declarations(source.decode(), function)
        except StrategySourceRefused as exc:
            raise LedgerError(f"{cid}'s strategy: {exc}") from None
        if cid not in configurations:
            raise LedgerError(f"{path} does not declare {cid} among its CONFIGURATIONS")
        pins[cid] = {"path": path, "function": function}
    current = ledger.snapshot(ledger.local_head())
    if any(e.type != GENESIS for e in current.entries):
        raise LedgerError(
            "the registration is closed: an entry follows the genesis entry (section 16)"
        )
    anchors = [
        name for name, commit in ledger.milestone_tags().items()
        if name.startswith(GENESIS_ANCHOR) and git.try_text("cat-file", "-t", commit) == "commit"
        and git.blob(commit, LEDGER_PATH) is not None
    ]
    if not current.entries and anchors:
        raise LedgerError(
            f"{anchors[0]} already anchors a ledger on the remote; a new one cannot start"
        )
    body = {
        "type": GENESIS,
        "format": FORMAT,
        "preregistration": {"tag": tag, "commit": found, "files": files},
        **genesis_fields(registration),
        "periods": periods,
        "tree_paths": groups,
        "key_record": {"commit": key_commit, "path": key_path, "sha256": sha256_hex(key_data)},
        "remote": {"url": pinned, "branch": LEDGER_BRANCH, "code_branch": CODE_BRANCH},
        "strategies": dict(sorted(pins.items())),
    }
    entry = ledger.append(body)
    first = ledger.snapshot(ledger.local_head()).entries[0]
    anchor = f"{GENESIS_ANCHOR}{first.hash}"
    if anchor not in ledger.milestone_tags():
        ledger.tag_milestone(anchor, f"genesis entry {first.hash}", _anchor=True)
    return entry


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m norse_research.gitledger verify|sync|tag|genesis``.

    ``verify`` runs the anchored verification and prints what it checked; ``sync``
    fast-forwards the local ledger to the remote's; ``tag`` marks the ledger head at
    a milestone (there is no freeze, decision D21);
    ``genesis`` writes the genesis entry and its ``ledger-genesis-*`` anchor, a separate and
    deliberate step taken once G1.6 has committed the holdout key record. Never run
    ``git push --tags`` in this repository: a stray ``ledger-*`` tag is permanent.
    Every command refuses the environment overrides the harness refuses (N10).
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
    genesis.add_argument("--strategies", required=True, type=Path,
                         help="a JSON file mapping every registered id to {path, function}")
    for group in ("harness", *HYPOTHESES):
        genesis.add_argument(f"--{group.lower()}", nargs="+", required=True, metavar="PATH")
    args = parser.parse_args(argv)
    ledger = GitLedger(args.repo, args.remote)
    check_environment(os.environ, args.repo)
    if args.command == "verify":
        verified = ledger.verify()
        snapshot = verified.snapshot
        print(
            f"ledger verified: {len(snapshot.entries)} entries, head entry {snapshot.head_hash}, "
            f"ledger commit {verified.head} (the remote's {verified.remote_head}), "
            f"{sum(1 for e in snapshot.opens(TRIAL))} trial entries; milestone tags: "
            f"{', '.join(sorted(verified.tags)) or 'none'}"
        )
        if verified.ignored_tags:
            print(f"ignored ledger-* tags on commits holding no ledger: {verified.ignored_tags}")
    elif args.command == "sync":
        print(f"local ledger at {ledger.sync()}")
    elif args.command == "tag":
        print(f"{args.name}: tag object {ledger.tag_milestone(args.name, args.message)}")
    else:
        commit, _, path = args.key_record.partition(":")
        paths = {"harness": args.harness, **{h: getattr(args, h.lower()) for h in HYPOTHESES}}
        strategies = json.loads(args.strategies.read_text())
        entry = write_genesis(
            ledger, tree_paths=paths, strategies=strategies, key_record=(commit, path),
            tag=args.tag,
        )
        print(f"genesis entry {entry.seq} written and anchored: {entry.hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
