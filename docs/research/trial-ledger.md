# The trial ledger (G1.5)

Owner: technical. Built 2026-11-14 against the threat model
([ledger-threat-model.md](ledger-threat-model.md), G1.4) and the signed pre-registration
([PREREGISTRATION.md](../PREREGISTRATION.md), tagged `prereg-v1` on 80a5e0c), and revised
on 2026-11-15 after the security role's first review of PR 56. The code is
`research/norse_research`: `canonical.py`, `registration.py`, `ledger.py`, `rules.py`,
`gitledger.py`, `harness.py`, `gate.py` and `audit.py`, standard library only. No genesis
entry has been written and no `ledger-*` tag exists: writing the genesis entry is a
separate, deliberate step (below).

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
informative only: no trust decision reads the clock.

* **Genesis** pins the registration: the `prereg-v1` commit, the SHA-256 of
  PREREGISTRATION.md and preregistration.json at that commit, the manifest hashes, the
  period constants, the windows, the budgets, the harness and strategy paths whose git
  trees every run records, G1.6's key record, the canonical remote URL with the `ledger`
  and `main` branches, and the strategy module and function that runs each registered
  configuration, benchmark and control. A superseding genesis naming a new tag is allowed
  only until the first entry of any other type (section 16). The genesis step tags
  `ledger-genesis` straight after the entry, so an immutable tag anchors it in every clone.
* **Open** is written, committed and pushed before anything is computed. It holds the
  kind (from the registered configuration, never a label), the fully resolved
  configuration and its SHA-256, the commit, the git trees of the registered paths, the
  manifest hash, the cost basis, the window, any rerun record or reserve reason, the
  pinned strategy and the interpreter.
* **Result** closes an open entry: the status, a summary, and for a completed run the
  SHA-256 of its committed return series. It repeats the open entry's configuration hash,
  commit, manifest hash and cost basis, so each result entry holds every field G1.5 names.

N is the number of open entries of kind `trial`: reruns, failures and runs that never
finished all count, and nothing is deduplicated by configuration hash (section 9).

## What a run goes through

`norse_research.harness.run(repo, {"configuration": "H1-01", ...}, store=...)` takes a
registered configuration and nothing else: the strategy that runs it, its `PARAMETERS`
and its `DEFAULTS` come from the module genesis pins for that configuration, executed from
its bytes at HEAD. Before it writes anything it refuses on any of these:

* an environment override: `NORSE_*` (other than `NORSE_DATASETS_DIR`), `RISK_*`, `GIT_*`
  outside a short allowlist (identity, dates, the global configuration file, prompts),
  `PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP` or `PYTHONHASHSEED`;
* a remote that is not the pinned one: its resolved fetch and push URLs must equal the URL
  genesis pins, no `insteadOf` rewrite may be configured, and `refs/replace/` refs are
  refused (git runs with `GIT_NO_REPLACE_OBJECTS`);
* a local ledger head that is not the remote's, or a ledger that fails anchored
  verification, the replay of the run rules included;
* no genesis entry, or a registration whose files do not match the genesis hashes;
* a manifest that is not the committed file at HEAD or not the registered one;
* a dirty tree (untracked files included, read with the filesystem monitor off), or any
  file git is told to ignore changes to (`--skip-worktree`, `--assume-unchanged`);
* a HEAD that is not on the remote's `main`;
* a pinned module that does not list the configuration in its `CONFIGURATIONS`, or any
  module under the hypothesis's strategy paths that imports outside the allowlist, calls
  `open`, `eval`, `getattr` and their kind, or touches a dunder attribute;
* any `norse_research` module, or any module loaded from the repository, that is not byte
  for byte its file at HEAD;
* a request that resolves to no registered configuration or carries an unknown key;
* a run the registered rules forbid: out of registered order; a rerun without its
  recorded defect, or naming a fix commit that the run it repeats already had; a
  benchmark, or a control seed, run again without a recorded defect; past the budget; a
  reserve draw without its reason; anything but a candidate's sensitivity run after the
  freeze; a sensitivity run before it.

It then appends the open entry, refusing if the ledger moved since the run was admitted,
and reads the remote back to confirm it holds the entry. If the write, the commit, the push
or that read fails, the run is refused and the strategy is never called. The strategy
receives the frozen configuration and a view that loads rows through the registered loader;
while it runs, the environment is empty and an audit hook refuses files, sockets and
processes. A strategy that fails is ledgered as failed, with every absolute local path
removed from its error. Rows from 2018-05-01 on, the start of the registered out-of-sample
windows, load only inside an open ledger entry (the REST comparison of G1.1 is the one
integrity audit allowed past).

## Verifying, tagging, freezing and the genesis step

```bash
cd research
python -m norse_research.gitledger verify    # the chain plus its anchors
python -m norse_research.gitledger sync      # fast-forward a fresh clone's local ledger
python -m norse_research.gitledger tag ledger-m1 --message "M1: the gate"
python -m norse_research.gate freeze --message "M2: the ledger freeze"
```

`verify` checks the remote, the chain, that the remote's ledger head is in the local file,
that `ledger-genesis` exists and that every `ledger-*` tag on a ledger commit is an ancestor
of the head whose ledger is a prefix of the current one, that every committed version of
the file along the branch's first-parent history only appended (naming the commit that did
not), that every committed return series still matches its hash, that `prereg-v1` still
points where genesis says, and that the run rules hold over the whole ledger. A `ledger-*`
tag on a commit holding no ledger is reported, not fatal, because the tag ruleset makes
such a tag permanent. **Never run `git push --tags` in this repository**: it would publish
any local `ledger-*` or `prereg-*` tag for good.

`tag` creates an annotated tag object and pushes it straight to the remote after full
verification; it never replaces a tag and refuses `ledger-freeze`, which is irreversible.
The freeze has its own command: it refuses while a registered configuration lacks a
selectable run, unless `--allow-incomplete` states that the freeze leaves it unrun. From the
freeze on, the gate computes only on the ledger at that tag (and only on a ledger the gate
itself issued), and only the candidates' sensitivity runs remain.

The genesis entry is written once, after G1.6 has committed the holdout key record
(section 11, item 4):

```bash
python -m norse_research.gitledger genesis --key-record <commit>:<path> \
  --strategies <pins.json> --harness <paths> --h1 <paths> --h2 <paths>
```

It refuses a key record that is not scrypt at or above T25's floor (JSON with `kdf`
`scrypt`, `n` of at least 2^17, `r` 8, `p` 1 and a hex `salt` of at least 16 bytes; G1.6
writes the record to this contract or changes it before genesis), or that is not on the
remote's main; tree paths that are missing on main or that overlap (the harness paths must
exclude the strategy paths, so they list the harness's own files rather than the whole
package); and strategy pins that do not cover every registered configuration, benchmark
and control, or that lie outside their hypothesis's paths. `pins.json` maps each id
(`H1-01` to `H1-08`, `H2-01` to `H2-06`, `B-H1`, `B-H2`, `C-H1`, `C-H2`) to `{"path": ...,
"function": ...}`; the module at that path lists the ids it implements in
`CONFIGURATIONS`, and its `PARAMETERS` and `DEFAULTS`.

## What the owner needs to set

Before the genesis push, a branch ruleset for `ledger`, active with an empty bypass list:
restrict deletions, block force pushes and require a linear history, with no required
status check, pull request or update restriction (they would refuse the harness's pushes).
The security review gives it in full. The tag ruleset of D16 already covers `ledger-*`.
Until the branch ruleset exists, a deleted or force pushed ledger is detected (by the
`ledger-genesis` anchor, by any other clone, and by `audit.force_push_findings` over
GitHub's activity record), not prevented.

## Open questions

* **V and a disqualified hypothesis (security review L7, for the quant).** Under run rule
  4, a hypothesis whose selectable runs come from different git trees is treated as having
  none, and V is then computed over the other hypothesis's runs only. If that lowers V, it
  lowers the bar for the other hypothesis's candidate. The gate does this exactly as
  section 10 registers it; whether it should is a registration question, and nothing here
  changes it.

## What this does not cover

* **Runs outside the harness** (T2) and **holdout reads outside it** (T21): no test can
  refuse them, as the threat model says.
* **The research gateway** (G1.2, huginn's `cmd/research`): its token, CORS, Host check,
  datasets by manifest hash and its refusal when the ledger write fails are G1.2's Go
  tests. The gateway runs in a container that cannot see this tree, so it must either hand
  runs to this harness over an authenticated channel or keep its run endpoint disabled for
  H1 and H2 (threat model finding 9); neither exists yet, which is why G1.5 waits on it.
* **The unseal and the holdout run** (G1.6): the harness refuses a holdout run until then.
* **Strategies** (G1.9, G2.2, G2.3): the harness runs Python strategies. If H1 and H2 are
  built in huginn instead, the allowlist and the tripwire do not apply to them and T13
  needs another design.
* **A forged `.pyc` for a `norse_research` module** (review L12): strategy modules are
  executed from their committed bytes, never from a cached `.pyc`, but the package itself
  is imported normally, so a bytecode file forged with its source's timestamp and size
  would run unchecked. The remedy is to start the harness with
  `python -X pycache_prefix=<an empty folder outside the repository>`; the harness does
  not enforce it.
