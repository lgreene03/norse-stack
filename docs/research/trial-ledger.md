# The trial ledger (G1.5)

Owner: technical. Built 2026-11-14 against the threat model
([ledger-threat-model.md](ledger-threat-model.md), G1.4) and the signed pre-registration
([PREREGISTRATION.md](../PREREGISTRATION.md), tagged `prereg-v1` on 80a5e0c). The code is
`research/norse_research`: `canonical.py`, `registration.py`, `ledger.py`, `gitledger.py`,
`harness.py`, `gate.py` and `audit.py`, standard library only. No genesis entry has been
written and no `ledger-*` tag exists: writing the genesis entry is a separate, deliberate
step (below).

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
informative only: no trust decision reads the clock.

* **Genesis** pins the registration: the `prereg-v1` commit, the SHA-256 of
  PREREGISTRATION.md and preregistration.json at that commit, the manifest hashes, the
  period constants, the windows, the budgets, the harness and strategy paths whose git
  trees every run records, and G1.6's key record. A superseding genesis naming a new tag
  is allowed only until the first entry of any other type (section 16).
* **Open** is written, committed and pushed before anything is computed. It holds the
  kind (from the registered configuration, never a label), the fully resolved
  configuration and its SHA-256, the commit, the git trees of the registered paths, the
  manifest hash, the cost basis, the window, any rerun record or reserve reason, and the
  interpreter.
* **Result** closes an open entry: the status, a summary, and for a completed run the
  SHA-256 of its committed return series. It repeats the open entry's configuration hash,
  commit, manifest hash and cost basis, so each result entry holds every field G1.5 names.

N is the number of open entries of kind `trial`: reruns, failures and runs that never
finished all count, and nothing is deduplicated by configuration hash (section 9).

## What a run goes through

`norse_research.harness.run(repo, strategy, request, store=...)` refuses, before it writes
anything, on any of these: a `NORSE_*` (other than `NORSE_DATASETS_DIR`) or `RISK_*`
environment variable; a local ledger head that is not the remote's; a ledger that fails
anchored verification; no genesis entry; a registration whose files do not match the
genesis hashes; a manifest that is not the committed file at HEAD or not the registered
one; a dirty tree, untracked files included; a strategy module that is untracked or
imports outside the allowlist; any module loaded from the repository that is not tracked
at HEAD (a sourceless `.pyc` in an ignored folder, for example); a HEAD the remote does
not have; a request that resolves to no registered configuration or carries an unknown
key; and a run the registered rules forbid (out of registered order, a rerun without its
recorded defect, past the budget, a reserve draw without its reason, anything but a
candidate's sensitivity run after the freeze, a sensitivity run before it).

It then appends the open entry. If the write, the commit or the push fails, the run is
refused and the strategy is never called. The strategy receives the frozen configuration
and a view that loads rows through the registered loader; while it runs, the environment
is empty and an audit hook refuses files, sockets and processes. A strategy that fails is
ledgered as failed. Rows from 2018-05-01 on, the start of the registered out-of-sample
windows, load only inside an open ledger entry, so a script calling the loader directly is
stopped and told why (the REST comparison of G1.1 is the one integrity audit allowed past).

## Verifying, tagging and the genesis step

```bash
cd research
python -m norse_research.gitledger verify    # the chain plus its anchors
python -m norse_research.gitledger sync      # fast-forward a fresh clone's local ledger
python -m norse_research.gitledger tag ledger-m1 --message "M1: the gate"
```

`verify` checks the chain, that every committed version of the file along the branch's
first-parent history only appended (naming the commit that did not), that every committed
return series still matches its hash, that the remote's ledger head is in the local file,
that every `ledger-*` tag is an ancestor of the head whose ledger is a prefix of the current
one, and that `prereg-v1` still points where the genesis entry says. `tag` creates an
annotated tag object and pushes it straight to the remote after full verification; it
never replaces a tag. The freeze is the tag `ledger-freeze` (G2.7): from then on the gate
computes only on the ledger at that tag, and only the candidates' sensitivity runs remain.

The genesis entry is written once, after G1.6 has committed the holdout key record
(section 11, item 4), with the paths whose trees every run records:

```bash
python -m norse_research.gitledger genesis --key-record <commit>:<path> \
  --harness <paths> --h1 <paths> --h2 <paths>
```

## What the owner needs to set

A branch ruleset for `ledger`, active with an empty bypass list: restrict deletions and
block force pushes, with no required status check (a required check would refuse the
harness's pushes). The tag ruleset of D16 already covers `ledger-*`. Until the ruleset
exists, a force push to `ledger` is detected (by the anchors in any other clone, and by
`audit.force_push_findings` over GitHub's activity record), not prevented.

## What this does not cover

* **Runs outside the harness** (T2) and **holdout reads outside it** (T21): no test can
  refuse them, as the threat model says.
* **The research gateway** (G1.2, huginn's `cmd/research`): its token, CORS, Host check,
  datasets by manifest hash and its refusal when the ledger write fails are G1.2's Go
  tests. The Python harness exposes no network surface (a test checks that no module in
  the package imports a server). The gateway runs in a container that cannot see this
  tree, so it must either hand runs to this harness over an authenticated channel or keep
  its run endpoint disabled for H1 and H2 (threat model finding 9); neither exists yet.
* **The unseal and the holdout run** (G1.6): the harness refuses a holdout run until then.
* **Strategies** (G1.9, G2.2, G2.3): the harness runs Python strategies under
  `norse_research.strategies`. If H1 and H2 are built in huginn instead, the allowlist and
  the tripwire do not apply to them and T13 needs another design.
