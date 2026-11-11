# Threat model for the trial ledger and the holdout seal (G1.4)

Owner: security. Written 2026-11-11, before any of it is built, for the three goals that
implement it: the hash-chained trial ledger (G1.5), the holdout sealed in code (G1.6) and
the locked-down research gateway (G1.2). It lists how a result could be quietly re-run,
edited, deleted, reordered, or computed on unsealed or unmanifested data; says for each
whether it is prevented, detected or only deterred by process; and names the adversarial
test that proves each refusal, or says plainly where no test can. The test names are a
contract: the goals implement them under these names, and a CI check (T28) fails when one
is missing or skipped.

Read with [NEXT_ITERATION.md](../NEXT_ITERATION.md) (the gate),
[ITERATION_GOALS.md](../ITERATION_GOALS.md) (G1.2, G1.5, G1.6, G2.7, G3.4),
[DECISIONS.md](../DECISIONS.md) (D4, the holdout key) and [datasets.md](datasets.md) (the
loader and manifests this builds on).

## What it answers

| G1.4's wording | Threats |
|---|---|
| quietly re-run | T1, T2, T3, T8, T18 |
| edited | T4, T7, T16 |
| deleted | T5, T8 |
| reordered | T6, T11 |
| computed on unsealed data | T20, T21, T22, T23 |
| computed on unmanifested data | T12, T13, T14, T15 |

The other threats cover what those depend on: concurrent writers, the git history the
ledger lives in, the configuration hash, the registered grid, reporting, the
pre-registration tag, the owner's key, the gateway, the REST comparison and CI itself.

Two of them no test can refuse, and this document does not pretend otherwise: a run made
outside the harness (T2) and a look at the holdout outside it (T21). The data is public
and every agent runs as the owner, so nothing on this Mac can stop either. What the tests
can do is make such a run count for nothing: only ledgered results are published, the
grid and the holdout candidate are fixed by rule, and the audit looks for traces. G1.4's
done-when asks for a test proving each case is refused; for these two that is not
possible, and finding 4 records it.

## Who the adversary is

The adversary that matters is not a stranger. It is the people and agents who run the
research: the owner, the lead session, the specialist agents and the scheduled driver, all
acting in good faith, under a deadline, and hoping an edge exists. What they produce is
not theft but a result chosen after the fact: a rerun that is not counted, a window
nudged, a configuration that quietly differs, a look at 2026 before the rule that will be
judged on 2026 is fixed. Each can feel like diligence at the time ("just checking the fix
works"). So the question asked of every control here is whether it holds against someone
who has the owner's files, the owner's GitHub account and a plausible reason.

Three facts decide what can be prevented and what can only be detected.

**Everyone is the same principal.** Every agent session, scheduled or not, runs as the
owner's macOS user, reads the same files (`.env` and the dataset drive included) and
pushes to GitHub as `lgreene03`, the repository's only collaborator and an admin. No check
on this Mac or on GitHub can tell the owner from an agent, with one exception: a secret
only the owner knows, the unseal passphrase of D4. Tokens, signing keys and branch rules
bind "anyone working on this Mac", not "the owner".

**The data is public.** The holdout is a public archive anyone can fetch again in
minutes, so the seal cannot be secrecy. D4 already says the seal is tamper evidence. This
model adds that it is tamper evidence only for runs made through the harness, and
otherwise a barrier against accidents.

**A hash chain proves only that it agrees with itself.** SHA-256 needs no key, so whoever
edits an entry can recompute every later hash. A chain catches an edit only when compared
with a copy the editor could not rewrite: git history on GitHub, a tag, another clone. And
no chain notices that its last entries are gone unless something outside it remembers
the head.

Outsiders matter less, but are not absent: the repository is public and accepts pull
requests, and a web page open in the owner's browser can send requests to a service on
127.0.0.1 (T7, T26).

## What exists today

Found on 2026-11-11 by reading the code and with read-only queries. Nothing was changed.

**The loader's seal.** `DatasetLoader` refuses the holdout manifest and any request ending
after 2026-01-01T00:00Z, and has no unseal path; `test_the_holdout_manifest_cannot_be_loaded`
and `test_requests_reaching_into_the_holdout_are_refused` prove both, and
`Manifest.load` refuses a development manifest that lists a 2026 month.

**Other ways to holdout rows, in the same package.** The loader is one path among
several. `ArchiveStore.read`, `manifest.read_series_file` and `coverage.read_rows` return
the full rows of any mirrored month, prices included, with no seal. `manifest.verify` and
the coverage report read every holdout file by design: G0.6 ranked holdout volumes this
way, which is how three holdout-only pairs came to be named in the coverage report (the
Medium follow-up from #47). `rest_check.fetch_rest_klines` fetches any time range from
Binance's public API.

**The holdout files on disk.** The mirror under `NORSE_DATASETS_DIR` on the external drive
"Mac 4T" holds 8,517 holdout zip files, the number the holdout manifest pins, as plain
files. The volume is APFS, not encrypted, and mounted with ownership disabled
(`noowners`), so file permissions are not enforced on it.

**Branch protection.** `gh api repos/lgreene03/norse-stack/branches/main/protection`
returns 404, "Branch not protected". There are no rulesets (`/rulesets` and
`/rules/branches/main` both return `[]`), so force pushing main, deleting it, and moving
or deleting any tag are all allowed. The repository is public, its one collaborator is
`lgreene03` as admin, and it has no tags yet: `prereg-v1` does not exist. GitHub's server
side activity record (`/activity?activity_type=force_push`) lists one force push, to a
dependabot branch on 2026-09-09, and none to main. The owner's local command guard stops
agent shells force pushing a ref named main, but not every shape of command and not the
owner's own terminal.

**CI.** The `research` job lints and tests `research/` on every pull request, but no
status check is required, so a red pull request can still be merged. Actions are pinned by
version tag rather than commit SHA, and checkout is shallow (one commit), which a history
check will need to change.

**The research gateway** (huginn's `cmd/research`, the `research` service on
127.0.0.1:8094). Run submission (`POST /api/research/runs`) needs no token. CORS is
hard-coded to `*`; the compose file's `ACCESS_CONTROL_ALLOW_ORIGIN` is not read by this
service. The body is decoded as JSON whatever its Content-Type; its `data` field may name
any file path inside the container; the configuration is built from defaults in code
(`baseConfig`, including the 5 plus 2 bps costs) plus the request's thresholds; and
results are kept and served to anyone who asks. There is no ledger.

**Ignored files.** `.gitignore` ignores any folder named `data/` at any depth, compiled
Python (`*.py[cod]`), `.env` and every `.env.*`.

**Not yet built.** The ledger, `prereg-v1`, the unseal and its key record.

## Principles the tests assume

G1.5 and G1.6 may meet these however they choose, but the tests below assume them.

1. **Anchor the chain.** Verifying the ledger means the chain plus three anchors: every
   committed version of the ledger file, along the ledger ref's first-parent history, is
   a byte prefix of the next; the remote ledger head is present in the file; and each
   milestone tag's ledger is a prefix of the current one.
2. **Ledger first, result second.** An open entry naming the run is committed and pushed
   before any out-of-sample number is computed, and the result is a second entry. A run
   that never finishes still counts.
3. **N counts entries.** N is the count of trial entries under a counting rule fixed in
   `prereg-v1`, never the number of distinct configurations, and never an argument a
   caller supplies.
4. **Pin the registered things once.** A genesis entry records the `prereg-v1` commit,
   the SHA-256 of the pre-registration, the manifest hashes, the grid, the budgets, the
   period constants, the out-of-sample windows, the selection rule and the unseal key
   record. The harness compares against the genesis entry, never against a tag, a branch
   or the working tree, all of which can move.
5. **The unseal opens one run, not a door.** It names one configuration, the one the
   registered rule selects, and is spent by one result.
6. **Say what cannot be prevented.** Where only detection or process is possible, the
   residual risk is stated.

## Threat register

"Today" is the state on 2026-11-11. "With the tests" is the state once the tests named
for that threat pass in CI and, where stated, the owner has made the settings change
described under [Settings only the owner can change](#settings-only-the-owner-can-change).

| ID | Threat | Today | With the tests | Residual |
|---|---|---|---|---|
| T1 | Rerun through the harness, not counted | No ledger | Prevented | None beyond T2 and T3 |
| T2 | Run or exploration outside the harness | Nothing records it | Process only; agent runs on this Mac detectable by audit | High |
| T3 | Run on a branch, clone or fork whose ledger never reaches main | No ledger | Prevented for the harness; otherwise as T2 | Medium |
| T4 | An entry edited | No ledger | Detected | A rewrite of GitHub history (T10) |
| T5 | An entry deleted, the last one included | No ledger | Detected | T10; seconds before a push |
| T6 | Entries reordered | No ledger | Detected | T10 |
| T7 | A forged entry appended, or a ledger edit proposed from outside | No ledger | Detected, by reproduction | Found late (G3.4) unless reproduced earlier |
| T8 | Result seen before the entry; a failed, uncommitted or unpushed entry | No ledger | Prevented | Seconds between commit and push |
| T9 | Concurrent writers fork the ledger | No ledger | Prevented | None beyond T10 |
| T10 | History rewrite, force push, tag moved | Allowed; force pushes recorded by GitHub | Detected; refused for everyone with the owner's ruleset | An admin can lift the ruleset |
| T11 | Clock and timestamp manipulation | No ledger | Detected by server times; trust never reads the clock | Local times are informative only |
| T12 | An uncommitted or unregistered manifest | Loader accepts any manifest file | Prevented | T2 |
| T13 | Data read outside the loader inside a ledgered run | Nothing stops it | Prevented against accident, deterred against intent | Evasion visible in the diff |
| T14 | Local mirror altered after the manifest was built | Prevented alone; undetected with the manifest rebuilt | Prevented after genesis; detected before it | A re-published file fails closed |
| T15 | Dirty tree, ignored files, environment | No harness | Prevented | Interpreter outside the repository |
| T16 | Configuration changed while its hash stays the same | Gateway resolves defaults in code | Prevented | None material |
| T17 | Configuration outside the registered grid or budget | Nothing stops it | Prevented | Needs G0.7 in machine-readable form |
| T18 | A trial disguised by its label | Nothing stops it | Prevented | Needs G0.7's out-of-sample windows |
| T19 | Cherry-picked reporting from a valid ledger | No ledger | Prevented in generated output; detected by reproduction | Prose around the numbers |
| T20 | Holdout read through the harness before the unseal | Prevented by the loader | Prevented | None |
| T21 | Holdout read outside the harness | Open: files on disk, package readers, public download | Process only; accidents prevented (option B); agent reads detectable | High and irreducible |
| T22 | Several holdout runs after one unseal; candidate chosen late | Not in G1.6's spec | Prevented | Needs G0.7's selection rule |
| T23 | A second unseal | Not built | Prevented, except by rewriting the remote, which is detected | T10 |
| T24 | `prereg-v1` moved or replaced | Tags unprotected | Prevented for the harness; detected | T10 |
| T25 | Owner's key guessed, exposed or substituted | Not built | Guessing and substitution prevented; exposure process only | The passphrase reaching an agent |
| T26 | Unauthenticated submission to the gateway | Open whenever the service runs | Prevented from browsers and the network | The token authenticates this Mac, not the owner |
| T27 | The REST comparison's cached responses and register | Fixtures clean; register consistent | Fixtures prevented; register detected | Low |
| T28 | CI is not a gate; tests skipped or removed | No required checks | Detected; prevented with a required check | The same pull request can edit the inventory |

## Threats in detail

### The ledger

#### T1. A rerun through the harness that is not counted

**How.** A trial disappoints; a "fix" is made and the trial run again. Or one configuration
is rerun with another seed until it looks better. Or the rerun is recorded but treated as
replacing the first, because N is computed as the number of distinct configuration hashes.

**Control.** Every harness run writes an entry before it computes (T8). N is the number of
trial entries in the anchored ledger, reruns, aborted and failed runs included, under the
counting rule registered in `prereg-v1`. The seed is part of the resolved configuration.
**Prevented** for runs made through the harness.

**Tests.** `test_rerun_of_an_identical_configuration_adds_a_trial` (G1.5),
`test_n_counts_entries_not_distinct_configurations` (G1.5),
`test_open_entry_without_a_result_counts_as_a_trial` (G1.5).

**Residual.** None for the harness; the risk moves to T2 and T3.

#### T2. A run or exploration outside the harness

**How.** A notebook or script calls the loader directly; huginn's walk-forward command line
or today's gateway runs a strategy; an agent does a quick "sanity check" in its scratch
folder; a one-off read of the mirror with pandas. The result is seen and nothing records
it. The loader is a library, so nothing forces a caller through the harness. Pressure makes
this the likeliest threat of all, and P2's rule that no out-of-sample result for H1 or H2
may be produced until the ledger exists is, today, a rule only.

**Control.** Process, with detection for agents. Only ledgered results count: every figure
on GATE_STATUS, the tear sheets, the verdict page, EDGE_VERDICT and the README is generated
and cites an entry hash (G1.8, G2.4, G3.6), so an unledgered number cannot be published
without a visible break. The P2 audit (G2.7) searches branches, notebooks and logs, and
should also search Claude Code session transcripts under `~/.claude/projects` and the
agents' memory files. Transcripts record every command an agent ran and what it printed,
filed under whichever folder the session started in (norse work is often filed under
another project's folder), so they are the best available trace of an unledgered run; they
can also be deleted. Every agent brief, the scheduled driver's included, states that no
out-of-sample computation happens outside the harness. As a speed bump, G1.5 may make the
loader return rows overlapping a registered out-of-sample window only inside an open trial
entry; a caller can still read files directly (T13), but the easy path stops and says why.
**Process only.**

**Tests.** No test can prove a run did not happen outside the harness. If the speed bump
is adopted: `test_loader_returns_out_of_sample_rows_only_inside_an_open_trial` (G1.5). The
audit itself is a script run at G2.7, not a test.

**Residual.** High, and the main residual risk of the iteration. It is reduced by making a
private look useless rather than impossible: the grid is fixed (T17), the trial is defined
by its data window (T18) and the holdout candidate is chosen by rule (T22).

#### T3. A run on a branch, clone or fork whose ledger never reaches main

**How.** Every change here is made in a worktree on a branch. A trial run there appends to
that branch's copy of the ledger; the branch is closed or deleted and main never sees the
entry. The same happens in a scratch clone, a fork, or a CI run on a branch.

**Control.** Before a run the harness fetches the remote ledger ref (main, or a dedicated
ledger branch if G1.5 chooses one) and refuses unless the local ledger head equals it; it
then pushes the open entry before computing (T8). A run on a branch can only be made by
writing to the remote's ledger. **Prevented** for the harness; a harness edited to skip the
check is T2.

**Tests.** `test_run_is_refused_when_the_local_ledger_differs_from_the_remote` (G1.5, with a
bare repository as the remote), `test_open_entry_is_pushed_before_the_strategy_runs` (G1.5).

**Residual.** Medium: a modified harness on a fork. GitHub records branch deletions
(`branch_deletion` in the activity record) but not the deleted branch's content.

#### T4. An entry edited

**How.** A result summary, configuration hash, manifest hash or cost basis is changed in an
existing entry. A naive edit breaks the chain; a careful editor recomputes every later
hash and the chain verifies again. Two quieter variants: a JSON line holding a key twice
(parsers disagree on which value wins, so the hash covers a value some readers never see),
and an entry stored in a non-canonical form whose meaning differs from its hashed form.

**Control.** Verification is the chain plus the anchors of principle 1: the file at every
commit on the ledger ref is a byte prefix of the file at the next, so an edit with a
recomputed chain fails at the commit that made it. Lines must be canonical JSON with no
repeated keys. **Detected**; git lets anyone rewrite a file, so prevention is not possible.

**Tests.** `test_edited_entry_breaks_the_chain` (G1.5),
`test_recomputed_chain_after_an_edit_is_refused_by_history` (G1.5),
`test_entry_with_duplicate_keys_is_refused` (G1.5), `test_non_canonical_entry_is_refused`
(G1.5).

**Residual.** An edit made together with a rewrite of GitHub's history (T10). CI needs the
full history (`fetch-depth: 0`) to run the history check.

#### T5. An entry deleted, the last one included

**How.** A bad trial is removed. Deleting from the middle breaks the chain unless the
chain is recomputed (as in T4). Deleting the last entry, or the last several, leaves a
chain that verifies perfectly: a hash chain cannot detect truncation at its tail.

**Control.** The anchors again: the remote head and the newest milestone tag's head must
both be in the file, and every committed version must be a prefix of the next. **Detected.**

**Tests.** `test_deleted_middle_entry_breaks_the_chain` (G1.5),
`test_truncated_tail_is_refused_against_the_remote_head` (G1.5), which drops the last entry,
confirms the chain alone still verifies, and asserts that verification fails.

**Residual.** An entry deleted before it was pushed; under T8 that window is seconds. A
rewrite of GitHub's history (T10).

#### T6. Entries reordered

**How.** A trial is moved before the pre-registration anchor, before or after the freeze,
or before another trial so that a different one appears to be "first".

**Control.** The chain, a contiguous sequence number in every entry, and the genesis rule:
the first entry is the pre-registration anchor and no trial precedes it. Order is chain
position, never time (T11). **Detected.**

**Tests.** `test_reordered_entries_break_the_chain` (G1.5),
`test_sequence_numbers_are_contiguous` (G1.5), `test_no_trial_precedes_the_genesis_entry`
(G1.5).

**Residual.** As T4.

#### T7. A forged entry appended, or a ledger edit proposed from outside

**How.** An agent writes an entry by hand rather than through the harness, with a
fabricated result, the right previous hash and a valid chain: a "passing" trial that never
ran, or ran and failed. Or a contributor's pull request from a fork appends or alters
ledger lines. Signing entries cannot help: any key the harness can use on this Mac, an
agent can use too.

**Control.** Reproduction is the only authentication of an entry's content. Each result
entry stores the SHA-256 of the trial's out-of-sample daily return series, the series is
committed beside the ledger, and every entry is re-run from its commit, configuration and
manifest at the G2.7 freeze and again at G3.4, with the result hash compared. That needs
determinism, which is tested. A pull request that alters existing lines fails the T4
history check. **Detected**, by reproduction.

**Tests.** `test_entry_result_hash_matches_the_committed_return_series` (G1.5),
`test_identical_inputs_give_identical_result_hash` (G1.5). The full reproduction is an
attestation step (see [Attestation checks](#attestation-checks-not-unit-tests)).

**Residual.** Found only when reproduction runs. Run it at the freeze, not only at G3.4.

#### T8. A result seen before its entry, or an entry lost after it

**How.** Three ways. The run computes and prints its result, then the ledger write fails
(disk, lock, git): the result is seen and not counted, and refusing at that point is too
late. Or the entry is appended to the working file but not committed, and
`git checkout -- <ledger>` discards it, leaving a clean tree for the next run. Or it is
committed but not pushed, and `git reset --hard HEAD~1` discards it.

**Control.** Write ahead (principle 2): append an open entry (resolved configuration hash,
commit, manifest hashes, cost basis, data window), commit it and push it; only then call
the strategy; then append, commit and push the result entry, which references the open
one. Any failure before the strategy runs refuses the run. A failure after it leaves the
open entry, which counts. **Prevented.**

**Tests.** `test_strategy_is_not_invoked_when_the_open_entry_cannot_be_written` (G1.5),
which makes the file write, the commit and the push fail in turn and asserts the strategy
is never called and nothing reaches standard output or disk;
`test_open_entry_without_a_result_counts_as_a_trial` (G1.5);
`test_partial_trailing_line_is_refused` (G1.5), for a crash part way through an append;
`TestRunIsRefusedWhenTheLedgerEntryCannotBeWritten` (G1.2).

**Residual.** A process killed between commit and push, then reset: seconds, local, and it
takes intent.

#### T9. Concurrent writers fork the ledger

**How.** Two runs append at once: two agents, the gateway and the command line, or two
clones. Both entries name the same previous hash; one is lost in a merge, or both survive
as a fork.

**Control.** One writer. A lock plus a compare-and-swap on the head locally, and T3's
remote check, which makes the second push fail as non-fast-forward. G1.2's gateway runs in
a container that cannot see the norse-stack tree, so it cannot check that the tree is clean
or append to the git ledger itself; it should hand runs to the host harness, or stay
disabled for H1 and H2 (finding 9). **Prevented.**

**Tests.** `test_two_concurrent_appends_cannot_share_a_previous_hash` (G1.5),
`test_verifier_refuses_two_entries_with_the_same_previous_hash` (G1.5).

**Residual.** None beyond T10.

#### T10. History rewrite, force push, tag moved

**How.** Main is rewritten to drop or change ledger commits and force pushed; a milestone
tag or `prereg-v1` is deleted or moved (`git push -f origin <tag>`). Today GitHub refuses
none of this: main is unprotected, there are no rulesets, tags are unprotected, and the one
admin identity is the one every agent uses.

**Control.** Three layers. Detection that exists today: GitHub's activity record keeps
force pushes server side with the actor and the before and after SHAs, and every other
clone (the owner's checkout, each worktree) still holds the old commits. Prevention the
owner can add: the rulesets under [Settings only the owner can
change](#settings-only-the-owner-can-change), which refuse force pushes and tag moves for
everyone, admins included, until someone with admin rights changes the ruleset. Since agents
hold the same admin token, that turns a rewrite from something a command does by accident
into something done on purpose, which agents' standing rules already forbid. Independence
from tags: the harness trusts the genesis entry, not the tag (T24). **Detected** today;
**refused** for accidents with the ruleset; **deterred** against a deliberate admin.

**Tests.** `test_every_milestone_tag_is_an_ancestor_of_the_ledger_head` (G1.5),
`test_ledger_at_each_milestone_tag_is_a_prefix_of_the_current_ledger` (G1.5),
`test_force_push_audit_flags_a_force_push_to_the_ledger_ref` (G1.5, against a recorded
activity response). The audit itself queries GitHub, so it is an attestation step.

**Residual.** An admin can lift the ruleset, rewrite and restore it; the activity record
keeps the force push. Its retention is not documented here: on 2026-11-11 it still held the
2026-09-09 record. Whether it records tag moves is not verified, because no repository of
the owner's has a tag to check against.

#### T11. Clock and timestamp manipulation

**How.** The Mac's clock is set back, a run uses a faked time, `GIT_COMMITTER_DATE` is set,
or a timestamp is edited before hashing, to show a trial before the pre-registration, an
unseal on its date, or a trial before the freeze.

**Control.** Nothing the harness trusts reads the clock. "After the pre-registration" means
after the genesis entry in the chain; "before the freeze" means inside the tagged prefix;
"early unseal" means before the freeze tag and both G3.1 drafts are in the chain, not
before a date. The only trustworthy times are GitHub's (push activity, pull request merges,
Actions runs), at push granularity, which T8's push per entry makes per entry. G3.4 reports
them beside the entries' own times. **Detected**; local timestamps are informative only.

**Tests.** `test_timestamps_never_decrease_along_the_chain` (G1.5; catches only naive
backdating), `test_trust_decisions_do_not_read_the_clock` (G1.5 and G1.6), which sets the
clock before the pre-registration and after 2026-12-08 and asserts that verification, the
trial gate and the unseal check decide exactly as before.

**Residual.** A local timestamp can be wrong by any amount.

### The data

#### T12. An uncommitted or unregistered manifest

**How.** `DatasetLoader` accepts any `Manifest` loaded from any path, and
`Loaded.manifest_sha256` is the hash of whatever file was given. A temporary manifest that
adds a series or a month, or pins a series rebuilt from altered files, produces results
citing a hash that is in no commit.

**Control.** The harness accepts a manifest only if its SHA-256 is the one in the genesis
entry and the file at HEAD is that file; the gateway accepts datasets only by a registered
manifest hash. **Prevented.**

**Tests.** `test_harness_refuses_a_manifest_not_committed_at_head` (G1.5),
`test_harness_refuses_a_manifest_other_than_the_registered_one` (G1.5),
`TestDatasetNamedByPathIsRefused` (G1.2), `TestUnknownOrHoldoutManifestHashIsRefused` (G1.2).

**Residual.** T2.

#### T13. Data read outside the loader inside a ledgered run

**How.** The run is ledgered with the right manifest hash, but the strategy opens files
itself: the mirror read with pandas, `coverage.read_rows`, a file in an ignored `data/`
folder, a URL. The loader's checks are bypassed while the entry looks correct.

**Control.** The harness loads the data and hands rows to the strategy, which receives no
store, path or network. Strategy modules may import only an allowlist (no `os`, `pathlib`,
`io`, `urllib`, `http`, `socket`, `subprocess`, nor `norse_research.store`, `coverage`,
`manifest`, `binance_archive` or `rest_check`), checked by parsing their source, and file
and socket access fail during a strategy run. Python cannot be sandboxed from inside
itself, so this is a tripwire, not a wall. **Prevented** against accident, **deterred**
against intent: the code is at a recorded commit and reviewable.

**Tests.** `test_strategy_modules_import_only_the_allowlist` (G1.5),
`test_strategy_run_cannot_open_files_or_sockets` (G1.5), which also asserts the failed run
is still ledgered.

**Residual.** A determined author can evade an in-process tripwire; the diff is the
backstop.

#### T14. The local mirror altered after the manifest was built

**How.** (a) A mirrored file and its stored `.CHECKSUM` are edited on disk. (b) The same,
followed by `python -m norse_research.datasets manifest`, which rebuilds both manifests
from the mirror, and a commit of the new manifest.

**Control.** (a) is **prevented today**: the loader checks every file's SHA-256, size and
row count against the committed manifest (`test_a_file_that_differs_from_the_manifest_is_refused`,
`test_read_refuses_a_file_changed_on_disk`). (b) is the gap. `datasets verify`, and G2.5's
audit as worded ("re-verified against its manifest"), compare the mirror with its own
stored checksums and with the manifest, which after (b) all agree. Only a fresh download
notices. After genesis, T12's registered-manifest check refuses (b) outright; before it,
an upstream audit fetches every pinned file's `.CHECKSUM` from data.binance.vision afresh
and compares it with the manifest (51,756 small requests, about 90 minutes at ten a
second). Run it once at genesis and again at G2.7. **Prevented** after genesis,
**detected** before it.

**Tests.** `test_harness_refuses_a_manifest_other_than_the_registered_one` (G1.5),
`test_upstream_audit_detects_a_mirror_and_manifest_altered_together` (G1.5 or G2.5, offline,
with a fake fetcher serving the published checksum). The live audit is an attestation step.

**Residual.** Binance itself re-publishing a file: the audit reports it and the loader
refuses it, which fails closed.

#### T15. A dirty tree, ignored files and the environment

**How.** A run with uncommitted changes, so the recorded commit does not describe the code
that ran. `git status --porcelain` does not show ignored files, and three ignore rules
matter: `data/` at any depth can hold inputs; `*.py[cod]` hides compiled modules, and
Python imports a sourceless `name.pyc` placed where `name.py` would be (outside
`__pycache__`) when no `name.py` exists; and `.env` sets `NORSE_DATASETS_DIR`. Environment
variables change results with no file at all (huginn's `config.Load` reads `RISK_*`
overrides and fills defaults silently).

**Control.** Refuse on any change to a tracked file and any untracked file; refuse if any
module loaded from the repository is not a tracked file at HEAD; run the strategy with a
scrubbed environment and record the interpreter and dependency versions; refuse unless HEAD
is on the remote, so the commit can be reproduced. **Prevented.**

**Tests.** `test_modified_tracked_file_is_refused` (G1.5), `test_untracked_file_is_refused`
(G1.5), `test_ignored_sourceless_module_is_refused` (G1.5),
`test_environment_overrides_are_refused` (G1.5),
`test_run_at_a_commit_not_on_the_remote_is_refused` (G1.5).

**Residual.** The interpreter and the standard library live outside the repository; their
versions are recorded, not verified.

### Configuration and reporting

#### T16. A configuration changed while its hash stays the same

**How.** (a) The hash covers the request, not the resolved configuration. huginn's gateway
builds its configuration from defaults in code (`baseConfig`: costs, risk limits) plus the
request's thresholds, so changing a default changes the result and leaves a request-level
hash alone. (b) Inputs outside the configuration: a seed, an environment variable, a
parameter file read at run time. (c) Canonicalisation: key order or float spelling. (d) A
shortened hash: eight hex characters is 32 bits, and a second configuration with the same
short hash can be found on a laptop in hours. The effect is that two different trials look
like one, and "same configuration" comparisons hide a change.

**Control.** Hash the fully resolved configuration after every default is applied, as
canonical JSON, with full SHA-256; the strategy reads parameters only from that frozen
configuration; the commit SHA covers the code. N never deduplicates (T1). **Prevented.**

**Tests.** `test_config_hash_covers_the_resolved_configuration` (G1.5) and
`TestConfigHashCoversTheResolvedConfiguration` (G1.2), each asserting that a changed default
in code, with an identical request, changes the hash; `test_every_field_changes_the_hash`
(G1.5); `test_config_hash_is_canonical` (G1.5); `test_unknown_configuration_keys_are_refused`
(G1.5); `test_hashes_are_full_sha256` (G1.5), which refuses any shorter hash in an entry.

**Residual.** None material beyond T13.

#### T17. A configuration outside the registered grid, or past the budget

**How.** A thirteenth H1 trial "just to check"; a threshold between two grid values; a
reserve trial drawn without its recorded reason.

**Control.** The grid, the budgets and the reserve rule from `prereg-v1` are in the genesis
entry in machine-readable form, and the harness refuses anything else. **Prevented.**

**Tests.** `test_configuration_outside_the_registered_grid_is_refused` (G1.5),
`test_trial_beyond_the_registered_budget_is_refused` (G1.5),
`test_reserve_trial_requires_a_recorded_reason` (G1.5).

**Residual.** Depends on G0.7 publishing the grid, budgets and reserve rule in a form code
can read (finding 15).

#### T18. A trial disguised by its label

**How.** A trial is any run whose out-of-sample result is viewed. In walk-forward every
out-of-sample window is some other fold's training data, so a run labelled "in-sample",
"smoke test" or "debug", or a benchmark over the whole development period, shows
out-of-sample performance without being counted.

**Control.** The harness classifies a run by its data window, not its label: any run whose
returns cover a registered out-of-sample window is a trial. Benchmark and control runs
are trials marked unselectable, as NEXT_ITERATION.md says. **Prevented.**

**Tests.** `test_a_run_covering_an_out_of_sample_window_is_a_trial_whatever_its_label`
(G1.5).

**Residual.** Depends on G0.7 stating the out-of-sample windows exactly (finding 11).

#### T19. Cherry-picked reporting from a valid ledger

**How.** Every trial is ledgered, but the report takes N for one hypothesis where the
registered N spans both; computes statistics over a prefix of the ledger that ends before
a bad trial; quotes the best trial's in-sample figure; or quotes a Sharpe ratio undeflated.

**Control.** The gate's statistics take the ledger, not N. GATE_STATUS, the tear sheets and
the verdict page are generated from the frozen ledger and cite entry hashes (G1.8, G2.4,
G3.6). Once the freeze tag exists (G2.7), the gate refuses any other ledger. **Prevented**
in generated output; **detected** by reproduction (P1) for anything typed.

**Tests.** `test_statistics_take_n_only_from_the_ledger` (G1.5 with G1.3), asserting the
gate's public functions have no N parameter and use the registered count of a given
ledger; `test_gate_refuses_a_ledger_that_is_not_the_frozen_head` (G1.5);
`test_every_published_figure_cites_an_entry_in_the_ledger` (G1.8 and G3.6).

**Residual.** Prose around the numbers can still mislead; G3.1's two outcomes drafted
before the unseal are the process control.

### The holdout and the seal

#### T20. Holdout read through the harness before the unseal

**How.** A request reaching past 2026-01-01; the holdout manifest passed to the loader; or
`HOLDOUT_START` and `LAST_DEVELOPMENT_MONTH` in `periods.py` edited and the development
manifest rebuilt to include 2026 months. `Manifest.load` checks months against constants
in the same file, so the third passes the loader.

**Control.** The first two are **prevented today**. The third leaves a commit that the
entry records, and after genesis the period constants and the manifest hash no longer
match the genesis entry, so the harness refuses. **Prevented.**

**Tests.** Existing: `test_the_holdout_manifest_cannot_be_loaded`,
`test_requests_reaching_into_the_holdout_are_refused`,
`test_a_development_manifest_holding_holdout_months_is_refused`. New:
`test_period_constants_match_the_genesis_entry` (G1.6), `test_early_unseal_is_refused`
(G1.6), "early" meaning before the freeze tag and both G3.1 drafts are in the chain.

**Residual.** None for the harness.

#### T21. Holdout read outside the harness

**How.** Every route below works today. The 8,517 plain holdout files on "Mac 4T", readable
by any process. The package's own unsealed readers, `ArchiveStore.read`,
`manifest.read_series_file` and `coverage.read_rows`, which return a holdout month's rows,
prices included. `rest_check.fetch_rest_klines`, which fetches any range. A download from
data.binance.vision or data-api.binance.vision, which takes minutes. muninn's own
recordings, which began on 2026-10-27 and so overlap the holdout's last five days. And, as
D4 records, the models doing the work have seen 2026 prices in training. It has already
happened in a bounded way: G0.6 computed holdout volume rankings, and the coverage report
names three pairs that reach the top ten only in the holdout. D4's statement that "any run
on the holdout without an unseal entry in the trial ledger is detectable" is true only of
runs that present a result citing a manifest hash; a private look leaves no trace.

**Control.** Nothing prevents a deliberate look at public data. Three things help. Remove
the accidental paths: take the holdout files off the drive until the unseal (option B
below), and make the package's readers refuse holdout months unless the manifest tools
call them with an explicit audit flag. Make a look useless: the holdout candidate is
chosen by rule (T22) and the pass criterion is registered, so knowing 2026 cannot change
which configuration is judged or how. Detect agent reads: the P2 audit greps session
transcripts and memory for holdout archive keys (`-2026-01` to `-2026-10`), REST calls
with a 2026 `startTime`, and reads of the mirror's 2026 paths. **Process only**, with
accidents **prevented** and agent reads on this Mac **detectable**.

**Tests.** No test can prove nobody looked at public data. Testable:
`test_rest_fetch_refuses_a_holdout_range` (G1.6),
`test_package_readers_refuse_holdout_months_without_the_audit_flag` (G1.6),
`test_only_manifest_tools_pass_the_audit_flag` (G1.6),
`test_sync_does_not_mirror_holdout_months_before_the_unseal` (G1.6, if option B is taken),
`test_no_committed_fixture_holds_holdout_rows` (G1.6).

**Residual.** High and irreducible. The window that matters most is now: the holdout files
are on disk and `prereg-v1` is not yet signed, so a look today can still shape the
registered rule (finding 4).

#### T22. Several holdout runs after one unseal, or a candidate chosen late

**How.** The unseal opens the holdout and several configurations are run on it, the best
reported; or a person picks the candidate after seeing development results and a private
look at 2026 (T21). G1.6's done-when limits unseals, not runs, and does not say which
configuration may run.

**Control.** The unseal entry names one configuration hash. The harness computes the
candidate by the selection rule registered in `prereg-v1`, over the frozen ledger, and
refuses an unseal naming anything else; if no trial passed development there is no
candidate and no unseal, matching G3.2's "or none". One result entry spends the seal. A
run that crashes before producing a result may be retried under the same unseal for the
same configuration only, every attempt ledgered. **Prevented.**

**Tests.** `test_unseal_must_name_the_candidate_selected_by_the_registered_rule` (G1.6),
`test_holdout_run_of_another_configuration_is_refused` (G1.6),
`test_second_holdout_result_after_one_unseal_is_refused` (G1.6),
`test_no_candidate_means_no_unseal` (G1.6).

**Residual.** Depends on G0.7 writing the selection rule as a mechanical rule (finding 3).

#### T23. A second unseal

**How.** On the same ledger, a check for an earlier unseal entry refuses it. On a ledger
that lacks the first unseal, it does not: a clone made before it, a branch, a revert, a
reset, or a rewritten main (T10). The key still works, because a passphrase cannot be made
single use without a third party that remembers.

**Control.** The unseal refuses unless the local ledger head equals the remote's; the
unseal entry is pushed before any holdout row is released; and every holdout load checks
that the unseal entry is in the remote's ledger, fetched at load time. **Prevented**,
except by rewriting the remote, which T10 detects.

**Tests.** `test_second_unseal_is_refused` (G1.6, from its spec),
`test_unseal_on_a_ledger_behind_the_remote_is_refused` (G1.6),
`test_holdout_rows_require_the_unseal_entry_on_the_remote` (G1.6),
`test_unseal_is_written_to_the_ledger_before_any_holdout_row_is_returned` (G1.6).

**Residual.** T10.

#### T24. `prereg-v1` moved or replaced

**How.** After a look at results, `prereg-v1` is deleted and recreated on a commit with a
different grid or criterion, or a `prereg-v2` is tagged after the first trial. Tags here
are unprotected.

**Control.** The genesis entry pins the `prereg-v1` commit SHA and the SHA-256 of the
pre-registration and grid files; every trial chains from it; the unseal must name the same
values; and once a trial exists the genesis cannot change, which is G0.7's rule (no change
after the first trial) enforced. The verifier flags a tag that no longer points where the
genesis says. **Prevented** for the harness, **detected** for the tag.

**Tests.** `test_trials_require_a_genesis_entry_naming_the_preregistration` (G1.5),
`test_preregistration_change_after_the_first_trial_is_refused` (G1.5),
`test_verifier_flags_a_tag_that_no_longer_matches_the_genesis_entry` (G1.5),
`test_unseal_naming_another_preregistration_hash_is_refused` (G1.6).

**Residual.** The genesis entry rewritten along with history (T10).

#### T25. The owner's key guessed, exposed or substituted

**How.** (a) Guessing offline: the repository is public, so the salted hash D4 commits is
public, and a fast hash (SHA-256 of salt and passphrase) lets anyone try billions of
guesses a second on a graphics card; a passphrase a person chose falls. (b) Exposure: the
passphrase given on a command line (shell history, the process list), in an environment
variable or a file, typed into a terminal an agent can read, or into a chat, after which
it sits in a transcript. (c) Substitution: an agent commits a different salted hash, one
for a passphrase it knows, or edits the comparison. (d) The password manager: if an agent
session can drive its command-line integration, the passphrase is in reach.

**Control.** (a) A memory-hard key derivation: `hashlib.scrypt` from the standard library
with n of at least 2^17, r 8, p 1 (with `maxmem` raised to 256 MiB, since that needs 128
MiB) and a random 16-byte salt, and a passphrase of at least six words from a diceware list,
generated by the password manager. **Prevented.** (b) The key is read only with `getpass`
from the controlling terminal: no option, environment variable or file is accepted, and the
unseal refuses without an interactive terminal, which an agent's shell tool does not have.
The owner types it in a terminal of their own, never in a session where an agent is
working. **Process**, with the terminal check as a speed bump. (c) The key record is
committed before `prereg-v1` is tagged and read through the genesis entry from the
`prereg-v1` tree, never from the working tree. **Prevented.** (d) The owner keeps the entry
out of reach of any command-line integration and never approves a request for it from an
agent session. **Process.**

**Tests.** `test_unseal_without_the_key_is_refused` (G1.6, from its spec),
`test_unseal_with_a_wrong_key_is_refused` (G1.6),
`test_key_record_uses_scrypt_with_the_registered_parameters` (G1.6), which refuses a fast
hash or parameters below the floor; `test_key_is_read_only_from_an_interactive_terminal`
(G1.6); `test_key_record_is_read_from_the_genesis_preregistration_not_the_working_tree`
(G1.6).

**Residual.** An agent that somehow holds the passphrase can fake a terminal. The control
is that the passphrase never reaches one.

### The gateway and the evidence around the ledger

#### T26. An unauthenticated submission to the research gateway

**How.** Today, whenever the `research` service runs, any local process can submit a run
and read every result. Binding to 127.0.0.1 does not stop a web page in the owner's
browser: CORS is `*` and allows any origin to read responses, and because the body is
decoded whatever its Content-Type, a cross-site "simple" POST with `text/plain` needs no
preflight at all. Browsers' protections for local network requests vary and were not
tested. A DNS-rebinding page can reach it too unless the Host header is checked. The `data`
field names any file in the container.

**Control.** G1.2: a bearer token on every route that runs or shows a run (viewing a result
is what makes a trial, so reading results needs the token too), compared in constant time;
CORS for the console's own origin only; the Host header must be `127.0.0.1:8094` or
`localhost:8094`; datasets only by registered manifest hash; and the ledger entry written
before the run (T8). The token lives in `.env`, which every agent and local process can
read, so it authenticates "this Mac", not the owner; insiders are T1 to T3. **Prevented**
from browsers and the network.

**Tests** (Go, in huginn's `cmd/research`). `TestRunWithoutTokenIsRefused`,
`TestRunWithWrongTokenIsRefused`, `TestSimpleTextPlainPostWithoutTokenIsRefused`,
`TestResultsRequireToken`, `TestCorsAllowsOnlyTheConsoleOrigin`,
`TestForeignHostHeaderIsRefused`, `TestDatasetNamedByPathIsRefused`,
`TestUnknownOrHoldoutManifestHashIsRefused`,
`TestRunIsRefusedWhenTheLedgerEntryCannotBeWritten`,
`TestConfigHashCoversTheResolvedConfiguration` (all G1.2). G1.5's spec lists "an
unauthenticated submission" among its own tests; it is met by these, and by the same
assertions against any other submission surface the harness exposes.

**Residual.** Insiders, as above. The console server (`console/serve.py`) proxies run
submission and binds every interface until G0.2 lands.

#### T27. The REST comparison's cached responses and register

**How.** (a) Committed fixtures: `research/tests/fixtures/rest-BTCUSDT-*-2026-11-09.json`
and the archive zips for the same day are recorded from 2026-11-09, after the holdout and
in neither period, so they hold no holdout rows; a later fixture could be recorded from a
holdout day. (b) `rest-differences.json` decides which archive and API differences the live
check forgives, so adding an entry silences a real difference. It records the development
manifest it was built against, and that hash (`c27499e9...`) equals the committed
development manifest's. (c) The live check runs only by hand, so "passed locally" is a
statement, not an artefact. (d) `fetch_rest_klines` reaches holdout prices (T21). (e) The
cached archive listings under `listings/` are not hashed; a doctored listing changes which
months a rebuilt manifest pins, which the manifest's diff shows.

**Control.** (a) **Prevented** by a scan of committed fixtures. (b) **Detected** in review,
since the register is a diff, and tied to the registered manifest by a test. (c)
**Process.** (e) **Detected** in the manifest diff and refused after genesis (T12).

**Tests.** `test_no_committed_fixture_holds_holdout_rows` (G1.6), which scans every
committed fixture and data file for row times in [2026-01-01, 2026-11-01);
`test_rest_register_names_the_registered_development_manifest` (G1.5);
`test_rest_fetch_refuses_a_holdout_range` (G1.6).

**Residual.** Low.

#### T28. CI is not a gate

**How.** No status check is required, so a pull request that weakens the harness can be
merged red, or can skip (`pytest.mark.skip`), mark as expected to fail, or delete the very
test that would catch it. Actions pinned by tag can run changed code if a tag moves upstream
(a supply-chain risk, low).

**Control.** An inventory test reads the G1.5 and G1.6 tables under [Adversarial test
inventory](#adversarial-test-inventory) and fails if a listed test is missing, not
collected, skipped or expected to fail, for every goal already marked done; the owner's
ruleset makes the `research` check required. G1.2's tests live in huginn, whose pull
request lists them. **Detected**; **prevented** with a required check.

**Tests.** `test_every_threat_model_test_exists_and_is_not_skipped` (G1.5).

**Residual.** The pull request that removes a test can also edit this table; review sees
both. The verifier itself could be weakened the same way; G3.4 runs it from a clean clone
at the attested commit and G3.5's independent agent runs it again.

## The holdout files on disk: options

The seal lives in the loader, but the files sit in the mirror unencrypted, and the volume
does not enforce permissions. These are the realistic options, with what each costs. None
stops a deliberate download from Binance, because the data is public.

| Option | Stops | Does not stop | Cost |
|---|---|---|---|
| A. Leave as is | Nothing outside the loader | Any read | None |
| B. Remove the holdout files from the mirror until the unseal; fetch them again at the unseal and verify each against the committed holdout manifest; the owner keeps an encrypted offline copy as a fallback | Accidental reads by any tool, glob, notebook or agent; the package's unsealed readers find nothing | A deliberate download | `sync` and the coverage tools must stop at 2025-12 until the unseal, or the next sync restores the files; `datasets verify` covers development only until then; the unseal fetches about 17,000 objects again (about 30 minutes); if Binance has re-published a holdout file since 2026-11-11 the fetched file fails the manifest and the fallback copy is used |
| C. Encrypt the holdout files in place with a key derived from the owner's passphrase; the unseal decrypts | As B, without depending on Binance at the unseal | A deliberate download | Python's standard library has no cipher and `research/` uses nothing else, so this needs `openssl` on the command line or a new dependency; verification must work on the encrypted files; one more thing for the passphrase to protect |
| D. A second macOS user owns the holdout files | Agents running as the owner | A download | Impossible on "Mac 4T" as mounted (ownership disabled), so the files move to the internal disk or the volume setting changes, which is the owner's call; administrator set-up, and `sudo` at the unseal |
| E. Block agents' network access to Binance | Downloads by agents | The owner; agents need the network for other work | Impractical; not recommended |
| F. Detection only: the P2 audit searches session transcripts and memory for holdout paths, 2026 REST ranges and runs outside the harness | Nothing; detects agent reads on this Mac | Activity elsewhere, or deleted transcripts | One script, run at G2.7 |

**Recommendation.** B with F, done before `prereg-v1` is signed, together with T22's
mechanical candidate selection, which is what makes a look useless rather than merely
harder. B is a small change to `sync`, the coverage tools and G1.6's unseal, and needs
nothing new. Whichever is chosen, the documents should describe the seal as what it is:
tamper evidence for runs through the harness and a barrier against accidents, not secrecy.

## Settings only the owner can change

Nothing here was changed; these are the owner's to decide. Each binds agents only as long
as they do not change it back, because they act with the same admin token; the agents'
standing rules already forbid changing security settings.

**A branch ruleset.** In the repository's Settings, Rules, Rulesets, a new branch ruleset
targeting the default branch (and the ledger branch, if G1.5 uses one), enforcement Active,
with an empty bypass list so admins are bound too, and the rules "Restrict deletions" and
"Block force pushes". On main also "Require status checks to pass" with the `research` and
`validate` jobs. If the harness pushes ledger entries straight to main, a required check
would refuse those pushes; in that case put the ledger on its own branch with only the
first two rules, or have the harness open a pull request per entry.

**A tag ruleset.** A tag ruleset targeting `prereg-*` and `ledger-*`, enforcement Active,
empty bypass list, with "Restrict updates" and "Restrict deletions" (creation stays
allowed).

**Optionally, a signed `prereg-v1`.** An annotated tag signed with a key that needs a touch
(a hardware key), so a replacement cannot be made silently from an agent session.

**The passphrase.** Generated by the password manager as at least six diceware words, typed
only in the owner's own terminal at the single holdout run, never on a command line or in
a session where an agent is working, and kept out of reach of any command-line integration.

**The holdout files.** The choice among the options above.

## Adversarial test inventory

The tests each goal implements, under these names. G1.5's
`test_every_threat_model_test_exists_and_is_not_skipped` reads the first two tables. "Spec"
marks a test the goal's own done-when already names.

### G1.5, the trial ledger (Python, `research/tests/`)

| Test | Asserts | Threats |
|---|---|---|
| `test_rerun_of_an_identical_configuration_adds_a_trial` | Two runs of one resolved configuration append two trial entries and N is 2 | T1 |
| `test_n_counts_entries_not_distinct_configurations` | N is the count of trial entries under the registered rule, never the count of distinct configuration hashes | T1, T16 |
| `test_open_entry_without_a_result_counts_as_a_trial` | A run killed after its open entry is written still counts in N | T1, T8 |
| `test_loader_returns_out_of_sample_rows_only_inside_an_open_trial` | If adopted: a load overlapping a registered out-of-sample window without an open trial entry is refused | T2, T18 |
| `test_run_is_refused_when_the_local_ledger_differs_from_the_remote` | A local ledger head behind or ahead of the remote ledger ref (a bare repository fixture) refuses the run | T3, T9, T23 |
| `test_open_entry_is_pushed_before_the_strategy_runs` | The remote holds the open entry before the strategy is first called | T3, T8 |
| `test_edited_entry_breaks_the_chain` | Spec. One byte changed in any field of entry k fails verification at k | T4 |
| `test_recomputed_chain_after_an_edit_is_refused_by_history` | An edit with every later hash recomputed and committed fails the append-only history check, naming the commit | T4, T7 |
| `test_entry_with_duplicate_keys_is_refused` | A line holding any key twice is refused | T4 |
| `test_non_canonical_entry_is_refused` | A line that differs from its canonical serialisation is refused | T4 |
| `test_deleted_middle_entry_breaks_the_chain` | Spec. Removing an entry from the middle fails verification | T5 |
| `test_truncated_tail_is_refused_against_the_remote_head` | Spec. With the last entry dropped the chain alone verifies, and verification still fails because the remote head is missing | T5 |
| `test_reordered_entries_break_the_chain` | Spec. Swapping two adjacent entries fails verification | T6 |
| `test_sequence_numbers_are_contiguous` | A gap or repeat in sequence numbers fails verification | T6 |
| `test_no_trial_precedes_the_genesis_entry` | A trial entry before the genesis entry fails verification | T6, T24 |
| `test_entry_result_hash_matches_the_committed_return_series` | Each result entry's hash equals the SHA-256 of its committed out-of-sample return series | T7 |
| `test_identical_inputs_give_identical_result_hash` | The same commit, configuration and manifest give the same result hash twice | T7 |
| `test_strategy_is_not_invoked_when_the_open_entry_cannot_be_written` | Spec. With the write, the commit and the push failed in turn, the strategy is never called and nothing reaches standard output or disk | T8 |
| `test_partial_trailing_line_is_refused` | A ledger ending in a partial line fails verification | T8 |
| `test_two_concurrent_appends_cannot_share_a_previous_hash` | Two simultaneous appends produce two chained entries or one refusal, never a fork | T9 |
| `test_verifier_refuses_two_entries_with_the_same_previous_hash` | A forked ledger fails verification | T9 |
| `test_every_milestone_tag_is_an_ancestor_of_the_ledger_head` | Every `ledger-*` tag is an ancestor of the ledger ref's head | T10 |
| `test_ledger_at_each_milestone_tag_is_a_prefix_of_the_current_ledger` | The ledger file at each milestone tag is a byte prefix of the current file | T10 |
| `test_force_push_audit_flags_a_force_push_to_the_ledger_ref` | Against a recorded activity response, the audit fails on a force push to the ledger ref and passes without one | T10 |
| `test_timestamps_never_decrease_along_the_chain` | An entry timestamped before its predecessor fails verification | T11 |
| `test_trust_decisions_do_not_read_the_clock` | With the clock set before the pre-registration and after 2026-12-08, verification, the trial gate and the unseal check decide exactly as before (also G1.6) | T11 |
| `test_harness_refuses_a_manifest_not_committed_at_head` | Spec (unmanifested data). A manifest file that differs from HEAD or is untracked refuses the run | T12 |
| `test_harness_refuses_a_manifest_other_than_the_registered_one` | A committed manifest whose hash is not in the genesis entry refuses the run | T12, T14, T20 |
| `test_strategy_modules_import_only_the_allowlist` | Parsing every strategy module finds no import outside the allowlist | T13 |
| `test_strategy_run_cannot_open_files_or_sockets` | A strategy that opens a file or socket fails, and the failed run is ledgered | T13 |
| `test_upstream_audit_detects_a_mirror_and_manifest_altered_together` | With a fake fetcher serving the published checksum, an altered file and a manifest rebuilt to match are reported | T14 |
| `test_modified_tracked_file_is_refused` | Spec (dirty tree). A changed tracked file refuses the run | T15 |
| `test_untracked_file_is_refused` | An untracked file anywhere in the repository refuses the run | T15 |
| `test_ignored_sourceless_module_is_refused` | A `.pyc` in an ignored place on the import path, imported by the run, refuses it | T15 |
| `test_environment_overrides_are_refused` | A `NORSE_*` or `RISK_*` variable in the environment refuses the run, or never reaches the strategy | T15 |
| `test_run_at_a_commit_not_on_the_remote_is_refused` | A HEAD the remote does not have refuses the run | T15 |
| `test_config_hash_covers_the_resolved_configuration` | A changed default in code, with an identical request, changes the configuration hash | T16 |
| `test_every_field_changes_the_hash` | Changing each field of the resolved configuration in turn changes the hash | T16 |
| `test_config_hash_is_canonical` | The same values in another key order or float spelling give the same hash | T16 |
| `test_unknown_configuration_keys_are_refused` | A key the strategy does not define refuses the run | T16 |
| `test_hashes_are_full_sha256` | Any hash field shorter than 64 hex characters fails verification | T16 |
| `test_configuration_outside_the_registered_grid_is_refused` | A configuration not in the genesis grid refuses the run | T17 |
| `test_trial_beyond_the_registered_budget_is_refused` | A trial past its hypothesis's budget refuses the run | T17 |
| `test_reserve_trial_requires_a_recorded_reason` | A reserve trial without a reason entry refuses the run | T17 |
| `test_a_run_covering_an_out_of_sample_window_is_a_trial_whatever_its_label` | A run labelled in-sample, smoke or debug whose returns cover a registered out-of-sample window is ledgered as a trial | T18 |
| `test_statistics_take_n_only_from_the_ledger` | Spec (N from the ledger). The gate's public functions take no N and use the registered count of the ledger given (with G1.3) | T19 |
| `test_gate_refuses_a_ledger_that_is_not_the_frozen_head` | Once the freeze tag exists, a prefix or other ledger is refused | T19 |
| `test_trials_require_a_genesis_entry_naming_the_preregistration` | A trial entry without a genesis entry naming `prereg-v1`'s commit and file hashes refuses the run | T24 |
| `test_preregistration_change_after_the_first_trial_is_refused` | A new genesis after the first trial fails verification | T24 |
| `test_verifier_flags_a_tag_that_no_longer_matches_the_genesis_entry` | A `prereg-v1` tag pointing elsewhere than the genesis says is reported | T24 |
| `test_rest_register_names_the_registered_development_manifest` | The register's `manifest_sha256` equals the genesis development manifest hash | T27 |
| `test_every_threat_model_test_exists_and_is_not_skipped` | Every test in the G1.5 and G1.6 tables exists, is collected, and is neither skipped nor expected to fail, for each goal marked done | T28 |

### G1.6, the seal (Python, `research/tests/`)

| Test | Asserts | Threats |
|---|---|---|
| `test_period_constants_match_the_genesis_entry` | A changed `HOLDOUT_START` or `LAST_DEVELOPMENT_MONTH` refuses the run | T20 |
| `test_early_unseal_is_refused` | Spec. An unseal before the freeze tag and both G3.1 drafts are in the chain is refused | T20 |
| `test_rest_fetch_refuses_a_holdout_range` | A REST request overlapping [2026-01-01, 2026-11-01) is refused before the unseal | T21, T27 |
| `test_package_readers_refuse_holdout_months_without_the_audit_flag` | `ArchiveStore.read`, `read_series_file` and `coverage.read_rows` refuse a holdout month without the flag | T21 |
| `test_only_manifest_tools_pass_the_audit_flag` | Parsing the package finds the flag passed only by the manifest build and verify code | T21 |
| `test_sync_does_not_mirror_holdout_months_before_the_unseal` | If option B is taken: `sync` lists no month after 2025-12 before the unseal | T21 |
| `test_no_committed_fixture_holds_holdout_rows` | No committed fixture or data file holds a row time in [2026-01-01, 2026-11-01) | T21, T27 |
| `test_unseal_must_name_the_candidate_selected_by_the_registered_rule` | An unseal naming any configuration other than the rule's choice over the frozen ledger is refused | T22 |
| `test_holdout_run_of_another_configuration_is_refused` | After an unseal, a holdout run of a different configuration hash is refused | T22 |
| `test_second_holdout_result_after_one_unseal_is_refused` | After one holdout result entry, another holdout run is refused | T22 |
| `test_no_candidate_means_no_unseal` | With no trial passing development, the unseal is refused | T22 |
| `test_second_unseal_is_refused` | Spec. A second unseal on the same ledger is refused | T23 |
| `test_unseal_on_a_ledger_behind_the_remote_is_refused` | An unseal on a ledger whose head is not the remote's is refused | T23 |
| `test_holdout_rows_require_the_unseal_entry_on_the_remote` | A holdout load fails unless the remote's ledger holds the unseal entry | T23 |
| `test_unseal_is_written_to_the_ledger_before_any_holdout_row_is_returned` | Spec (unseal written). With the write failed, no holdout row is returned | T23, T8 |
| `test_unseal_naming_another_preregistration_hash_is_refused` | An unseal naming a pre-registration other than the genesis entry's is refused | T24 |
| `test_unseal_without_the_key_is_refused` | Spec. No key, no unseal | T25 |
| `test_unseal_with_a_wrong_key_is_refused` | A wrong passphrase is refused | T25 |
| `test_key_record_uses_scrypt_with_the_registered_parameters` | A key record using a fast hash, or scrypt below n 2^17, r 8, p 1 or a 16-byte salt, is refused | T25 |
| `test_key_is_read_only_from_an_interactive_terminal` | No option, environment variable or file supplies the key, and without a terminal the unseal is refused | T25 |
| `test_key_record_is_read_from_the_genesis_preregistration_not_the_working_tree` | A changed key record in the working tree does not change the outcome | T25 |

### G1.2, the gateway (Go, huginn `cmd/research`)

| Test | Asserts | Threats |
|---|---|---|
| `TestRunWithoutTokenIsRefused` | Spec (unauthenticated submission). No token gives 401, schedules nothing and writes nothing | T26 |
| `TestRunWithWrongTokenIsRefused` | A wrong token gives 401 | T26 |
| `TestSimpleTextPlainPostWithoutTokenIsRefused` | A cross-site `text/plain` POST without a token is refused | T26 |
| `TestResultsRequireToken` | Listing or reading a run without the token gives 401 | T26 |
| `TestCorsAllowsOnlyTheConsoleOrigin` | A preflight from any other origin gets no `Access-Control-Allow-Origin`, and `*` is never sent | T26 |
| `TestForeignHostHeaderIsRefused` | A Host header other than `127.0.0.1:8094` or `localhost:8094` is refused | T26 |
| `TestDatasetNamedByPathIsRefused` | Spec (datasets by manifest hash). A `data` path is refused | T12, T26 |
| `TestUnknownOrHoldoutManifestHashIsRefused` | A manifest hash not registered, or the holdout's before the unseal, is refused | T12, T26 |
| `TestRunIsRefusedWhenTheLedgerEntryCannotBeWritten` | Spec. With the ledger write failing, the run is refused before the strategy is called | T8, T26 |
| `TestConfigHashCoversTheResolvedConfiguration` | A changed `baseConfig` default with an identical request changes the hash | T16 |

### Attestation checks (not unit tests)

These need the network, GitHub or the full history, so they run as steps of the G2.7
freeze and the G3.4 attestation script rather than in the offline test suite. Their
parsing and decision logic is unit-tested above.

| Check | Asserts | Threats |
|---|---|---|
| Anchored chain verification | The chain verifies, every committed version is a prefix of the next, the remote head and every milestone tag are in the file | T4 to T6, T10 |
| Force-push audit | GitHub's activity record shows no force push or deletion on the ledger ref since 2026-11-04 | T10 |
| Upstream manifest audit | Every pinned SHA-256 equals the `.CHECKSUM` fetched afresh from data.binance.vision | T14 |
| Reproduction of every entry | Each result entry, re-run from its commit, configuration and manifest, gives the same result hash | T7 |
| Exactly one unseal | The ledger holds at most one unseal entry and at most one holdout result | T22, T23 |
| Transcript and memory audit (P2) | No session transcript or memory file shows an out-of-sample computation outside the harness or a read of holdout data | T2, T21 |

## Findings

Severity is by what a failure would do to the verdict; confidence says how it was
established. None blocks this document; several change what G1.5, G1.6, G1.2 and G0.7 must
deliver.

1. **High, confirmed.** Main has no branch protection (404) and no rulesets, tags are
   unprotected, and the one admin identity is the one every agent uses. G1.5's
   append-only ledger and milestone tags, and G1.6's reference to `prereg-v1`, rest on
   refs that any session can rewrite or move. Exploit: `git push --force` to main after
   editing the ledger, or `git push -f origin prereg-v1`. GitHub records force pushes, so
   it is detectable today; the rulesets above make it a deliberate act (T10, T24).
2. **High, confirmed.** G1.5's adversarial tests for an edited, deleted or reordered entry
   can all pass while the property they are meant to prove is false. A chain verified on
   its own detects only naive changes: an editor who recomputes later hashes passes it,
   and deleting the last entry passes it with nothing recomputed. The tests must verify
   against git history, the remote head and the tags (T4, T5).
3. **High, confirmed from the spec.** G1.6 limits unseals, not holdout runs, and does not
   bind which configuration runs. After the one permitted unseal, nothing in its done-when
   stops several configurations being run on the holdout and the best reported, so G3.2's
   "exactly one candidate run once" is process only. The unseal must name the candidate the
   registered rule selects and be spent by one result (T22); G0.7 must write that rule.
4. **High, confirmed.** The seal is one path among many. Holdout rows are reachable today
   through 8,517 plain files on a volume that is unencrypted and does not enforce
   permissions, three unsealed readers in the package, an unguarded REST function and a
   public download. D4's "any run on the holdout without an unseal entry in the trial
   ledger is detectable" holds only for results presented with a manifest hash. And
   `prereg-v1`, due 11-07, is unsigned while all of this is open, so the window in which a
   look can shape the registered rule is open now. G1.4's own done-when, a test proving
   each case refused, cannot be met for runs and reads outside the harness (T2, T21).
5. **Medium, confirmed.** "Refuses when the ledger write fails" (G1.2, G1.5) is safe only
   if the entry is written, committed and pushed before the result is computed. Written
   after, the result has been seen; written but not committed, `git checkout --` discards
   it and leaves a clean tree (T8).
6. **Medium, confirmed.** A dirty-tree check built on `git status` misses ignored files.
   Here that is any `data/` folder, compiled Python and `.env`, plus environment
   variables. Both halves were shown on 2026-11-11: Python imported a `helper.pyc` with
   no `helper.py` beside it, and `git check-ignore` reports `research/norse_research/helper.pyc`
   and `research/data/holdout.csv` as ignored (T15).
7. **Medium, confirmed.** `DatasetLoader` accepts a `Manifest` from any path, and the hash
   it reports is whatever file was given, so a result can cite a manifest that is in no
   commit (T12).
8. **Medium, confirmed.** Nothing compares the committed manifests with Binance's published
   checksums after they were built. A mirror file and the manifest altered together and
   committed pass `datasets verify` and G2.5's audit as worded; only a fresh download
   notices (T14).
9. **Medium, confirmed in code; browser reach not tested.** The gateway has no
   authentication, hard-codes CORS `*` (ignoring the compose variable), decodes any
   Content-Type and reads any `data` path, so whenever it runs a web page can submit runs
   and read results. Separately, a gateway in a container with a read-only mount cannot
   check the norse-stack tree or append to its git ledger, so G1.2 and G1.5 as written
   imply two ledger writers; one should hand runs to the other (T9, T26).
10. **Medium, confirmed.** A configuration hash over the request misses the gateway's
    in-code defaults, costs included. The hash must cover the resolved configuration, and
    N must never deduplicate by hash (T16).
11. **Medium, likely; depends on G0.7.** If a trial is whatever the caller labels one, any
    run touching a walk-forward out-of-sample window is an uncounted look. The harness must
    classify by data window, which needs G0.7 to state the windows exactly. This is the
    quant's call (T18).
12. **Medium, confirmed.** The repository is public, so D4's committed salted hash is
    public. A fast hash would let a human-chosen passphrase be guessed offline; it must be
    scrypt with stated parameters over a generated passphrase, read only from a terminal
    (T25).
13. **Medium, confirmed.** G1.6's "a second unseal fails" can pass on one ledger while
    failing across clones, branches or a reset, because the check sees only the ledger in
    front of it. The unseal and every holdout load must check the remote (T23).
14. **Medium, confirmed.** CI is not a gate: no check is required, so the pull request
    that weakens the harness can skip or delete its test, or merge red. Actions are pinned
    by tag rather than SHA (low on its own) (T28).
15. **Medium, likely.** G1.5 and G1.6 depend on G0.7 publishing, in a form code can read,
    the grid, the budgets and reserve rule, the counting rule for N, the out-of-sample
    windows and the holdout selection rule. G0.7 has not started and was due 11-07;
    without it, the tests for T17, T18, T19 and T22 cannot be written.
16. **Low, confirmed.** Ledger and commit timestamps come from the local clock. Nothing
    trusted may read it, and "early unseal" must be defined by what is in the chain, not by
    a date (T11).
17. **Low, confirmed.** muninn's recordings from 2026-10-27 overlap the holdout's last five
    days and sit outside the seal. They are not inputs to H1 or H2 (T21).
18. **Low, confirmed.** `fetch_rest_klines` has no holdout guard. The committed REST
    fixtures are from 2026-11-09 and hold no holdout rows, and the register's manifest hash
    matches the committed development manifest (T27).
19. **Low, confirmed.** On a machine where every actor is the same user, an entry written
    by hand cannot be told from one written by the harness, and signatures do not change
    that. Reproduction against a committed return series is the only authentication of an
    entry's content (T7).
20. **Informational.** Session transcripts are the best available trace of unledgered
    agent runs. They are spread across project folders and can be deleted, so the P2 audit
    should collect them at G2.7 rather than rely on them later (T2).

## Assumptions and limits

This model assumes GitHub's servers, Binance's archive as published, the Python
interpreter and the Mac itself are not compromised; a malicious archive publication would
be pinned faithfully by the manifest, and malware on the Mac could do anything an agent
can. It does not cover physical theft of the drive, which holds only public data. It is a
design review written before the ledger, the seal or the pre-registration exists; once they
are built, the security specialist reviews each against it, and this document is updated
where the built design differs.
