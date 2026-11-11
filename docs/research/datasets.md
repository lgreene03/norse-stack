# Research datasets: loaders, checksums and manifests (G1.1)

Owner: technical. This page says what the research harness can load, where it lives,
how each file is checked, and how the holdout is kept out of reach until the unseal
exists (G1.6). The coverage behind it, and why these instruments, is in
[data-coverage.md](data-coverage.md).

## What is loaded

Everything comes from Binance's public archive, data.binance.vision, under its
licence (CC BY-NC-SA 4.0: research and paper trading only, never live proprietary
trading). No API key, account, paid feed or trading endpoint is used anywhere.

| Series | Instruments | Why |
|---|---|---|
| Spot daily klines | every USDT spot pair the archive lists except stablecoins and fiat: 749 pairs | the input the dated universe is ranked from |
| Spot daily, four-hour and 15-minute klines | every pair H1's dated universe can select, delisted ones included, with BTC and ETH for H2: 115 pairs | H1's bars and fill prices, H2's spot leg |
| Funding and four-hour premium index | each of those pairs with a USDT-margined perpetual: 107, four of them listed as a multiple (1000PEPEUSDT and so on) | H2, and H1's funding-filter trial |
| Perpetual klines (daily, four-hour, 15-minute), daily and 15-minute premium index | BTCUSDT and ETHUSDT | H2's short leg and its fill prices |

The candidate list is `research/coverage/universe-candidates.json`, written by G0.6.
Daily premium index bars for the other pairs follow exactly from the four-hour ones.

## Where it lives

`NORSE_DATASETS_DIR`, set in norse-stack's git-ignored `.env` (on the owner's Mac,
`"/Volumes/Mac 4T/norse/datasets"`, on the external drive). The tools refuse to run
without it rather than guess a folder. Inside it, `data.binance.vision/` mirrors the
archive's own key layout, each file beside the `.CHECKSUM` it was verified against,
and `listings/` caches the archive listings with the date each was taken.

On 2026-11-11 the mirror held 51,756 archive files with their CHECKSUM files,
957,192,229 bytes (about 957 MB), every one verified.

## How each file is checked

1. The published `.CHECKSUM` is fetched first; the file is then fetched and its
   SHA-256 compared. A mismatch is an error and nothing is written. The file is
   written before its CHECKSUM, each by atomic rename, so a CHECKSUM on disk means a
   complete download.
2. Every response is size-capped (listing pages 2 MB, CHECKSUM files 1 KB, a monthly
   file 512 bytes per possible row plus 4 KB, so about 1.5 MB for a 15-minute month),
   and the CSV inside each zip is capped likewise before it is decompressed. A zip
   holding anything other than the one expected CSV is refused.
3. Every read from the mirror re-verifies the file against its stored CHECKSUM, so a
   file edited or corrupted on disk is refused.
4. The loader also checks each file's SHA-256, size and row count against the
   committed manifest, so a file the archive re-published since the manifest was
   written is refused too.

The fetcher is paced at no more than ten requests a second across all threads, keeps
connections alive, and retries only transient failures. A missing file (404) is
raised at once, never retried into silence.

## Manifests

Two committed files, one per period, never mixed:

* `research/manifests/binance-vision-development.json`: every file up to 2025-12;
* `research/manifests/binance-vision-holdout.json`: 2026-01 to 2026-10.

Each holds, per series, every monthly file as `[month, sha256, bytes, rows]` (the
archive key is `DatasetSpec.from_name(series).key(month)`), the series' row count,
first and last row, every gap found as `[after, before, missing]`, every row the
archive published twice identically, and every bar it published off the interval grid,
with the source and licence at the top. The files are written deterministically, one
series per line, so rebuilding from the same data reproduces the same bytes. A
manifest's SHA-256 is the dataset hash every result and ledger entry cites (G1.5):

| Manifest | Series | Files | Bytes pinned | SHA-256 |
|---|---|---|---|---|
| development | 1,047 | 43,239 | 808,613,752 | `c27499e97edd1d2288a7d38a7f0a890f9be108919dda22f7e3c9c119e725a55d` |
| holdout | 966 | 8,517 | 143,927,301 | `806e1d6acdd90c5454297b78c42c37cfb6f522ebcb81f01b773ba66b9308ea77` |

`python -m norse_research.datasets verify` re-reads every pinned file; on 2026-11-11
all 51,756 matched. `Manifest.load` refuses a development manifest that lists a 2026
month, and a holdout manifest that lists anything outside 2026-01 to 2026-10.

## Loading, and the seal

Research code gets rows only through `DatasetLoader(manifest, store).load(series,
start, end)`, with timezone-aware UTC bounds and `end` exclusive. It:

* refuses the holdout manifest outright, and any request whose end is after
  2026-01-01T00:00Z, with `HoldoutSealed`. There is no unseal in this code: G1.6 adds
  the single permitted one (the `prereg-v1` hash, the owner's key and a ledger
  entry). Two barriers hold until then, and each test was shown failing with its
  barrier removed: the time check, and the refusal of the holdout manifest. The
  development manifest also pins no 2026 file;
* refuses a series the manifest does not pin, and a month it does not list
  (`ManifestError`, `MissingData`), including a request that starts before a pair
  was listed;
* collapses a row the archive published twice identically only where the manifest
  records the repeat, and never returns a bar published off the interval grid (after
  the 2018-02-08 outage, for example); such bars must be recorded in the manifest,
  their slots count as a gap, and the result lists them;
* fails on any gap in the range (`GapError`) instead of skipping it. Exchange
  outages leave real gaps in the archive (listed in data-coverage.md); a caller that
  must cross one passes `accept_recorded_gaps=True`, which allows only gaps the
  committed manifest records and returns them with the rows so the run can record
  them. A gap the manifest does not record still fails.

Every result carries the manifest's SHA-256 and the files read.

## Archive rows against the REST API

`research/tests/test_rest_check.py` shows closed klines from the public REST API
(`data-api.binance.vision`, Binance's market-data-only host) equal the archive rows
field by field, matched by bar time, and that the comparison catches a changed value,
a missing bar on either side and an empty reply. Its recorded fixture is BTCUSDT on
2026-11-09, a day after the holdout, so it belongs to neither period; its timestamps
are microseconds, so it also proves the unit handling.

Equality does not hold everywhere, so the archive was swept against the API in full:
every development bar of BTCUSDT and ETHUSDT, daily, four-hour and 15-minute, through
the manifest and loader (`rest-sweep`, about 630 requests). The differences are in the
committed register `research/manifests/rest-differences.json`:

| Series | Bars compared | Differ |
|---|---|---|
| BTCUSDT daily | 3,059 | 11 |
| BTCUSDT four-hour | 18,337 | 15 |
| BTCUSDT 15-minute | 293,083 | 113 |
| ETHUSDT daily | 3,059 | 10 |
| ETHUSDT four-hour | 18,337 | 14 |
| ETHUSDT 15-minute | 293,083 | 110 |

What they are:

* **Incomplete archive bars.** In 10 daily, 11 four-hour and 27 15-minute bars of BTC
  (9, 10 and 24 for ETH) the archive has fewer trades than the API, and so less volume,
  and sometimes a different close, high or low: for example the 15-minute bars of
  2018-03-04 14:30 and 2021-04-23 01:30 to 02:30, and the daily bars of 2018-11-26,
  2019-10-01, 2021-01-11 and 2021-12-24. In every one the archive was the side short of
  trades.
* **The February 2018 restart.** For 2018-02-09 10:00 to 2018-02-10 05:45 the API has 81
  15-minute bars on the grid where the archive has its 81 off-grid ones; the loader
  sets the archive's aside and records the gap.
* **Bar boundaries around outages.** A handful of bars differ only in close time, or
  exist on one side only, around 2017-09-06, 2017-12-04, 2018-02-08, 2019-11-25 and
  2020-12-21.
* **None after 2022-04-13.** From 2022-04-14 to 2025-12-31 every bar of both coins, at
  all three intervals, is identical in the archive and the API.

The live test (`NORSE_LIVE_TESTS=1`) compares 24 development windows (BTC and ETH;
daily, four-hour and 15-minute; 2018-03, 2021-05, 2024-08 and 2025-06) and fails on
any difference not in the register. On 2026-11-11 it passed locally: 9,416 closed bars
compared, 2 differences, both recorded. It does not run in CI, because it needs the
local mirror and calls an external API from GitHub's runners, where Binance may refuse
service; the register test, which needs neither, does run there.

## Commands

From `research/`, with `NORSE_DATASETS_DIR` set or present in `.env`:

```bash
python -m norse_research.coverage universe    # rank the universe (G0.6)
python -m norse_research.datasets sync        # mirror every series above
python -m norse_research.datasets manifest    # rewrite both manifests
python -m norse_research.datasets verify      # re-verify every pinned file
python -m norse_research.datasets rest-check  # 24 windows against the live REST API
python -m norse_research.datasets rest-sweep  # every BTC and ETH bar; rewrites the register
python -m pytest tests/ -q                    # offline tests, as CI runs them
```

Each step is idempotent: files already mirrored and verified are not fetched again.

## Findings

1. **Medium, confirmed.** The archive is not always complete: before 2022-04-14, 10 of
   BTC's daily bars, 11 four-hour and 27 15-minute bars carry fewer trades than the
   exchange's own API reports (similar for ETH). Only BTC and ETH were swept; other
   pairs likely have the same defect on the same dates (likely, not checked). G0.7
   should say whether H1 and H2 take the archive as published (it is the pinned,
   reproducible source) or patch from the API (which cannot be pinned, and covers only
   pairs still listed).
2. **Medium, confirmed.** The live REST check cannot run in CI, so equality is proven
   locally and recorded, not re-proven on every pull request.
3. **Low, confirmed.** Development and holdout manifests together are 4.9 MB of JSON in
   git; regenerating them rewrites only the lines of series that changed.

## What this does not do yet

* Nothing here writes to the trial ledger (G1.5) or refuses a dirty tree; the
  harness does that. The loader's manifest hash is what the ledger will cite.
* The unseal is G1.6's. Until it exists the holdout files sit in the mirror, pinned by
  their own manifest, and cannot be returned by the loader.
* A monthly archive file is published once a month has closed; the tools never use
  the archive's daily files for development or holdout data.
