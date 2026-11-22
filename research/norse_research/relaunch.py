"""The seal's launcher: ``seal unseal`` and ``seal candidate`` run the candidate's code
(security review of #70, H-3).

Section 11, item 6 lets ``seal.py`` and the data loading modules change between the
candidate's selectable run and the unseal, and the owner's review of exactly that change
is what the unseal's accepted diff stands for. So the code that renders the review, reads
the passphrase and signs must not be HEAD's: a change to ``seal.py`` could leave itself
out of the diff it renders and keep the passphrase. ``python -m norse_research.seal
unseal`` and ``... candidate`` therefore never run HEAD's seal. The package's
``__init__.py`` calls ``maybe_relaunch`` before Python imports ``seal.py`` or any module of
the seal or of data loading; it:

1. takes the candidate's commit from the command line alone, ``--candidate-commit C``, a
   full commit id the owner reads on GitHub's view of the ``ledger`` branch. It never
   chooses code from the clone's local ledger ref, which any process of the owner's user
   can rewrite (review of #70, round 2, H-4);
2. fetches the remote's ``ledger`` branch with ``environment``'s pinned git (this module
   imports nothing of the package but ``environment``) and refuses unless ``C`` is the
   commit of a completed trial there: for ``unseal``, the last completed trial of the
   configuration hash the command names, which is how the gate picks a configuration's
   selectable run;
3. if the package on disk is ``C``'s, file for file, lets the command run in place;
4. otherwise writes ``C``'s package, read from git's objects with ``cat-file`` (no
   checkout, so no hook, attribute or filter of the clone runs, and nothing is added to the
   clone's git folder), into a new temporary folder, runs the same command there with this
   interpreter and ``--repo`` naming the clone, so every module it imports is ``C``'s, and
   removes the folder afterwards.

``C``'s own code then checks, against the remote's verified ledger, that the registered
rule selects the run at ``C`` and that every module it loaded is ``C``'s, and refuses
otherwise, before any passphrase is read. The keyless re-fetch of the holdout files, which
needs data loading as fixed after the candidate's run, stays at HEAD: ``seal
fetch-holdout``.

What this cannot do: the launcher itself is HEAD's code when the owner runs the command
from the clone. It lives outside the seal set (this module, ``environment.py`` and
``__init__.py``), so a change to it after the candidate's run is one item 6 never allows,
but code that changes it can also skip it. So the owner runs the unseal from ``C``'s own
code, exported with plain git into a new folder (holdout-seal.md; ``seal candidate`` prints
the commands): then the launcher that runs is ``C``'s.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from .environment import CREDENTIALS, HARDENED, REMOTE_COMMANDS, git_environment

PACKAGE_DIR = Path(__file__).resolve().parent
PACKAGE_PATH = "research/norse_research"
REPO_ROOT = PACKAGE_DIR.parents[1]
LEDGER_BRANCH = "ledger"
LEDGER_PATH = "ledger/trials.jsonl"
MODULE = "norse_research.seal"
COMMANDS = frozenset({"unseal", "candidate"})
CANDIDATE_COMMIT = "--candidate-commit"
# The seal's options that take a value; ``seal`` parses with allow_abbrev off, so these are
# spelt out in full on any command line it accepts.
VALUED = frozenset({
    "--repo", "--remote", "--commit", "--tag", "--configuration-sha256", "--accept-diff",
    "--unseal-commit", "--ledger-head", "--boundary", "--out", CANDIDATE_COMMIT,
})
FULL_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
REMOTE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class RelaunchRefused(SystemExit):
    """The launcher refuses: nothing runs, at HEAD or anywhere."""


def _git(repo: Path, *args: str, input: bytes | None = None) -> bytes:
    program, environ = git_environment()
    credentials = CREDENTIALS if args and args[0] in REMOTE_COMMANDS else ()
    proc = subprocess.run(
        [program, *HARDENED, *credentials, *args], cwd=repo, input=input, capture_output=True,
        env=environ, check=False,
    )
    if proc.returncode:
        message = proc.stderr.decode(errors="replace").strip()[:400]
        raise RelaunchRefused(f"seal: git {args[0]} failed: {message}")
    return proc.stdout


def seal_arguments(argv: Sequence[str]) -> list[str] | None:
    """The arguments after ``-m norse_research.seal`` in ``argv`` (``sys.orig_argv``), or
    None when ``argv`` does not run the seal as a module."""
    for i, arg in enumerate(argv):
        if arg == "-m" and i + 1 < len(argv) and argv[i + 1] == MODULE:
            return list(argv[i + 2:])
        if arg == f"-m{MODULE}":
            return list(argv[i + 1:])
    return None


def parse(arguments: Sequence[str]) -> tuple[str | None, dict[str, str]]:
    """The seal's subcommand and the value of each valued option in ``arguments``."""
    command, values, i = None, {}, 0
    while i < len(arguments):
        arg = arguments[i]
        name, equals, value = arg.partition("=")
        if equals and name in VALUED:
            values[name] = value
        elif arg in VALUED and i + 1 < len(arguments):
            values[arg] = arguments[i + 1]
            i += 1
        elif command is None and not arg.startswith("-"):
            command = arg
        i += 1
    return command, values


def remote_ledger_entries(repo: Path, remote: str) -> list[tuple[str, dict]]:
    """The remote's ``ledger`` branch, fetched now with the pinned git into the clone's
    remote-tracking ref, as (entry hash, body) pairs parsed with ``json`` alone. The
    clone's own ``refs/heads/ledger`` is never read (review of #70, round 2, H-4); what is
    taken from this is checked afterwards by the candidate's own code against the verified
    ledger."""
    if not REMOTE_NAME.fullmatch(remote):
        raise RelaunchRefused(f"seal: {remote!r} is not a remote name")
    tracking = f"refs/remotes/{remote}/{LEDGER_BRANCH}"
    _git(repo, "fetch", "--no-tags", "--quiet", "--", remote,
         f"+refs/heads/{LEDGER_BRANCH}:{tracking}")
    data = _git(repo, "cat-file", "blob", f"{tracking}:{LEDGER_PATH}")
    return [
        (hashlib.sha256(line.encode("ascii")).hexdigest(), json.loads(line))
        for line in data.decode("ascii").splitlines()
    ]


def completed_trials(entries: Sequence[tuple[str, dict]]) -> list[dict]:
    """The open entries of every completed trial, in ledger order."""
    completed = {
        body.get("opens") for _, body in entries
        if body.get("type") == "result" and body.get("status") == "completed"
    }
    return [
        body for digest, body in entries
        if body.get("type") == "open" and body.get("kind") == "trial" and digest in completed
    ]


def trial_commit(entries: Sequence[tuple[str, dict]], configuration_sha256: str) -> str | None:
    """The commit of the last completed trial of ``configuration_sha256``."""
    chosen = None
    for body in completed_trials(entries):
        if body.get("configuration_sha256") == configuration_sha256:
            chosen = body.get("commit")
    return chosen


def _package_tree(repo: Path, commit: str) -> dict[str, str]:
    listing = _git(repo, "ls-tree", "-r", "-z", "--full-tree", commit, "--", PACKAGE_PATH)
    tree = {}
    for item in listing.split(b"\0"):
        if item:
            meta, path = item.split(b"\t", 1)
            mode, kind, oid = meta.decode().split()
            if kind != "blob" or mode not in ("100644", "100755"):
                raise RelaunchRefused(f"seal: {path.decode(errors='replace')} at {commit} is not a file")
            tree[path.decode()] = oid
    if not tree:
        raise RelaunchRefused(f"seal: {commit} holds no {PACKAGE_PATH}")
    return tree


def _blob_id(data: bytes, oid: str) -> str:
    digest = hashlib.sha1 if len(oid) == 40 else hashlib.sha256
    return digest(f"blob {len(data)}\0".encode() + data).hexdigest()


def package_matches(repo: Path, commit: str, package: Path = PACKAGE_DIR) -> bool:
    """Whether the package at ``package`` is ``commit``'s, file for file: the same paths,
    outside ``__pycache__``, with the same bytes."""
    tree = _package_tree(repo, commit)
    on_disk = {
        f"{PACKAGE_PATH}/{path.relative_to(package).as_posix()}": path
        for path in package.rglob("*")
        if path.is_file() and "__pycache__" not in path.relative_to(package).parts
    }
    return set(on_disk) == set(tree) and all(
        _blob_id(on_disk[rel].read_bytes(), oid) == oid for rel, oid in tree.items()
    )


def export_package(repo: Path, commit: str, dest: Path) -> None:
    """Write ``commit``'s package under ``dest`` from git's objects, as stored: no checkout,
    so no hook, attribute or filter of the clone runs."""
    tree = _package_tree(repo, commit)
    oids = list(tree.values())
    out = _git(repo, "cat-file", "--batch", input=("\n".join(oids) + "\n").encode())
    pos = 0
    for rel, oid in tree.items():
        end = out.index(b"\n", pos)
        header = out[pos:end].split()
        if header[-1] == b"missing" or header[0].decode() != oid:
            raise RelaunchRefused(f"seal: object {oid} of {rel} is missing")
        size = int(header[2])
        data = out[end + 1 : end + 1 + size]
        pos = end + 1 + size + 1
        if _blob_id(data, oid) != oid:
            raise RelaunchRefused(f"seal: object {oid} of {rel} does not match its id")
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def run_at(repo: Path, commit: str, arguments: Sequence[str]) -> int:
    """Run ``python -m norse_research.seal`` with ``commit``'s package, ``--repo`` naming
    ``repo``, and return its exit status. The folder is removed afterwards."""
    rest = []
    skip = False
    for arg in arguments:
        if skip:
            skip = False
            continue
        if arg == "--repo":
            skip = True
            continue
        if arg.startswith("--repo="):
            continue
        rest.append(arg)
    folder = Path(tempfile.mkdtemp(prefix="norse-seal-at-candidate-"))
    try:
        export_package(repo, commit, folder)
        print(
            f"seal: running with the code of commit {commit}, the candidate's run, from "
            f"{folder}, against the clone at {repo} (review of #70, H-3)",
            file=sys.stderr, flush=True,
        )
        command = [sys.executable, "-s", "-B", "-m", MODULE, "--repo", str(repo), *rest]
        return subprocess.run(command, cwd=folder / "research", check=False).returncode
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def maybe_relaunch(argv: Sequence[str]) -> None:
    """Run ``seal unseal`` or ``seal candidate`` with the code of the commit
    ``--candidate-commit`` names, as the module docstring says, and exit with its status;
    return, so the command runs in place, when ``argv`` runs neither, asks only for help,
    or the package on disk is already that commit's. Raises ``SystemExit`` in every other
    case, refusals included: nothing then runs, at HEAD or anywhere."""
    arguments = seal_arguments(argv)
    if arguments is None:
        return
    command, values = parse(arguments)
    if command not in COMMANDS or "-h" in arguments or "--help" in arguments:
        return
    commit = values.get(CANDIDATE_COMMIT, "")
    if not FULL_COMMIT.fullmatch(commit):
        raise RelaunchRefused(
            f"seal: {command} needs {CANDIDATE_COMMIT} with the full commit id of the "
            "candidate's selectable run, read on GitHub's view of the ledger branch; the "
            "launcher never takes it from the clone's own ledger. Nothing ran (review of #70, "
            "round 2, H-4)"
        )
    repo = Path(values.get("--repo", REPO_ROOT)).resolve()
    remote = values.get("--remote", "origin")
    entries = remote_ledger_entries(repo, remote)
    if command == "unseal":
        hashed = values.get("--configuration-sha256", "")
        found = trial_commit(entries, hashed)
        if found != commit:
            where = ("has no completed trial" if found is None
                     else f"has its last completed trial at {found}")
            raise RelaunchRefused(
                f"seal: on the remote's ledger, configuration {hashed} {where}, not at "
                f"{commit}; nothing ran. Check the commit on GitHub's view of the ledger "
                "branch (review of #70, round 2, H-4)"
            )
    elif commit not in {body.get("commit") for body in completed_trials(entries)}:
        raise RelaunchRefused(
            f"seal: no completed trial on the remote's ledger ran at {commit}; nothing ran. "
            "Check the commit on GitHub's view of the ledger branch (review of #70, round 2, "
            "H-4)"
        )
    if package_matches(repo, commit):
        return
    raise SystemExit(run_at(repo, commit, arguments))


def relaunch_from_main(argv: Sequence[str] | None = None) -> None:
    """``maybe_relaunch`` on this process's own command line (``sys.orig_argv``)."""
    maybe_relaunch(list(getattr(sys, "orig_argv", ()) if argv is None else argv))