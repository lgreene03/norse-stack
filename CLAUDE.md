# CLAUDE.md

## What Is norse-stack

Meta-repository and entry point for the Norse Stack, a distributed quantitative trading infrastructure that boots as 19 containers (`docker compose config --services | wc -l`) via one-command `make bootstrap`. Which of them this iteration keeps, freezes or retires, and what each costs, is in `docs/CONTAINERS.md` (G2.6). This repo provides the unified docker-compose, console, monitoring stack, end-to-end smoke test, and architecture documentation. The core engines (muninn, huginn, sleipnir) live in their own sibling repos, but this repo is **not** purely orchestration: the analytics, research, and bridge services under `services/` are application code built directly from here.

## Commands

```bash
# Clone all service repos (safe to re-run)
./scripts/clone-all.sh

# Boot the full stack (builds all service Docker images)
docker compose up -d --build

# Run the end-to-end smoke test
./scripts/smoke.sh

# Tear down
docker compose down -v
```

## Service Repos

The core engines live as sibling directories:

```
parent/
  norse-stack/     ← this repo
  muninn/          ← Java feature engine
  huginn/          ← Go strategy engine (also builds the `research` gateway, cmd/research)
  sleipnir/        ← Go execution gateway
  muninn-py/       ← Python research SDK
```

## In-Repo Services

These services are application code built directly from this repo (`services/<name>/Dockerfile`):

```
norse-stack/services/
  obi-bridge/      ← order-book-imbalance feature bridge (Muninn → Kafka)
  odin/            ← performance / risk analytics (port 8086)
  forseti/         ← execution TCA + market impact / capacity (port 8096)
  recording-audit/ ← daily recording gap audit, G0.15 (port 8098)
```

bragi, huginn-ai, mimir and heimdall were retired on 2026-11-22 by the owner's decision (G2.6, `docs/CONTAINERS.md`). mimir's Docker volume, `norse-stack_mimir-data`, is kept on the owner's decision; nothing declares it any more, so never prune it.

## Ports

Every host port binds to 127.0.0.1 only, and CI rejects any that does not. The
Postgres databases publish no host port; use `docker compose exec`.

| Port  | Service            |
|-------|--------------------|
| 8080  | Muninn API         |
| 8083  | Huginn API         |
| 8085  | Sleipnir API       |
| 8086  | Odin Analytics     |
| 8088  | Redpanda Console   |
| 8094  | Research Gateway   |
| 8096  | Forseti TCA        |
| 8098  | Recording Audit    |
| 9002  | MinIO API          |
| 9003  | MinIO Console      |
| 9091  | Prometheus         |
| 9093  | Alertmanager       |
| 3001  | Grafana            |
| 50051 | Huginn gRPC        |
| 19092 | Redpanda (Kafka)   |

## Analytics Services

### Odin (Performance Monitor)
- `GET /api/analytics` — comprehensive analytics:
  - Rolling Sharpe and Sortino ratios (1h, 6h, 24h windows)
  - Risk: CVaR (95th percentile), Calmar ratio, Kelly criterion
  - Portfolio VaR: variance-covariance method with diversification benefit
  - Drawdown: magnitude, duration, recovery time
  - Monte Carlo permutation test (p-value, percentile, significance)
  - Cross-asset correlation matrix
  - Per-instrument breakdown with win rate, P&L
- `GET /api/equity` — full equity curve time series
- `GET /api/trades` — recent round-trip trades with P&L
- `GET /api/reconciliation` — backtest-vs-live reconciliation: compares live realized PnL / fees / fill count against the committed `docs/RESULTS.md` expectation, models the 5 bps + 2 bps cost on live notional, and flags material divergence (e.g. live net worse than backtest predicted → a fee/fill-model gap). Also surfaced under the `reconciliation` key of `/api/analytics`.

### Research Gateway (read-only since G1.2, `huginn` cmd/research)
- Runs nothing: `POST /api/research/runs` is refused with or without a token, because H1 and H2 trials run only through `research/norse_research`'s ledgered harness. Do not re-enable it without the ledgered path G1.2 describes.
- `GET /api/research/runs` and `GET /api/research/runs/{id}` serve runs earlier versions persisted.
- Every route but `/healthz` needs `Authorization: Bearer <token>`; the token is the untracked file `secrets/research_token`, mounted as a compose secret and read by `console/serve.py` (docs/RUNBOOK.md, section 1.1). No CORS headers; a Host other than 127.0.0.1, localhost or research on 8094 is refused.
- `console/serve.py` binds 127.0.0.1 and refuses a Host other than `localhost:PORT` or `127.0.0.1:PORT`, because it adds the research token for its callers; `console/tests` (run in CI's validate job) holds it to that.

### Forseti (Execution TCA + Market Impact / Capacity)
- `GET /api/tca` — aggregate transaction-cost analysis: slippage, maker/taker, fees, implementation shortfall
- `GET /api/tca/fills` — underlying per-fill records
- `GET /api/impact?instrument=&size=&adv=&sigma=&eta=` — pre-trade market-impact estimate: square-root-law temporary impact (`eta·sigma·sqrt(Q/ADV)`) plus an Almgren-Chriss permanent term, in bps. Missing inputs fall back to documented defaults and the `basis` field states exactly which were defaulted (no silent fabrication).
- `GET /api/impact/schedule?size=&slices=&riskAversion=` — Almgren-Chriss optimal execution schedule (per-slice sizes trading off impact against timing risk)
- `GET /api/capacity?edgeBps=&instrument=` — strategy capacity: the notional at which modelled impact equals an ASSUMED edge. The response labels the edge as illustrative — this simulation has no measured out-of-sample edge (PBO=1.0), so capacity is a "what-if" bound, not a live figure.
- Computed from real fills; reports `null` slippage when there is no arrival price (e.g. paper fills) rather than fabricating a benchmark
