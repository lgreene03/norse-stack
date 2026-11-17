# The holdout seal (G1.6)

Owner: technical; security reviews. Built 2026-11-16 against section 11 of the signed
pre-registration ([PREREGISTRATION.md](../PREREGISTRATION.md), tagged `prereg-v1`), decisions
D4 and D17 ([DECISIONS.md](../DECISIONS.md)), the threat model's T20 to T25
([ledger-threat-model.md](ledger-threat-model.md)) and the trial ledger it extends
([trial-ledger.md](trial-ledger.md)). The code is `research/norse_research/seal.py`, with the
holdout run in `harness.py`, the development criteria and the holdout candidate in `gate.py`,
and the replay of the seal's rules in `ledger.py`, `rules.py` and `gitledger.py`. Standard
library only. No key record has been created and no unseal written: both are the owner's.

## What the seal is

The holdout, 2026-01-01 to 2026-10-31, is public data, so the seal cannot be secrecy. It is
tamper evidence (D4): holdout rows reach research code in exactly one place, the holdout run
the ledger ties to the single unseal entry, and that entry is written only after someone types
the owner's passphrase. Everywhere else the package refuses them. The loader returns a row on or
after 2026-01-01 only to a loader given the frozen holdout manifest, inside the unsealed run that
only the runner enters, inside an open ledger entry. The archive store, the manifest reader and
the coverage reader refuse a holdout month's file unless the manifest build and verify code
passes its audit flag, or the read happens inside that same unsealed run. The holdout files stay
off disk until the unseal (D17); no tool fetches them before it.

## Creating the key record (the owner, once, before the genesis entry)

The passphrase is the owner's alone and never an agent's (D4). Generate it in the password
manager, at least six words from its word list, and keep it there, out of reach of any
command-line integration an agent could drive. Then, in a terminal of your own, never one in which
an agent session is working, run from `research/`:

```bash
python -m norse_research.seal key-record --out keys/holdout-key-record.json
```

It asks for the passphrase twice, without echo, and refuses if the two differ or the passphrase
has fewer than six words or 20 characters (a word is a run of letters, whatever separates them;
the code cannot tell generated words from chosen ones, so generating them is the owner's part).
It accepts the passphrase only from an interactive terminal: no option, environment variable or
file can carry it. It derives scrypt at n 2^17, r 8, p 1 and 32 bytes over a fresh 16-byte salt
and writes a JSON file holding exactly the seven keys the genesis step's contract names (`kdf`,
`n`, `r`, `p`, `dklen`, `salt`, `verifier_sha256`). The last is the SHA-256 of the scrypt output,
never the output itself: the record is a commitment, and the unseal later reveals the output. The
command checks the file against the contract and against the passphrase once more, and prints only
the path and the file's SHA-256, which the owner keeps beside the passphrase in the password
manager. It never replaces an existing record. The verifier is public once committed, which is why
the derivation is memory-hard: guessing a generated six-word passphrase against it costs one scrypt
per guess and is out of reach, guessing a short one a person chose is not (T25). The format holds no
field the repository's secret scan reads as a credential, so committing it needs no change to the
scan; `research/tests/fixtures/key-record-test-passphrase-only.json`, a sample for a passphrase that
exists only in the tests, is scanned on every pull request to show it.

Commit `research/keys/holdout-key-record.json` on main through a pull request. The genesis step
then names it (see trial-ledger.md), as `--key-record <that commit>:research/keys/holdout-key-record.json`,
and pins its commit, path and SHA-256 in the genesis entry. From then on the seal reads the record
only from that commit and checks it against that hash; a different record committed later, or
edited in the working tree, changes nothing. Whoever runs the genesis step chooses which record it
pins, so straight after it the owner runs, in a terminal of their own:

```bash
python -m norse_research.seal check-key
```

It reads the passphrase from the terminal, checks it against the key record the genesis entry on
the remote pins, prints that record's SHA-256 for comparison with the one kept in the password
manager, and writes nothing. If it refuses, the genesis entry pins a record that is not the owner's,
and a superseding genesis is still possible until the first entry of any other type (section 16).

## What happens at the unseal (G3.2, with the owner holding the key)

The unseal comes after the freeze anchor (G2.7) and after both README outcomes are drafted
(G3.1). First see what the unseal must name, which needs no key:

```bash
python -m norse_research.seal candidate
```

It prints, for each hypothesis, its candidate and whether it meets DEV-1 to DEV-5 on the frozen
ledger, then the pre-registration the genesis entry names and the holdout candidate's
configuration id and hash. If no hypothesis passes development there is no holdout candidate, and
then no unseal and no holdout run (section 11, item 3). Otherwise the owner runs, in a terminal of
their own:

```bash
python -m norse_research.seal unseal --commit <the prereg-v1 commit> --configuration-sha256 <the candidate's hash>
```

Before it asks for the passphrase it refuses on any environment override; a local ledger head
that is not the remote's (T23); a ledger that fails anchored verification; a ledger with no freeze
anchor, which is what an early unseal means here (T20); a ledger that already holds an unseal
entry (T23); a dirty tree, a HEAD not on main, or code running that is not the code at HEAD;
period constants that differ from the genesis entry's (T20); a pre-registration commit, tag or file
hash other than the genesis entry's (T24); and a configuration hash other than the holdout
candidate's, which it computes by the registered rule over the ledger at the freeze anchor (T22).
Only then does it read the passphrase from the terminal and check that the SHA-256 of its scrypt
output is the key record's verifier, in constant time (T25). A missing or wrong passphrase writes
nothing.

When the passphrase verifies, the unseal entry is appended through the ledger's ordinary append,
committed, pushed and read back from the remote. It names the pre-registration, the candidate's
configuration id, hypothesis and hash, the open and result entries of its selectable run, the
freeze anchor's commit, and the hashes of the holdout manifest and the key record, and it reveals
the scrypt output as its `proof`. The ledger itself checks that proof against the verifier in the
key record genesis pins, when the entry is written and on every replay, so an unseal written
without the owner's key, by the seal, by a direct append or by git plumbing, is refused, and with
it the re-fetch and the holdout run (security review H1). Revealing the scrypt output is safe
because the unseal is single use (T23); a rewrite of the remote to replay it is T10's. Only after the
remote holds it are the holdout files fetched again (D17): every file the frozen holdout manifest
pins, about 8,517 of them, each checked against its published CHECKSUM and against the manifest's
SHA-256 and size before it is written, and then the whole manifest is verified, rows included. The
first failure stops it, before any holdout return exists. If the archive has re-published a file
since 2026-11-11, restore that file from the encrypted offline copy into the mirror and run
`python -m norse_research.seal fetch-holdout`, which keeps every file that already matches its pin
and fetches the rest. The unseal itself is never repeated.

Verification replays all of this on every later read of the ledger: a second unseal entry, an
unseal before the freeze anchor, one without a proof that verifies, one naming another
pre-registration, key record or holdout manifest, or one naming any configuration other than the
candidate the rule selects on the frozen ledger, each fails verification, so the harness and the
gate refuse the ledger.

## The holdout run

The holdout run goes through the harness like any run:
`harness.run(repo, {"configuration": "holdout", "candidate_sha256": <the unseal's hash>}, store=...)`.
It takes the harness's usual checks (environment, ledger in step and verified, periods, the
development manifest, a clean tree, HEAD on main, the code at HEAD), and is then refused without
the unseal entry, for any configuration hash but the unseal's (T22), once a completed holdout result
has spent the seal, when the candidate's configuration no longer resolves the same at HEAD, when
the code a run executes (the harness paths and the hypothesis's paths) changed since the
candidate's run outside the data loading and seal modules (`seal`, `loader`, `store`, `manifest`,
`formats`, `binance_archive`, `periods`; section 11, item 6), when the committed holdout manifest is
not the registered one, and when the holdout files on disk do not all verify against it.

Its open entry is written and pushed as any other, of kind `holdout`: the candidate's configuration
with its window running from the start of the development window through 2026-10-31, plus the
candidate's configuration hash and the holdout manifest's hash. Before any holdout row is read the
harness fetches the remote's ledger and requires it to hold the unseal entry and that open entry
(T23). The strategy then runs twice in the harness's fresh interpreter. First it runs over the
candidate's own development window, without the unseal, so the loader refuses every holdout row;
its returns must equal the candidate's committed series exactly, or the attempt fails with no
holdout return computed (section 11, item 5). Then it runs once over the whole window, inside the
unsealed run; its returns must cover every day and its development part must match the committed
series again. The result entry commits the whole series. It records no statistic of the holdout
returns: HOLD-1 and the verdict read the committed series afterwards (G3.2, G3.3).

A failed attempt is ledgered and may be retried under the same unseal, for the same configuration
only, at the candidate's commit plus changes to data loading or the seal (item 6). The first
completed result spends the seal: every later holdout run is refused, and a holdout entry written
by hand after it fails verification. The holdout run is outside N.

## Choices made here

Section 11 leaves a few things to the implementer; each is chosen as close to its text as the code
allows. "Early" means before the freeze anchor exists. The threat model also names both G3.1 drafts
being in the chain, but the drafts have no form the ledger can read and section 11 does not register
them, so they remain a process control for G3.1 and the security role. The development criteria
DEV-2 to DEV-5 and the decision whether a hypothesis passes were not yet in `gate.py`, and the holdout
candidate cannot be chosen without them, so they are built here exactly as section 10 registers them,
with D15's beta-matched excess for H2 (`benchmark.beta_matched`, with a hand-computed known answer,
which section 15, item 7 asks for). DEV-2's benchmark returns come from the benchmark's last completed
run covering the window, the rule a selectable run follows; without one the gate refuses rather than
call DEV-2 a fail. The modules that count as data loading or the seal are listed above and in
`gitledger.SEAL_AND_DATA_LOADING`. The passphrase is normalised to Unicode NFC before derivation, so
the same words typed on another keyboard derive the same key.

## What G3.2 and the strategies still own

G3.2 runs the unseal and the holdout run, and computes HOLD-1 and the verdict from the committed
series; neither computation is here. The simulation across the 2025/2026 boundary, with positions
carried over it and the 2025-12 month-end universe taking effect at 2026-01-01 00:00, is the pinned
strategy's own behaviour when its window reaches past 2025-12-31 (G2.2, G2.3). Because item 6 allows
only data loading and seal changes after the candidate's run, that behaviour must be in the strategy
code before the trials run, not added at G3.2. A gap that spans the boundary itself (a last
development bar and a first holdout bar further apart than one interval) is recorded in neither
manifest, so a load across it fails as an unrecorded gap. Manifest verification does not look for
one, so G3.2 should check each series the candidate reads for it before the run.

## What this does not cover

A deliberate download of the public holdout outside the harness (T21): nothing prevents it, and the
mechanical candidate rule is what makes such a look useless. A key record substituted at genesis
(T25(c)): whoever runs the genesis step chooses the record, and `make_key_record` needs no terminal,
so an agent could pin a record for a passphrase it knows; `check-key` straight after genesis is the
control, and it is process, not prevention. An agent that somehow holds the passphrase can fake a
terminal; the control is that the passphrase never reaches one. The G3.1 drafts, as above. The
research gateway (G1.2), which runs no holdout run and must keep its run endpoint off for H1 and H2.
