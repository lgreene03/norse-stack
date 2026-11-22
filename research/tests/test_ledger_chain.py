"""The chain on its own: what verification of the file's bytes catches (G1.5).

Threat model T4, T5, T6, T8, T9, T11, T16 and T24. A chain verified on its own
cannot catch a careful editor who recomputes later hashes, nor a truncated tail;
those cases are in test_gitledger.py, against git history and the remote.
"""

import json

import pytest
from ledger_fixtures import Book, lcg_returns
from norse_research.ledger import (
    VerificationError,
    genesis_of,
    make_entry,
    parse_ledger,
    trial_count,
)

N_H1 = 2802


def small_book() -> Book:
    book = Book()
    book.run("H1-01", lcg_returns(1, N_H1))
    book.run("B-H1", lcg_returns(2, N_H1))
    book.run("H1-02", None)
    book.open("C-H1", seed=3)
    return book


def lines(book: Book) -> list[str]:
    return [e.line for e in book.entries]


def join(parts: list[str]) -> bytes:
    return ("\n".join(parts) + "\n").encode()


def mutate(line: str, key: str) -> str:
    """Change one character inside the value of ``key`` in a canonical line."""
    start = line.index(f'"{key}":') + len(key) + 3
    for i in range(start, len(line)):
        c = line[i]
        if c.isdigit():
            return line[:i] + str((int(c) + 1) % 10) + line[i + 1 :]
        if c.isalpha():
            return line[:i] + ("b" if c == "a" else "a") + line[i + 1 :]
    raise AssertionError(f"no character to change in {key}")


def test_a_valid_ledger_verifies():
    book = small_book()
    entries = parse_ledger(book.data)
    assert [e.seq for e in entries] == list(range(len(book.entries)))
    assert entries[-1].hash == book.entries[-1].hash


def test_edited_entry_breaks_the_chain():
    book = small_book()
    original = lines(book)
    for k in range(len(original) - 1):  # the last entry is the tail: see the anchors
        for key in json.loads(original[k]):
            edited = list(original)
            edited[k] = mutate(original[k], key)
            assert edited[k] != original[k]
            with pytest.raises(VerificationError, match=rf"entry {k}\b"):
                parse_ledger(join(edited))


def test_deleted_middle_entry_breaks_the_chain():
    original = lines(small_book())
    for k in range(1, len(original) - 1):
        with pytest.raises(VerificationError):
            parse_ledger(join(original[:k] + original[k + 1 :]))


def test_reordered_entries_break_the_chain():
    original = lines(small_book())
    for k in range(len(original) - 1):
        swapped = list(original)
        swapped[k], swapped[k + 1] = swapped[k + 1], swapped[k]
        with pytest.raises(VerificationError):
            parse_ledger(join(swapped))


def chained(bodies: list[dict]) -> bytes:
    """Lines whose previous hashes are all correct, whatever else is wrong."""
    out, prev = [], "0" * 64
    for body in bodies:
        entry = make_entry({**body, "prev": prev})
        out.append(entry.line)
        prev = entry.hash
    return join(out)


def test_sequence_numbers_are_contiguous():
    bodies = [json.loads(e.line) for e in small_book().entries]
    for seqs in ([0, 1, 3, 4, 5, 6, 7, 8], [0, 1, 1, 3, 4, 5, 6, 7]):
        changed = [{**b, "seq": s} for b, s in zip(bodies, seqs, strict=True)]
        with pytest.raises(VerificationError, match="sequence number"):
            parse_ledger(chained(changed))
    assert parse_ledger(chained(bodies))


def test_no_trial_precedes_the_genesis_entry():
    book = small_book()
    genesis, trial = json.loads(book.entries[0].line), json.loads(book.entries[1].line)
    with pytest.raises(VerificationError, match="precedes the genesis entry"):
        parse_ledger(chained([{**trial, "seq": 0}, {**genesis, "seq": 1}]))
    with pytest.raises(VerificationError, match="precedes the genesis entry"):
        parse_ledger(chained([{**trial, "seq": 0}]))


def test_partial_trailing_line_is_refused():
    data = small_book().data
    with pytest.raises(VerificationError, match="partial line"):
        parse_ledger(data[:-1])
    with pytest.raises(VerificationError, match="partial line"):
        parse_ledger(data + data.split(b"\n")[1][:40])


def test_verifier_refuses_two_entries_with_the_same_previous_hash():
    book = small_book()
    base = book.entries[:3]
    fork_a, fork_b = Book(), Book()
    for fork in (fork_a, fork_b):
        fork.entries = list(base)
    fork_a.open("H1-03")
    fork_b.open("H1-04")
    forked = [*lines(fork_a), fork_b.entries[-1].line]
    with pytest.raises(VerificationError, match="forked"):
        parse_ledger(join(forked))


def test_timestamps_never_decrease_along_the_chain():
    book = Book()  # the genesis entry is timestamped 2026-11-20T00:00:00Z
    opened = book.open("H1-01")
    book.entries.pop()
    body = {k: v for k, v in opened.body.items() if k not in ("seq", "prev", "time")}
    book.add(body, time="2026-11-19T23:59:59Z")
    with pytest.raises(VerificationError, match="earlier than its predecessor"):
        parse_ledger(book.data)


HASH_FIELDS = {
    "genesis": [("preregistration", "files", "docs/preregistration.json"),
                ("manifests", "development"), ("key_record", "sha256")],
    "open": [("configuration_sha256",), ("manifest_sha256",)],
    "result": [("opens",), ("result_sha256",), ("manifest_sha256",)],
}
GIT_ID_FIELDS = {
    "genesis": [("preregistration", "commit"), ("key_record", "commit")],
    "open": [("commit",)],
    "result": [("commit",)],
}


def _set(body: dict, path: tuple, value: str) -> dict:
    body = json.loads(json.dumps(body))
    target = body
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return body


def test_hashes_are_full_sha256():
    book = small_book()
    bodies = [json.loads(e.line) for e in book.entries]
    first = {"genesis": 0, "open": 1, "result": 2}
    checked = 0
    for kind, fields in [*HASH_FIELDS.items(), *GIT_ID_FIELDS.items()]:
        for path in fields:
            k = first[kind]
            value = bodies[k]
            for key in path:
                value = value[key]
            changed = [*bodies[:k], _set(bodies[k], path, value[:8]), *bodies[k + 1 :]]
            with pytest.raises(VerificationError, match=rf"entry {k}: .*full"):
                parse_ledger(chained(changed))
            checked += 1
    # the previous hash too, shortened in the entry that follows genesis
    data = small_book().data.decode().split("\n")
    prev = json.loads(data[1])["prev"]
    data[1] = data[1].replace(prev, prev[:12])
    with pytest.raises(VerificationError, match="entry 1: prev is not a full SHA-256"):
        parse_ledger("\n".join(data).encode())
    assert checked == 12


def test_entry_with_duplicate_keys_is_refused():
    book = small_book()
    last = book.entries[-1].line
    for duplicate in (',"seq":99}', ',"label":"x"}', ',"kind":"benchmark"}'):
        twice = [*lines(book)[:-1], last[:-1] + duplicate]
        with pytest.raises(VerificationError, match="appears twice"):
            parse_ledger(join(twice))


def test_non_canonical_entry_is_refused():
    book = small_book()
    last = book.entries[-1].line
    body = json.loads(last)
    variants = [
        json.dumps(body, sort_keys=True),  # whitespace
        json.dumps(dict(reversed(list(body.items()))), separators=(",", ":")),  # key order
        last.replace('"seed":3', '"seed":3.0'),  # number spelling
        last.replace('"seed":3', '"seed":3e0'),
    ]
    for variant in variants:
        assert variant != last
        with pytest.raises(VerificationError, match=r"canonical|differs"):
            parse_ledger(join([*lines(book)[:-1], variant]))


def test_preregistration_change_after_the_first_trial_is_refused():
    # Before any other entry a superseding genesis naming a new tag is allowed, and
    # it governs (section 16 of docs/PREREGISTRATION.md).
    book = Book()
    book.add(book.genesis_body("prereg-v2"))
    assert genesis_of(parse_ledger(book.data)).body["preregistration"]["tag"] == "prereg-v2"
    # The same tag twice is not a new registration.
    again = Book()
    again.add(again.genesis_body("prereg-v1"))
    with pytest.raises(VerificationError, match="new tag"):
        parse_ledger(again.data)
    # After the first entry of any kind (section 15, item 13), no new genesis.
    for first in ("trial", "benchmark", "control", "result"):
        book = Book()
        if first == "trial":
            book.open("H1-01")
        elif first == "benchmark":
            book.open("B-H2")
        elif first == "control":
            book.open("C-H2", seed=0)
        else:
            book.run("H1-01", None)
        book.add(book.genesis_body("prereg-v2"))
        with pytest.raises(VerificationError, match="closed the registration"):
            parse_ledger(book.data)


def test_n_counts_entries_not_distinct_configurations():
    book = Book()
    book.run("H1-01", lcg_returns(1, N_H1))
    fix = {"defect": "synthetic", "fix_commit": "9" * 40, "section": "6"}
    book.run("H1-01", lcg_returns(1, N_H1), rerun=fix)  # the same configuration again
    book.run("H1-02", None)  # failed
    book.open("H1-03")  # never finished
    book.run("B-H1", lcg_returns(2, N_H1))
    book.open("C-H1", seed=0)
    entries = parse_ledger(book.data)
    trials = [e for e in entries if e.type == "open" and e.body["kind"] == "trial"]
    assert len({e.body["configuration_sha256"] for e in trials}) == 3
    assert trials[0].body["configuration_sha256"] == trials[1].body["configuration_sha256"]
    assert trial_count(entries) == 4


def with_crossed_gaps(book: Book, cid: str, values, gaps) -> None:
    """A result entry for ``cid`` whose crossed gaps are ``gaps``, chained as the harness
    chains it."""
    opened = book.open(cid)
    body = {k: v for k, v in book.result(opened, values).body.items()
            if k not in ("seq", "prev", "time")}
    book.entries.pop()
    book.add({**body, "crossed_gaps": gaps})


GAP = ["spot/klines/GUSDT/4h", "2025-12-04 08:00", "2025-12-04 16:00", 1]


def test_replay_accepts_crossed_gaps_only_in_the_shape_the_harness_writes():
    """Section 5, Gaps: a run's ledger entry lists the recorded gaps it crossed. The
    harness checks them against the registered manifest; replay checks the shape."""
    book = Book()
    later = ["spot/klines/XUSDT/1d", "2025-03-01 00:00", "2025-03-03 00:00", 1]
    with_crossed_gaps(book, "H1-01", lcg_returns(1, N_H1), [GAP, later])
    assert parse_ledger(book.data)[-1].body["crossed_gaps"] == [GAP, later]
    malformed = [
        ({"gap": GAP}, "is not a list"),
        ([GAP[:3]], "not \\[series, after, before, missing\\]"),
        ([["not/a/series", *GAP[1:]]], "not \\[series"),
        ([[GAP[0], "2025-12-04T08:00", *GAP[2:]]], "not \\[series"),
        ([[GAP[0], GAP[2], GAP[1], 1]], "not \\[series"),
        ([[*GAP[:3], -1]], "not \\[series"),
        ([[*GAP[:3], True]], "not \\[series"),
        ([[*GAP[:3], "1"]], "not \\[series"),
        ([later, GAP], "not sorted without repeats"),
        ([GAP, GAP], "not sorted without repeats"),
    ]
    for gaps, message in malformed:
        bad = Book()
        with_crossed_gaps(bad, "H1-01", lcg_returns(1, N_H1), gaps)
        with pytest.raises(VerificationError, match=message):
            parse_ledger(bad.data)
    failed = Book()
    with_crossed_gaps(failed, "H1-01", None, [GAP])
    with pytest.raises(VerificationError, match="a failed run lists no crossed gaps"):
        parse_ledger(failed.data)


def test_replay_refuses_a_genesis_whose_harness_paths_leave_part_of_the_package_out():
    """Security review round 3: the harness digest covers the whole package, the
    simulation engine strategies import included."""
    parts = {"harness": ["research/norse_research/sim"], "H1": ["strategies/h1"],
             "H2": ["strategies/h2"]}
    with pytest.raises(VerificationError, match="does not cover all of research/norse_research"):
        parse_ledger(Book(tree_paths=parts).data)
    wider = {**parts, "harness": ["research"]}
    assert genesis_of(parse_ledger(Book(tree_paths=wider).data)) is not None
