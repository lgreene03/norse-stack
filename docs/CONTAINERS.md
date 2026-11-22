# Containers: keep, freeze or retire

Goal G2.6 of [ITERATION_GOALS.md](ITERATION_GOALS.md), written by the CTPO on 2026-11-22.
Every service in [docker-compose.yml](../docker-compose.yml) has a decision below, with
what it costs in memory and in the owner's attention, what this iteration needs it for,
and one sentence of reason. The containers run on the owner's Mac as `norse-stack-*-1`.
The console (`console/serve.py`, a host process) and the data heartbeat (a launchd agent,
decision D8) are not containers and are not covered; containers belonging to other
projects on the same machine are out of scope.

The iteration's goal is one pre-registered verdict on H1 (dated-universe trend) and H2
(funding carry), computed by the Python harness in `research/` from Binance Vision
archives, with muninn's live recordings kept running as the one asset that no free source
offers ([NEXT_ITERATION.md](NEXT_ITERATION.md)). Paper trading is not part of it: G1.9's
engine runs paper trading inside the research package, and wiring a passed strategy into
live paper trading is the next iteration's work (G3.7). That settles most of the list.

## How the costs were measured

Memory and CPU come from one `docker stats --no-stream` taken at 2026-11-22T10:40Z, so the
CPU figures are a single sample and indicative only; "of N" is the container's
`mem_limit`, and a container without one may use the whole 7.65 GiB Docker VM. Image size
is as `docker images` reports it. Images share base layers (`python:3.12-slim` under the
in-repository Python services, `postgres:16-alpine` under both databases, the redpanda
image under topic-init), so adding the column overstates the disk the images take.

Attention is what a container asks of the owner. It counts the dependabot and security
pull requests in the repository the container is built from over the 90 days from
2026-08-24; open issues, of which there are none in norse-stack, muninn, huginn or
sleipnir; the alerts it fired within Prometheus's 15-day retention, all of which went to
Alertmanager's null receiver under D2 and so reached nobody; the CI job each in-repository
service runs on every pull request; and the follow-ups the register already holds against
it. Dependabot alerts are disabled on all four repositories, so known vulnerabilities show
up only as pull requests, as `pip-audit` in this repository's CI (clean for every
in-repository service on main on 2026-11-21) and as the sibling repositories' own scanners.

**Keep** means the iteration needs the container running. **Freeze** means it keeps
running but takes no rebuild, upgrade or configuration change this iteration; dependabot
pull requests in its source repository may still merge, they are simply not built into the
running stack. A fix the security role judges necessary overrides a freeze, under its veto
on releasing a known-exploitable vulnerability. **Retire** means it is removed; only
news-sentinel is removed in this pull request, and every other retirement is a
recommendation for the owner to decide.

## The list

| Container | What it does | Memory, CPU | Image | Attention | What the iteration needs it for | Decision | Reason |
|---|---|---|---|---|---|---|---|
| muninn | Records Binance and OKX trades and books for five pairs into `muninn-raw`; its feature engine is off | 662 MiB of 1 GiB, 2.5% | 1.66 GB | muninn repository: 31 dependabot and 3 security pull requests; the running build predates muninn#92; TargetDown fired once, for 45 s | The recordings and guardrail P3 ("Keeps running" in NEXT_ITERATION.md; D5, G0.17, G0.18) | keep | It is the one live asset, so it is rebuilt only at a planned restart whose gap is recorded. |
| redpanda | Kafka broker between muninn's ingest and its recorder; also carries the trading topics | 371.5 MiB, 4.8% | 650 MB | A 78.8 GB anonymous data volume; two Medium follow-ups (the unprovisioned snapshot topic, and `down -v` deleting the volume) | The recording path: the recorder catches up from it (G0.17) | keep | Recreating it would break recording. |
| postgres-muninn | muninn's database | 23.1 MiB, 0.0% | 389 MB | None; a 48 MB volume | The recording path | keep | muninn does not start without it. |
| minio | Object store holding `muninn-raw` on the external drive | 801.4 MiB, 7.1% | 235 MB | Two Medium follow-ups: the image can no longer be pulled anonymously, and the live store uses default credentials | The recording path (G0.17); recording-audit reads it | keep | It holds every recording and must not be pruned or recreated. |
| minio-init | One-shot: creates the three buckets, then exits | Exited | 109 MB | None | muninn's start waits for it to complete | keep | It costs nothing while exited, and removing it breaks muninn's `depends_on`. |
| topic-init | One-shot: provisions the Kafka topics with their retention, then exits | Exited | Shares redpanda's | None | Topic retention, where the snapshot-topic follow-up is fixed | keep | It costs nothing while exited. |
| recording-audit | Daily recording gap audit per venue and day, as files and metrics | 80.6 MiB of 256 MiB, 0.0% | 208 MB | One dependabot pull request (its base image); a CI job with an S3 contract test | G0.15, P3 and G0.13's Recording Health row | keep | It is how P3 is measured. |
| prometheus | Scrapes recording-audit, muninn and the trading services; evaluates the alert rules | 83.2 MiB of 256 MiB, 0.1% | 369 MB | A 198.5 MB volume; carries huginn's continuously firing latency alerts | P3 and G0.13 read the audit's metrics through it | keep | The Recording Health row has no other source. |
| grafana | Dashboards; the one provisioned dashboard charts only huginn and sleipnir | 90.2 MiB of 256 MiB, 0.0% | 585 MB | G0.13, due 11-10, not started | G0.13 | keep | G0.13 still has to repair it and add Recording Health. |
| alertmanager | Routes Prometheus's alerts to a null receiver | 26.2 MiB of 128 MiB, 0.1% | 102 MB | None | Nothing: G0.14 is deferred (D2) | freeze | D2 brings alerting back before any live paper wiring, and removing it means editing Prometheus's configuration to save 26 MiB. |
| tempo | Trace collector; muninn, huginn and sleipnir export to it | 149.7 MiB of 512 MiB, 0.2% | 160 MB | None | Nothing reads traces | freeze | Removing it cleanly means unsetting muninn's exporter, which recreates muninn and leaves a recording gap. |
| redpanda-console | Kafka web interface on port 8088; the console's health panel probes it | 51.0 MiB, 0.0% | 159 MB | None | Diagnosing the recording broker | freeze | It is cheap and harmless, and `rpk` inside redpanda does the same job if it goes with the trading path. |
| research | huginn's research gateway: read-only, runs nothing, a token on every route but `/healthz` (G1.2) | 11.0 MiB of 256 MiB, 0.0% | 36.3 MB | huginn repository: 10 dependabot pull requests (one open) and 4 security pull requests | G1.2 (interim) and G1.5's review finding M10; the console's research panel | freeze | H1 and H2 do not use it, and a rebuild would reopen G1.2's security review. |
| huginn | Composite strategy on `features.obi.v1`, idle since 2026-06-22 | 26.7 MiB of 512 MiB, 0.1% | 44.5 MB | As research; the running build predates huginn#45 and #50; two latency SLO alerts firing for about 326 hours each; HuginnStatePersistErrors for 9 minutes | D9 (left idle, with G0.8's metrics saying why); the console's trading panels until G1.10 and G3.6 | freeze | The verdict decides it (G3.6, G3.7), and Prometheus waits on its health check. |
| postgres-huginn | huginn's state, including the five stored positions D9 leaves in place | 18.2 MiB, 0.0% | Shares postgres-muninn's | A 66 MB volume | D9 | freeze | It goes wherever huginn goes. |
| sleipnir | Simulated execution gateway; it has received no intent since 2026-06-22 | 24.0 MiB of 512 MiB, 0.1% | 123 MB | sleipnir repository: 26 dependabot and 3 security pull requests; the running image predates sleipnir#73 and #75 | Nothing this iteration: G1.9 runs paper trading in the research package | freeze | The verdict decides it; on a pass, G3.7's paper wiring chooses between it and G1.9's driver. |
| obi-bridge | Polls Binance's REST API every 60 s and publishes `features.obi.v1` for five pairs | 20.4 MiB of 256 MiB, 0.1% | 221 MB | A CI job; licence register findings 4 and 9 | D1 (keeps the topic, frozen); huginn's only input | freeze | D1 already froze it. |
| odin | Performance analytics and backtest-versus-live reconciliation from fills | 18.4 MiB of 256 MiB, 0.2% | 220 MB | A CI job; two alert rules, never fired | The console's portfolio and equity panels; its reconciliation is a candidate for G3.6's shortfall template on a pass | freeze | With no fill since June it reports `available: false`, and the verdict decides it. |
| forseti | Transaction-cost analysis of fills, market impact and capacity | 17.7 MiB of 256 MiB, 0.6% | 220 MB | A CI job | The console's cost and impact panels; a candidate for G3.6's shortfall template on a pass | freeze | It has recorded no fill, and the verdict decides it. |
| bragi | Explains each order-book-imbalance decision from `features.obi.v1` with its own 0.95 threshold | 25.2 MiB of 256 MiB, 0.2% | 220 MB | A CI job | Nothing: it explains the retired signal, and no console panel, scrape job or alert reads it | recommend retire, owner to decide | Nothing in the stack reads it, so removing it breaks nothing. |
| huginn-ai | XGBoost model trained on fills; serves a confidence score to obi-bridge | 81.1 MiB of 512 MiB, 0.6% | 1.61 GB, the largest | A CI job with the heaviest dependencies (xgboost, scikit-learn, numpy); a scrape job; three alert rules, never fired | Nothing: NEXT_ITERATION.md puts training the model out of scope, and with no fill since June it cannot train | recommend retire, owner to decide | It reports `model_ready: false` and serves the neutral 0.5 that obi-bridge falls back to without it. |
| heimdall | Hidden Markov model regime labels from `features.obi.v1` | 40.5 MiB of 512 MiB, 0.2% | 305 MB | A CI job | Nothing: no registered hypothesis uses regimes, and `prereg-v1` cannot gain one | recommend retire, owner to decide | Only the console's regime panel reads it. |
| mimir | Point-in-time store of `features.obi.v1`: 404,379 rows since 2026-06-22 | 32.5 MiB of 256 MiB, 4.4% | 220 MB, and a 446.5 MB volume | A CI job; `make bench` | Nothing: H1 and H2 read manifest-pinned archives, where the loader and the seal prevent look-ahead | recommend retire, owner to decide | It stores the retired signal's features; its volume should be kept until the owner decides. |
| news-sentinel | Classified CoinDesk and Cointelegraph headlines with a local LLM | Stopped by the owner on 2026-11-21 | 219 MB | Licence register finding 1 (Medium); a CI job | Nothing | retire, in this pull request | Its sources' terms bar automated access and AI use, and the register assigned its retirement here. |

The 21 running containers used 2,654 MiB between them. The seven kept and running use
2,112 MiB, of which minio and muninn are 1,463; the ten frozen use 363 MiB; the four
recommended for retirement use 179 MiB and hold 2.36 GB of images and mimir's 446.5 MB
volume. Memory is not what makes this stack expensive. Attention is: the trading path
draws the sibling repositories' dependabot traffic, eight in-repository Python services
each run a CI job and must pass every ruff upgrade (ruff 0.16.0 turned all eight red at
once on 2026-07-24), and the stack shows a trading platform that this iteration is not
running.

## The recording path

muninn, redpanda, postgres-muninn and minio, with their two one-shot initialisers, are
kept and are never recreated by this iteration's work. recording-audit, Prometheus and
Grafana are kept because guardrail P3 and G0.13 are measured through them. muninn's
running build dates from 2026-11-10T22:10Z and so lacks muninn#92 (a security fix that
pins the API documentation page's bundled DOMPurify, merged 11-14) and four dependency
upgrades merged on 11-16. Its port binds 127.0.0.1 only. Rebuilding it leaves a recording
gap, so the recommendation is one planned muninn restart, at a time the owner chooses,
with the gap recorded by the audit. That restart is also the moment to retire tempo by
removing muninn's trace exporter settings, and the moment to decide whether huginn and
sleipnir stop exporting too; the owner decides both.

## Frozen until the verdict

huginn, postgres-huginn, sleipnir, obi-bridge, odin and forseti are the paper trading
path and its accounting, and the research gateway is built from huginn. None of them
contributes to H1 or H2, and each of them is a question the verdict answers. G3.6 says
that on a fail the trading panels are removed or reframed around recording and research;
the path behind those panels should then be retired in the same pull request. On a pass,
the next iteration's paper wiring (G3.7) decides which of them it needs, given that
G1.9's engine already runs paper trading inside the research package. Freezing them until
then costs 363 MiB and keeps the verdict from being bundled with a deployment.

The freeze has a security cost the security role should rule on. huginn's running build
(2026-11-11T00:46Z) predates huginn#45, the move to Go 1.26.11 for GO-2026-6859 (a race in
net/http's MaxBytesReader), and sleipnir's running image predates both sleipnir#73
(klauspost/compress for GO-2026-6865) and #75 (GO-2026-6859). Both bind 127.0.0.1 only,
neither receives orders, and rebuilding either touches neither the recorder nor muninn,
so a rebuild is cheap if the security role judges it necessary.

alertmanager, tempo and redpanda-console are frozen for their own reasons, given in the
table. tempo is the only one of the three worth retiring, at the planned muninn restart
above.

## Recommended for retirement, owner to decide

bragi, huginn-ai, heimdall and mimir serve neither outcome of the verdict. Each consumes
the retired signal's feature stream or fills that no longer occur, and none of them is
read by a goal of this iteration. Retiring them would cut four of the eight
in-repository CI jobs and the largest dependency set in the repository, and would take
away the console's regime and feature-store panels and the header's regime tile, which
show the retired signal's analytics as if they were live: the honesty problem G0.11 and
G0.12 exist to fix.

Removing bragi breaks nothing in the stack; it loses only its port (8087) and its rows in
README.md and CLAUDE.md. Removing huginn-ai changes nothing obi-bridge publishes, because
the model is not trained and obi-bridge's fallback (`mlScore` 0.5, `mlReady` 0) is what
it already reports. It does need Prometheus's `huginn-ai` scrape job removed, with its three
alert rules (HuginnAiFillsRejectedBurst, HuginnAiDuplicateFills and
HuginnAiModelPersistFailures) and their promtool unit tests, and the comment in huginn's
compose block saying the model stays served for provenance corrected. Removing heimdall
leaves the console's regime panel and header tile in their empty state, which the console
already handles, until they are removed. Removing mimir does the same to the console's
feature-store panel, and heimdall, while it remains, warms up from the live stream
instead of mimir's history. mimir's volume should be kept until the owner
says otherwise, since 404,379 rows of the retired signal cannot be recomputed after the
fact.

If the owner agrees, one pull request can remove all four: the compose blocks, their
directories under `services/`, their CI jobs, the scrape job and alerts above, the
console's regime and features panels and proxy routes, and their rows in the documents.
The running containers should then be stopped and removed by name (for example
`docker rm -f norse-stack-bragi-1`), never by a whole-stack `docker compose up`, which
could recreate muninn if anything else in its configuration has changed.

## news-sentinel, retired in this pull request

The owner stopped news-sentinel on 2026-11-21. It polled CoinDesk's and Cointelegraph's
RSS feeds every 300 seconds and ran each headline through a local LLM, which both
publishers' terms bar: CoinDesk forbids automated access and Cointelegraph requires
written consent to operate an AI system on its content (DATA_LICENCES.md, finding 1).
This pull request removes its service from `docker-compose.yml`, its build context
`services/news-sentinel/`, its CI job, and the documents' statements that it runs. It
had no Makefile target, Prometheus scrape job, Grafana panel or alert rule.

Nothing that runs changes. obi-bridge still calls it, because D1 froze obi-bridge's code;
the call fails and falls back to a `newsSentiment` of 0.0, exactly as it has since the
container stopped. The only reader of that field is huginn's `obi` strategy, which vetoes
a sell when the score is above 0.4 and a buy when it is below -0.4, and the running
strategy is `composite`, which does not read it. bragi's "sentiment" filter reads the Fear and Greed index, not the
news score. news-sentinel was the stack's only caller of Ollama.

The stopped container and its image remain on the machine, because this change may not
touch a container. The owner can remove them with `docker rm norse-stack-news-sentinel-1`
and `docker image rm norse-stack-news-sentinel:latest`, which frees 219 MB; neither
command affects any other container. A plain `docker compose up -d` afterwards would
report the container as an orphan and leave it alone.

## Findings

Each finding carries a severity (what goes wrong if it is ignored) and a confidence.

**1. Medium, confirmed.** Redpanda's anonymous data volume holds 78.8 GB, against the
register's estimate of about 55 GB for the unprovisioned `events.book.snapshot` topic.
It sits in Docker's disk image, and if that fills, the broker stops and the recorder with
it. The register's existing follow-ups on the snapshot topic and on `down -v` own the fix;
this adds the measured size.

**2. Low, confirmed.** The running builds of muninn, huginn and sleipnir predate security
fixes merged in their repositories (muninn#92; huginn#45; sleipnir#73 and #75). All three
bind 127.0.0.1 only. The security role decides whether any of them overrides a freeze or
waits for the planned muninn restart.

**3. Low, confirmed.** huginn's two latency SLO alerts have fired without a break since
2026-11-10T20:48Z and 2026-11-11T08:04Z, about 326 hours each, nearly all of
Prometheus's retention. In the hour to 10:43Z, huginn's own signal-to-decision histogram
put about 196 of 207 decisions between 1,024 and 2,048 ms and the rest above, against an
objective of 256 ms. They page nobody under D2, but they leave a permanently critical
state on the trading path that nobody acts on. Whether the delay is real or an artefact of
how it is measured was not investigated; it goes with huginn's freeze.

**4. Low, confirmed.** huginn's compose block says the served model's score is
near-constant at about 0.08. huginn-ai currently reports no model (`model_ready: false`,
no version) and serves 0.5, so the comment is stale. It is left alone because huginn's
block is frozen, and should be corrected by whichever change decides huginn-ai.

**5. Low, confirmed.** The image `norse-stack-huginn:pre-g08` (44.6 MB), kept from G0.8,
is used by no container. The owner can remove it once that rollback is no longer wanted.

**6. Low, likely.** The images of odin, bragi, mimir, huginn-ai and heimdall were built
four to five months ago. `pip-audit` checks their pinned requirements, not the operating
system packages in the images, and with Dependabot alerts disabled nothing reports on
those. Retiring four of them removes most of that unseen surface; odin, forseti and
obi-bridge keep it until the verdict.
