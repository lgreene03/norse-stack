# Manifest audit, 2026-11-21 (G2.5)

Owner: security. This is the manifest half of goal G2.5: every development dataset file
re-verified against its committed manifest, and every gap the manifests and the coverage
notes record listed in one place. The licence half is [DATA_LICENCES.md](../../DATA_LICENCES.md).
Nothing here is a trial. The verify step checks hashes, sizes and row counts and computes no
statistic of a price; the gap lists below were read from the committed manifest JSON (bar
times and slot counts), never from the archive files.

## What was run

On 2026-11-21, from a worktree at origin/main `fb66ac6`, with the store on the owner's
external drive:

```bash
NORSE_DATASETS_DIR="/Volumes/Mac 4T/norse/datasets" \
  PYTHONPATH=research python3 -m norse_research.datasets verify
```

The package's own command was used, from the worktree rather than the main checkout
because the main checkout was two commits behind origin/main (its `manifest.py` and
`store.py` differ); the three committed manifests are byte-identical in both. `verify`
finds the store from `NORSE_DATASETS_DIR` (environment first, then the repository's
git-ignored `.env`, never a guessed default) and re-reads every file the development
manifest pins through `ArchiveStore.read`, which checks each file against its stored
`.CHECKSUM`, then compares SHA-256, size and row count with the manifest.

## Result

```
development: 43239 files match, sha256 c27499e97edd1d2288a7d38a7f0a890f9be108919dda22f7e3c9c119e725a55d
holdout: committed manifest unchanged, sha256 806e1d6acdd90c5454297b78c42c37cfb6f522ebcb81f01b773ba66b9308ea77
holdout: its 8517 files are off disk until the unseal (G1.6), not verified
```

Exit status 0. All 43,239 development files (1,047 series, 808,613,752 bytes) match their
manifest entries in hash, size and rows; there is no mismatch to report. The holdout
manifest still has its frozen SHA-256, and its 8,517 files are off disk by design
(decision D17): they were neither fetched nor opened, and are re-verified only at the
unseal (G1.6).

`verify` does not report files in the mirror that no manifest pins, so the mirror's file
names were also compared with the manifest's keys, without opening any file. The mirror
holds exactly 43,239 archive files and 43,239 `.CHECKSUM` files, one pair for every pinned
key, with no unpinned file, no orphan CHECKSUM, no leftover temporary file from an atomic
write, and no file dated 2026 or later.

## Gaps the manifests record

The development manifest records 1,367 gaps in 201 of its 1,047 series, 57,033 missing
slots in all, in 92 distinct windows (an exchange-wide outage shows once per window with
the number of series it touched). It records no repeated row in development, and 495 bars
set aside off the interval grid: 81 in each of the six 15-minute series trading after the
February 2018 outage (BCC, BNB, BTC, ETH, LTC and NEO, from 2018-02-09 09:58) and 9 in
ONTUSDT's four-hour series on 2018-06-26 and 27; their slots count in the gaps below.

| Series kind | Gap records |
|---|---|
| spot klines 15m | 976 |
| spot klines 4h | 166 |
| spot klines 1d | 19 |
| um premiumIndexKlines 4h | 179 |
| um premiumIndexKlines 15m | 14 |
| um premiumIndexKlines 1d | 8 |
| um fundingRate | 5 |

Every window follows. "After" is the last row before the gap and "Before" the first row
after it, both UTC; "Missing" is the slots missing in each series of that window. The four
funding records with 0 missing are settlements at an irregular interval (FTT and YGG
changed interval), recorded as gaps but with no settlement absent.

| After | Before | Series | Kind | Pairs | Missing |
|---|---|---|---|---|---|
| 2017-09-06 12:00 | 2017-09-06 20:00 | 2 | spot klines 4h | BTCUSDT, ETHUSDT | 1 |
| 2017-09-06 15:45 | 2017-09-06 23:00 | 2 | spot klines 15m | BTCUSDT, ETHUSDT | 28 |
| 2017-12-18 12:15 | 2017-12-18 13:30 | 6 | spot klines 15m | BCCUSDT, BNBUSDT, BTCUSDT, ETHUSDT, LTCUSDT, NEOUSDT | 4 |
| 2018-01-04 03:00 | 2018-01-04 05:00 | 6 | spot klines 15m | BCCUSDT, BNBUSDT, BTCUSDT, ETHUSDT, LTCUSDT, NEOUSDT | 7 |
| 2018-02-08 00:00 | 2018-02-09 08:00 | 6 | spot klines 4h | BCCUSDT, BNBUSDT, BTCUSDT, ETHUSDT, LTCUSDT, NEOUSDT | 7 |
| 2018-02-08 00:15 | 2018-02-10 06:15 | 6 | spot klines 15m | BCCUSDT, BNBUSDT, BTCUSDT, ETHUSDT, LTCUSDT, NEOUSDT | 215 |
| 2018-02-11 03:45 | 2018-02-11 04:30 | 3 | spot klines 15m | BCCUSDT, LTCUSDT, NEOUSDT | 2 |
| 2018-02-11 04:00 | 2018-02-11 04:30 | 3 | spot klines 15m | BNBUSDT, BTCUSDT, ETHUSDT | 1 |
| 2018-06-26 00:00 | 2018-06-26 12:00 | 13 | spot klines 4h | 13 pairs | 2 |
| 2018-06-26 00:00 | 2018-06-27 12:00 | 1 | spot klines 4h | ONTUSDT | 8 |
| 2018-06-26 01:45 | 2018-06-26 12:00 | 12 | spot klines 15m | 12 pairs | 40 |
| 2018-06-27 12:45 | 2018-06-27 14:45 | 14 | spot klines 15m | 14 pairs | 7 |
| 2018-07-04 00:00 | 2018-07-04 08:00 | 14 | spot klines 4h | 14 pairs | 1 |
| 2018-07-04 00:15 | 2018-07-04 08:00 | 14 | spot klines 15m | 14 pairs | 30 |
| 2018-07-23 00:00 | 2018-10-19 00:00 | 1 | spot klines 1d | VENUSDT | 87 |
| 2018-10-19 05:45 | 2018-10-19 09:30 | 15 | spot klines 15m | 15 pairs | 14 |
| 2018-11-14 00:00 | 2018-11-14 08:00 | 15 | spot klines 4h | 15 pairs | 1 |
| 2018-11-14 01:45 | 2018-11-14 09:00 | 15 | spot klines 15m | 15 pairs | 28 |
| 2018-11-15 00:00 | 2018-11-20 00:00 | 1 | spot klines 1d | BCCUSDT | 4 |
| 2018-11-15 16:00 | 2018-11-20 00:00 | 1 | spot klines 4h | BCCUSDT | 25 |
| 2018-11-15 16:30 | 2018-11-20 02:45 | 1 | spot klines 15m | BCCUSDT | 424 |
| 2019-03-12 00:00 | 2019-03-12 08:00 | 20 | spot klines 4h | 20 pairs | 1 |
| 2019-03-12 01:45 | 2019-03-12 08:00 | 20 | spot klines 15m | 20 pairs | 24 |
| 2019-05-15 00:00 | 2019-05-15 12:00 | 25 | spot klines 4h | 25 pairs | 2 |
| 2019-05-15 02:45 | 2019-05-15 13:00 | 25 | spot klines 15m | 25 pairs | 40 |
| 2019-06-07 21:00 | 2019-06-07 22:15 | 26 | spot klines 15m | 26 pairs | 4 |
| 2019-08-15 00:00 | 2019-08-15 08:00 | 30 | spot klines 4h | 30 pairs | 1 |
| 2019-08-15 01:45 | 2019-08-15 10:00 | 30 | spot klines 15m | 30 pairs | 32 |
| 2019-11-13 01:45 | 2019-11-13 04:15 | 32 | spot klines 15m | 32 pairs | 9 |
| 2019-11-25 01:45 | 2019-11-25 04:00 | 32 | spot klines 15m | 32 pairs | 8 |
| 2020-01-17 00:00 | 2020-02-03 00:00 | 2 | spot klines 1d | ETHBEARUSDT, ETHBULLUSDT | 16 |
| 2020-01-17 12:00 | 2020-02-03 08:00 | 2 | spot klines 4h | ETHBEARUSDT, ETHBULLUSDT | 100 |
| 2020-01-17 12:30 | 2020-02-03 10:00 | 2 | spot klines 15m | ETHBEARUSDT, ETHBULLUSDT | 1,621 |
| 2020-01-19 13:00 | 2020-01-19 13:30 | 2 | um premiumIndexKlines 15m | BTCUSDT, ETHUSDT | 1 |
| 2020-02-09 01:45 | 2020-02-09 03:00 | 35 | spot klines 15m | 35 pairs | 4 |
| 2020-02-19 08:00 | 2020-02-19 16:00 | 35 | spot klines 4h | 35 pairs | 1 |
| 2020-02-19 11:30 | 2020-02-19 17:30 | 35 | spot klines 15m | 35 pairs | 23 |
| 2020-03-04 09:15 | 2020-03-04 11:30 | 35 | spot klines 15m | 35 pairs | 8 |
| 2020-04-25 01:45 | 2020-04-25 04:30 | 33 | spot klines 15m | 33 pairs | 10 |
| 2020-06-28 01:45 | 2020-06-28 05:30 | 34 | spot klines 15m | 34 pairs | 14 |
| 2020-11-09 00:00 | 2021-04-14 00:00 | 2 | spot klines 1d | BCHDOWNUSDT, BCHUPUSDT | 155 |
| 2020-11-30 05:45 | 2020-11-30 07:00 | 49 | spot klines 15m | 49 pairs | 4 |
| 2020-12-01 22:30 | 2020-12-02 00:00 | 2 | um premiumIndexKlines 15m | BTCUSDT, ETHUSDT | 5 |
| 2020-12-21 14:00 | 2020-12-21 18:00 | 50 | spot klines 15m | 50 pairs | 15 |
| 2020-12-25 01:45 | 2020-12-25 03:00 | 50 | spot klines 15m | 50 pairs | 4 |
| 2021-01-19 00:00 | 2021-01-23 00:00 | 1 | spot klines 1d | COCOSUSDT | 3 |
| 2021-02-11 03:30 | 2021-02-11 05:00 | 51 | spot klines 15m | 51 pairs | 5 |
| 2021-03-06 01:45 | 2021-03-06 03:30 | 51 | spot klines 15m | 51 pairs | 6 |
| 2021-03-15 00:00 | 2021-03-19 00:00 | 1 | spot klines 1d | BTCSTUSDT | 3 |
| 2021-03-29 00:00 | 2021-04-02 00:00 | 1 | spot klines 1d | DREPUSDT | 3 |
| 2021-04-20 01:45 | 2021-04-20 04:30 | 53 | spot klines 15m | 53 pairs | 10 |
| 2021-04-25 04:00 | 2021-04-25 08:45 | 53 | spot klines 15m | 53 pairs | 18 |
| 2021-06-14 00:00 | 2021-06-18 00:00 | 1 | spot klines 1d | SUNUSDT | 3 |
| 2021-06-30 00:00 | 2021-07-02 00:00 | 2 | um premiumIndexKlines 1d | BTCUSDT, ETHUSDT | 1 |
| 2021-06-30 20:00 | 2021-07-02 00:00 | 47 | um premiumIndexKlines 4h | 47 pairs | 6 |
| 2021-06-30 23:45 | 2021-07-02 00:00 | 2 | um premiumIndexKlines 15m | BTCUSDT, ETHUSDT | 96 |
| 2021-07-23 00:00 | 2021-07-28 00:00 | 2 | um premiumIndexKlines 1d | BTCUSDT, ETHUSDT | 4 |
| 2021-07-23 20:00 | 2021-07-28 00:00 | 48 | um premiumIndexKlines 4h | 48 pairs | 24 |
| 2021-07-23 23:45 | 2021-07-28 00:00 | 2 | um premiumIndexKlines 15m | BTCUSDT, ETHUSDT | 384 |
| 2021-08-13 01:45 | 2021-08-13 06:30 | 56 | spot klines 15m | 56 pairs | 18 |
| 2021-09-29 06:45 | 2021-09-29 09:00 | 58 | spot klines 15m | 58 pairs | 8 |
| 2022-05-13 00:00 | 2022-05-31 00:00 | 1 | spot klines 1d | LUNAUSDT | 17 |
| 2022-05-13 00:00 | 2022-05-31 04:00 | 1 | spot klines 4h | LUNAUSDT | 108 |
| 2022-05-13 00:30 | 2022-05-31 06:00 | 1 | spot klines 15m | LUNAUSDT | 1,749 |
| 2022-07-12 04:00 | 2023-03-30 12:00 | 1 | um premiumIndexKlines 4h | TLMUSDT | 1,567 |
| 2022-07-12 13:45 | 2022-07-12 14:30 | 2 | um premiumIndexKlines 15m | BTCUSDT, ETHUSDT | 2 |
| 2022-09-11 00:00 | 2022-10-01 00:00 | 1 | spot klines 1d | NBTUSDT | 19 |
| 2022-10-01 00:00 | 2022-10-03 00:00 | 2 | um premiumIndexKlines 1d | BTCUSDT, ETHUSDT | 1 |
| 2022-10-01 20:00 | 2022-10-03 00:00 | 29 | um premiumIndexKlines 4h | 29 pairs | 6 |
| 2022-10-01 23:45 | 2022-10-03 00:00 | 2 | um premiumIndexKlines 15m | BTCUSDT, ETHUSDT | 96 |
| 2022-10-31 00:00 | 2022-11-09 00:00 | 1 | spot klines 1d | VIDTUSDT | 8 |
| 2022-11-10 00:00 | 2022-11-10 04:00 | 1 | um fundingRate | FTTUSDT | 0 |
| 2022-11-14 02:00 | 2022-11-14 08:00 | 1 | um fundingRate | FTTUSDT | 0 |
| 2022-11-15 00:00 | 2023-09-22 00:00 | 1 | spot klines 1d | FTTUSDT | 310 |
| 2022-11-15 04:00 | 2023-09-22 08:00 | 1 | spot klines 4h | FTTUSDT | 1,866 |
| 2022-11-15 04:15 | 2023-09-22 08:00 | 1 | spot klines 15m | FTTUSDT | 29,870 |
| 2022-12-09 00:00 | 2023-05-12 00:00 | 1 | spot klines 1d | CVCUSDT | 153 |
| 2023-02-10 00:00 | 2023-03-10 00:00 | 1 | spot klines 1d | KEYUSDT | 27 |
| 2023-02-16 00:00 | 2023-02-22 00:00 | 1 | spot klines 1d | BNXUSDT | 5 |
| 2023-02-23 00:00 | 2023-02-25 00:00 | 1 | um premiumIndexKlines 1d | BTCUSDT | 1 |
| 2023-02-23 20:00 | 2023-02-25 00:00 | 15 | um premiumIndexKlines 4h | 15 pairs | 6 |
| 2023-02-23 23:45 | 2023-02-25 00:00 | 1 | um premiumIndexKlines 15m | BTCUSDT | 96 |
| 2023-03-24 12:30 | 2023-03-24 14:00 | 67 | spot klines 15m | 67 pairs | 5 |
| 2023-04-08 00:00 | 2023-04-10 00:00 | 1 | um premiumIndexKlines 1d | ETHUSDT | 1 |
| 2023-04-08 20:00 | 2023-04-10 00:00 | 38 | um premiumIndexKlines 4h | 38 pairs | 6 |
| 2023-04-08 23:45 | 2023-04-10 00:00 | 1 | um premiumIndexKlines 15m | ETHUSDT | 96 |
| 2023-07-17 00:00 | 2023-07-21 00:00 | 1 | spot klines 1d | QUICKUSDT | 3 |
| 2023-08-07 08:00 | 2023-08-07 14:00 | 1 | um fundingRate | YGGUSDT | 0 |
| 2023-08-08 12:00 | 2023-08-08 16:00 | 1 | um fundingRate | YGGUSDT | 0 |
| 2024-03-20 00:00 | 2024-03-28 00:00 | 1 | spot klines 1d | STRAXUSDT | 7 |
| 2025-06-19 08:00 | 2025-07-23 08:00 | 1 | um fundingRate | SLPUSDT | 203 |
| 2025-07-10 08:00 | 2025-07-23 08:00 | 1 | um premiumIndexKlines 4h | SLPUSDT | 77 |

The table was produced from the committed manifest with a short script that groups each
series' `gaps` array by its `[after, before]` pair; rerunning it on the same manifest
reproduces it exactly, so the manifest, not this page, is the record.

## Differences from the REST API

The committed register `research/manifests/rest-differences.json` (built against the same
development manifest hash) records 273 bars, out of 628,958 compared, where the archive and
Binance's public REST API disagree, for BTCUSDT and ETHUSDT at 1d, 4h and 15m. They fall
into the three kinds [datasets.md](datasets.md) describes: archive bars short of trades,
and so of volume and sometimes of close, high or low (most of them); the February 2018
restart, where the API has 81 on-grid 15-minute bars for each coin in place of the
archive's 81 off-grid ones; and bars around outages that differ only in close time (6 per
coin) or exist on one side only (per coin, 3 archive-only 15-minute bars and 1 API-only
four-hour bar). None is later than 2022-04-13. The register holds bar times and field
names, never a price.

## Gaps the coverage notes record outside the manifests

[data-coverage.md](data-coverage.md) adds what a development manifest cannot hold. Nine
monthly files never existed in the archive (FTTUSDT spot, 2022-12 to 2023-08) and seven
are missing from TLMUSDT's premium index (2022-08 to 2023-02); both show above as the long
FTT and TLM gaps. Its finding 11 notes single-day gaps in the holdout (BTC and ETH
perpetual klines on 2026-10-28, premium index on 2026-06-29), measured on 2026-11-11 before
the holdout files left the disk; they are carried here by reference only, are off disk by
design, and are for G3.2 to handle as the development gaps are handled. It also lists
delistings and renames that end a series inside the window.

## Findings

Each with severity (what goes wrong if ignored) and confidence.

1. **Low, likely.** Fourteen daily-series gaps, in 13 windows, in ranking-universe pairs
   that are not H1 candidates (VENUSDT, BCHUPUSDT, BCHDOWNUSDT, COCOSUSDT, BTCSTUSDT,
   DREPUSDT, SUNUSDT, NBTUSDT, VIDTUSDT, CVCUSDT, KEYUSDT, BNXUSDT, QUICKUSDT and
   STRAXUSDT) are recorded in the manifest but not described in data-coverage.md, whose
   daily list covers candidates only. The dated universe ranks on these series, so how a ranking window that crosses a
   daily gap is treated should be confirmed by the quant specialist against
   PREREGISTRATION.md. Path to harm: a pair ranked on a volume window with missing days
   enters or leaves a top ten differently from what the registered rule intends.
2. **Low, confirmed.** `verify` raises on the first file that does not match and stops,
   so a damaged mirror would report one bad file, not all of them; and it does not report
   files in the mirror that no manifest pins. Neither mattered today (no mismatch, and the
   name inventory above found no unpinned file), but an audit that relies on `verify` alone
   would miss both. A change to list every mismatch and every unpinned name would be the
   technical specialist's to make.
3. **Informational.** The verify step reads every development file in full to count its
   rows, as it must; it computes no price statistic. In classifying committed repository
   files for the licence register, this audit printed the first row of huginn's
   `data/btc_test.jsonl` (one minute of 2026-05-18, inside the holdout window) and the
   header lines of `docs/research/obi-decile-2026-10-28-to-30.txt`, both already public in
   git. No return or statistic was computed. It is recorded here so the P2 audit (G2.7) can
   account for it; the files themselves are finding 4 of
   [DATA_LICENCES.md](../../DATA_LICENCES.md).
