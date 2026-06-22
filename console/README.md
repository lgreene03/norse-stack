# Norse Console

A standalone, dependency-free operator + research dashboard for the Norse Stack.
It renders a dark trading console — live trading (equity curve, signed open
positions, fills feed), the **alpha factory** (composite score + per-alpha
contribution / confidence / IC), **portfolio construction** (dollar-neutral
target weights, factor exposures, risk contributions), the **walk-forward /
PBO edge verdict**, plus three research/ops panels — the **research gateway**
(walk-forward as a service, run on demand), the **feature store** (Mimir
point-in-time, no-lookahead), and **execution TCA** (Forseti transaction-cost
analysis) — and updates itself from the live services every 3s.

No build step, no npm, no React. Pure static HTML + CSS + vanilla JS, served by a
tiny stdlib Python server that also reverse-proxies the service APIs so the
browser talks to a **single origin** (no CORS configuration required).

## Files

| File                     | What it is                                                                                  |
|--------------------------|---------------------------------------------------------------------------------------------|
| `index.html`             | The full dashboard. Renders the complete design with **zero JS** (demo values are the static default content). `data-nc` hooks mark every live field. |
| `live.js`                | The live data layer. Polls the proxied endpoints, maps responses to the panels, patches the DOM. |
| `serve.py`               | Stdlib HTTP server (no-cache) **+ reverse proxy** for `/api/*` → the backend services.      |
| `norse-console.dc.html`  | The original Claude Design "DC" export, kept unchanged as the design artifact of record.    |

## Run

```bash
# the stack must be up:  (cd .. && docker compose up -d)
python3 serve.py
# then open http://localhost:8090
```

Override the port with `PORT=9000 python3 serve.py`. The proxy targets
`127.0.0.1` by default; point it at other hosts with `HUGINN_HOST`, `ODIN_HOST`,
`SLEIPNIR_HOST`, `MUNINN_HOST`, `REDPANDA_CONSOLE_HOST`, `RESEARCH_HOST`,
`MIMIR_HOST`, `FORSETI_HOST` (in compose: `research`, `mimir`, `forseti`).

Opening `index.html` directly via `file://` (or adding `?demo=1` to the URL)
renders the design with the baked-in **demo** dataset — useful for previewing the
UI with no stack running.

## Live by default — no CORS dance

`serve.py` reverse-proxies these paths to the backends, so every fetch is
**same-origin** and CORS never enters the picture:

| Console path        | Proxied to                              |
|---------------------|------------------------------------------|
| `/api/snapshot`     | huginn `:8083/api/snapshot`              |
| `/api/metrics`      | huginn `:8083/metrics`                   |
| `/api/alphas`       | huginn `:8083/api/alphas`                |
| `/api/validation`   | huginn `:8083/api/validation`            |
| `/api/portfolio`    | odin `:8086/api/portfolio`               |
| `/api/equity`       | odin `:8086/api/equity`                  |
| `/api/research[/*]` | research `:8094/api/research[/*]` (GET list/detail **+ POST** run submission; query + body forwarded) |
| `/api/sources`      | mimir `:8095/api/sources`                |
| `/api/features`     | mimir `:8095/api/features` (`?as_of=`, `?instrument=` forwarded) |
| `/api/features/history` | mimir `:8095/api/features/history` (`?instrument=`, `?limit=` forwarded) |
| `/api/tca`          | forseti `:8096/api/tca`                  |
| `/api/tca/fills`    | forseti `:8096/api/tca/fills`            |
| `/api/health/<svc>` | each service's health endpoint           |
| `/api/breaker`      | huginn `:8083/api/breaker/*` (POST, HALT)|

A backend that is down returns a `502 {error}` from the proxy — the console
treats that section as unavailable (honest empty state / red service dot) rather
than crashing. The footer badge shows **`LIVE · N sources`** (green) or, only
when nothing responds, a red **backend-unreachable** state. The baked demo data
is reachable solely via the explicit `?demo=1` flag — the console never silently
shows stale fake numbers.

## What each panel reads (all real)

| Panel              | Source                                   | Notes                                                                                          |
|--------------------|------------------------------------------|------------------------------------------------------------------------------------------------|
| Live trading       | `/api/snapshot` + `/api/metrics`         | `portfolio.{Cash,TotalValue,RealizedPnL,UnrealizedPnL,TotalCosts,TotalFills,Positions{}}` + top-level `fills[]`; `ordersCostSuppressed` from `huginn_orders_cost_suppressed_total`. Positions are an object keyed by instrument; flat (0-qty) entries are dropped. |
| Equity curve       | `/api/equity`                            | `points[].value` time series.                                                                  |
| Alpha factory      | `/api/alphas` (+ `/api/metrics`)         | Live composite score, per-alpha weight / contribution / confidence / rolling IC. Powered by the **composite** strategy (`STRATEGY_NAME=composite`); a field the engine hasn't computed yet renders as a muted dash, never a fake value. |
| Portfolio          | `/api/portfolio`                         | Inverse-vol, dollar-neutral target weights + factor exposures + risk contributions, computed by Odin from recent per-instrument returns. `{available:false}` → "no portfolio run" empty state. |
| Validation         | `/api/validation`                        | Walk-forward folds + PBO + deflated Sharpe, derived from the real artifact (see below). Undefined deflated Sharpe renders as `n/a`, not `0`. `{available:false}` → "no walk-forward run yet". |
| Research gateway   | `/api/research/runs` (+ `/runs/{id}`)    | Walk-forward **as a service**, re-run on demand off the live trading process. The Run button `POST`s a job (`{strategy, thresholds, folds}`), then polls the run by id until terminal and renders the result; the recent-runs list + most-recent fold table (best_threshold / test_pnl / test_fills / sharpe) refresh on the 3s cadence. PBO / deflated Sharpe render `n/a` when null. |
| Feature store · Mimir | `/api/sources` + `/api/features?as_of=` | Per-instrument freshness (count, last event time, max ingest lag) from `/api/sources`, plus an **as-of lookup** (datetime input, defaults to now) showing each instrument's `event_time` vs `ingest_time` so the **no-lookahead** behavior is visible. Shows the point-in-time basis string. |
| Execution TCA · Forseti | `/api/tca`                          | Overall tiles (avg slippage bps, total fees, maker/taker ratio, implementation shortfall) + by-instrument table + basis string. `avgSlippageBps: null` (no arrival benchmark, e.g. paper fills) renders `n/a` with the honest basis "fees + reported-slippage only", never a fabricated `0`. `{available:false}` → "no fills yet". |
| Services           | `/api/health/<svc>`                      | huginn/sleipnir/odin `/healthz`, muninn `/actuator/health`, redpanda-console reachability. |
| Regime             | — (no live source)                       | Rendered as `—`. The per-instrument regime exists in the feature stream but isn't surfaced as a single snapshot value; left honest rather than fabricated. |

### The validation artifact

`/api/validation` reads the walk-forward results JSON produced by huginn's
`cmd/walkforward`. Regenerate it and the panel updates:

```bash
cd ../huginn
go run ./cmd/walkforward --data data/btc_test.jsonl --config <obi.yaml> \
  --folds 4 --thresholds 0.5,0.6,0.7,0.8
```

`docker-compose.yml` mounts `huginn/data/walkforward_results.json` into the
huginn container (read-only) at `WALKFORWARD_RESULTS_PATH`. If the file is
absent/empty the endpoint returns `{available:false}` — it never fabricates
folds. (The committed run shows the honest verdict: 0/4 OOS folds profitable,
PBO 1.00.)

### HALT / RESUME

The HALT/RESUME button reads a token from `window.NC_TOKEN` or
`localStorage['nc_token']`:

- **With a token** it `POST`s through the proxy to huginn's breaker endpoint with
  `Authorization: Bearer <token>` and reflects the halted state (red banner).
- **Without a token** it only toggles the local visual state and `console.warn`s
  — live order routing is **not** changed.

> `norse-console.dc.html` is the original Claude Design export and is not used at
> runtime; `index.html` is the servable build.
