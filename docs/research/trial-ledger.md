# The trial ledger (G1.5)

Owner: technical. Built 2026-11-14 against the threat model
([ledger-threat-model.md](ledger-threat-model.md), G1.4) and the signed pre-registration
([PREREGISTRATION.md](../PREREGISTRATION.md), tagged `prereg-v1` on 80a5e0c), and revised
on 2026-11-15 after the security role's first and second reviews of PR 56. The code is
`research/norse_research`: `canonical.py`, `registration.py`, `ledger.py`, `rules.py`,
`gitledger.py`, `sources.py`, `environment.py`, `harness.py`, `runner.py`, `gate.py` and
`audit.py`, standard library only. No genesis entry has been written and no `ledger-*`
tag exists: writing the genesis entry is a separate, deliberate step (below).

**G1.5 is not done until G1.2's gateway tests pass, or the gateway's run endpoint is
disabled for H1 and H2.** The done-when's "unauthenticated submission" is met here only by
a proxy (this package exposes no network surface); huginn's research gateway still accepts
runs without authentication (security review M10).

## Where it lives and why

The ledger is the file `ledger/trials.jsonl` on its own branch, `ledger`, beside the daily
return series of every completed run under `ledger/returns/<sha256>.csv`. It is not on main
because main needs the `research` and `validate` checks and a pull request (D16), which
would refuse a push per entry; the threat model foresaw this. Appends never touch the
working tree: the harness builds each commit with git's plumbing and pushes it without
force, so the tree it checks for cleanliness stays clean and a second writer's push is
refused as non-fast-forward.

Each line is one entry in canonical JSON (sorted keys, no whitespace, ASCII, one spelling
per number, no repeated key), chained by the SHA-256 of the previous line, with a
contiguous sequence number and a timestamp that never decreases. The timestamp is
informative only: no trust decision reads the clock, and a test fails if code outside
`ledger.utc_now` starts to.

* **Genesis** pins the registration: the `prereg-v1` commit, the SHA-256 of
  PREREGISTRATION.md and preregistration.json at that commit, the manifest hashes, the
  period constants, the windows, the budgets, the harness and strategy paths whose files
  every run records, G1.6's key record, the canonical remote URL with the `ledger` and
  `main` branches, and the strategy module and function that runs each registered
  configuration, benchmark and control. A superseding genesis naming a new tag is allowed
  only until the first entry of any other type (section 16), and may not move the pinned
  remote. The genesis step tags `ledger-genesis-<hash of the genesis entry>` straight
  after the entry, so an immutable tag anchors it in every clone.
* **Open** is written, committed and pushed before anything is computed. It holds the
  kind (from the registered configuration, never a label), the fully resolved
  configuration and its SHA-256, the commit, a digest of the files under the harness
  paths and under each hypothesis's paths, the manifest hash, the cost basis, the window,
  any rerun record or reserve reason, the pinned strategy, and the Python and git
  versions.
* **Result** closes an open entry: the status, a summary, and for a completed run the
  SHA-256 of its committed return series. It repeats the open entry's configuration hash,
  commit, manifest hash and cost basis, so each result entry holds every field G1.5 names.
  It also lists, as `crossed_gaps`, every gap the registered manifests record that the
  run's loads crossed, as `[series, after, before, missing]` (section 5, Gaps); the
  harness checks each against those manifests before writing it, and a failed run lists
  none.
* **Unseal** (G1.6) is written at most once, when the owner's passphrase verifies, and
  closes development: there is no ledger freeze (decision D21), so no trial, benchmark or
  control run may be opened after it, and N, V and the holdout candidate are computed on
  the entries before it. The result of a run opened before it may still be written after
  it; replay accepts it and the gate ignores it (security review of #65, L-1). It names the pre-registration genesis names, the holdout candidate
  the registered rule selects on those entries, the commit the holdout runs at, the hash
  of the data loading and seal diff the owner accepted since the candidate's run, and the
  hashes of the holdout manifest and the key record, and carries a one-time signature over
  all of it by the key the key record commits to (decision D27; format below); the holdout
  run follows it. See [holdout-seal.md](holdout-seal.md).

N is the number of open entries of kind `trial`: reruns, failures and runs that never
finished all count, and nothing is deduplicated by configuration hash (section 9).

## What a run goes through

`norse_research.harness.run(repo, {"configuration": "H1-01", ...}, store=...)` takes a
registered configuration and nothing else. The harness never imports or executes a
strategy. It reads the pinned module's `CONFIGURATIONS`, `PARAMETERS` and `DEFAULTS`,
which must be literals assigned once, by parsing the committed source; after the open
entry is pushed, it runs the pinned function in a fresh `python -s -P -B -X
pycache_prefix=<an empty folder>` process whose environment holds only `PYTHONHASHSEED=0`
(so the same commit gives the same series; G1.6 security review round 2, N3), built from the files
committed at HEAD (the package, the hypothesis's strategy paths and the manifest), and
reads the daily returns back over a pipe. In that process an audit hook, installed before
the strategy's module-level code runs, lets it read the interpreter's own library, the
committed code it was given and the dataset files the registered manifest pins, read-only,
and nothing else: no other file, socket or process. The round 3 security review (R1)
showed that committed strategy code reaching the hook's state through frames or the
garbage collector could disable it; since PR 61's round 4 the hook also refuses the four
ways in to a frame (`gi_frame`, `cr_frame`, `ag_frame`, `tb_frame`) and every use of the
garbage collector, and the source check refuses the frame names. It is still not a
sandbox, since Python cannot be sealed from inside itself, so the control against a
deliberate escape remains that the code is committed, ledgered and reviewed (T13).
Nothing survives from one run to the next.

Before it writes anything the harness refuses on any of these:

* an environment override: `NORSE_*` (other than `NORSE_DATASETS_DIR`), `RISK_*`, `GIT_*`
  outside a short allowlist (identity, dates, the global configuration file, prompts and
  the editor), `PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP`, `PYTHONHASHSEED`,
  `PYTHONUSERBASE` or `PYTHONOPTIMIZE`; an interpreter started with `-O`; a `git` on the
  path inside the repository. The genesis, tag and seal commands refuse them too;
* a remote that is not the pinned one: its resolved fetch and push URLs must equal the URL
  genesis pins, and no URL rewrite, `receivepack`, `uploadpack`, proxy or SSH command may
  be configured in any scope git reads; `refs/replace/` refs are refused and git runs with
  `GIT_NO_REPLACE_OBJECTS`. Every ledger git command is `/usr/bin/git`, never one found
  on `PATH`, and only while it, its exec path and its credential helper belong to root; it
  reads no global or system configuration (`GIT_CONFIG_GLOBAL=/dev/null`,
  `GIT_CONFIG_NOSYSTEM=1`), gets an environment built from a short allowlist rather than the
  shell's, and, if it reaches the remote, the owner's credential helper alone; and the clone's
  own configuration may hold only the keys `gitledger.REPOSITORY_SCOPE_ALLOWED` lists
  (security review of #65, M-3; review of #70, L-5 and L-6;
  [holdout-seal.md](holdout-seal.md));
* a local ledger head that is not the remote's, or a ledger that fails anchored
  verification, which includes the replay of the run rules and of every entry against its
  registration;
* no genesis entry, or a registration whose files do not match the genesis hashes;
* a manifest that is not the committed file at HEAD or not the registered one;
* a dirty tree (untracked files included, read with the filesystem monitor off), or any
  file git is told to ignore changes to (`--skip-worktree`, `--assume-unchanged`);
* a HEAD that is not on the remote's `main`;
* a pinned module that does not declare the configuration, or any module under the
  hypothesis's strategy paths that imports outside the allowlist or outside that
  hypothesis's own paths, calls `open`, `eval`, `getattr` and their kind, or names a dunder
  attribute, `sys`, `modules`, `attrgetter` or the like;
* any `norse_research` module the harness process runs, or any module loaded from the
  repository, that is not byte for byte its file at HEAD;
* a request that resolves to no registered configuration or carries an unknown key;
* a run the registered rules forbid: out of registered order; a rerun without its
  recorded defect, or naming a fix commit that the run it repeats already had; a
  benchmark, a control seed or a sensitivity cost run again without a recorded defect;
  past the budget; a reserve draw without its reason; a trial, benchmark or control after
  the unseal, which closes development (decision D21); a sensitivity run of anything but
  its hypothesis's candidate, which exists once every registered configuration of that
  hypothesis has a selectable run (before or after the unseal; section 8).

It then appends the open entry, under a lock, refusing if the ledger moved since the run
was admitted (an unseal written meanwhile moves it, and the unseal rule is checked under
the lock too), and reads the remote back to confirm it holds the entry. If the write, the commit, the push or that read fails, the run is refused
and nothing of the strategy has run. A strategy that fails or whose process exits is
ledgered as failed, with the repository, the dataset store, the home folder, any
`/Volumes/<name>` and every other absolute path removed from its error. Rows from 2018-05-01
on, the start of the registered out-of-sample windows, load only inside an open ledger
entry (the REST comparison of G1.1 is the one integrity audit allowed past).

## Verifying, tagging and the genesis step

```bash
cd research
python -m norse_research.gitledger verify    # the chain plus its anchors
python -m norse_research.gitledger sync      # fast-forward a fresh clone's local ledger
python -m norse_research.gitledger tag ledger-m1 --message "M1: the gate"
```

There is no ledger freeze and no freeze command (decision D21, 2026-11-21): the unseal
entry closes development.

`verify` checks the remote, the chain, that the remote's ledger head is in the local file,
that the genesis anchor exists and that every `ledger-*` tag on a ledger commit is an
ancestor of the head whose ledger is a prefix of the current one, that every committed
version of the file along the branch's first-parent history only appended, that every
committed return series still matches its hash, that `prereg-v1` still points where
genesis says, that the run rules hold over the whole ledger, that every open entry is
exactly what the registration resolves it to with the strategy genesis pins, that no
trial, benchmark or control run is opened after the unseal (the result of one opened before
it is accepted and ignored; security review of #65, I-5), that every sensitivity run re-costs its
hypothesis's candidate on the entries before it (or before the unseal), that the unseal
carries the owner's signature over its own bytes, comes only once each hypothesis's grid is
complete or its own entries and the reserve are spent, names the candidate the registered rule
selects on the entries before it and records the hash of the data loading and seal diff from
the candidate's commit to its own (recomputed, not rendered; L-7), and that every rerun's fix
commit is new since the run it repeats.

The genesis anchor is named for its content: `ledger-genesis-<hash of the first genesis
entry>`. A tag on a commit holding no ledger, or the reserved name (`ledger-genesis` with
any suffix) not matching its content, is reported and ignored, so a stray tag can never
take the anchor's place or block the genesis step. Any other `ledger-*` tag on a ledger commit must sit in the ledger's history,
because one that does not is the evidence of a rewritten ledger (T10). **Never run `git
push --tags` in this repository**: it would publish any local `ledger-*` or `prereg-*` tag
for good.

`tag` creates an annotated tag object and pushes it straight to the remote after full
verification; it never replaces a tag and refuses the reserved name. The gate computes
only on a ledger it issued itself, after anchored verification: the current ledger before
the unseal, which is provisional, and the entries before the unseal once it exists, so
check `closed` before relying on a figure.

The genesis entry is written once, after the owner has created the holdout key record with
`python -m norse_research.seal key-record` and committed it on main (section 11, item 4;
[holdout-seal.md](holdout-seal.md)):

```bash
python -m norse_research.gitledger genesis --key-record <commit>:<path> \
  --strategies <pins.json> --harness <paths> --h1 <paths> --h2 <paths>
```

`pins.json` maps each id (`H1-01` to `H1-08`, `H2-01` to `H2-06`, `B-H1`, `B-H2`, `C-H1`,
`C-H2`) to `{"path": ..., "function": ...}`. The genesis step refuses pins that do not cover
every id, that lie outside their hypothesis's paths, whose module does not declare the id in
`CONFIGURATIONS`, or whose hypothesis's modules fail the source checks; tree paths missing on
main or overlapping (a hypothesis's paths may sit inside the harness paths, which leaves
them out of the harness digest; no other overlap is allowed); harness paths that do not
cover all of `research/norse_research`, which would leave part of the package, the
simulation engine included, out of the harness digest; and a key record that breaks its
contract.

The genesis step also refuses a tag whose pre-registration files are not, by SHA-256, the
registration this code implements (`gitledger.PREREGISTRATION_SHA256`, which a test holds to
`docs/` in the checkout), and a tag while a newer `prereg-v<N>` is on the remote. Either is an
incident, not a reason to change the code: find out who created or moved the tag before anything
else (security review of #65, L-9). The ledger's append refuses such a genesis entry too, however
it is written, and `seal check-key` refuses a genesis that pins one (review of #70, L-11). So the
tag must go on a commit whose two files are exactly the pinned ones, and both are frozen from the
tag: the lead tags `prereg-v2` on main after #66's calibration results, with the files unchanged.
A tag made before a later edit to either file could never pass genesis again (review of #70,
L-12).

Straight after the genesis step the owner runs, in a terminal of their own,
`python -m norse_research.seal check-key`. It checks the typed passphrase against the key
record the genesis entry on the remote pins and writes nothing. Whoever runs the genesis step
chooses which record it pins, so this is the check that it pinned the owner's (security review
M2); the owner also compares the SHA-256 it prints with the one `key-record` printed.

### The holdout key record contract (for G1.6)

The genesis step accepts the key record only as a JSON object with exactly these keys, and
G1.6's `key-record` command builds the record to it (a test feeds the command's output to
the genesis step):

| Key | Value |
|---|---|
| `kdf` | `"scrypt"` |
| `n` | an integer, a power of two, at least 131072 (2^17) |
| `r` | `8` |
| `p` | `1` |
| `dklen` | an integer, at least 32 |
| `salt` | lower-case hex, an even number of characters, at least 32 (16 bytes) |
| `verifier_sha256` | lower-case hex SHA-256 of the scrypt output (64 characters) |
| `signer_sha256` | lower-case hex SHA-256 of the one-time public key the scrypt output derives (64 characters; decision D27) |

The parameters must also be ones `hashlib.scrypt` accepts within `maxmem` of 256 MiB; the
genesis step makes one trial call to check. The record must be committed on the remote's
main. Nothing else may be in it: in particular no other hash of the passphrase, and never the
scrypt output itself, which nothing reveals. The seal checks a typed passphrase against both
hashes before it signs, and `check-key` does the same straight after genesis. The contract
alone cannot tell whether `signer_sha256` came from the same passphrase as the verifier; those
two checks can.

`signer_sha256` is computed with the standard library only (`research/norse_research/lamport.py`
states it exactly): with `D` the ASCII bytes `norse-stack/holdout-unseal/lamport-sha256/v1/`, the
seed is HMAC-SHA256 keyed with the scrypt output, of `D` + `seed`; secret `(i, b)`, for `i` from
0 to 255 and `b` 0 or 1, is HMAC-SHA256 keyed with the seed, of `D` + `secret` + `i` as two
bytes, big-endian, + `b` as one byte; its public half is SHA-256 of `D` + `leaf` + the secret; and
`signer_sha256` is SHA-256 of `D` + `public` + the 512 public halves, `i` ascending and `b` 0
before 1.

The unseal entry carries `signature` in place of round 1's `proof` (security review of #65, H-2;
decision D27). It is an object holding exactly `preimages` and `complements`, each a list of 256
lower-case hex strings of 64 characters. It signs the SHA-256 of `D` + `message` + the entry's
canonical line without the `signature` key, so every other field, `seq`, `prev` and `time`
included. Bit `i` of that digest (byte `i // 8`, most significant bit first) is the `b` whose
secret `preimages[i]` reveals, and `complements[i]` is the public half of the other secret of
pair `i`. The append, `verify()` and replay rebuild the public key from the two lists and compare
its SHA-256 with the pinned record's `signer_sha256`, in constant time. The unseal's `time` is
its predecessor's, and the owner's command line pins the ledger head and HEAD it reviewed, so its
bytes are fixed: a retry with the identical command signs the same bytes, or refuses if either
has moved (review of #70, M-4). `research/tests/fixtures/key-record-test-passphrase-only.json` is a sample record in this
format for a passphrase that exists only in the tests, and a test recomputes its two hashes from
the passphrase with `hashlib` and `hmac` alone.

## The ledger branch ruleset

The owner created the branch ruleset for `ledger` (ruleset 27314544): deletion,
non-fast-forward and required linear history, with no bypass. The tag ruleset of D16
already covers `ledger-*`. A deleted or force pushed ledger is now refused by GitHub for
everyone while the ruleset stands, and still detected by the genesis anchor, by any other
clone and by `audit.force_push_findings` over GitHub's activity record.

## Open questions

* **V and a disqualified hypothesis (security review L7, for the quant). Closed by
  decision D22.** Under run rule 4, a hypothesis whose selectable runs come from different
  trees is treated as having none. Under `prereg-v1`, V was then computed over the other
  hypothesis's runs only, which could lower the bar for the other hypothesis's candidate.
  Since `prereg-v2`, V is the null variance of the candidate's window, which reads no run,
  so the other hypothesis's bar is the same either way
  (`test_one_hypothesis_s_sr0_does_not_change_when_the_other_has_no_selectable_runs`).

## What this does not cover

* **Runs outside the harness** (T2) and **holdout reads outside it** (T21): no test can
  refuse them, as the threat model says.
* **The research gateway** (G1.2, huginn's `cmd/research`): its token, CORS, Host check,
  datasets by manifest hash and its refusal when the ledger write fails are G1.2's Go
  tests. The gateway runs in a container that cannot see this tree, so it must either hand
  runs to this harness over an authenticated channel or keep its run endpoint disabled for
  H1 and H2 (threat model finding 9); neither exists yet, which is why G1.5 waits on it.
* **The unseal and the holdout run** are G1.6's, in [holdout-seal.md](holdout-seal.md);
  HOLD-1 and the verdict on the holdout result are G3.2's and G3.3's.
* **Strategies** (G1.9, G2.2, G2.3): the harness runs Python strategies. If H1 and H2 are
  built in huginn instead, the source checks and the runner do not apply to them and T13
  needs another design.
* **A forged `.pyc` in the harness process.** The strategy and the loader that compute a
  result run in the fresh process from committed source, never from a cached `.pyc`. The
  harness process itself, which resolves and writes the entries, imports the package
  normally, so a bytecode file forged with its source's timestamp and size there would
  run unchecked; starting the harness with `python -X pycache_prefix=<an empty folder
  outside the repository>` closes that, and the harness does not enforce it.
* **Python is not a sandbox.** The source checks and the runner's audit hook stop
  accidents and make evasion visible in the diff; a determined author with the owner's
  access can still do anything the owner can.
