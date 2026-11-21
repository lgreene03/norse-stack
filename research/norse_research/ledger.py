"""The hash-chained trial ledger: entries, verification and N (G1.5).

The ledger is a JSONL file, ``ledger/trials.jsonl``, on its own git branch
(``gitledger``), beside the committed daily return series of every completed run
under ``ledger/returns/``. This module is pure: it parses and verifies the file's
bytes and counts trials. Anchoring the chain to git history, the remote and the tags
is ``gitledger``'s, because a chain on its own proves only that it agrees with
itself (docs/research/ledger-threat-model.md, principle 1).

Each line is one entry in canonical JSON (``canonical``): a line that differs from
its canonical form, or holds a key twice, is refused (T4). An entry's hash is the
SHA-256 of its line without the newline. Every entry holds:

* ``seq``, its position from 0, contiguous (T6);
* ``prev``, the previous entry's hash (64 zeros for the first);
* ``type``: ``genesis``, ``open`` or ``result``;
* ``time``, a UTC timestamp that never decreases along the chain (T11). It is
  informative only: nothing that decides trust reads it.

**Genesis** pins the registration (principle 4, section 16 of PREREGISTRATION.md):
the ``prereg-*`` tag it names, its commit and the SHA-256 of PREREGISTRATION.md and
preregistration.json at that commit, the manifest hashes, the period constants, the
out-of-sample windows, the budgets, the harness and strategy paths whose files
every run records, the holdout key record G1.6 commits, the canonical remote URL with
the ledger and code branches, and the strategy module and function that runs each
registered configuration, benchmark and control. The ledger's first
entry is a genesis entry. A later genesis may supersede it, naming a new tag, only
until the first entry of any other type; after that the registration is closed and a
genesis entry fails verification (section 16; section 15, item 13).

**Open** is written, committed and pushed before a run computes anything (principle
2, T8). It holds the run's kind (from its registered configuration, never a label),
the resolved configuration and its hash, the commit, a digest of the files under
the registered paths, the dataset manifest hash, the cost basis, the window, any rerun record or
reserve reason, and the interpreter. **Result** follows it, naming the open entry's
hash, with the status, a summary, and for a completed run the SHA-256 of its
committed return series (T7). It repeats the open entry's configuration hash,
commit, manifest hash and cost basis, so each result entry holds every field G1.5
names on its own.

**Unseal** (G1.6) is written at most once, before any holdout file is fetched again and
before any holdout run, and it closes development (decision D21): N, V and the holdout
candidate are computed on the entries before it, and no trial, benchmark or control
run may be opened after it; the result of one opened before it may land after it, and
is accepted and ignored. It names the commit and file hashes of the pre-registration tag
the genesis entry names, the holdout candidate's configuration id, hypothesis and
configuration hash, the open and result entries of its selectable run, the commit the
code is frozen at, the SHA-256 of the data loading and seal diff the owner accepted
since the candidate's run, and the hashes of the holdout manifest and the key record
genesis pins (section 11, item 4),
and its ``proof``: the scrypt output of the owner's passphrase, whose SHA-256 the key
record holds (a commit-reveal; security review H1). Only someone who knows the
passphrase can produce it, and ``gitledger`` checks it against the pinned record when
the entry is written and on every replay. A second unseal entry fails verification
here; its proof, its accepted diff and its candidate are checked by ``gitledger`` and
``gate``.

**Attempt** and **attempt close** (G1.6; security review H2, M1) bracket every unsealed
run of the holdout: the attempt is written before the strategy is first given the
unseal, and its close records the outcome (``returned``, ``failed``, or ``abandoned``
when a later run closes an attempt a killed process left open). Attempts run one at a
time, a completed holdout result needs its own attempt closed as returned, and there is
only one. The unseal records the commit item 6's rule was applied to, and ``rules``
holds every holdout run to exactly that commit (security review round 2, N1).

**N** is the number of open entries of kind ``trial``: every trial opened counts
once, whether it completes, fails or never finishes, and reruns count again. It is
never deduplicated by configuration hash (section 9; T1).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from itertools import pairwise
from typing import Any

from .canonical import (
    GIT_OBJECT_ID,
    SHA256_HEX,
    CanonicalError,
    dumps,
    loads_canonical,
    sha256_hex,
    value_hash,
)
from .formats import DatasetSpec
from .registration import (
    HOLDOUT,
    HYPOTHESES,
    KINDS,
    PREREGISTRATION_FILES,
    TRIAL,
)

UNSEAL = "unseal"
# A holdout attempt (G1.6; security review H2): written before every unsealed run, and
# closed after it with its outcome.
ATTEMPT = "attempt"
ATTEMPT_CLOSE = "attempt_close"
RETURNED = "returned"
ABANDONED = "abandoned"

FORMAT = "norse-stack/trial-ledger/v1"
LEDGER_BRANCH = "ledger"
CODE_BRANCH = "main"
LEDGER_PATH = "ledger/trials.jsonl"
RETURNS_DIR = "ledger/returns"
ZERO_HASH = "0" * 64

GENESIS = "genesis"
OPEN = "open"
RESULT = "result"
COMPLETED = "completed"
FAILED = "failed"

TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_RETURNS_FILE = re.compile(r"ledger/returns/([0-9a-f]{64})\.csv")
# A gap's ends as the manifests record them (manifest.gap_record).
_GAP_TIME = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")
# The whole package the harness digest must cover (security review round 3).
HARNESS_PACKAGE = "research/norse_research"

_COMMON = frozenset({"seq", "prev", "type", "time"})
_KEYS = {
    GENESIS: _COMMON
    | {"format", "preregistration", "manifests", "periods", "windows", "budgets",
       "tree_paths", "key_record", "remote", "strategies"},
    OPEN: _COMMON
    | {"kind", "hypothesis", "configuration_id", "configuration", "configuration_sha256",
       "commit", "trees", "manifest_sha256", "cost_basis", "window", "label", "rerun",
       "reserve_reason", "strategy", "environment"},
    RESULT: _COMMON
    | {"opens", "open_seq", "status", "configuration_sha256", "commit", "manifest_sha256",
       "cost_basis", "result", "result_sha256", "returns_path", "crossed_gaps"},
    UNSEAL: _COMMON
    | {"preregistration", "configuration_id", "hypothesis", "configuration_sha256",
       "candidate", "accepted_diff", "holdout_manifest_sha256", "key_record_sha256", "proof",
       "commit"},
    ATTEMPT: _COMMON | {"opens", "open_seq", "commit"},
    ATTEMPT_CLOSE: _COMMON | {"attempt", "attempt_seq", "outcome"},
}
# Fields a result entry copies from the open entry it closes.
_COPIED = ("configuration_sha256", "commit", "manifest_sha256", "cost_basis")


class LedgerError(Exception):
    """Base class for the ledger's errors."""


class VerificationError(LedgerError):
    """The ledger does not verify."""


def utc_now() -> str:
    """The local clock, for timestamps only. Informative: no trust decision reads it."""
    return datetime.now(UTC).strftime(TIME_FORMAT)


@dataclass(frozen=True)
class Entry:
    line: str  # canonical JSON, no newline
    hash: str  # SHA-256 of the line
    body: dict

    @property
    def seq(self) -> int:
        return self.body["seq"]

    @property
    def type(self) -> str:
        return self.body["type"]

    def get(self, key: str) -> Any:
        return self.body.get(key)


def entry_line(body: Mapping) -> str:
    return dumps(body)


def make_entry(body: Mapping) -> Entry:
    line = entry_line(body)
    return Entry(line, sha256_hex(line.encode("ascii")), loads_canonical(line))


# --- schema ----------------------------------------------------------------------------


def _fail(seq: int, message: str) -> VerificationError:
    return VerificationError(f"entry {seq}: {message}")


def _sha(seq: int, name: str, value: Any) -> None:
    if not isinstance(value, str) or not SHA256_HEX.fullmatch(value):
        raise _fail(seq, f"{name} is not a full SHA-256 (64 lowercase hex characters)")


def _git_id(seq: int, name: str, value: Any) -> None:
    if not isinstance(value, str) or not GIT_OBJECT_ID.fullmatch(value):
        raise _fail(seq, f"{name} is not a full git object id")


def _mapping(seq: int, name: str, value: Any, keys: set | frozenset | None = None) -> dict:
    if not isinstance(value, dict):
        raise _fail(seq, f"{name} is not an object")
    if keys is not None and set(value) != set(keys):
        raise _fail(seq, f"{name} holds {sorted(value)}, expected {sorted(keys)}")
    return value


def _check_preregistration(seq: int, body: dict, what: str) -> None:
    prereg = _mapping(seq, "preregistration", body["preregistration"], {"tag", "commit", "files"})
    if not isinstance(prereg["tag"], str) or not prereg["tag"].startswith("prereg-"):
        raise _fail(seq, f"the {what} entry names no prereg-* tag")
    _git_id(seq, "preregistration.commit", prereg["commit"])
    files = _mapping(seq, "preregistration.files", prereg["files"], set(PREREGISTRATION_FILES))
    for name, digest in files.items():
        _sha(seq, f"preregistration.files[{name}]", digest)


def _check_genesis(seq: int, body: dict) -> None:
    if body["format"] != FORMAT:
        raise _fail(seq, f"format {body['format']!r}, expected {FORMAT}")
    _check_preregistration(seq, body, "genesis")
    manifests = _mapping(seq, "manifests", body["manifests"], {"development", "holdout"})
    for name, digest in manifests.items():
        _sha(seq, f"manifests.{name}", digest)
    _mapping(
        seq, "periods", body["periods"],
        {"out_of_sample_start", "holdout_start", "last_development_month"},
    )
    windows = _mapping(seq, "windows", body["windows"], set(HYPOTHESES))
    for h, window in windows.items():
        _mapping(seq, f"windows.{h}", window, {"first_return_day", "last_return_day", "days"})
    _mapping(seq, "budgets", body["budgets"], {*HYPOTHESES, "reserve", "total"})
    paths = _mapping(seq, "tree_paths", body["tree_paths"], {"harness", *HYPOTHESES})
    for group, listed in paths.items():
        if not isinstance(listed, list) or not all(isinstance(p, str) and p for p in listed):
            raise _fail(seq, f"tree_paths.{group} is not a list of paths")
    if not any(HARNESS_PACKAGE == p or HARNESS_PACKAGE.startswith(p.rstrip("/") + "/")
               for p in paths["harness"]):
        raise _fail(seq, f"tree_paths.harness does not cover all of {HARNESS_PACKAGE}")
    key = _mapping(seq, "key_record", body["key_record"], {"commit", "path", "sha256"})
    _git_id(seq, "key_record.commit", key["commit"])
    _sha(seq, "key_record.sha256", key["sha256"])
    remote = _mapping(seq, "remote", body["remote"], {"url", "branch", "code_branch"})
    if remote["branch"] != LEDGER_BRANCH or remote["code_branch"] != CODE_BRANCH:
        raise _fail(seq, f"the ledger lives on {LEDGER_BRANCH} and the code on {CODE_BRANCH}")
    if not isinstance(remote["url"], str) or not remote["url"].strip():
        raise _fail(seq, "the genesis entry pins no remote URL")
    strategies = _mapping(seq, "strategies", body["strategies"])
    for cid, pin in strategies.items():
        pin = _mapping(seq, f"strategies[{cid}]", pin, {"path", "function"})
        if not (isinstance(pin["path"], str) and pin["path"].endswith(".py")
                and isinstance(pin["function"], str) and pin["function"].isidentifier()):
            raise _fail(seq, f"strategies[{cid}] does not name a Python file and function")


def _check_open(seq: int, body: dict) -> None:
    if body["kind"] not in KINDS:
        raise _fail(seq, f"unknown kind {body['kind']!r}")
    if body["hypothesis"] not in HYPOTHESES:
        raise _fail(seq, f"unknown hypothesis {body['hypothesis']!r}")
    _sha(seq, "configuration_sha256", body["configuration_sha256"])
    _sha(seq, "manifest_sha256", body["manifest_sha256"])
    _git_id(seq, "commit", body["commit"])
    config = _mapping(seq, "configuration", body["configuration"])
    if value_hash(config) != body["configuration_sha256"]:
        raise _fail(seq, "configuration_sha256 is not the hash of the configuration")
    for name, key in (("kind", "kind"), ("configuration_id", "id"), ("hypothesis", "hypothesis"),
                      ("cost_basis", "costs"), ("window", "window"), ("strategy", "strategy")):
        if config.get(key) != body[name]:
            raise _fail(seq, f"{name} differs from the configuration's {key}")
    trees = _mapping(seq, "trees", body["trees"], {"harness", *HYPOTHESES})
    for group, digest in trees.items():
        if digest is not None:
            _sha(seq, f"trees[{group}]", digest)
    if body["rerun"] is not None:
        rerun = _mapping(seq, "rerun", body["rerun"], {"defect", "fix_commit", "section"})
        _git_id(seq, "rerun.fix_commit", rerun["fix_commit"])
    if body["kind"] == HOLDOUT and body["rerun"] is not None:
        raise _fail(seq, "the holdout run carries no rerun record")
    if body["kind"] != TRIAL and body["reserve_reason"] is not None:
        raise _fail(seq, "only a trial carries a reserve reason")


def _check_result(seq: int, body: dict) -> None:
    _sha(seq, "opens", body["opens"])
    _sha(seq, "configuration_sha256", body["configuration_sha256"])
    _sha(seq, "manifest_sha256", body["manifest_sha256"])
    _git_id(seq, "commit", body["commit"])
    _mapping(seq, "result", body["result"])
    if body["status"] == COMPLETED:
        _sha(seq, "result_sha256", body["result_sha256"])
        if body["returns_path"] != returns_path(body["result_sha256"]):
            raise _fail(seq, "returns_path does not name the result's return series")
    elif body["status"] == FAILED:
        if body["result_sha256"] is not None or body["returns_path"] is not None:
            raise _fail(seq, "a failed run has no return series")
        if body["crossed_gaps"] != []:
            raise _fail(seq, "a failed run lists no crossed gaps")
    else:
        raise _fail(seq, f"unknown status {body['status']!r}")
    _check_crossed_gaps(seq, body["crossed_gaps"])


def _check_crossed_gaps(seq: int, gaps: Any) -> None:
    """Section 5, Gaps: "a gap the committed manifest records is crossed and listed in
    the run's ledger entry". Only the shape the harness writes: a sorted list, without
    repeats, of [series, after, before, missing], the times as the manifests record
    them. The harness checks each against the registered manifest before writing it."""
    if not isinstance(gaps, list):
        raise _fail(seq, "crossed_gaps is not a list")
    for gap in gaps:
        if not (
            isinstance(gap, list) and len(gap) == 4
            and isinstance(gap[0], str) and _is_series(gap[0])
            and all(isinstance(t, str) and _GAP_TIME.fullmatch(t) for t in gap[1:3])
            and gap[1] < gap[2]
            and isinstance(gap[3], int) and not isinstance(gap[3], bool) and gap[3] >= 0
        ):
            raise _fail(seq, f"crossed_gaps holds {gap!r}, not [series, after, before, missing]")
    if any(b <= a for a, b in pairwise(gaps)):
        raise _fail(seq, "crossed_gaps is not sorted without repeats")


def _is_series(name: str) -> bool:
    try:
        return DatasetSpec.from_name(name).name == name
    except ValueError:
        return False


def _check_unseal(seq: int, body: dict) -> None:
    _check_preregistration(seq, body, "unseal")
    if body["hypothesis"] not in HYPOTHESES:
        raise _fail(seq, f"unknown hypothesis {body['hypothesis']!r}")
    if not isinstance(body["configuration_id"], str) or not body["configuration_id"]:
        raise _fail(seq, "the unseal names no configuration")
    _sha(seq, "configuration_sha256", body["configuration_sha256"])
    candidate = _mapping(seq, "candidate", body["candidate"], {"open", "result"})
    _sha(seq, "candidate.open", candidate["open"])
    _sha(seq, "candidate.result", candidate["result"])
    _sha(seq, "accepted_diff", body["accepted_diff"])
    _sha(seq, "holdout_manifest_sha256", body["holdout_manifest_sha256"])
    _sha(seq, "key_record_sha256", body["key_record_sha256"])
    proof = body["proof"]
    if not isinstance(proof, str) or not re.fullmatch(r"(?:[0-9a-f]{2}){32,}", proof):
        raise _fail(seq, "the unseal's proof is not a scrypt output (lower-case hex, 32 bytes or more)")
    _git_id(seq, "commit", body["commit"])


def _check_attempt(seq: int, body: dict) -> None:
    _sha(seq, "opens", body["opens"])
    _git_id(seq, "commit", body["commit"])
    if type(body["open_seq"]) is not int:
        raise _fail(seq, "open_seq is not an entry number")


def _check_attempt_close(seq: int, body: dict) -> None:
    _sha(seq, "attempt", body["attempt"])
    if type(body["attempt_seq"]) is not int:
        raise _fail(seq, "attempt_seq is not an entry number")
    if body["outcome"] not in (RETURNED, FAILED, ABANDONED):
        raise _fail(seq, f"unknown attempt outcome {body['outcome']!r}")


_CHECKS = {
    GENESIS: _check_genesis, OPEN: _check_open, RESULT: _check_result, UNSEAL: _check_unseal,
    ATTEMPT: _check_attempt, ATTEMPT_CLOSE: _check_attempt_close,
}


def _closes(entries: Sequence[Entry], attempt: Entry) -> Entry | None:
    return next(
        (e for e in entries if e.type == ATTEMPT_CLOSE and e.body["attempt"] == attempt.hash), None
    )


def _holdout_completed(entries: Sequence[Entry]) -> bool:
    held = {e.hash for e in entries if e.type == OPEN and e.body["kind"] == HOLDOUT}
    return any(e.type == RESULT and e.body["opens"] in held and e.body["status"] == COMPLETED
               for e in entries)


def _attempt_error(body: dict, entries: list[Entry], hashes: dict[str, int]) -> str | None:
    """Why an attempt, an attempt's close or a holdout result does not follow from the
    entries before it, or None (security review H2 and M1). Attempts are strictly one
    at a time: each is closed before the next opens. A completed holdout result needs
    its own attempt closed with outcome ``returned``, and there is at most one."""
    if body["type"] == ATTEMPT:
        opened = entries[hashes[body["opens"]]] if body["opens"] in hashes else None
        if opened is None or opened.type != OPEN or opened.body["kind"] != HOLDOUT:
            return "an attempt names no earlier holdout run"
        if opened.seq != body["open_seq"] or opened.body["commit"] != body["commit"]:
            return "an attempt's open_seq or commit is not its holdout run's"
        if any(e.type == RESULT and e.body["opens"] == opened.hash for e in entries):
            return "an attempt for a holdout run that already has its result"
        attempts = [e for e in entries if e.type == ATTEMPT]
        if any(a.body["opens"] == opened.hash for a in attempts):
            return "a second attempt within one holdout run"
        unclosed = [a.seq for a in attempts if _closes(entries, a) is None]
        if unclosed:
            return (
                f"an attempt while attempt {unclosed[0]} is open: attempts run one at a time "
                "(security review M1)"
            )
        if _holdout_completed(entries):
            return "an attempt after the holdout result spent the seal (section 11, item 6)"
        return None
    if body["type"] == ATTEMPT_CLOSE:
        attempt = entries[hashes[body["attempt"]]] if body["attempt"] in hashes else None
        if attempt is None or attempt.type != ATTEMPT or attempt.seq != body["attempt_seq"]:
            return "an attempt's close names no earlier attempt"
        if _closes(entries, attempt) is not None:
            return f"attempt {attempt.seq} is closed twice"
        return None
    # A result closing a holdout run.
    if body["status"] != COMPLETED:
        return None
    if _holdout_completed(entries):
        return "a second completed holdout result: one result spends the seal (section 11, item 6)"
    attempt = next(
        (e for e in entries if e.type == ATTEMPT and e.body["opens"] == body["opens"]), None
    )
    closed = _closes(entries, attempt) if attempt is not None else None
    if closed is None or closed.body["outcome"] != RETURNED:
        return "a completed holdout result needs its own attempt, closed as returned"
    return None


def _unseal_error(body: dict, entries: list[Entry], hashes: dict[str, int]) -> str | None:
    """Why an unseal entry is not one the seal would write over the entries before it,
    or None. Its proof, its accepted diff and its candidate need git and the gate."""
    if any(e.type == UNSEAL for e in entries):
        raise VerificationError(
            "a second unseal entry: the holdout is unsealed once (section 11, item 4; T23)"
        )
    genesis = genesis_of(entries)
    if genesis is None:
        return "an unseal precedes the genesis entry"
    if body["preregistration"] != genesis.body["preregistration"]:
        return "it names a pre-registration other than the genesis entry's (T24)"
    if body["key_record_sha256"] != genesis.body["key_record"]["sha256"]:
        return "it names a key record other than the one genesis pins (T25)"
    if body["holdout_manifest_sha256"] != genesis.body["manifests"]["holdout"]:
        return "it names a holdout manifest other than the registered one"
    opened = hashes.get(body["candidate"]["open"])
    closed = hashes.get(body["candidate"]["result"])
    if opened is None or closed is None:
        return "its candidate's open or result entry is not earlier in the ledger"
    opened, closed = entries[opened], entries[closed]
    if opened.type != OPEN or opened.body["kind"] != TRIAL or any(
        opened.body[k] != body[k] for k in ("configuration_id", "hypothesis", "configuration_sha256")
    ):
        return "its candidate is not a trial entry of the configuration it names"
    if closed.type != RESULT or closed.body["opens"] != opened.hash or closed.body["status"] != (
        COMPLETED
    ):
        return "its candidate's result is not the completed result of that trial"
    return None


# --- verification ----------------------------------------------------------------------


def parse_ledger(data: bytes) -> tuple[Entry, ...]:
    """Every entry of a ledger file, after verifying the chain on its own.

    This catches an edited, deleted, reordered, forked, duplicated or partly
    written entry; it cannot catch a careful editor who recomputes every later hash,
    nor a truncated tail. Those need the anchors in ``gitledger`` (T4, T5).
    """
    if not data:
        return ()
    if not data.endswith(b"\n"):
        raise VerificationError("the ledger ends in a partial line (an append did not finish)")
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        raise VerificationError("the ledger is not ASCII, so it is not canonical") from None
    entries: list[Entry] = []
    seen_prev: dict[str, int] = {}
    hashes: dict[str, int] = {}
    closed_at: int | None = None
    genesis_tags: set[str] = set()
    last_time = ""
    for index, line in enumerate(text[:-1].split("\n")):
        if not line:
            raise _fail(index, "an empty line")
        try:
            body = loads_canonical(line)
        except CanonicalError as exc:
            raise _fail(index, str(exc)) from None
        if not isinstance(body, dict) or body.get("type") not in _KEYS:
            raise _fail(index, "not a ledger entry")
        if set(body) != _KEYS[body["type"]]:
            raise _fail(index, f"a {body['type']} entry holds {sorted(body)}")
        _sha(index, "prev", body["prev"])
        if body["prev"] in seen_prev:
            raise _fail(
                index,
                f"shares its previous hash with entry {seen_prev[body['prev']]}: the "
                "ledger has forked",
            )
        seen_prev[body["prev"]] = index
        if body["seq"] != index:
            raise _fail(index, f"sequence number {body['seq']!r}; sequence numbers are contiguous")
        want_prev = entries[-1].hash if entries else ZERO_HASH
        if body["prev"] != want_prev:
            raise _fail(
                index,
                f"its previous hash is not the hash of entry {index - 1}: entry {index - 1} "
                "was changed, or entries were removed or reordered",
            )
        if not isinstance(body["time"], str) or not _TIME.fullmatch(body["time"]):
            raise _fail(index, "time is not a UTC timestamp")
        if body["time"] < last_time:
            raise _fail(index, "its timestamp is earlier than its predecessor's")
        last_time = body["time"]
        _CHECKS[body["type"]](index, body)

        if body["type"] == GENESIS:
            if closed_at is not None:
                raise _fail(
                    index,
                    f"a new genesis after entry {closed_at} closed the registration "
                    "(section 16 of docs/PREREGISTRATION.md)",
                )
            tag = body["preregistration"]["tag"]
            if tag in genesis_tags:
                raise _fail(index, f"a superseding genesis must name a new tag, not {tag}")
            if entries and body["remote"] != entries[0].body["remote"]:
                raise _fail(index, "a superseding genesis may not move the pinned remote")
            genesis_tags.add(tag)
        else:
            if not entries:
                raise _fail(index, f"a {body['type']} entry precedes the genesis entry")
            if closed_at is None:
                closed_at = index
        if body["type"] == RESULT:
            opened = hashes.get(body["opens"])
            if opened is None or entries[opened].type != OPEN:
                raise _fail(index, "closes no earlier open entry")
            if opened != body["open_seq"]:
                raise _fail(index, "open_seq does not match the entry it closes")
            for key in _COPIED:
                if body[key] != entries[opened].body[key]:
                    raise _fail(index, f"its {key} differs from the open entry's")
        if body["type"] == UNSEAL:
            problem = _unseal_error(body, entries, hashes)
            if problem:
                raise _fail(index, problem)
        if body["type"] in (ATTEMPT, ATTEMPT_CLOSE) or (
            body["type"] == RESULT and entries[hashes[body["opens"]]].body["kind"] == HOLDOUT
        ):
            problem = _attempt_error(body, entries, hashes)
            if problem:
                raise _fail(index, problem)
        entry = Entry(line, sha256_hex(line.encode("ascii")), body)
        hashes[entry.hash] = index
        entries.append(entry)
    results = [e.body["opens"] for e in entries if e.type == RESULT]
    if len(results) != len(set(results)):
        raise VerificationError("an open entry is closed by two result entries")
    return tuple(entries)


def genesis_of(entries: Sequence[Entry]) -> Entry | None:
    """The governing genesis entry: the last of the leading genesis entries."""
    found = None
    for entry in entries:
        if entry.type != GENESIS:
            break
        found = entry
    return found


def trial_count(entries: Sequence[Entry]) -> int:
    """N: the open entries of kind trial, never deduplicated (section 9)."""
    return sum(1 for e in entries if e.type == OPEN and e.body["kind"] == TRIAL)


def unseal_of(entries: Sequence[Entry]) -> Entry | None:
    """The ledger's unseal entry, if it holds one (it holds at most one)."""
    return next((e for e in entries if e.type == UNSEAL), None)


# --- return series ---------------------------------------------------------------------


def returns_path(result_sha256: str) -> str:
    return f"{RETURNS_DIR}/{result_sha256}.csv"


def _number(value: float) -> str:
    return repr(float(value) + 0.0)  # + 0.0 turns -0.0 into 0.0


def encode_returns(series: Sequence[tuple[str, float]]) -> bytes:
    """A daily return series as committed: ``day,return`` lines, each return in
    Python's shortest round-trip spelling. Deterministic, so identical inputs give
    an identical result hash (T7)."""
    lines = ["day,return"]
    for day, value in series:
        if not isinstance(day, str) or not _DAY.fullmatch(day):
            raise ValueError(f"{day!r} is not a day")
        if not math.isfinite(float(value)):
            raise ValueError(f"the return of {day} is not finite")
        lines.append(f"{day},{_number(value)}")
    return ("\n".join(lines) + "\n").encode("ascii")


def decode_returns(data: bytes) -> list[tuple[str, float]]:
    text = data.decode("ascii")
    lines = text.split("\n")
    if lines[0] != "day,return" or lines[-1] != "":
        raise VerificationError("a return series has no header or no final newline")
    series = []
    for line in lines[1:-1]:
        day, _, value = line.partition(",")
        series.append((day, float(value)))
    if encode_returns(series) != data:
        raise VerificationError("a return series is not in its committed form")
    return series


def consecutive_days(series: Sequence[tuple[str, float]]) -> bool:
    days = [date.fromisoformat(d) for d, _ in series]
    return all((b - a).days == 1 for a, b in pairwise(days))


# --- snapshot --------------------------------------------------------------------------


@dataclass(frozen=True)
class LedgerSnapshot:
    """A verified ledger with its committed return series, at one commit."""

    entries: tuple[Entry, ...]
    series_files: Mapping[str, bytes] = field(default_factory=dict)
    commit: str | None = None

    @classmethod
    def from_bytes(
        cls, data: bytes, series_files: Mapping[str, bytes] | None = None,
        commit: str | None = None,
    ) -> LedgerSnapshot:
        snapshot = cls(parse_ledger(data), dict(series_files or {}), commit)
        snapshot.check_series()
        return snapshot

    @property
    def data(self) -> bytes:
        return "".join(e.line + "\n" for e in self.entries).encode("ascii")

    @property
    def genesis(self) -> Entry | None:
        return genesis_of(self.entries)

    @property
    def head_hash(self) -> str | None:
        return self.entries[-1].hash if self.entries else None

    def opens(self, kind: str | None = None) -> list[Entry]:
        return [e for e in self.entries if e.type == OPEN and (kind is None or e.body["kind"] == kind)]

    def result_of(self, opened: Entry) -> Entry | None:
        for entry in self.entries:
            if entry.type == RESULT and entry.body["opens"] == opened.hash:
                return entry
        return None

    def trial_count(self) -> int:
        return trial_count(self.entries)

    def returns_of(self, result: Entry) -> list[tuple[str, float]]:
        digest = result.body["result_sha256"]
        data = self.series_files.get(returns_path(digest))
        if data is None:
            raise VerificationError(f"entry {result.seq}: its return series is not committed")
        if sha256_hex(data) != digest:
            raise VerificationError(
                f"entry {result.seq}: the committed return series does not match its hash"
            )
        return decode_returns(data)

    def check_series(self) -> None:
        """Every completed result's committed series matches its hash (T7), and no
        series file is committed that no result names."""
        named = set()
        for entry in self.entries:
            if entry.type == RESULT and entry.body["status"] == COMPLETED:
                self.returns_of(entry)
                named.add(entry.body["returns_path"])
        for path in self.series_files:
            if not _RETURNS_FILE.fullmatch(path) or path not in named:
                raise VerificationError(f"{path} is committed beside the ledger but no result names it")
