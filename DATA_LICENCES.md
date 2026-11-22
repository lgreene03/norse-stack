# Data licences and terms

This register records, for every external data source the Norse Stack reads, the licence
or terms that govern it, what those permit and forbid for this project, and how the
project complies. It is goal G2.5's licence register, written by the security role and
checked against the primary sources on **2026-11-21**. The companion manifest audit is
[docs/research/manifest-audit-2026-11-21.md](docs/research/manifest-audit-2026-11-21.md).

**This is research, not legal advice.** It records what each source's published terms say,
read by an engineer, and where the project's use is clearly inside them, clearly outside
them, or unclear. Where a page could not be fetched, this says so rather than guessing.
Before any commercial use, publication of data, or live trading, the owner should take
qualified advice on the current terms.

Nothing in the stack holds an exchange account or API key, sends an order to an exchange,
or charges anyone for anything: execution is simulated (`EXCHANGE_BACKEND: sim`) and every
published port binds to 127.0.0.1. All five Norse repositories are public on GitHub under
Apache-2.0, so anything committed to them is published.

## Summary

| Source | Used by | Governing terms | Live trading on it | Publishing it | Status |
|---|---|---|---|---|---|
| Binance public data archive, data.binance.vision | `research/norse_research` (mirror, manifests, `verify`); huginn `cmd/fetcher` | Binance Vision Dataset Terms v1.0 (26 Aug 2026), which license the Datasets under CC BY-NC-SA 4.0 | Forbidden (clause 4.2) | Only non-commercially, with attribution, under CC BY-NC-SA 4.0 (clause 4.5) | Use complies; committed fixtures and derived files carry the notice in [NOTICE](NOTICE) since 2026-11-22 (findings 2, 3) |
| Binance market-data REST host, data-api.binance.vision | `rest-check`, `rest-sweep` | Vision terms if it is one of their "associated endpoints", otherwise the Binance Terms of Use | As above | Not licensed | Committed REST fixtures, listed in [NOTICE](NOTICE) on the associated-endpoint reading (finding 3) |
| Binance live API (stream.binance.com, api.binance.com, fapi.binance.com) | muninn recorder; obi-bridge; huginn `cmd/fetcher` | Binance Terms of Use (live page not fetchable; UK version of 27 Jul 2023 read) | Needs an account; out of scope (D3) | Not licensed | Recordings kept local; derived files published (finding 4) |
| OKX public WebSocket, ws.okx.com | muninn recorder | OKX API Agreement (last updated 28 Jul 2026) | Own non-commercial trading only, with an account | Forbidden (9.4(a)) | Recordings kept local; fixtures published in muninn (findings 5, 6) |
| Fear and Greed Index, api.alternative.me | obi-bridge; huginn-ai | alternative.me usage rules (undated) | Not addressed | Allowed with prominent attribution | Used internally (finding 9) |
| CoinDesk RSS | news-sentinel | CoinDesk Terms of Use (effective 30 Oct 2026) | Not addressed | Forbidden | Automated access barred (finding 1) |
| Cointelegraph RSS | news-sentinel | Cointelegraph terms (undated) | Not addressed | Forbidden | AI use needs consent (finding 1) |
| Coinbase feed; Binance spot testnet | muninn adapter (disabled); sleipnir backend (not configured) | Not reviewed | | | Review before enabling (finding 10) |

## Binance public data (data.binance.vision)

**Terms.** The archive's page links to its terms as a PDF,
[Binance_Vision-Terms_of_Use.pdf](https://data.binance.vision/Binance_Vision-Terms_of_Use.pdf)
(via [terms-of-use.html](https://data.binance.vision/terms-of-use.html)), titled *Binance
Vision Dataset Terms*, version 1.0, last updated 26 August 2026, provided by Nest Exchange
Limited (ADGM). The same text is
[TERMS_AND_CONDITIONS.md](https://github.com/binance/binance-public-data/blob/master/TERMS_AND_CONDITIONS.md)
in binance/binance-public-data, added on 2026-09-30 (commit `bd110bb`) and last changed on
2026-10-16 (`d3357a5`). Both were read on 2026-11-21. That repository's README ends with
"Licence: MIT", which on its face covers the repository's helper scripts; the README sends
readers to the terms file for how the datasets may be used, and GitHub detects no licence
file. Clause 3.1 provides the Datasets under the
[Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International Public License](https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode.en)
(read on 2026-11-21), and clause 3.3 adds the Vision terms on top, the stricter obligation
applying where both bind. Clause 1.4 makes amendments prospective: data keeps the terms in
force when it was downloaded. The mirror was downloaded on 2026-11-11, under version 1.0.

**What it permits.** Clause 4.1 limits permitted use to academic research, non-monetised
open educational projects, algorithmic historical backtesting for purely personal,
non-production research, and non-commercial open-source data science evaluation. Under CC
BY-NC-SA 4.0 the data may be reproduced, adapted and shared for NonCommercial purposes
(not primarily intended for commercial advantage or monetary compensation), and section 4
of the licence extends that to the database right where it applies.

**What it forbids.** Clause 4.2 forbids using the Datasets for live proprietary trading
execution, automated commercial order generation, or distributing signals to third parties
for compensation; this is the clause decision D3 cites. Clause 4.3 forbids licensing,
selling, hosting or otherwise commercially exploiting the Datasets or feeds derived from
them; clause 4.4 forbids building them into commercial financial products, fund tools, paid
newsletters or commercial trading-bot platforms; clause 3.4 puts any commercial use behind
a separate written enterprise licence. Clause 2.4 extends all of this to anything derived
from the Datasets (charts, models, indicators). Clause 4.5 requires any redistributed
derivative work to attribute Binance Vision and remain under CC BY-NC-SA 4.0, matching the
licence's own attribution and ShareAlike conditions (its sections 3(a) and 3(b)). Clause 7
binds users to posted rate limits and forbids automated querying that circumvents them;
clause 8.2 forbids implying Binance endorses the project.

**How the project complies.** The research harness uses the archive for personal,
non-production historical backtesting, which clause 4.1 names. Nothing trades live: paper
fills are simulated, and D3 records that clause 4.2 rules out a live path on this data.
Nothing is sold, hosted for others, or built into a product. The mirror (43,239 files,
about 809 MB) lives on the owner's external drive, outside every repository, and is not
redistributed.
The fetcher keeps to ten requests a second and the REST check to four. The project does not
present itself as endorsed by Binance.

**The notice.** Binance Vision data and files derived from it are committed to this
public Apache-2.0 repository (finding 2). Since 2026-11-22 the repository's
[NOTICE](NOTICE) lists each of them and offers them under CC BY-NC-SA 4.0, not
Apache-2.0, with the attribution clause 4.5 asks for. Attribution is also given here:

> Binance market data in this repository is from Binance Vision (data.binance.vision),
> provided by Binance under the Binance Vision Dataset Terms and CC BY-NC-SA 4.0
> (https://creativecommons.org/licenses/by-nc-sa/4.0/). The files listed in finding 2
> are unmodified archive files, or hashes, row counts, bar times and volume ranks computed
> from them.

Offering them under CC BY-NC-SA 4.0 states the licence they already carry rather than
granting a new one; replacing the two zip fixtures with synthetic ones remains open if the
owner would rather publish no Binance data at all (finding 2).

## Binance market-data REST host (data-api.binance.vision)

`norse_research.rest_check` compares archive bars with closed klines from
`data-api.binance.vision`, Binance's market-data-only host. Clause 2.1 of the Vision terms
covers data made available through data.binance.vision "and associated endpoints"; whether
that includes this host is not stated. Read strictly, both the Vision terms and the Binance
Terms of Use apply, and neither licenses publishing the responses. The use itself (a
non-commercial integrity check, about 630 requests once) fits clause 4.1 and the
non-commercial licence of the Terms of Use. The two committed response fixtures are
finding 3.

## Binance live market data (muninn, obi-bridge, huginn fetcher)

**What reads it.** muninn records Binance's public trade and depth streams from
`wss://stream.binance.com:9443` into the `muninn-raw` bucket, a MinIO folder on the owner's
machine (`MINIO_DATA_DIR`). obi-bridge reads `api.binance.com` and `fapi.binance.com` and
the aggregate-trade stream to build the `features.obi.v1` events huginn paper-trades on and
huginn-ai trains on. huginn's `cmd/fetcher` can pull aggregate trades from `api.binance.com`.

**Terms.** These public endpoints need no account, but the Binance Terms of Use bind anyone
who accesses the Platform, which they define to include a Binance API. The current page,
[binance.com/en/terms](https://www.binance.com/en/terms), redirects UK visitors to
`/en-GB/terms` and renders its text with JavaScript; neither could be read by fetching it
on 2026-11-21, so its current wording is **not verified here**. The most recent text that
could be read is the consolidated
[Terms of Use, United Kingdom](https://public.bnbstatic.com/static/terms_doc/2023_07_27_terms_of_use_uk_consolidated-f8ef267b2.pdf),
last updated 27 July 2023, effective 10 August 2023, which may since have been replaced.
In it, clause 22 licenses Binance IP only as needed to receive the services, for
non-commercial personal or internal business use; clause 26(c) forbids resale or
commercial use without written agreement; clause 26(i) forbids automated means of
obtaining information other than in the ways the services purposely provide, and loads
that are unreasonable; clause 26(l) forbids copying, storing, transmitting, publishing or
making derivative works of Binance IP beyond that licence; clause 34.2 applies UK law to
UK residents.

**About clause 4.2.** Decision D3 cites "Binance's public-data terms (clause 4.2)". That is
clause 4.2 of the Vision Dataset Terms above, which governs the archive and what is derived
from it. It is not a clause of the live API's terms: the live streams muninn records are
governed by the Terms of Use, whose limits are the non-commercial personal or internal use
of clause 22 and the prohibitions of clause 26. Live trading on any Binance data would in
any case need an account, and D3 records why that path is closed to the owner as things
stand.

**How the project complies.** The streams and endpoints are the ones Binance publishes for
programmatic use, read at modest rates. Recordings and features stay on the owner's
machine for personal, non-commercial research and simulated trading; nothing is sold or
streamed to anyone. **Gaps:** small recorded payloads are committed in muninn, and a day of
features derived from the live stream is committed in huginn and described in this
repository's docs (findings 4 and 5).

## OKX public market data (muninn)

**Terms.** muninn records OKX's public trades and `books5` channels from
`wss://ws.okx.com:8443/ws/v5/public` for the configured pairs. The
[OKX API Agreement](https://www.okx.com/en-gb/help/okx-api-agreement) (published 26 March
2026, last updated 28 July 2026, read on 2026-11-21) applies: clause 3.2(a) says public
market data endpoints, though usable without an API key, remain subject to it and to the
OKX Terms of Service. Clause 9.2 grants a limited, revocable licence for the user's own
internal purposes. Clause 9.4 restricts Market Data, public endpoints expressly included,
to the user's own personal, non-commercial trading and account management; forbids
redistributing, publishing or displaying it to any third party (9.4(a)), building a
competing data product, price feed or analytics platform from it (9.4(b)), and training
machine-learning models intended for commercial use on it (9.4(c)); and limits automated
extraction to what personal trading reasonably needs. Clause 13.3 restricts AI training on
API data by reference back to 9.4. Clause 16.1 applies the law of the user's contracting
OKX entity, which the Agreement does not name. Nothing in it addresses research use or a
user's storing or archiving of Market Data.

**How the project complies.** muninn records the same five pairs as on Binance (decision
D5) for the owner's own, non-commercial research towards trading decisions; the
recordings are not published,
displayed to anyone else, or used to train a commercial model. **Gaps:** research and
continuous archiving are not named as permitted purposes (finding 6), and muninn commits a
few real OKX messages as test fixtures (finding 5).

## Fear and Greed Index (alternative.me)

obi-bridge fetches `https://api.alternative.me/fng/?limit=1` and passes the value into
`features.obi.v1`, where huginn-ai uses it as a model input. The
[API section of the index page](https://alternative.me/crypto/fear-and-greed-index/) (read
on 2026-11-21, no date given) allows use, commercial use included, provided the data's
source is acknowledged prominently next to wherever the data is shown, and forbids using
it to impersonate alternative.me or to build a service that could be confused with
theirs. The stack uses the value internally and does not display it as alternative.me's
index, so nothing is owed beyond attributing it wherever it is shown (finding 9).

## News feeds (CoinDesk and Cointelegraph)

news-sentinel polls `https://www.coindesk.com/arc/outboundfeeds/rss/` and
`https://cointelegraph.com/rss` every 300 seconds, classifies each headline's sentiment
with a local LLM (Ollama), serves the scores on 127.0.0.1:8089, and obi-bridge adds them to
`features.obi.v1`. It runs by default in `docker-compose.yml`.

The [CoinDesk Terms of Use](https://www.coindesk.com/terms) (effective 30 October 2026,
read on 2026-11-21) license only viewing, playing, printing and downloading the content,
forbid reproducing, republishing or distributing it, and forbid accessing the services by
robot, spider, scraper or other automated means; they say nothing about the RSS feed.
Cointelegraph's [terms](https://cointelegraph.com/terms-and-privacy) (no date given, read
on 2026-11-21) allow personal and informational access only, forbid automated scraping and
content pipelines without written authorisation, permit quoting only as fair use with
attribution, and require Cointelegraph's prior written consent to use its content for
training, fine-tuning, validating, benchmarking or operating AI or machine-learning
systems. Running every Cointelegraph headline through an LLM to produce a trading input is,
on that wording, operating an AI system on its content (finding 1).

## Sources not in use

muninn has a Coinbase adapter (`ws-feed.exchange.coinbase.com`), disabled in
`docker-compose.yml` (`MUNINN_INGESTION_COINBASE_ENABLED: "false"`); Coinbase publishes
separate market data terms. sleipnir can execute against the Binance spot testnet
(`testnet.binance.vision`), which needs a testnet key, under Binance's testnet terms; the
stack runs sleipnir with `EXCHANGE_BACKEND: sim`. Neither set of terms was reviewed for
this register (finding 10). The console loads typefaces from Google Fonts; that is not a
data source.

## Is anything restricted committed?

**How it was checked.** For norse-stack at origin/main `fb66ac6`: `git ls-tree -r -l` over
every tracked file with its size; every blob in the full history (`git rev-list --objects
--all` with `git cat-file`) filtered for data, dump, archive, model, key and certificate
extensions, `.env` and `secrets/`, and anything over 300 KB; a list of deleted files of
those kinds in history; and `git grep` for private-key headers and common token formats
(AWS, GitHub, Slack, OpenAI, Google), and for password, secret and token assignments in
configuration. The same history and pattern scans were run, read-only, on muninn
(`65f8624`), huginn (origin/main `2e3e509`) and sleipnir (`c5f5b1f`), which this goal lists
but does not change. CI also runs gitleaks on this repository.

**Secrets and keys.** None found in any of the four repositories. What matched was a
placeholder or a test value: `.env.example` holds `change-me` placeholders; the compose
file falls back to well-known local defaults when `.env` is absent (finding 8);
`services/recording-audit/tests/test_s3client.py` uses AWS's published documentation
example key, not a credential; and
`research/tests/fixtures/key-record-test-passphrase-only.json` is the scrypt verifier of a
test-only passphrase with a fixed test salt. No `.env`, `secrets/` file, private key,
database dump or backup is tracked or was ever committed.

**Holdout data.** No file from the 8,517 holdout archive files is committed, in this
repository or the others, and `test_no_committed_fixture_holds_holdout_rows` holds every
data file under `research/` to that. Outside that test's reach, holdout-window prices
derived from muninn's live recordings are committed in huginn and summarised in this
repository's docs (finding 4).

**Market data.** Committed market data is small but present, and its licence terms are not
yet met: the Binance archive and REST fixtures and the derived manifests and coverage
files in this repository (findings 2 and 3), a day of Binance-derived features in huginn
(finding 4), and recorded Binance and OKX messages in muninn (finding 5). So the
confirmation this goal asks for holds for secrets, credentials, dumps and holdout archive
files, and does **not** yet hold for licensed market data; the findings below say what
would close it.

## Findings

Each with severity (what goes wrong if ignored), confidence, and the concrete path. None is
fixed in this change, which adds documents only; each needs the owner's decision or a
technical change in the repository named.

1. **Medium, likely.** news-sentinel's automated polling of two publishers' feeds, and its
   LLM processing of their headlines into a trading input, are outside both publishers'
   terms: Cointelegraph requires prior written consent to operate AI systems on its content
   and forbids automated content pipelines, and CoinDesk forbids automated access. Path: the
   service runs by default, every 300 seconds, with no consent on record. The owner should
   decide whether to remove the two feeds (or news-sentinel), seek consent, or substitute
   sources whose terms allow automated and AI use; the change itself belongs in
   `services/news-sentinel/sentinel.py` and `docker-compose.yml`.
2. **Medium, likely.** Binance Vision data, and files derived from it, are published in
   this Apache-2.0 repository without the CC BY-NC-SA 4.0 licence that clause 4.5 of the
   Vision terms requires of a redistributed derivative work: the unmodified archive files
   `research/tests/fixtures/BTCUSDT-15m-2026-11-09.zip` (5,544 bytes) and
   `BTCUSDT-4h-2026-11-09.zip` (541 bytes) with their CHECKSUMs, and the derived
   `research/manifests/*.json`, `research/coverage/series-coverage.json` and
   `universe-candidates.json`. Whether hashes, counts and bar times are a "derivative work"
   is unclear (confidence likely, not confirmed); clause 2.4 reads broadly. The attribution
   above meets the attribution half. The owner should decide whether to declare these
   files CC BY-NC-SA 4.0 in the repository's licence notice, or replace the two zip
   fixtures with synthetic ones; the manifests cannot be replaced without losing what they
   prove.
3. **Low, confirmed.** `research/tests/fixtures/rest-BTCUSDT-15m-2026-11-09.json`
   (16,866 bytes) and `rest-BTCUSDT-4h-2026-11-09.json` (1,095 bytes) are raw responses from
   `data-api.binance.vision`, published without a licence that permits it under either set
   of Binance terms. Same remedy as finding 2.
4. **Medium, confirmed.** huginn's `data/btc_test.jsonl` (550,754 bytes on origin/main;
   1,440 one-minute BTC feature rows from 2026-05-18 01:01 to 2026-05-19 01:00 +01:00,
   with micro-price, VWAP and volume) was captured from the live stack's Binance-derived
   `features.obi.v1` and is published in a public Apache-2.0 repository, which clauses 22
   and 26(l) of the Binance Terms of Use do not license. It also lies inside the holdout
   window (2026-01-01 to 2026-10-31), and this repository's `docs/RESULTS.md` names the
   window and reports a buy-and-hold figure over it; `docs/research/obi-decile-2026-10-28-to-30.txt`
   likewise reports statistics from Binance and OKX recordings inside that window, and
   huginn's history keeps `data/backtest_trades.jsonl` (paper fills dated 2026-05-18 and
   19, removed from the tree on 2026-06-22 but still public in history; only a history
   rewrite, which is the owner's call, would remove it). None is an input to H1 or H2, which use the manifested archive, so like threat-model finding 17
   they do not breach the seal as built; but they are accidental-read paths that
   `test_no_committed_fixture_holds_holdout_rows` does not reach (it scans `research/`
   only). The owner should decide whether to move huginn's fixture to a window outside the
   holdout and outside the published repositories, and the threat model (T21) should list
   these files. This audit read the first row of the fixture to identify it (see the
   manifest audit, finding 3).
5. **Low, confirmed.** muninn commits real messages captured from both venues as parser
   fixtures: `src/test/resources/okx/trades.jsonl` (1,056 bytes) and `books5.jsonl`
   (2,309 bytes), and `src/test/resources/fixtures/binance/trade.json` (195 bytes) and
   `depth20.json` (232 bytes). Publishing OKX Market Data to third parties is forbidden by
   clause 9.4(a), public endpoints included; the Binance payloads fall under clause 26(l).
   The amounts are trivial, but the breach is literal. Replacing them with synthetic
   messages of the same shape would close it; muninn is listed here, not changed.
6. **Low, likely.** OKX licenses Market Data for the user's own personal, non-commercial
   trading and account management, and limits automated extraction to what personal
   trading reasonably needs; it does not name research or continuous archiving. muninn's
   round-the-clock recording for research is defensible as preparation for the owner's own
   trading, but it is not expressly permitted, and clause 9.7 lets OKX revoke the licence.
   Keep the recordings private; ask OKX if the recordings are ever to support anything
   beyond the owner's own research.
7. **Low, confirmed.** The current Binance Terms of Use could not be read (the page renders
   with JavaScript and redirects UK visitors); this register relies on the UK consolidated
   version of 27 July 2023. The owner should read the live page in a browser and note here
   any change to clauses 22, 26 or the UK governing-law clause.
8. **Low, confirmed.** `docker-compose.yml` falls back to well-known credentials when `.env`
   is absent (MinIO `minioadmin`, Postgres `muninn` and `postgres`, Grafana `norse`, control
   tokens `localdev-change-me`). Path: on a machine started without `.env`, any local
   process reads the recordings in MinIO on 127.0.0.1:9002 with the default key. The ports
   bind to loopback and docs/SECURITY.md already records these as placeholders; restated
   for completeness.
9. **Informational.** alternative.me requires its source to be acknowledged next to any
   display of its index. The stack shows the value only inside its own features and
   explanations, on loopback; if the console or a published result ever shows it, credit
   alternative.me beside it.
10. **Informational.** The Coinbase market data terms and the Binance testnet terms were
    not reviewed because neither source is enabled. Add a section here before enabling
    either.

## Keeping this current

Every new data source, and every change to how an existing one is used (publishing data,
a new consumer, any live trading), needs a section here before it merges, with the terms'
version and the date they were read. The terms above were last read on 2026-11-21; the
Binance Vision terms in particular apply prospectively, so record the date of each new
download alongside the version in force.
