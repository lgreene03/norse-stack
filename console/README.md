# Norse Console

A standalone, dependency-free dashboard for the Norse Stack quant-trading
infrastructure. It renders a dark trading console (equity curve, open positions,
fills feed, alpha factory, portfolio construction, and the walk-forward
validation / edge verdict) and updates itself from the live services — falling
back, **per section**, to baked-in demo values whenever an endpoint is
unreachable.

No build step, no npm, no React. Pure static HTML + CSS + vanilla JS.

## Files

| File                      | What it is                                                                 |
|---------------------------|----------------------------------------------------------------------------|
| `index.html`             | The full dashboard. Renders the complete design with **zero JS** (demo data is the static default content). |
| `live.js`                | The live data layer. Polls the services, maps responses to the data shape, and patches the DOM. |
| `serve.py`               | Tiny stdlib HTTP server with no-cache headers.                              |
| `norse-console.dc.html`  | The original Claude Design "DC" export, kept unchanged as the design artifact. |

## Run

```bash
python3 serve.py
# then open http://localhost:8090
```

Override the port with `PORT=9000 python3 serve.py`.

`index.html` also works opened directly via `file://` — it renders the design;
the live layer simply stays on demo data if it can't reach the services or is
blocked by CORS.

## Live vs. Demo behaviour

- `live.js` embeds a `DEMO` constant (the exact `defaultData()` object from the
  DC export). On load and every **3000 ms** it calls `loadLive()`, which fetches
  each source independently with a short `AbortController` timeout.
- Each data section (`live`, `alpha`, `portfolio`, `validation`, `services`) is
  merged over `DEMO` **only if its fetch succeeds**. A failed or unreachable
  section keeps its demo values. Nothing ever throws — on a total failure the
  page just stays on demo data (which still renders perfectly).
- The footer badge reflects state: **`DEMO DATA`** (amber) when no section went
  live, or **`LIVE · N sources`** (green) listing how many sections were
  refreshed from real endpoints.

## Endpoint map

Base URL defaults to `http://localhost` and is overridable via
`window.NC_BASE` (set it in an inline `<script>` before `live.js`, e.g. when
running behind a reverse proxy).

| Section      | Source                                   | Mapping                                                                                                  |
|--------------|------------------------------------------|----------------------------------------------------------------------------------------------------------|
| `live`       | `GET :8083/api/snapshot`                 | `portfolio.{Cash→cash, TotalValue→totalValue, RealizedPnL→realizedPnL, UnrealizedPnL→unrealizedPnL, TotalCosts→fees, TotalFills→totalFills, Positions[]→positions[]}` and top-level `fills[]` (`Side` 0=BUY/1=SELL, timestamp → `HH:MM:SS`). |
| `live`       | `GET :8083/metrics`                      | `ordersCostSuppressed` = sum of all `huginn_orders_cost_suppressed_total` samples.                       |
| `live`       | `GET :8086/api/equity` (optional)        | `equitySeries` (array of numbers or `{equity\|value\|TotalValue}`); else keeps demo.                     |
| `live`       | regime                                   | Keeps demo (not trivially available).                                                                    |
| `alpha`      | `GET :8083/metrics`                      | `huginn_composite_score` → `compositeScore`; `huginn_alpha_contribution{alpha="X"}` → that alpha's `contribution`. Weight / confidence / IC keep demo (not in metrics). |
| `portfolio`  | — (demo)                                 | Kept on demo. *Future:* a muninn-py optimizer endpoint for weights / factors / risk.                     |
| `validation` | — (demo)                                 | Kept on demo. These are the honest committed walk-forward numbers (0/4 OOS folds profitable, PBO 1.00).  |
| `services`   | health checks                            | `huginn :8083/healthz`, `sleipnir :8085/healthz`, `odin :8086/healthz`, `muninn :8080/actuator/health` (fallback `/healthz`), `redpanda :8088` reachability (redpanda-console). `ok → up`, network-fail → `down`. |

### HALT / RESUME

The HALT/RESUME button checks for a token in `window.NC_TOKEN` or
`localStorage['nc_token']`:

- **With a token** it `POST`s to `:8083/api/breaker` with
  `Authorization: Bearer <token>` (huginn breaker endpoints live under
  `/api/breaker/*`) and reflects the halted state (red banner) on success.
- **Without a token** it only toggles the local visual halted state and
  `console.warn`s that no token is set — live order routing is **not** changed.

## CORS note

The browser fetches **cross-origin** to `:8083`, `:8086`, etc. while the page is
served from `:8090`. For the live layer to work you must either:

1. **Allow the console origin on each service:**
   - huginn: `HUGINN_DASHBOARD_ORIGIN=http://localhost:8090`
   - the Python services (odin, etc.): `ACCESS_CONTROL_ALLOW_ORIGIN=http://localhost:8090`
2. **Or run the console behind a same-origin reverse proxy** that fronts both
   the static files and the service APIs under one origin (and set
   `window.NC_BASE` accordingly).

Without either, cross-origin fetches are blocked by the browser and the console
stays in DEMO mode — which still renders the full design perfectly.

> `norse-console.dc.html` is kept as the original Claude Design export and is
> not used at runtime.
