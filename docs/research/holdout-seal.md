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

There is no ledger freeze (decision D21, 2026-11-21): the unseal closes development. No trial,
benchmark or control may run after it, and N, V and the holdout candidate are computed on the
entries before it, so it comes once every run the owner means to make is in the ledger, after the
G2.7 ledger audit and after both README outcomes are drafted (G3.1). It is refused until each
hypothesis has a selectable run of every registered configuration or has exhausted its own
entries and the reserve (a hypothesis that ran out is FAIL at the verdict), so neither grid is
stopped early by the other's unseal (section 11, item 4). First see what the unseal must name,
which needs no key:

```bash
python -m norse_research.seal candidate
```

It prints, for each hypothesis, its candidate and whether it meets DEV-1 to DEV-5 on the ledger as
it stands (provisional until the unseal), then the pre-registration the genesis entry names, the
holdout candidate's configuration id and hash, the data loading and seal files changed since the
candidate's run and the hash of that diff at HEAD. If no hypothesis passes development there is no
holdout candidate, and then no unseal and no holdout run (section 11, item 3).

Then rehearse the holdout run, which also needs no key and writes nothing to the ledger:

```bash
python -m norse_research.seal rehearse
```

It takes the checks the unseal and the holdout run take at HEAD, including item 6's rule between the
candidate's commit and HEAD, then runs the holdout candidate's strategy through the holdout run's own
loading path on development data only: the development manifest is split at 2025-03 (`--boundary`
moves it), its last ten months stand in for the holdout's ten, and the run must cross that boundary
and reproduce the candidate's committed series exactly. Any refusal or failure stops it with the
reason. Because the code is frozen at the unseal (below), a data loading or seal bug must be found
here, fixed and committed on main, and the rehearsal run again until it passes. It cannot exercise
the real boundary at 2026-01-01, whose data stays off disk, so a gap there is still G3.2's to check
(below).

Otherwise the owner runs, in a terminal of their own:

```bash
python -m norse_research.seal unseal --commit <the pre-registration commit> --configuration-sha256 <the candidate's hash> --accept-diff <the hash it prints>
```

`--tag` defaults to the tag the genesis entry names. Before it asks for the passphrase it refuses
on any environment override; a local ledger head that is not the remote's (T23); a ledger that
fails anchored verification; a ledger on which a hypothesis has neither a selectable run of every
registered configuration nor its own entries and the reserve used up, which is what an early
unseal means here (T20; an unseal while the other grid can still run would stop it early); a ledger that already
holds an unseal entry (T23); a dirty tree, a HEAD not on main, or code running that is not the code
at HEAD; period constants that differ from the genesis entry's (T20); a pre-registration commit,
tag or file hash other than the genesis entry's (T24); a configuration hash other than the holdout
candidate's, which it computes by the registered rule over the ledger before the unseal (T22); a
HEAD at which anything but data loading or the seal has changed since the candidate's selectable
run (section 11, item 6, applied here once; the entry records that HEAD); and an accepted diff hash
that is not the hash of the data loading and seal diff from the candidate's commit to HEAD. Without
`--accept-diff`, or with another hash, it prints the files changed, git's summary of the change,
the command to read it in full and the hash, and refuses, so the owner reads every data loading or
seal change before anything is unsealed; with nothing changed it prints the hash of the empty diff
to accept (security review round 3, T1).
Only then does it read the passphrase from the terminal and check that the SHA-256 of its scrypt
output is the key record's verifier, in constant time (T25). A missing or wrong passphrase writes
nothing.

When the passphrase verifies, the unseal entry is appended through the ledger's ordinary append,
committed, pushed and read back from the remote. It names the pre-registration, the candidate's
configuration id, hypothesis and hash, the open and result entries of its selectable run, the
commit the holdout runs at, the accepted diff hash, and the hashes of the holdout manifest and the
key record, and it reveals
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
early unseal, one without a proof that verifies, one naming another pre-registration, key record or
holdout manifest, one naming any configuration other than the candidate the rule selects on the
entries before it, or one recording a diff hash other than that of the data loading and seal diff
from the candidate's commit to its own, each fails verification, so the harness and the gate
refuse the ledger. The ledger's append makes the same checks before it pushes the entry, so none
of them can be broken through it.

## The holdout run

The holdout run goes through the harness like any run:
`harness.run(repo, {"configuration": "holdout", "candidate_sha256": <the unseal's hash>}, store=...)`.
It takes the harness's usual checks (environment, ledger in step and verified, periods, the
development manifest, a clean tree, HEAD on main, the code at HEAD), and is then refused without
the unseal entry, for any configuration hash but the unseal's (T22), once a completed holdout result
has spent the seal, once three runs have reached the unsealed stage, when the candidate's
configuration no longer resolves
the same at HEAD, at any commit but the one the unseal entry records, when the committed holdout
manifest is not the registered one, and when the holdout files on disk do not all verify against it.

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

The unsealed run is bracketed in the ledger (security review H2). Before the strategy is first
given the unseal, an `attempt` entry is appended under the append lock; after it, an
`attempt_close` records the outcome (`returned` or `failed`). A process killed in between leaves the
attempt open; the next run closes it as `abandoned`. Attempts run one at a time: the ledger refuses
an attempt while another is open, and the whole holdout run holds a lock on its clone, so a second
run there is refused while the first is in flight (M1). On another clone the second run closes the
first's attempt as abandoned, and the first can then neither close its attempt nor complete. A
completed holdout result needs its own attempt closed as returned, and there is only one.

Section 11, item 6's file rule is applied once, at the unseal: from the candidate's selectable-run
commit to the unseal's HEAD, only the data loading and seal modules (`seal`, `loader`, `store`,
`manifest`, `formats`, `binance_archive`, `periods`) may change anywhere in the package or the
hypothesis's paths, and the unseal entry records that HEAD. Every holdout run after it, and every
attempt within one, must be at exactly that commit with a clean tree; no other commit is ever
accepted. At most three runs reach the unsealed stage, an attempt entry each
(`rules.HOLDOUT_ATTEMPTS`); a run refused or failed before its attempt entry, in the development
reproduction or on a network error, read nothing of the holdout and does not count (security review
round 3, C1). The ledger, the harness and replay all enforce both rules, and nothing a run reports about itself decides anything. A
failed run may therefore be retried only where the same code and data give the same series, for a
failure outside them (a crash, a full disk). Once the strategy has been given the unseal, a
failure's own message is withheld from the public ledger, which records only the exception's type,
because the message may carry holdout values (L7); the harness's own check messages are kept. The
first completed result spends the seal: every later holdout run is refused, and a holdout entry
written by hand after it fails verification. The holdout run is outside N.

## Choices made here

Section 11 leaves a few things to the implementer; each is chosen as close to its text as the code
allows. "Early" means before each hypothesis has a selectable run of every registered configuration
or has exhausted its own entries and the reserve, since section 11, item 3 compares the two
candidates (it meant before the freeze anchor until decision D21 removed the freeze). The threat model also names both G3.1 drafts
being in the chain, but the drafts have no form the ledger can read and section 11 does not register
them, so they remain a process control for G3.1 and the security role. The development criteria
DEV-2 to DEV-5 and the decision whether a hypothesis passes were not yet in `gate.py`, and the holdout
candidate cannot be chosen without them, so they are built here exactly as section 10 registers them,
with D15's beta-matched excess for H2 (`benchmark.beta_matched`, with a hand-computed known answer,
which section 15, item 7 asks for). DEV-2's benchmark returns come from the benchmark's last completed
run covering the window on the same harness code as the hypothesis's selectable runs (the quant's
reading of section 8, in gate-statistics.md); without one the gate refuses rather than call DEV-2 a
fail, so the unseal is refused while a benchmark DEV-2 needs lacks one; no benchmark may run after
the unseal.
Both candidate rules rank by the DSR's z rather than its floating-point value, which rounds to 1.0
for strong runs; the order is the registered one. The modules that count as data loading or the seal are listed above and in
`gitledger.SEAL_AND_DATA_LOADING`. The passphrase is normalised to Unicode NFC before derivation, so
the same words typed on another keyboard derive the same key.

The commit rule above is the coordinator's decision, taken in security review round 2 (finding
N1) and recorded here for the quant role and the owner. Round 1's rule let a run use changed data
loading or seal files after the unseal whenever every earlier attempt reported that no holdout row
had been served; round 2 showed that report is the caller's word, written by the process the
change runs in, and that a peek off the ledger followed by a rewriting first attempt needed no
report at all. So item 6's permission to change data loading or the seal is used up at the unseal,
the code is frozen there, and the cap of three runs reaching the unsealed stage (round 3, C1)
implements item 6's retry conservatively. The
attempt close no longer records a "rows served" flag. The price is that a data loading bug found
only after the unseal leaves the holdout untested; the rehearsal below exists to find such bugs
first, and the quant and the owner should accept that price explicitly.

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

## Residual risks (security review round 1, L2, L4 and L5)

**The in-package seal is a naming convention (L2).** `periods.inside_unsealed_run` accepts any
64-character string and never checks that an unseal entry by that hash exists, and any script can
pass `holdout_audit=True` to the store or the manifest reader; the tests that only the runner and
the manifest tools do so read the package, not every script on the machine. After the unseal, when
the holdout files are on disk, a script that uses either reads holdout rows outside the holdout run.
This is threat T21's residual: the package stops accidents, not a deliberate read of public data,
and the mechanical candidate rule is what makes such a read useless.

**"Early" does not see the G3.1 drafts (L4).** The threat model once defined an early unseal as one
before the freeze tag and both G3.1 README drafts are in the chain; there is no freeze now (D21), the
code checks only that each hypothesis's grid is complete or its budget exhausted, and the unseal
entry records no commit of the drafts. Until that is built, it is a manual check, which
G3.1's owner (the CTPO role) makes before the owner runs `seal unseal`, and records in G3.1's row of
docs/ITERATION_GOALS.md: both drafts, the README text for a pass and for a fail, are merged on main;
the commit that merged them is an ancestor of the HEAD at which the unseal will run (`git
merge-base --is-ancestor <drafts commit> HEAD`); and the SHA-256 of each draft file at that commit is
written in the row, so a later edit to either is visible.

**Replaying the unseal depends on the gate code at verify time (L5).** Verification recomputes the
holdout candidate with the `gate`, `sharpe`, `cscv` and `benchmark` modules as they are when it runs,
not as they were at the unseal. A later change to any of them could make the ledger fail
verification for good, or change the candidate replay expects. The control is process: from the
unseal on, those four modules are not changed without a recorded defect and a security review, and
G3.4 checks that they are unchanged since the unseal entry's commit.

## What this does not cover

A deliberate download of the public holdout outside the harness (T21): nothing prevents it, and the
mechanical candidate rule is what makes such a look useless. A key record substituted at genesis
(T25(c)): whoever runs the genesis step chooses the record, and `make_key_record` needs no terminal,
so an agent could pin a record for a passphrase it knows; `check-key` straight after genesis is the
control, and it is process, not prevention. An agent that somehow holds the passphrase can fake a
terminal; the control is that the passphrase never reaches one. The G3.1 drafts, as above. The
research gateway (G1.2), which runs no holdout run and must keep its run endpoint off for H1 and H2.
