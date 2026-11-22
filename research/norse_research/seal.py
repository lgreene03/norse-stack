"""The holdout seal: the owner's key record, the one unseal and the re-fetch (G1.6).

The holdout (2026-01-01 to 2026-10-31) is public data, so the seal is not secrecy but
tamper evidence (decision D4): holdout rows reach research code in one place only, the
holdout run the ledger ties to the single unseal entry, and that entry can be written
only by someone who types the owner's passphrase. Section 11 of
docs/PREREGISTRATION.md registers the rules; docs/research/holdout-seal.md tells the
owner what to run, in prose.

**The key record** (``key-record``, run by the owner, once). The passphrase is read
twice with ``getpass`` from an interactive terminal, never from an option, an
environment variable or a file (threat T25), and must hold at least six words and 20
characters. It is derived with scrypt at n 2^17, r 8, p 1 and dklen 32 over a fresh
16-byte salt from ``secrets``, and the record holds exactly the keys the genesis step's
contract names (``gitledger.check_key_record``): the parameters, the salt,
``verifier_sha256``, the SHA-256 of the scrypt output, and ``signer_sha256``, the SHA-256
of the one-time public key the scrypt output derives (``lamport``; decision D27), never
the output itself. Nothing secret is printed; the record's own SHA-256 is, for the owner
to keep beside the passphrase. The owner commits the record on main; the genesis entry
then pins its commit, path and SHA-256, and the seal only ever reads it from there, never
from the working tree. Straight after genesis the owner runs ``check-key``, which checks
the typed passphrase against both commitments of the record genesis pinned and writes
nothing, so a record substituted at genesis is found then rather than at the unseal
(security review M2).

**The unseal** (``unseal``, the owner, once). There is no ledger freeze (decision
D21): the unseal closes development, so no trial, benchmark or control run may be
opened after it (a result of one opened before it is accepted and ignored). It is
refused, before the passphrase is asked for, unless: the environment carries no override,
nor ``GIT_ASKPASS`` or ``SSH_ASKPASS`` (which no ledger git command inherits any more;
``gitledger.PASSED_ENVIRONMENT``); the clone's own configuration sets nothing that runs a
program or redirects a read or a push (``gitledger.REPOSITORY_SCOPE_REFUSED``); the local
ledger head is the remote's (T23) and the ledger verifies; each
hypothesis has a selectable run of every registered configuration or has exhausted its
own entries and the reserve, since an unseal while the other grid can still run would
stop it early ("early" means before that, T20); no unseal entry exists yet (T23); the tree is clean, HEAD is on main
and the code running is the code at HEAD; the period constants are the genesis entry's
(T20); the request names exactly the pre-registration the genesis entry names (T24); a
holdout candidate exists on the ledger and the request names its configuration hash
(section 11, items 1 to 3; T22); item 6's rule holds between the candidate's commit and
HEAD (only data loading or the seal may differ, and no ``.gitattributes`` file); and the
request accepts exactly the hash of that data loading and seal diff. Otherwise the
refusal prints the full diff, rendered here from the blobs themselves so that no git
attribute, filter or setting can hide a line, and a long line wrapped and flagged, and
only after it, and only when an interactive terminal is attached, the hash, so the owner
reviews the change before anything is unsealed (security review round 3, T1; review of
#65, M-1, L-10 and I-7). Every holdout run is then at exactly the commit the entry
records, and the entry records the accepted hash, which replay recomputes. Then the
passphrase is read and checked against both of the key record's commitments, in constant
time (T25). Only then is the unseal entry appended, signed, pushed and read back from the
remote, and only after that are the holdout files fetched again. The entry carries a
one-time signature (``lamport``) over its own bytes, everything but the signature, by the
key the scrypt output derives; the scrypt output itself is never revealed. The ledger
refuses an unseal whose signature does not verify against the pinned record's
``signer_sha256``, when it is written and on every replay, so no code path opens the
holdout without the owner's key (security review H1), and a copy of the entry, taken
while it is pushed, authorises nothing but that same entry (decision D27; review of #65,
H-2). The unseal's timestamp is its predecessor's, so running it again after a failed
push, at once and with nothing changed, signs the same bytes and reveals nothing new.

**The re-fetch** (D17; ``fetch-holdout`` repeats it). Every file the frozen holdout
manifest pins is fetched again through the given fetcher and checked against its
published CHECKSUM and against the manifest's SHA-256 and size before it is written;
then the whole manifest is verified, hashes, sizes and rows. Any failure stops there,
before any holdout return is computed. A file already in the mirror that matches its
pin is kept, so the owner's offline copy (option B) can stand in for a file the
archive has re-published since 2026-11-11.

**The holdout run** is the harness's (``harness.run`` with ``{"configuration":
"holdout", "candidate_sha256": ...}``). This module gives it the checks it shares with
the unseal: the committed holdout manifest, the files on disk, and the unseal entry on
the remote's ledger at the moment the run starts.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from . import lamport
from .binance_archive import (
    CHECKSUM_MAX_BYTES,
    DOWNLOAD_URL,
    ArchiveError,
    Fetch,
    HttpFetcher,
    parse_checksum,
    sha256_hex,
)
from .environment import check_environment
from .formats import DatasetSpec
from .gate import development, gate_ledger, holdout_candidate, unseal_early_error
from .gitledger import (
    DIFF_WIDTH,
    SCRYPT_MAXMEM,
    Git,
    GitLedger,
    check_key_record,
    holdout_commit_error,
    period_constants,
    pinned_key_record,
    render_diff,
    seal_digest,
    seal_record,
)
from .ledger import (
    CODE_BRANCH,
    LEDGER_PATH,
    UNSEAL,
    Entry,
    LedgerError,
    parse_ledger,
    unseal_of,
)
from .manifest import Manifest, ManifestError
from .manifest import verify as verify_manifest
from .registration import HYPOTHESES, PREREGISTRATION_FILES
from .store import REPO_ROOT, ArchiveStore, _atomic_write, datasets_root

HOLDOUT_MANIFEST = "research/manifests/binance-vision-holdout.json"
# The contract's floor, exactly (threat model T25; gitledger.check_key_record).
SCRYPT_PARAMETERS = {"n": 2**17, "r": 8, "p": 1, "dklen": 32}
SALT_BYTES = 16
# T25 asks for at least six words from a diceware list, generated by the password
# manager. The code requires six words (runs of letters, whatever the separator) and 20
# characters; it cannot tell generated words from chosen ones (security review L6).
MIN_PASSPHRASE_CHARACTERS = 20
MIN_PASSPHRASE_WORDS = 6
_WORD = re.compile(r"[^\W\d_]+")
UNSEAL_REQUEST_KEYS = frozenset({"preregistration", "configuration_sha256", "accepted_diff"})
# Programs git runs to ask for a credential when no helper supplies one (security review
# of #65, H-1's class). No ledger git command inherits either since M-3's fix
# (gitledger.PASSED_ENVIRONMENT), so refusing them here is belt and braces. GIT_SSH,
# GIT_SSH_COMMAND, GIT_EXEC_PATH, GIT_CONFIG_PARAMETERS and every other GIT_* variable
# outside check_environment's allowlist are refused for every ledger write already.
UNSEAL_ENVIRONMENT_REFUSED = ("GIT_ASKPASS", "SSH_ASKPASS")

ReadKey = Callable[[str], str]


class SealRefused(LedgerError, PermissionError):
    """The seal refuses: no unseal, no re-fetch, no holdout row."""


class NoTerminal(SealRefused):
    """The passphrase was asked for without an interactive terminal (T25)."""


# --- the passphrase and the key record --------------------------------------------------


def terminal_attached() -> bool:
    """Whether an interactive terminal is attached, where the passphrase can be typed."""
    return sys.stdin is not None and sys.stdin.isatty()


def read_passphrase(prompt: str) -> str:
    """The passphrase, typed at an interactive terminal with nothing echoed. There is
    no other way in: no option, environment variable or file (T25). An agent's shell
    tool has no terminal, so this refuses there; that is a speed bump, not a lock, and
    the control is that the passphrase never reaches an agent's session."""
    if not terminal_attached():
        raise NoTerminal(
            "the holdout passphrase is read only from an interactive terminal; run this "
            "in a terminal of your own, never in a session where an agent is working"
        )
    return getpass.getpass(prompt)


def _encode(passphrase: str) -> bytes:
    # One spelling per passphrase, whatever the keyboard or the password manager sends.
    return unicodedata.normalize("NFC", passphrase).encode("utf-8")


def derive(passphrase: str, record: Mapping) -> str:
    """scrypt of the passphrase under the record's salt and parameters, as hex."""
    return hashlib.scrypt(
        _encode(passphrase), salt=bytes.fromhex(record["salt"]), n=record["n"], r=record["r"],
        p=record["p"], dklen=record["dklen"], maxmem=SCRYPT_MAXMEM,
    ).hex()


def verifier_of(output: str) -> str:
    """The record's ``verifier_sha256`` for a scrypt output given as hex."""
    return sha256_hex(bytes.fromhex(output))


def signing_seed(output: str) -> bytes:
    """The unseal's one-time signing seed for a scrypt output given as hex (D27)."""
    return lamport.seed_of(bytes.fromhex(output))


def signer_of(output: str) -> str:
    """The record's ``signer_sha256`` for a scrypt output given as hex (D27)."""
    return lamport.signer_sha256(signing_seed(output))


def make_key_record(passphrase: str, salt: bytes | None = None) -> dict:
    """A key record for ``passphrase``: the contract's keys exactly, a fresh salt, the
    SHA-256 of the scrypt output and the SHA-256 of the one-time public key it derives,
    never the output."""
    record = {
        "kdf": "scrypt",
        **SCRYPT_PARAMETERS,
        "salt": (secrets.token_bytes(SALT_BYTES) if salt is None else salt).hex(),
    }
    output = derive(passphrase, record)
    record["verifier_sha256"] = verifier_of(output)
    record["signer_sha256"] = signer_of(output)
    return record


def key_record_bytes(record: Mapping) -> bytes:
    return (json.dumps(dict(record), indent=2, sort_keys=True) + "\n").encode("ascii")


def passphrase_problem(passphrase: str) -> str | None:
    """Why a passphrase is too weak for the key record, or None (T25; review L6)."""
    if len(passphrase.strip()) < MIN_PASSPHRASE_CHARACTERS:
        return f"it is shorter than {MIN_PASSPHRASE_CHARACTERS} characters"
    if len(_WORD.findall(passphrase)) < MIN_PASSPHRASE_WORDS:
        return f"it holds fewer than {MIN_PASSPHRASE_WORDS} words"
    return None


def write_key_record(out: Path, read: ReadKey | None = None) -> str:
    """Ask for the passphrase twice and write its key record to ``out``, which must not
    exist. Returns the record's SHA-256; prints nothing and never writes the
    passphrase anywhere."""
    read = read or read_passphrase
    out = Path(out)
    if out.exists():
        raise SealRefused(f"{out} exists; a key record is never replaced")
    first = read("Holdout passphrase (nothing is echoed): ")
    second = read("The same passphrase again: ")
    if not (isinstance(first, str) and isinstance(second, str)) or not hmac.compare_digest(
        _encode(first), _encode(second)
    ):
        raise SealRefused("the two entries differ; nothing was written")
    problem = passphrase_problem(first)
    if problem:
        raise SealRefused(
            f"the passphrase is too weak: {problem}; use one of at least six words that the "
            "password manager generates (threat model T25)"
        )
    record = make_key_record(first)
    data = key_record_bytes(record)
    check_key_record(data)  # the contract the genesis step holds it to
    check_key(record, second)  # the record verifies the passphrase as typed again
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "xb") as handle:
        handle.write(data)
    return sha256_hex(data)


def key_record_of(git: Git, genesis: Entry) -> dict:
    """The key record the genesis entry pins, read from its commit and checked against
    its SHA-256 and the contract; never the working tree's copy (T25)."""
    try:
        return pinned_key_record(git, genesis)
    except LedgerError as exc:
        raise SealRefused(str(exc)) from None


def check_key(record: Mapping, passphrase: object) -> str:
    """The scrypt output of ``passphrase``, as hex, refused unless its SHA-256 is the
    record's verifier and the one-time public key it derives is the record's signer;
    both compared in constant time. The output never leaves this process: the unseal
    signs with the key it derives (D27)."""
    if not isinstance(passphrase, str) or not passphrase:
        raise SealRefused("no key: the unseal needs the owner's passphrase (D4, threat T25)")
    output = derive(passphrase, record)
    if not hmac.compare_digest(verifier_of(output), record["verifier_sha256"]):
        raise SealRefused("the key does not verify against the key record genesis pins (T25)")
    if not hmac.compare_digest(signer_of(output), record["signer_sha256"]):
        raise SealRefused(
            "the key verifies against the record's verifier but does not derive its "
            "signer_sha256, so the record was not made whole from this passphrase; nothing "
            "can be unsealed with it (D27, T25)"
        )
    return output


def check_genesis_key(repo: Path, read_key: ReadKey | None = None, remote: str = "origin") -> str:
    """Check the typed passphrase against the key record the genesis entry on the
    remote pins, and write nothing. Returns the record's SHA-256. The owner runs it
    straight after genesis, so a substituted record is found at once (review M2)."""
    repo = Path(repo)
    check_environment(os.environ, repo)
    ledger = GitLedger(repo, remote)
    head = ledger.fetch()
    if head is None:
        raise SealRefused("the remote has no ledger yet; write the genesis entry first")
    ledger.git.run(
        "fetch", "--quiet", "--no-tags", "--", remote,
        f"+refs/heads/{CODE_BRANCH}:refs/remotes/{remote}/{CODE_BRANCH}",
    )
    genesis = ledger.verify(head).snapshot.genesis
    record = key_record_of(ledger.git, genesis)
    check_key(record, (read_key or read_passphrase)("Holdout passphrase: "))
    return genesis.body["key_record"]["sha256"]


# --- the holdout manifest, its files and the unseal on the remote ---------------------


def committed_holdout_manifest(git: Git, genesis: Entry, commit: str = "HEAD") -> Manifest:
    """The holdout manifest committed at ``commit``, refused unless its SHA-256 is the
    one the genesis entry registers (frozen since 2026-11-11, D17)."""
    data = git.blob(commit, HOLDOUT_MANIFEST)
    if data is None:
        raise SealRefused(f"{HOLDOUT_MANIFEST} is not committed at {commit}")
    if sha256_hex(data) != genesis.body["manifests"]["holdout"]:
        raise SealRefused(
            f"{HOLDOUT_MANIFEST} at {commit} has sha256 {sha256_hex(data)}, not the registered "
            f"holdout manifest's {genesis.body['manifests']['holdout']}"
        )
    return Manifest.from_bytes(data, Path(HOLDOUT_MANIFEST))


def verify_holdout_files(manifest: Manifest, store: ArchiveStore) -> int:
    """Every file the holdout manifest pins, verified: hash, size and rows (D17)."""
    try:
        return verify_manifest(manifest, store)
    except (OSError, ManifestError, ArchiveError, ValueError) as exc:
        raise SealRefused(
            f"the holdout files do not all match the frozen holdout manifest ({exc}); fetch "
            "them again with `python -m norse_research.seal fetch-holdout`"
        ) from None


def require_unseal_on_remote(
    ledger: GitLedger, unseal: str, opened: str | None = None
) -> None:
    """The remote's ledger, fetched now, holds the unseal entry (and the run's open
    entry, if named); otherwise holdout data stays sealed (T23)."""
    remote = ledger.fetch()
    data = ledger.git.blob(remote, LEDGER_PATH) if remote else None
    held = {e.hash: e for e in parse_ledger(data or b"")}
    if unseal not in held or held[unseal].type != UNSEAL:
        raise SealRefused(
            f"the remote's ledger does not hold the unseal entry {unseal}; holdout data "
            "stays sealed (T23)"
        )
    if opened is not None and opened not in held:
        raise SealRefused(f"the remote's ledger does not hold the holdout run's entry {opened}")


def _pinned_copy_present(store: ArchiveStore, key: str, digest: str, size: int) -> bool:
    path = store.path(key)
    if not (path.is_file() and path.with_name(path.name + ".CHECKSUM").is_file()):
        return False
    data = path.read_bytes()  # hashed only, never parsed
    return len(data) == size and sha256_hex(data) == digest


def refetch_holdout(manifest: Manifest, store: ArchiveStore, fetch: Fetch) -> int:
    """Fetch every holdout file again and verify each against the frozen manifest
    before writing it, then verify the manifest in full (D17). The first failure
    stops it. Returns how many files the manifest pins."""
    count = 0
    for name, entry in sorted(manifest.datasets.items()):
        spec = DatasetSpec.from_name(name)
        for month, digest, size, _ in entry["files"]:
            key = spec.key(month)
            count += 1
            if _pinned_copy_present(store, key, digest, size):
                continue
            path = store.path(key)
            try:
                checksum = fetch(DOWNLOAD_URL + key + ".CHECKSUM", CHECKSUM_MAX_BYTES)
                published = parse_checksum(checksum, path.name)
                data = fetch(DOWNLOAD_URL + key, spec.max_bytes())
            except (ArchiveError, OSError, ValueError) as exc:
                raise SealRefused(f"{key} could not be fetched again: {exc}") from None
            actual = sha256_hex(data)
            if actual != published:
                raise SealRefused(f"{key}: sha256 {actual} is not its published {published}")
            if (actual, len(data)) != (digest, size):
                raise SealRefused(
                    f"{key}: the archive now serves a file that is not the one the frozen "
                    f"holdout manifest pins (sha256 {actual}, {len(data)} bytes); restore it "
                    "from the offline copy (D17, option B) and run fetch-holdout again"
                )
            _atomic_write(path, data)
            _atomic_write(path.with_name(path.name + ".CHECKSUM"), checksum)
    verify_holdout_files(manifest, store)
    return count


# --- the unseal --------------------------------------------------------------------------


def unseal(
    repo: Path,
    request: Mapping,
    *,
    read_key: ReadKey | None = None,
    store: ArchiveStore | None = None,
    fetch: Fetch | None = None,
    remote: str = "origin",
) -> Entry:
    """Write the single unseal entry; see the module docstring for what it refuses.

    ``request`` holds exactly ``preregistration`` ({tag, commit, files}, as the genesis
    entry names it), ``configuration_sha256`` (the holdout candidate's) and
    ``accepted_diff`` (the hash of the data loading and seal diff from the candidate's
    commit to HEAD, which the refusal without it prints; T1). With a ``store`` and a
    ``fetch``, the holdout files are fetched again after the entry is on the remote;
    otherwise ``fetch_holdout`` does that."""
    from .harness import (  # the harness imports this module
        check_clean_tree,
        check_head_on_main,
        check_loaded_modules,
    )

    repo = Path(repo)
    check_environment(os.environ, repo)
    askpass = [name for name in UNSEAL_ENVIRONMENT_REFUSED if name in os.environ]
    if askpass:
        raise SealRefused(
            f"{' and '.join(askpass)} is set, and git would run it inside the unseal's push, "
            "which carries the proof; unset it for the unseal (security review of #65)"
        )
    if not isinstance(request, Mapping) or set(request) != UNSEAL_REQUEST_KEYS:
        raise SealRefused(f"an unseal names exactly {sorted(UNSEAL_REQUEST_KEYS)}")
    ledger = GitLedger(repo, remote)
    git = ledger.git
    admitted_on = ledger.require_in_step()
    verified = ledger.verify(admitted_on)
    genesis = verified.snapshot.genesis
    if genesis is None or verified.registration is None:
        raise SealRefused("the ledger has no genesis entry, so nothing can be unsealed")
    if unseal_of(verified.snapshot.entries) is not None:
        raise SealRefused("a second unseal: the ledger already holds the unseal entry (T23)")
    if genesis.body["periods"] != period_constants():
        raise SealRefused(
            f"norse_research.periods {period_constants()} differs from the genesis entry's "
            f"{genesis.body['periods']} (T20)"
        )
    check_clean_tree(git)
    head = git.text("rev-parse", "HEAD")
    check_head_on_main(git, remote, head)
    check_loaded_modules(git, repo, head)
    if request["preregistration"] != genesis.body["preregistration"]:
        raise SealRefused(
            "the unseal names a pre-registration other than the one the genesis entry names "
            f"({genesis.body['preregistration']['tag']} at "
            f"{genesis.body['preregistration']['commit']}; T24)"
        )
    issued = gate_ledger(ledger, verified.snapshot)
    early = unseal_early_error(issued)
    if early:
        raise SealRefused(early)
    candidate = holdout_candidate(issued)
    if candidate is None:
        raise SealRefused(
            "no hypothesis passes development: there is no holdout candidate, so no unseal "
            "and no holdout run (section 11, item 3; T22)"
        )
    chosen = candidate.open_entry.body["configuration_sha256"]
    if request["configuration_sha256"] != chosen:
        raise SealRefused(
            f"the unseal names configuration {request['configuration_sha256']}, but the "
            f"registered rule selects {candidate.configuration_id} ({chosen}) on the ledger; "
            "the harness refuses any other (section 11, item 4; T22)"
        )
    # Item 6's rule, applied once and here: from the candidate's commit to this one only
    # data loading or the seal may change, and every holdout run is then at exactly this
    # commit, which the entry records (security review round 2, N1).
    paths = genesis.body["tree_paths"]
    problem = holdout_commit_error(
        git, candidate.open_entry.body["commit"], head, [*paths["harness"], *paths[candidate.hypothesis]]
    )
    if problem:
        raise SealRefused(f"the unseal is refused at this commit: {problem}")
    candidate_commit = candidate.open_entry.body["commit"]
    files, digest = seal_digest(git, candidate_commit, head)
    if request["accepted_diff"] != digest:
        diff = render_diff(git, seal_record(git, candidate_commit, head))  # for the refusal only
        raise SealRefused(diff_to_accept(candidate_commit, head, files, diff, digest,
                                         show_hash=terminal_attached()))
    record = key_record_of(git, genesis)
    seed = signing_seed(check_key(record, (read_key or read_passphrase)("Holdout passphrase: ")))
    entry = ledger.append(
        {
            "type": UNSEAL,
            "preregistration": genesis.body["preregistration"],
            "configuration_id": candidate.configuration_id,
            "hypothesis": candidate.hypothesis,
            "configuration_sha256": chosen,
            "candidate": {"open": candidate.open_entry.hash, "result": candidate.result_entry.hash},
            "accepted_diff": digest,
            "holdout_manifest_sha256": genesis.body["manifests"]["holdout"],
            "key_record_sha256": genesis.body["key_record"]["sha256"],
            "commit": head,
        },
        expected_head=admitted_on,
        # The append fixes seq, prev and time, then has this sign the entry's bytes (D27).
        sign=lambda message: lamport.sign(seed, message),
    )
    require_unseal_on_remote(ledger, entry.hash)
    if store is not None and fetch is not None:
        refetch_holdout(committed_holdout_manifest(git, genesis), store, fetch)
    return entry


def diff_to_accept(candidate: str, head: str, files: Sequence[str], diff: str, digest: str,
                   *, show_hash: bool) -> str:
    """What the owner reviews before the unseal: the full diff of the data loading and
    seal files changed since the candidate's run, rendered from the blobs themselves
    (``gitledger.render_diff``), and only after it the hash to accept (T1; security
    review of #65, M-1 and L-2). The owner is never sent to their own ``git diff``,
    which the clone's attributes and settings could filter.

    The hash is printed only when ``show_hash``, which the unseal sets when an interactive
    terminal is attached, the same test the passphrase prompt applies; without one the
    diff is printed and the hash is not (review of #65, I-7). An agent's shell has no
    terminal, so it cannot hand the owner a ready-made command line from this refusal;
    the hash is still computable from git's plumbing, so this is a speed bump, and the
    control remains the owner accepting only a hash read after the diff, in their own
    terminal. Requiring the passphrase first was the alternative, and was not chosen: it
    would have the owner type the passphrase twice per review, once to see the diff and
    again to unseal, where D27 keeps it to once."""
    if show_hash:
        accept = f"accept that with --accept-diff {digest}"
    else:
        accept = (
            "the hash that accepts it is printed only when this runs in an interactive "
            "terminal, where the passphrase can be typed: run it again in a terminal of your "
            "own (security review of #65, I-7)"
        )
    if not files:
        return (
            f"no data loading or seal file changed between the candidate's commit {candidate} "
            f"and HEAD {head}; {accept} (security review round 3, T1)"
        )
    return (
        f"data loading or seal files changed between the candidate's commit {candidate} and "
        f"HEAD {head}. Their full diff follows, rendered from the blobs themselves, so no git "
        "attribute, filter or setting can change it, with every unprintable character written "
        f"as an escape and every line over {DIFF_WIDTH} characters wrapped and flagged:"
        f"\n\n{diff}\n\nIf each change is only data loading or the seal, {accept} (security "
        "review round 3, T1; review of #65, M-1 and L-10)"
    )


def fetch_holdout(
    repo: Path, *, store: ArchiveStore, fetch: Fetch, remote: str = "origin"
) -> int:
    """The re-fetch on its own, after the unseal entry is on the remote (D17), for
    when the one at the unseal stopped. Idempotent: files already matching their pin
    are kept."""
    repo = Path(repo)
    check_environment(os.environ, repo)
    ledger = GitLedger(repo, remote)
    verified = ledger.verify(ledger.require_in_step())
    entry = unseal_of(verified.snapshot.entries)
    if entry is None:
        raise SealRefused("the holdout files are fetched again only after the unseal (D17)")
    require_unseal_on_remote(ledger, entry.hash)
    manifest = committed_holdout_manifest(ledger.git, verified.snapshot.genesis)
    return refetch_holdout(manifest, store, fetch)


def preregistration_at(git: Git, commit: str, tag: str) -> dict:
    """What an unseal names for the pre-registration at ``commit``: the tag, the full
    commit id and the SHA-256 of each pre-registration file in that commit. The tag is
    the one the genesis entry names unless the owner gives another."""
    full = git.text("rev-parse", "--verify", f"{commit}^{{commit}}")
    files = {}
    for path in PREREGISTRATION_FILES:
        data = git.blob(full, path)
        if data is None:
            raise SealRefused(f"{path} is not in {full}")
        files[path] = sha256_hex(data)
    return {"tag": tag, "commit": full, "files": files}


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m norse_research.seal key-record|check-key|rehearse|candidate|unseal|fetch-holdout``.

    ``key-record --out PATH`` writes the owner's key record (run once, by the owner, in
    a terminal of their own). ``check-key`` checks the typed passphrase against the key
    record the genesis entry on the remote pins and writes nothing; the owner runs it
    straight after genesis. ``rehearse [--boundary YYYY-MM]`` runs the holdout path at
    HEAD on development data only and writes nothing; the owner runs it before the
    unseal. ``candidate`` prints what the unseal must name, from the ledger as it
    stands, without any key, and the data loading and seal files changed since the
    candidate's run, but neither their diff nor the hash that accepts it (L-2).
    ``unseal --commit C --configuration-sha256 H --accept-diff D`` writes the single
    unseal entry, signed, after the passphrase verifies, then fetches the holdout files
    again; without the right ``D`` it prints the full diff to review, then, at an
    interactive terminal only, the hash, and refuses (T1; M-1, I-7). ``--tag``
    defaults to the tag the genesis entry names. ``fetch-holdout`` repeats only the
    re-fetch. No option takes the passphrase: it is typed at the terminal.
    """
    parser = argparse.ArgumentParser(description=main.__doc__.split("\n\n")[0])
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--remote", default="origin")
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("key-record")
    record.add_argument("--out", type=Path, required=True)
    commands.add_parser("check-key")
    commands.add_parser("candidate")
    rehearsal = commands.add_parser("rehearse")
    rehearsal.add_argument("--boundary", default=None,
                           help="the development month that stands in for the holdout's start")
    opening = commands.add_parser("unseal")
    opening.add_argument("--commit", required=True, help="the pre-registration tag's commit")
    opening.add_argument("--tag", default=None, help="defaults to the genesis entry's tag")
    opening.add_argument("--configuration-sha256", required=True)
    opening.add_argument("--accept-diff", default=None,
                         help="the hash of the data loading and seal diff, once reviewed")
    commands.add_parser("fetch-holdout")
    args = parser.parse_args(argv)
    if args.command == "key-record":
        digest = write_key_record(args.out)
        print(
            f"key record written to {args.out} (sha256 {digest}). Keep that SHA-256 beside "
            "the passphrase in the password manager. Commit the record on main; the genesis "
            "entry then names it as <commit>:<path from the repository root>, and straight "
            "after genesis run check-key."
        )
        return 0
    if args.command == "check-key":
        digest = check_genesis_key(args.repo, remote=args.remote)
        print(
            f"the passphrase verifies against the key record the genesis entry pins (sha256 "
            f"{digest}); compare that SHA-256 with the one kept beside the passphrase"
        )
        return 0
    if args.command == "rehearse":
        from .harness import REHEARSAL_BOUNDARY, rehearse_holdout  # the harness imports this

        done = rehearse_holdout(args.repo, store=ArchiveStore(datasets_root()), remote=args.remote,
                                boundary=args.boundary or REHEARSAL_BOUNDARY)
        print(
            f"rehearsal passed: {done['configuration_id']} at {done['commit']} reproduced its "
            f"{done['days']} committed returns across the boundary {done['boundary']}; nothing "
            "was written to the ledger"
        )
        return 0
    check_environment(os.environ, args.repo)
    ledger = GitLedger(args.repo, args.remote)
    if args.command == "candidate":
        verified = ledger.verify()
        issued = gate_ledger(ledger, verified.snapshot)
        for h in HYPOTHESES:
            judged = development(issued, h)
            print(f"{h}: " + ("no candidate (not every registered configuration has a "
                              "selectable run)" if judged is None else
                              f"candidate {judged.candidate.configuration_id}, "
                              f"{judged.criteria}"))
        chosen = holdout_candidate(issued)
        genesis = verified.snapshot.genesis
        named = {
            "preregistration": genesis.body["preregistration"],
            "holdout_candidate": None,
            "closed": issued.closed,
        }
        if chosen is not None:
            # The file list only: the hash to accept is printed by the unseal's refusal,
            # after the full diff it covers, never on its own (review of #65, L-2).
            head = ledger.git.text("rev-parse", "HEAD")
            files, _ = seal_digest(ledger.git, chosen.open_entry.body["commit"], head)
            named["holdout_candidate"] = {
                "configuration_id": chosen.configuration_id,
                "configuration_sha256": chosen.open_entry.body["configuration_sha256"],
                "seal_files_changed_since": files,
                "diff_to_accept": "run seal unseal without --accept-diff in a terminal of "
                                  "your own: it shows the full diff, then the hash that "
                                  "accepts it",
            }
        print(json.dumps(named, indent=2, sort_keys=True))
        return 0
    store = ArchiveStore(datasets_root())
    fetch = HttpFetcher()
    try:
        if args.command == "unseal":
            tag = args.tag
            if tag is None:
                genesis = ledger.snapshot(ledger.local_head()).genesis
                if genesis is None:
                    raise SealRefused("the ledger has no genesis entry, so nothing can be unsealed")
                tag = genesis.body["preregistration"]["tag"]
            request = {
                "preregistration": preregistration_at(ledger.git, args.commit, tag),
                "configuration_sha256": args.configuration_sha256,
                "accepted_diff": args.accept_diff,
            }
            entry = unseal(args.repo, request, store=store, fetch=fetch, remote=args.remote)
            print(f"unseal entry {entry.seq} written: {entry.hash}; holdout files verified")
        else:
            count = fetch_holdout(args.repo, store=store, fetch=fetch, remote=args.remote)
            print(f"{count} holdout files fetched again and verified against the manifest")
    finally:
        fetch.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
