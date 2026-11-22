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
and writes a JSON file holding exactly the eight keys the genesis step's contract names (`kdf`,
`n`, `r`, `p`, `dklen`, `salt`, `verifier_sha256`, `signer_sha256`; the table is in
[trial-ledger.md](trial-ledger.md)). `verifier_sha256` is the SHA-256 of the scrypt output, and
`signer_sha256` the SHA-256 of the public key of a one-time signing key that the scrypt output
derives (decision D27, below). Neither is the output itself, and nothing ever reveals it: the
record is a commitment, and the unseal later proves knowledge of the passphrase by signing its
own entry. The command checks the file against the contract and against the passphrase once
more, and prints only the path and the file's SHA-256, which the owner keeps beside the
passphrase in the password manager. It never replaces an existing record. Both hashes are public
once committed, which is why the derivation is memory-hard: guessing a generated six-word
passphrase against them costs one scrypt per guess and is out of reach, guessing a short one a
person chose is not (T25). The format holds no field the repository's secret scan reads as a
credential, so committing it needs no change to the scan;
`research/tests/fixtures/key-record-test-passphrase-only.json`, a sample for a passphrase that
exists only in the tests, is scanned on every pull request to show it.

### The unseal's signature (decision D27)

The owner decided on 2026-11-22 to "Adopt the signature" (security review of #65, H-2). Until
then the unseal revealed the scrypt output as its proof. While the unseal's commit was built
and pushed, that proof sat in loose objects in the owner's temporary folder, which every process
of the owner's user can read, every agent session included. A copy of it could be put on another
unseal entry, at a commit nobody reviewed, and that entry verified. Now the proof is a signature
bound to the entry, so a copy authorises nothing but that same entry.

The scheme is Lamport's one-time signature over SHA-256, written with the standard library's
`hashlib` and `hmac` only (`research/norse_research/lamport.py`, whose docstring states it
exactly). The scrypt output derives a seed with HMAC-SHA256 under a fixed domain string, and the
seed derives 256 pairs of 32-byte secrets. The SHA-256 of each secret is its public half, and
`signer_sha256` is the SHA-256 of all 512 public halves in order. The unseal entry's `signature`
is `{"preimages": [...], "complements": [...]}`, two lists of 256 hashes of 64 lower-case hex
characters. It signs the SHA-256 of the entry's canonical line without the signature, so it
covers every other field: the candidate, the commit, the accepted diff hash, the previous entry's
hash, the sequence number and the timestamp. Bit `i` of that digest chooses which secret of
pair `i` the signature reveals, as `preimages[i]`, and `complements[i]` is the public half of the
other secret. To verify, the code hashes each preimage, puts the two halves of each pair in
order, and compares the SHA-256 of the result with `signer_sha256` in constant time. The
append, `verify()` and every replay check it against the record genesis pins.

One signature reveals one secret of each pair and is safe. A second signature over different
bytes would reveal both secrets of about half the pairs, and each further one weakens the key
fast: the security role's review of #70 (M-4) forged an unseal after five, in under a second.
So the owner pins everything the bytes depend on. The unseal's timestamp is its predecessor's
(the ledger commit's own date still records when it was written), and the command line names the
ledger head and the HEAD the owner reviewed (`--ledger-head` and `--unseal-commit`, which `seal
candidate` prints). If either has moved, the unseal refuses rather than sign other bytes. So the
identical command, run again after a failed push, signs the very same bytes or nothing. Never
sign anything else with this key. The owner's steps do not change: one passphrase, typed once.

Commit `research/keys/holdout-key-record.json` on main through a pull request. The genesis step
then names it (see trial-ledger.md), as `--key-record <that commit>:research/keys/holdout-key-record.json`,
and pins its commit, path and SHA-256 in the genesis entry. From then on the seal reads the record
only from that commit and checks it against that hash; a different record committed later, or
edited in the working tree, changes nothing. Whoever runs the genesis step chooses which record it
pins, so straight after it the owner runs, in a terminal of their own:

```bash
python -m norse_research.seal check-key
```

It reads the passphrase from the terminal, checks it against both of the commitments in the key
record the genesis entry on the remote pins, the verifier and the signer, prints that record's
SHA-256 for comparison with the one kept in the password manager, and writes nothing. It then
checks that the registration the genesis entry pins is, by the SHA-256 of both files, the one the
code pins (`gitledger.PREREGISTRATION_SHA256`), and prints those hashes, since whoever wrote
genesis chose the registration too, by whatever route (review of #70, L-11; the genesis step and
the ledger's append check it as well). If it refuses, the genesis entry pins a record that is not
the owner's, or not wholly made from the owner's passphrase, or a registration this code does not
implement, and a superseding genesis is still possible until the first entry of any other type
(section 16).

**When `prereg-v2` is tagged** (review of #70, L-12). The code pins the content of
`docs/PREREGISTRATION.md` and `docs/preregistration.json`, and a test holds that pin to the files
in the checkout, so the tag must go on a commit whose two files are exactly the ones the code
pins, and both files are frozen from the tag on. The lead tags main after #66's calibration
results, with the registration files unchanged since this pin. A tag made before a later edit to
either file could never pass genesis again, and only a `prereg-v3` with a code change would
recover; the genesis step's refusal names that case too.

## What happens at the unseal (G3.2, with the owner holding the key)

There is no ledger freeze (decision D21, 2026-11-21): the unseal closes development. No trial,
benchmark or control run may be opened after it (the result of one opened before it is still
accepted, and the gate ignores it), and N, V and the holdout candidate are computed on the
entries before it, so it comes once every run the owner means to make is in the ledger, after the
G2.7 ledger audit and after both README outcomes are drafted (G3.1). It is refused until each
hypothesis has a selectable run of every registered configuration or has exhausted its own
entries and the reserve (a hypothesis that ran out is FAIL at the verdict), so neither grid is
stopped early by the other's unseal (section 11, item 4). First see what the unseal must name,
which needs no key. Read, on GitHub's view of `ledger/trials.jsonl` on the `ledger` branch, the
commit of the holdout candidate's selectable run: the `commit` of its open entry, in full. Call it
C, and give it to the command:

```bash
python -m norse_research.seal candidate --candidate-commit <C>
```

If you do not yet know which run that is, give the commit of any completed trial there: the
command then refuses, naming the open entry the registered rule selects and its commit; check that
entry on GitHub and run the command again with the commit you read there. It prints, for each
hypothesis, its candidate and whether it meets DEV-1 to DEV-5 on the ledger as it stands
(provisional until the unseal), then the pre-registration the genesis entry names, the holdout
candidate's configuration id and hash, C and the hash of the open entry that ran at it
(`candidate_open_entry`), the data loading and seal files changed since that run, the two pins the
unseal needs (`unseal_commit`, HEAD now, and `ledger_head`, the ledger's head now), the owner's
unseal procedure with all of them filled in (`unseal_procedure`, below), and the git every ledger
command runs (`/Library/Developer/CommandLineTools/usr/bin/git` on the owner's Mac). It prints
neither the diff nor the hash that accepts it: both come only from `seal unseal`, which prints the
hash after the full diff, and only at an interactive terminal, so the hash is never seen without
what it covers (security review of #65, L-2 and I-7). If no hypothesis passes development there is
no holdout candidate, and then no unseal and no holdout run (section 11, item 3).

**Both `seal candidate` and `seal unseal` run C's code, or nothing** (security review of #70, H-3,
and round 2, H-4). Item 6 lets `seal.py` and data loading change after the candidate's run, and
`seal.py` is the code that renders the owner's review of exactly that change and reads the
passphrase; HEAD's `seal.py` could leave its own lines out of the diff and keep the passphrase.
So, before Python imports any module of the seal or of data loading, the package's launcher
(`relaunch.py`, run from `__init__.py`) takes C from `--candidate-commit` alone, spelt out in full.
It never chooses code from the clone's own `ledger` ref, which any process of the owner's user can
rewrite (H-4). It fetches the remote's `ledger` branch with the pinned git and refuses unless C is
the commit of a completed trial there: for `unseal`, the last completed trial of the configuration
hash the command names, which is how the gate picks a configuration's selectable run. If the
package on disk is C's, file for file, the command runs in place. Otherwise the launcher writes C's
package, from git's objects and without a checkout, into a new temporary folder, runs the same
command there with `--repo` naming your clone, and removes the folder afterwards. It says so on the
terminal: "seal: running with the code of commit C, the candidate's run". C's code then checks,
against the remote's verified ledger, that the registered rule selects the run at C and that every
module it loaded is C's, and refuses otherwise, all before any passphrase is asked for.

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

Otherwise the owner unseals, in a terminal of their own, by the procedure below. **Run the unseal
from C's own code, exported with plain git into a new folder, never from the clone** (review of
#70, round 2). Run from the clone, the launcher is HEAD's code: it lives outside the seal set, so a
change to it after the candidate's run is one item 6 never allows, but code that changes it can
also skip it and run HEAD's seal. Run from the exported folder, the launcher that runs is C's, and
it finds its own package is C's and runs in place. `seal candidate` prints these steps with every
value filled in (`unseal_procedure`):

1. On GitHub's view of `ledger/trials.jsonl` on the `ledger` branch, check that C is the commit of
   the open entry `seal candidate` named. Use the commit you read there, not the printed one.
2. `folder=$(mktemp -d)`
3. `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 /Library/Developer/CommandLineTools/usr/bin/git -C <clone> archive <C> research | tar -x -C "$folder"`
4. `cd "$folder/research"`
5. `python -m norse_research.seal --repo <clone> unseal --candidate-commit <C> --commit <the pre-registration commit> --configuration-sha256 <the candidate's hash> --unseal-commit <HEAD, as seal candidate printed it> --ledger-head <the ledger head, as seal candidate printed it>`
6. Without `--accept-diff`, step 5 prints the full diff, then its hash, and refuses. Once every
   line has been read, run step 5 again with `--accept-diff <the hash printed after the diff>`,
   and type the passphrase.
7. Then, back in the clone, fetch the holdout files (below).

Accept only a hash read in your own terminal straight after the diff, never one handed to you in a
prepared command line. `git archive` writes C's tree as committed, with no checkout, so no hook,
filter or ignore file of the clone runs. It does read the clone's `.git/info/attributes`, so a
rewritten one could leave files out of the export: C's launcher then finds its package is not C's
and runs C's code from git's objects instead, or the import fails. The `python` printed is the
interpreter `seal candidate` ran with.

**A second look, outside the repository's code** (security review of #70, H-3, option 3). To see
every change since the candidate's run with nothing of the repository's code in the way, run git
yourself with no configuration, from C to HEAD:

```bash
GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 /Library/Developer/CommandLineTools/usr/bin/git -c core.attributesFile=/dev/null diff --stat --no-textconv --no-ext-diff <C> HEAD -- research
GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 /Library/Developer/CommandLineTools/usr/bin/git -c core.attributesFile=/dev/null diff --no-textconv --no-ext-diff <C> HEAD -- research/norse_research
```

Only the seal and data loading files (`seal.py`, `loader.py`, `store.py`, `manifest.py`,
`formats.py`, `binance_archive.py`, `periods.py`) may appear, with the lines the unseal printed.
Any other file, `__init__.py`, `relaunch.py` or `environment.py` above all, is an incident: stop.
A file a `-diff` attribute marks shows as "Binary files differ", which is visible, not hidden.

`--tag` defaults to the tag the genesis entry names. Before it asks for the passphrase it refuses
on any environment override; a local ledger head that is not the remote's (T23), or that is not
the `--ledger-head` given; a ledger that fails anchored verification; a ledger on which a
hypothesis has neither a selectable run of every registered configuration nor its own entries and
the reserve used up, which is what an early unseal means here (T20; an unseal while the other grid
can still run would stop it early); a ledger that already holds an unseal entry (T23); a dirty
tree, a HEAD other than the `--unseal-commit` given, a HEAD not on main, or code running that is not
the code of the candidate's commit; period constants that differ from the genesis entry's (T20); a
pre-registration commit, tag or file hash other than the genesis entry's (T24); a configuration
hash other than the holdout candidate's, which it computes by the registered rule over the ledger
before the unseal (T22), or a `--candidate-commit` other than the commit of that candidate's
selectable run (H-4); a
HEAD at which anything but data loading or the seal has changed since the candidate's selectable
run, or any `.gitattributes` file has (section 11, item 6, applied here once; the entry records
that HEAD); and an accepted diff hash that is not the hash of the data loading and seal diff from
the candidate's commit to HEAD. Without `--accept-diff`, or with another hash, it prints the full
diff of the files changed and only then the hash, and refuses, so the owner reads every data
loading or seal change before anything is unsealed; with nothing changed it prints the hash of the
empty diff to accept (security review round 3, T1). The diff is rendered by the seal itself, in
Python, from the very blobs the hash names, never by git's own `git diff`: the clone's attributes,
text conversion filters and settings, which an agent can change without leaving a trace in `git
status`, cannot hide or alter a line, and every unprintable character (a tab, a carriage return,
a terminal escape, a bidirectional override) is written as an escape (security review of #65,
M-1). So is every non-ASCII character, a homoglyph such as a Cyrillic letter in `json` included,
and each line holding such an escape is flagged by the line before it, so it is told apart from a
backslash written in the code (review of #70, L-10). A line wider than 100 characters is flagged
by the line before it and shown in pieces of at most 100, each ending in `|`, so padding cannot
push a change past the right edge of the terminal or of a pager that cuts long lines (L-10). Read
the diff it prints; the second look above is a cross-check, not a substitute.

The hash is printed only when an interactive terminal is attached, the test the passphrase prompt
applies; elsewhere, as in an agent's shell, the refusal prints the diff and says the hash is shown
only in a terminal (security review of #65, I-7). The alternative was to ask for the passphrase
before showing the hash. It was not chosen because the owner would then type the passphrase twice
for every review, once to see the hash and again to unseal, where D27 keeps it to once, and
because it would protect nothing more: anyone can compute the hash from git's plumbing (the
SHA-256 of `git diff-tree -r --no-renames --no-abbrev` over the seal's files). Both are speed
bumps. The control is the owner accepting only a hash read in their own terminal straight after
the diff.

Only then does it read the passphrase from the terminal and check, in constant time, that the
SHA-256 of its scrypt output is the key record's verifier and that the one-time public key it
derives is the record's signer (T25; D27). A missing or wrong passphrase writes nothing.

When the passphrase verifies, the unseal entry is appended through the ledger's ordinary append,
signed, committed, pushed and read back from the remote. It names the pre-registration, the
candidate's configuration id, hypothesis and hash, the open and result entries of its selectable
run, the commit the holdout runs at, the accepted diff hash, and the hashes of the holdout manifest
and the key record, and it carries the one-time signature over all of that (D27, above). The ledger
itself checks the signature against the `signer_sha256` of the key record genesis pins, when the
entry is written and on every replay, so an unseal written without the owner's key, by the seal, by
a direct append or by git plumbing, is refused, and with it the re-fetch and the holdout run
(security review H1). A copy of the signed entry, taken while it is pushed, authorises nothing but
that same entry at the commit the owner reviewed (H-2); a rewrite of the remote to replay it is
T10's. Every git command the ledger and the seal run has hooks and the file system monitor turned
off, and a clone that configures either is refused, since a hook in the clone's untracked
`.git/hooks` could copy the entry out of the unseal's push and fail it (security review of #65,
H-1). Any process of the owner's user can still fail the push, by reading the temporary objects or
by stopping the seal, so an unseal whose push fails is an incident. Just before it pushes, the
unseal prints the hash of the entry it signed, and its failure names that hash again (review of
#70, I-10). Then:

- If the remote's ledger holds an unseal entry with exactly that hash, it is yours, byte for
  byte, pushed by another clone that copied it; the unseal is done, so go on to `fetch-holdout`.
- If it holds another unseal entry, stop: that is an incident.
- If it holds none, check the clone's `.git/hooks` and configuration, and run the identical
  command again, pins and all. It signs the very same bytes. If it refuses because the ledger
  or HEAD has moved since `seal candidate` printed them, stop: that refusal is the incident, an
  attempt to make the key sign twice (review of #70, M-4). Do not change a pin to get past it.
  If you ever do, after finding the cause, do it once: two different signatures leave a forger
  about 2^126 work, but each further one cuts that drastically.

That denial is detected, not prevented; it can no longer put an unseal at a commit of anyone
else's.

Every ledger and seal git command runs with its configuration pinned (security review of #65,
M-3): `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1`, so nothing in the owner's global
configuration (`~/.gitconfig`) or the system's (`/opt/homebrew/etc/gitconfig` on the owner's Mac),
both of which an agent running as the owner can write, reaches it. The commands that reach the
remote, and only those, carry `-c credential.helper= -c credential.helper=osxkeychain` on the command
line: the empty value clears every helper a file names, so the owner's osxkeychain helper is the
only credential program that can run (`gitledger.CREDENTIAL_HELPER`). Git also gets an environment
built from a short allowlist rather than the shell's: the home folder, the locale, the temporary
folder, an ssh agent's socket, the switch that turns git's own prompt off, and a `PATH` that is the
git's own folder followed by the system's default (`environment.PASSED_ENVIRONMENT`). So a filter,
text conversion, hook, askpass or credential program written into either configuration file, and
a variable that moves the configuration (`XDG_CONFIG_HOME`), routes the network (`HTTPS_PROXY`),
changes trust (`SSL_CERT_FILE`), loads code (`DYLD_INSERT_LIBRARIES`) or picks the developer
tools (`DEVELOPER_DIR`), never reaches a ledger command (L-6). The owner no longer needs to
inspect the global and system configuration before the unseal.

**The git itself is the Command Line Tools' git, named in full, always** (review of #70, L-6 and
round 2): `/Library/Developer/CommandLineTools/usr/bin/git` on macOS (`environment.GIT_PROGRAM`),
and `/usr/bin/git` on Linux, where CI runs. No ledger, seal or launcher command looks git up on
`PATH`, so a wrapper an agent puts first on `PATH`, by an edit to the shell profile, never runs.
Nor does any go through macOS's `/usr/bin/git`, which is not git but a shim that asks `xcrun` for
the developer folder, and `xcrun` keeps a cache that any process of the owner's user can write
(`xcrun_db` in the user's temporary folder, present on the owner's Mac on 2026-11-22). The Command
Line Tools' git is 2.50.1 on 2026-11-22, and its exec path,
`/Library/Developer/CommandLineTools/usr/libexec/git-core`, holds Apple's own
`git-credential-osxkeychain`. That folder is not under System Integrity Protection, as `/usr/bin`
is, so ownership is the control: the code refuses to run unless the binary, its exec path, the
helper there and every folder above them belong to root and are writable by no group or other user
(`environment.git_program`), so neither the owner's user nor any agent can replace them. They do on
the owner's Mac. `seal candidate` prints the path, and the unseal prints it again when it is
written. On 2026-11-22 a dry-run push through exactly this command line, to a branch name that does
not exist, authenticated with the owner's existing GitHub credential and sent nothing, so the push
works with it. The genesis step and every run push the same way from the same clone, so a push that
worked for them works at the unseal.

Inside the clone, the refusals stay as belt and braces, and are now an allowlist (review of #70,
L-5). The clone's own configuration, its local and worktree scopes and any file they include, may
hold only these keys (`gitledger.REPOSITORY_SCOPE_ALLOWED`): what `git init` and `git clone` write
(`core.repositoryformatversion`, `core.filemode`, `core.bare`, `core.logallrefupdates`,
`core.ignorecase`, `core.precomposeunicode`, `core.symlinks`), `core.fsmonitor` set to false,
`extensions.worktreeconfig`, `extensions.objectformat` and `extensions.refstorage`, each remote's
`url`, `fetch` and `pushurl`, each branch's `remote` and `merge`, and `user.name` and
`user.email`. Every other key is refused, whatever it does: a credential helper, an askpass or
editor program, a filter or text conversion, an include, a URL rewrite, and also the keys the
review found a denylist let through (`http.curloptResolve`, `fetch.bundleURI`,
`core.attributesFile`, `gpg.ssh.defaultKeyCommand`, `submodule.*`, `alias.*`, a promisor remote),
and any key a later git adds. On 2026-11-22 the owner's clone held nothing else. The ledger, the
harness and the seal refuse such a clone before anything is written, and the unseal refuses it
before the passphrase is asked for. The unseal also refuses `GIT_ASKPASS` and `SSH_ASKPASS` in its
environment, although no ledger git command inherits either any more; an editor's built-in terminal
may set `GIT_ASKPASS`, so unseal from a plain terminal. `GIT_SSH`, `GIT_SSH_COMMAND`,
`GIT_EXEC_PATH`, `GIT_CONFIG_PARAMETERS` and every other `GIT_*` variable outside a short allowlist
are refused for every ledger write. The clean-tree check reads no ignore or attributes file under
the home folder, so `~/.config/git/ignore` cannot hide an untracked file from it (review of #70,
I-8). What is left is the Python interpreter, which belongs to the owner's user (T15), and the
files it reads, which a process of that user can rewrite on disk: the Python files the seal
imports, between the moment they are written and the moment they are imported, and the virtual
environment's `site-packages`, whose `.pth` files Python runs at start-up, before any of the
seal's code. The loaded-modules check finds a changed seal module, but only after it ran, and
nothing finds a `.pth` file.

Once the unseal entry is on the remote, the holdout files are fetched again (D17), as a separate
step, at HEAD, in your clone: data loading is the code item 6 lets differ from the candidate's,
so the re-fetch uses HEAD's, not the candidate's the unseal ran with (review of #70, H-3):

```bash
python -m norse_research.seal fetch-holdout
```

It needs no key. It fetches every file the frozen holdout manifest pins, about 8,517 of them, each
checked against its published CHECKSUM and against the manifest's SHA-256 and size before it is
written, and then verifies the whole manifest, rows included. The first failure stops it, before
any holdout return exists. If the archive has re-published a file since 2026-11-11, restore that
file from the encrypted offline copy into the mirror and run it again: it keeps every file that
already matches its pin and fetches the rest. The unseal itself is never repeated.

Verification replays all of this on every later read of the ledger: a second unseal entry, an
early unseal, one whose signature does not verify against the pinned record's signer, one naming
another pre-registration, key record or holdout manifest, one naming any configuration other than
the candidate the rule selects on the entries before it, or one recording a diff hash other than
that of the data loading and seal diff from the candidate's commit to its own, each fails
verification, so the harness and the gate refuse the ledger. Replay recomputes that hash from git's
raw record without rendering the diff, so a large data loading change costs no more to verify
than a small one (security review of #65, L-7). The ledger's append makes the same checks before
it pushes the entry, so none of them can be broken through it.

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

**Who starts a holdout run (decision D25).** Only the owner, and straight after the unseal; no
agent, scheduled task or second clone starts one. This is a written rule, and the code does not
enforce it. Starting a holdout run needs no key, so an agent could still use up the three
attempts without it, by killing a run straight after its attempt entry, or by starting one from a
second clone while the owner's is in flight, which closes the owner's attempt as abandoned and
costs two attempts for one (security review of #65, M-2, the residual of round 3's C1). A holdout
run started by anyone but the owner is an incident. D27 gave the key record a signing key for the
unseal only; attempts still need no key.

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
fail, so the unseal is refused while a benchmark DEV-2 needs lacks one; no benchmark run may be
opened after the unseal, and the result of one opened before it is accepted and ignored (security
review of #65, L-1 and I-5).
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

**What "the seal" means in item 6** (the quant role's ruling on #70; the quant is an AI agent the
owner runs, and its ruling is advice, not an approval). Item 6 lets "commits that touch data
loading or the seal" follow the candidate's run, and names no file; the list lives in code
(`gitledger.SEAL_AND_DATA_LOADING`) and here. It holds the reading side of the seal and the
owner's own client, `seal.py`, and never the verification of the owner's signature:
`lamport.py`, and the checks in `gitledger.py` (`unseal_signature_error`, `pinned_key_record`,
`check_key_record`) and `ledger.py` (`signed_bytes`), stay out, so the code that decides at the
holdout run whether the owner signed is byte for byte the code the trials ran under. The seal's
launcher (`relaunch.py`, `environment.py` and `__init__.py`) stays out too, since it runs before
the candidate's code is loaded. A test fails if the set widens. Its price: a fault in
`lamport.py` found after the trials that refuses the owner's genuine signature (its derivation
unchanged) could be fixed only by a rerun of every configuration already run, since
`lamport.py` is harness code both hypotheses use (run rules 3 and 4). With both grids complete
that is 14 more entries, N from 14 to 28, which spends the one harness rerun the budget allows for
(28 of 30 entries), and lifts the DEV-1 marks from 1.22 to 1.33 for H1 and from 1.38 to 1.51 for
H2 (section 12's assumptions; for a true Sharpe ratio of 1.5, a pass from about 0.78 to 0.68 for H1
and 0.61 to 0.49 for H2). The alternative is a holdout that stays sealed. The quant judges such a
fault unlikely, since signing and verifying behave the same whatever the message and the key, and
the tests run the whole path; the owner should accept this price explicitly, as the data loading
price above.

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

**Replaying the unseal depends on the code at verify time (L5).** Verification recomputes the
holdout candidate with the `gate`, `sharpe`, `cscv` and `benchmark` modules as they are when it runs,
not as they were at the unseal. It also recomputes the accepted diff over the seal file list,
`gitledger.SEAL_AND_DATA_LOADING`, and whether the unseal was early through `rules.py`'s budget rule
(`rules.budget_exhausted`). And it runs these functions of `gitledger.py` (security review of #65,
L-8): `holdout_commit_error`, with `PACKAGE_PATH` and `ATTRIBUTES_PATHSPEC`, for item 6's rule;
`seal_digest` and `seal_record` for the accepted diff's hash; `seal_changes` and `render_diff`, which
render that diff for the unseal's refusal and share the record replay hashes; and the signature
check, `unseal_signature_error`, with `pinned_key_record`, `check_key_record`,
`ledger.signed_bytes` and the whole of `lamport.py`. `unseal_signature_error` replaced
`unseal_proof_error` under D27; nothing calls the old check any more. A later change to any of them
could make the ledger fail verification for good, or pass something it would not have passed
before, or change the candidate replay expects; adding or removing a module in the seal file list
after the unseal would raise a false tamper alarm (security review of #65, L-3). The control is
process: from the unseal on, those four modules, `rules.py`, `lamport.py`, the seal file list and
the `gitledger.py` and `ledger.py` functions just named are not changed without a recorded defect
and a security review, and G3.4 checks that they are unchanged since the unseal entry's commit.

## What this does not cover

A deliberate download of the public holdout outside the harness (T21): nothing prevents it, and the
mechanical candidate rule is what makes such a look useless. A key record substituted at genesis
(T25(c)): whoever runs the genesis step chooses the record, and `make_key_record` needs no terminal,
so an agent could pin a record for a passphrase it knows; `check-key` straight after genesis is the
control, and it is process, not prevention. An agent that somehow holds the passphrase can fake a
terminal; the control is that the passphrase never reaches one. A process of the owner's user that
fails the unseal's push, or stops the seal, denies the iteration's single unseal; since D27 that is
all it can do, and it is detected, not prevented; and since the pins, it cannot make the key sign
twice either (review of #70, M-4). A change after the candidate's run to the seal's launcher
(`relaunch.py`, `environment.py`, `__init__.py`), which item 6 never allows, could skip the launcher
and run HEAD's code when the unseal is run from the clone; the owner's procedure runs it from C's
exported code instead, and the second look shows such a change (review of #70, H-3 and round 2).
Which configuration the registered rule selects is computed by code, C's own: the owner checks on
GitHub that C is the commit of the open entry it names, not the selection itself, so a `seal.py`
that was already hostile when it was merged to main, and ran a completed trial, could name its own
run. Every trial runs at a commit on main, so that change went through a pull request. A process of
the owner's user that rewrites files the interpreter reads, on disk, runs its code before any
check: the Python files the seal is about to import, the candidate's exported copy included, and
the virtual environment's `site-packages`, a `.pth` file there above all, which Python runs at
start-up and which also decides what `import norse_research` finds where the export lacks it. That
is the interpreter's tier (T15). The G3.1 drafts, as above. The research gateway (G1.2), which runs no
holdout run and must keep its run endpoint off for H1 and H2.
