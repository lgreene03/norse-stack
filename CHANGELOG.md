# Changelog

All notable changes to the Norse Stack meta-repo will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Security
- Every port published by docker-compose.yml now binds to 127.0.0.1; 20 of 24 had
  bound to every interface, exposing the databases, Grafana, the control planes
  and the research gateway to the local network. The Postgres host ports are
  removed, CI fails any port published on another address, and `.env`, `.env.*`
  and `secrets/` are gitignored
- The research gateway is locked down and runs nothing (G1.2, with
  lgreene03/huginn#49). It refuses every run submission, because H1 and H2
  trials run only through `research/norse_research`'s ledgered harness. Every
  route but `/healthz` needs a bearer token read from `secrets/research_token`,
  which compose mounts as a read-only secret file and `make up` and
  `scripts/smoke.sh` create when absent. The gateway sends no CORS headers and
  refuses a foreign Host header. The console sends the token on its research
  proxy, no longer forwards POSTs there, and its research panel has no Run
  button
- `console/serve.py` binds 127.0.0.1 by default and refuses any request whose
  Host header is not `localhost:PORT` or `127.0.0.1:PORT` (G0.2's console
  clause, pulled forward by G1.2). It rejects a malformed research token file
  instead of sending it, returns no exception text or backend URL in errors,
  follows no backend redirect and ignores proxy environment variables. New
  stdlib tests in `console/tests` run in CI's validate job

### Removed
- bragi, huginn-ai, heimdall and mimir (G2.6), retired on 2026-11-22 by the owner's
  decision ("Retire all four"). All four served the retired order-book-imbalance signal
  and nothing in H1 or H2 uses them. Their compose blocks, build contexts under
  `services/` and CI jobs are gone, with huginn-ai's Prometheus scrape job, its three
  alert rules and their promtool cases, the console's regime and feature-store panels,
  the header's regime tile and their proxy routes, and `make bench`. obi-bridge, frozen
  by D1, still calls huginn-ai and falls back to `mlScore` 0.5 and `mlReady` 0. mimir's
  Docker volume is kept on the owner's decision; compose no longer declares it, which
  does not delete it. The stack declares 19 services
- news-sentinel (G2.6). Its two RSS sources, CoinDesk and Cointelegraph, bar automated
  access and AI use (DATA_LICENCES.md, finding 1), and the owner stopped it on
  2026-11-21. The service, its build context under `services/` and its CI job are gone;
  obi-bridge's `newsSentiment` field reads 0.0. `docs/CONTAINERS.md` records a keep,
  freeze or retire decision, with its memory and attention cost, for every container

### Added
- Unified docker-compose.yml booting the full stack (23 containers) plus infrastructure
- End-to-end smoke test validating the full trade-to-fill pipeline
- clone-all.sh script for one-command repo setup
- README with architecture diagram, service table, quick start guide
- CONTRIBUTING.md, SECURITY.md, CODE_OF_CONDUCT.md governance files
- Apache 2.0 LICENSE
- CLAUDE.md agent context
- Analytics & ML services (built from `services/`): Odin (performance/risk analytics,
  :8086), Bragi (trade explainability, :8087), Huginn AI (XGBoost signal-quality
  model, served; not a live signal by default, :8092), and News Sentinel (LLM
  news-sentiment feed, :8089)
- Mimir point-in-time feature store (:8095) and Forseti execution TCA (:8096) services
- Heimdall market-regime detector (:8097): Gaussian HMM fit with Baum-Welch (EM),
  causal forward-filtered state, online refit, and a Mimir warm-start so it is trained
  on boot rather than waiting for the live feed. Descriptive regime labels only (no edge claim)
- Forseti market-impact & capacity endpoints (`/api/impact`, `/api/impact/schedule`,
  `/api/capacity`): square-root-law + Almgren-Chriss impact and assumed-edge capacity bound
- Console panels for both: MARKET REGIME · HEIMDALL and MARKET IMPACT & CAPACITY · FORSETI,
  wired live through the same-origin proxy (serve.py) with honest no-edge disclaimers
- Research gateway service (validation-as-a-service, walk-forward + PBO + Deflated-Sharpe,
  built from `../huginn` cmd/research, :8094)
- obi-bridge service (order-book-imbalance feature bridge, built from `services/obi-bridge`)
- Monitoring stack: Prometheus (:9091), Alertmanager (:9093), Grafana (:3001) with
  provisioned dashboards/datasources, and Tempo tracing
- Web console (`console/`) for live monitoring and HALT/RESUME breaker control

## [0.1.0] - 2026-06-18

### Added
- Initial meta-repo creation
- docker-compose.yml with muninn, huginn, sleipnir, redpanda, postgres, minio
- scripts/clone-all.sh
- scripts/smoke.sh (5-phase end-to-end pipeline test)
