/* Norse Console — live data layer (vanilla JS, no dependencies)
 *
 * Talks ONLY to the same-origin reverse proxy in serve.py (no cross-origin, no
 * hardcoded service URLs). Each panel is populated from a real backend
 * response:
 *
 *   /api/snapshot   → huginn  live trading (equity strip, positions, fills)
 *   /api/metrics    → huginn  composite score, alpha contributions, suppressed
 *   /api/alphas     → huginn  alpha rows (weight / contribution / confidence / IC)
 *   /api/portfolio  → odin    target weights / factor exposure / risk donut
 *   /api/validation → huginn  walk-forward folds / PBO / deflated Sharpe
 *   /api/equity     → odin    equity curve
 *   /api/health/<s> → <svc>   service status dots
 *   POST /api/breaker         HALT / RESUME (forwarded to huginn breaker)
 *
 * The page boots with NEUTRAL placeholders ("—", "connecting…"); whatever a
 * backend genuinely returns replaces them. A section the backend reports as
 * unavailable shows an HONEST empty state — never a fabricated number. The
 * baked DEMO object is used as the data source ONLY behind the ?demo=1 query
 * flag, so the design stays previewable offline; the default is LIVE.
 *
 * Config (set BEFORE this script loads, e.g. via an inline <script>):
 *   window.NC_TOKEN — bearer token for the HALT/RESUME breaker endpoint
 *   localStorage 'nc_token' — alternative source for the bearer token
 */
(function () {
  'use strict';

  // ---- palette (matches the design) ----
  var C = { green: '#2bd4a4', red: '#ff6262', blue: '#5b8def', amber: '#f3b13c', mut: '#9fb0c8', dim: '#6b7c95' };

  var POLL_MS = 3000;
  var FETCH_TIMEOUT_MS = 4000;

  // LIVE by default. ?demo=1 swaps the data SOURCE to the baked DEMO object so
  // the design is previewable with no backend.
  var DEMO_MODE = /[?&]demo=1\b/.test(window.location.search);

  // ====================================================================
  // DEMO — preview-only dataset (used as the data source ONLY when ?demo=1).
  // ====================================================================
  var DEMO = {
    live: {
      totalValue: 1000.3, cash: 935.8, realizedPnL: 0.58, unrealizedPnL: -0.06,
      fees: 0.21, totalFills: 22, ordersCostSuppressed: 10,
      regime: 'TREND',
      positions: [
        { instrument: 'BTC-USDT', quantity: 0.001, averageCost: 64197.4, lastMarkPrice: 64210.0, unrealizedPnL: 0.013 },
        { instrument: 'ETH-USDT', quantity: -0.002, averageCost: 1727.1, lastMarkPrice: 1723.0, unrealizedPnL: 0.008 }
      ],
      fills: [
        { instrument: 'ETH-USDT', side: 'SELL', quantity: 0.001, fillPrice: 1727.07, timestamp: '14:56:09' },
        { instrument: 'BTC-USDT', side: 'BUY', quantity: 0.001, fillPrice: 64197.4, timestamp: '14:55:52' },
        { instrument: 'SOL-USDT', side: 'BUY', quantity: 0.04, fillPrice: 142.18, timestamp: '14:55:31' }
      ],
      equitySeries: [998.4, 998.9, 999.4, 999.9, 1000.3, 1000.6, 1000.2, 1000.5, 1000.8, 1000.4, 1000.3]
    },
    alpha: {
      compositeScore: 0.42, entryThreshold: 0.30, blend: 'weighted-sum',
      alphas: [
        { name: 'imbalance', weight: 0.30, contribution: 0.51, confidence: 0.80, ic: [0.02, -0.01, 0.03, 0.0, -0.02] },
        { name: 'momentum', weight: 0.25, contribution: -0.12, confidence: 0.60, ic: [-0.01, 0.0, -0.02, 0.01, -0.03] },
        { name: 'mean_reversion', weight: 0.20, contribution: 0.33, confidence: 0.70, ic: [0.01, 0.02, 0.0, 0.03, 0.02] }
      ]
    },
    portfolio: {
      available: true,
      weights: { BTC: 0.32, ETH: -0.18, SOL: 0.10, XRP: -0.14, DOGE: -0.10 },
      factorExposures: { market: 0.05, momentum: 0.21, volatility: -0.12 },
      riskContributions: { BTC: 0.22, ETH: 0.19, SOL: 0.20, XRP: 0.21, DOGE: 0.18 }
    },
    validation: {
      available: true,
      folds: [
        { fold: 1, train: 288, test: 288, isPnL: -45.3, oosPnL: -57.8 },
        { fold: 2, train: 576, test: 288, isPnL: -99.6, oosPnL: -0.1 },
        { fold: 3, train: 864, test: 288, isPnL: -99.0, oosPnL: -20.8 },
        { fold: 4, train: 1152, test: 288, isPnL: -219.9, oosPnL: -65.4 }
      ],
      oosFoldsProfitable: '0/4', totalOOSPnL: -146.1, pbo: 1.0, deflatedSharpe: null
    },
    services: [
      { name: 'muninn', status: 'up' }, { name: 'huginn', status: 'up' },
      { name: 'sleipnir', status: 'degraded' }, { name: 'odin', status: 'up' },
      { name: 'redpanda', status: 'up' }
    ],
    research: {
      runs: [
        { id: 'r-1042', status: 'done', strategy: 'obi', submittedAt: '2026-06-22T14:50:11Z' },
        { id: 'r-1041', status: 'error', strategy: 'ou', submittedAt: '2026-06-22T14:31:02Z' },
        { id: 'r-1040', status: 'done', strategy: 'composite', submittedAt: '2026-06-22T13:58:44Z' }
      ],
      latest: {
        id: 'r-1042', status: 'done', strategy: 'obi', submittedAt: '2026-06-22T14:50:11Z',
        result: {
          folds: [
            { fold: 1, best_threshold: 0.6, test_pnl: -57.8, test_fills: 41, sharpe: -0.42 },
            { fold: 2, best_threshold: 0.7, test_pnl: -0.1, test_fills: 38, sharpe: -0.01 },
            { fold: 3, best_threshold: 0.5, test_pnl: -20.8, test_fills: 52, sharpe: -0.18 },
            { fold: 4, best_threshold: 0.8, test_pnl: -65.4, test_fills: 33, sharpe: -0.55 }
          ],
          oosFoldsProfitable: 0, totalOOSPnL: -146.1, pbo: 1.0, deflatedSharpe: null
        }
      }
    },
    features: {
      asOf: '2026-06-22T14:56:00Z',
      basis: 'point-in-time (event_time ≤ as_of AND ingest_time ≤ as_of)',
      sources: [
        { instrument: 'BTC-USDT', count: 18422, last_event_time: '2026-06-22T14:55:58Z', max_ingest_lag_secs: 3.2 },
        { instrument: 'ETH-USDT', count: 17988, last_event_time: '2026-06-22T14:55:57Z', max_ingest_lag_secs: 4.1 },
        { instrument: 'SOL-USDT', count: 16110, last_event_time: '2026-06-22T14:55:40Z', max_ingest_lag_secs: 88.0 }
      ],
      features: [
        { instrument: 'BTC-USDT', event_time: '2026-06-22T14:55:58Z', ingest_time: '2026-06-22T14:56:01Z', feature: {} },
        { instrument: 'ETH-USDT', event_time: '2026-06-22T14:55:57Z', ingest_time: '2026-06-22T14:56:01Z', feature: {} }
      ]
    },
    tca: {
      available: true, asOf: '2026-06-22T14:56:00Z',
      basis: 'fees + reported-slippage only',
      overall: { totalFills: 22, avgSlippageBps: null, totalFees: 0.21, makerTakerRatio: 0.64, totalImplementationShortfall: 0.0, avgFeeBps: 1.8, totalNotional: 412.6 },
      byInstrument: {
        'BTC-USDT': { totalFills: 9, avgSlippageBps: null, totalFees: 0.11, totalNotional: 230.4 },
        'ETH-USDT': { totalFills: 13, avgSlippageBps: null, totalFees: 0.10, totalNotional: 182.2 }
      }
    }
  };

  // ====================================================================
  // Formatting helpers
  // ====================================================================
  function nf(v, d) { return Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }); }
  function usd(v, d) { d = d == null ? 2 : d; return (v < 0 ? '-$' : '$') + nf(Math.abs(v), d); }
  function signedUsd(v, d) { d = d == null ? 2 : d; return (v >= 0 ? '+$' : '-$') + nf(Math.abs(v), d); }
  function pct(v, d) { d = d == null ? 2 : d; return (v >= 0 ? '+' : '') + nf(v, d) + '%'; }
  function polar(cx, cy, r, deg) { var a = (deg - 90) * Math.PI / 180; return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) }; }
  function arc(cx, cy, r, d0, d1) {
    var s = polar(cx, cy, r, d1), e = polar(cx, cy, r, d0);
    var large = Math.abs(d1 - d0) > 180 ? 1 : 0;
    return 'M ' + s.x.toFixed(1) + ' ' + s.y.toFixed(1) + ' A ' + r + ' ' + r + ' 0 ' + large + ' 0 ' + e.x.toFixed(1) + ' ' + e.y.toFixed(1);
  }
  // dash() — render a muted placeholder when a value is genuinely absent rather
  // than fabricate a number.
  var DASH = '—';
  function isNum(v) { return typeof v === 'number' && !isNaN(v); }

  // Render an ISO8601 timestamp as a compact HH:MM:SS (falling back to the
  // date+time, or the raw string) for the dense mono tables. Absent → dash.
  function fmtTime(iso) {
    if (!iso) return DASH;
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }
  // Human "Ns ago"-style lag rendering from a seconds value.
  function fmtLag(secs) {
    if (!isNum(secs)) return DASH;
    if (secs < 90) return nf(secs, secs < 10 ? 1 : 0) + 's';
    if (secs < 5400) return nf(secs / 60, 0) + 'm';
    return nf(secs / 3600, 1) + 'h';
  }

  // as-of for the Mimir lookup: the datetime-local input when set, else now.
  function currentAsOf() {
    var el = document.querySelector('[data-nc="fs-asof"]');
    if (el && el.value) {
      // datetime-local has no timezone; treat as local and convert to ISO.
      var d = new Date(el.value);
      if (!isNaN(d.getTime())) return d.toISOString();
    }
    return new Date().toISOString();
  }
  // Set the as-of input default to "now" (local) once, if empty.
  function ensureAsOfDefault() {
    var el = document.querySelector('[data-nc="fs-asof"]');
    if (!el || el.value) return;
    var d = new Date();
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    el.value = d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) +
               'T' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }

  // ====================================================================
  // Small DOM / fetch utilities
  // ====================================================================
  function $(key) { return document.querySelector('[data-nc="' + key + '"]'); }
  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function setText(key, value) { var el = $(key); if (el) el.textContent = value; }
  function setColor(key, color) { var el = $(key); if (el) el.style.color = color; }

  function fetchWithTimeout(url, opts) {
    opts = opts || {};
    var ctl = new AbortController();
    var t = setTimeout(function () { ctl.abort(); }, opts.timeout || FETCH_TIMEOUT_MS);
    var o = { method: opts.method || 'GET', signal: ctl.signal, cache: 'no-store' };
    if (opts.headers) o.headers = opts.headers;
    if (opts.body != null) o.body = opts.body;
    return fetch(url, o).finally(function () { clearTimeout(t); });
  }
  function getJSON(path) {
    return fetchWithTimeout(path).then(function (r) {
      if (!r.ok) throw new Error(path + ' ' + r.status);
      return r.json();
    });
  }

  // Parse Prometheus text exposition into [{name, labels:{}, value}]
  function parseMetrics(text) {
    var out = [];
    var lines = text.split('\n');
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i].trim();
      if (!line || line.charAt(0) === '#') continue;
      var m = line.match(/^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+([-+0-9.eE naN]+)/);
      if (!m) continue;
      var name = m[1];
      var labels = {};
      if (m[2]) {
        var inner = m[2].slice(1, -1);
        var re = /([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"/g, lm;
        while ((lm = re.exec(inner))) { labels[lm[1]] = lm[2].replace(/\\"/g, '"').replace(/\\n/g, '\n').replace(/\\\\/g, '\\'); }
      }
      var val = parseFloat(m[3]);
      if (!isNaN(val)) out.push({ name: name, labels: labels, value: val });
    }
    return out;
  }

  // ====================================================================
  // loadLive — fetch every same-origin proxy source independently.
  // Returns { data, sources:Set } where `data` carries ONLY what came back
  // live (each section may be null/absent → the renderer shows its honest
  // empty/placeholder state). In ?demo=1 mode it short-circuits to DEMO.
  // ====================================================================
  function loadLive() {
    if (DEMO_MODE) {
      var demo = JSON.parse(JSON.stringify(DEMO));
      return Promise.resolve({
        data: demo,
        sources: new Set(['demo']),
        demo: true
      });
    }

    var data = { live: null, alpha: null, portfolio: null, validation: null, services: null,
                 research: null, features: null, tca: null };
    var sources = new Set();

    // shared metrics text (huginn /api/metrics)
    var metricsP = fetchWithTimeout('/api/metrics')
      .then(function (r) { return r.ok ? r.text() : null; })
      .catch(function () { return null; });

    // ---- live ← /api/snapshot (+ metrics suppressed) ----
    var liveP = getJSON('/api/snapshot').then(function (j) {
      var p = j.portfolio || j.Portfolio || {};
      var L = {
        totalValue: p.TotalValue, cash: p.Cash,
        realizedPnL: p.RealizedPnL, unrealizedPnL: p.UnrealizedPnL,
        fees: p.TotalCosts, totalFills: p.TotalFills,
        ordersCostSuppressed: null, regime: null,
        positions: [], fills: [], equitySeries: null,
        // live breaker state — top-level `halted` (bool) + `halt_reason` (string)
        // on the huginn /api/snapshot response. Authoritative source for the
        // RUNNING/HALTED indicator (the optimistic `halted` flag is only an echo).
        halted: (j.halted === true || j.Halted === true),
        haltReason: (j.halt_reason || j.HaltReason || '')
      };
      // huginn serializes Positions as an OBJECT keyed by instrument
      // ({"BTC-USDT": {...}}), not an array — normalize both forms and drop
      // flat (zero-qty) entries so OPEN POS counts only live exposure.
      var posList = Array.isArray(p.Positions) ? p.Positions
        : (p.Positions && typeof p.Positions === 'object')
          ? Object.keys(p.Positions).map(function (k) { return p.Positions[k]; })
          : [];
      L.positions = posList
        .filter(function (pos) { return pos && pos.Quantity; })
        .map(function (pos) {
          return {
            instrument: pos.Instrument, quantity: pos.Quantity,
            averageCost: pos.AverageCost, lastMarkPrice: pos.LastMarkPrice,
            unrealizedPnL: pos.UnrealizedPnL
          };
        });
      if (Array.isArray(j.fills)) {
        L.fills = j.fills.map(function (f) {
          var side = f.Side === 1 || f.Side === 'SELL' ? 'SELL' : 'BUY';
          var ts = String(f.Timestamp == null ? '' : f.Timestamp);
          var hm = ts.match(/(\d{2}:\d{2}:\d{2})/);
          return {
            instrument: f.Instrument, side: side, quantity: f.Quantity,
            fillPrice: f.FillPrice, timestamp: hm ? hm[1] : ts
          };
        }).reverse(); // newest first for the feed
      }
      // /api/equity and /metrics resolve independently; if any landed first,
      // carry over the fields they set so this wholesale assignment doesn't
      // clobber the equity curve / suppressed count / regime.
      if (data.live) {
        if (data.live.equitySeries) L.equitySeries = data.live.equitySeries;
        if (data.live.ordersCostSuppressed != null) L.ordersCostSuppressed = data.live.ordersCostSuppressed;
        if (data.live.regime != null) L.regime = data.live.regime;
      }
      data.live = L;
      sources.add('huginn');
    }).catch(function () { /* live stays null → placeholders */ });

    // ordersCostSuppressed + composite/contributions ← metrics
    var metricsApplyP = metricsP.then(function (text) {
      if (!text) return;
      var samples = parseMetrics(text);
      var sup = 0, supFound = false, score = null;
      var contrib = {};
      for (var i = 0; i < samples.length; i++) {
        var s = samples[i];
        if (s.name === 'huginn_orders_cost_suppressed_total') { sup += s.value; supFound = true; }
        if (s.name === 'huginn_composite_score') { score = s.value; }
        if (s.name === 'huginn_alpha_contribution' && s.labels.alpha) { contrib[s.labels.alpha] = s.value; }
      }
      // Always surface the count when /metrics is reachable: a strategy that
      // doesn't emit the counter (e.g. composite) legitimately has 0 suppressed,
      // which is more honest than a dangling dash. supFound only gates nothing
      // extra now — sup is 0 when the series is absent.
      if (!data.live) data.live = {};
      data.live.ordersCostSuppressed = sup;
      sources.add('huginn');
      // Stash metrics-derived alpha hints so the alpha loader can fall back to
      // them if /api/alphas is unavailable.
      data._metricsAlpha = { score: score, contrib: contrib };
    }).catch(function () {});

    // ---- alpha ← /api/alphas (preferred) else metrics-only fallback ----
    var alphaP = getJSON('/api/alphas').then(function (j) {
      // Expected: {compositeScore, entryThreshold, blend, alphas:[{name,weight,contribution,confidence,ic}]}
      data.alpha = {
        compositeScore: isNum(j.compositeScore) ? j.compositeScore : null,
        entryThreshold: isNum(j.entryThreshold) ? j.entryThreshold : null,
        blend: j.blend || null,
        alphas: Array.isArray(j.alphas) ? j.alphas.map(function (a) {
          return {
            name: a.name,
            weight: isNum(a.weight) ? a.weight : null,
            contribution: isNum(a.contribution) ? a.contribution : null,
            confidence: isNum(a.confidence) ? a.confidence : null,
            ic: Array.isArray(a.ic) ? a.ic.filter(isNum) : []
          };
        }) : []
      };
      sources.add('huginn');
    }).catch(function () {
      // Fallback: build a minimal alpha view from /metrics only (composite
      // score + per-alpha contribution). Weight / confidence / IC are NOT in
      // metrics, so they stay null/empty → rendered as muted dashes.
      return metricsP.then(function () {
        var ma = data._metricsAlpha;
        if (!ma || (ma.score == null && Object.keys(ma.contrib).length === 0)) return;
        var names = Object.keys(ma.contrib);
        data.alpha = {
          compositeScore: ma.score,
          entryThreshold: null,
          blend: null,
          alphas: names.map(function (n) {
            return { name: n, weight: null, contribution: ma.contrib[n], confidence: null, ic: [] };
          })
        };
        sources.add('huginn');
      });
    });

    // ---- portfolio ← /api/portfolio ----
    var portfolioP = getJSON('/api/portfolio').then(function (j) {
      if (j && j.available === false) { data.portfolio = { available: false }; return; }
      data.portfolio = {
        available: true,
        weights: j.weights || {},
        factorExposures: j.factorExposures || {},
        riskContributions: j.riskContributions || {},
        asOf: j.asOf || null, basis: j.basis || null
      };
      sources.add('odin');
    }).catch(function () { /* portfolio stays null → empty state */ });

    // ---- validation ← /api/validation ----
    var validationP = getJSON('/api/validation').then(function (j) {
      if (j && j.available === false) { data.validation = { available: false }; return; }
      data.validation = {
        available: true,
        folds: Array.isArray(j.folds) ? j.folds : [],
        oosFoldsProfitable: j.oosFoldsProfitable != null ? j.oosFoldsProfitable : null,
        totalOOSPnL: isNum(j.totalOOSPnL) ? j.totalOOSPnL : null,
        pbo: isNum(j.pbo) ? j.pbo : null,
        deflatedSharpe: isNum(j.deflatedSharpe) ? j.deflatedSharpe : null
      };
      sources.add('huginn');
    }).catch(function () { /* validation stays null → empty state */ });

    // ---- equity ← /api/equity (odin) ----
    var equityP = getJSON('/api/equity').then(function (j) {
      var arr = Array.isArray(j) ? j : (j.points || j.equity || j.series || null);
      if (!Array.isArray(arr) || !arr.length) return;
      var series = arr.map(function (x) {
        if (typeof x === 'number') return x;
        return x.value != null ? x.value : (x.equity != null ? x.equity : x.TotalValue);
      }).filter(isNum);
      if (series.length) {
        if (!data.live) data.live = {};
        data.live.equitySeries = series;
        sources.add('odin');
      }
    }).catch(function () { /* no equity → curve placeholder */ });

    // ---- services ← /api/health/<svc> ----
    var svcList = [
      { name: 'muninn', svc: 'muninn' },
      { name: 'huginn', svc: 'huginn' },
      { name: 'sleipnir', svc: 'sleipnir' },
      { name: 'odin', svc: 'odin' },
      { name: 'redpanda', svc: 'redpanda-console' }
    ];
    var svcChecks = svcList.map(function (m) {
      return getJSON('/api/health/' + m.svc)
        .then(function (j) { return { name: m.name, status: j.status || 'down' }; })
        .catch(function () { return { name: m.name, status: 'down' }; });
    });
    var servicesP = Promise.all(svcChecks).then(function (results) {
      data.services = results;
      var anyUp = results.some(function (r) { return r.status === 'up' || r.status === 'degraded'; });
      if (anyUp) sources.add('health');
    }).catch(function () {});

    // ---- research ← /api/research/runs (list) + newest run detail (research) -
    var researchP = getJSON('/api/research/runs').then(function (list) {
      var runs = Array.isArray(list) ? list : [];
      data.research = { runs: runs, latest: null };
      sources.add('research');
      // Fetch the detail of the newest run (list is newest-first) so the
      // result panel can show its folds. A latest run still "running" is fine —
      // the result area renders its status honestly.
      if (runs.length && runs[0] && runs[0].id != null) {
        return getJSON('/api/research/runs/' + encodeURIComponent(runs[0].id))
          .then(function (det) { data.research.latest = det; })
          .catch(function () { /* keep list, no detail */ });
      }
    }).catch(function () { /* research stays null → empty state */ });

    // ---- features / sources ← mimir ----
    var asOf = currentAsOf();
    var featSources = getJSON('/api/sources').then(function (j) {
      data.features = data.features || {};
      data.features.sources = (j && Array.isArray(j.sources)) ? j.sources : [];
      sources.add('mimir');
    }).catch(function () {});
    var featAsOf = getJSON('/api/features?as_of=' + encodeURIComponent(asOf)).then(function (j) {
      data.features = data.features || {};
      data.features.asOf = (j && j.asOf) || asOf;
      data.features.basis = (j && j.basis) || null;
      data.features.features = (j && Array.isArray(j.features)) ? j.features : [];
      sources.add('mimir');
    }).catch(function () {});
    var featuresP = Promise.all([featSources, featAsOf]);

    // ---- tca ← forseti ----
    var tcaP = getJSON('/api/tca').then(function (j) {
      if (j && j.available === false) { data.tca = { available: false, basis: j.basis || null }; return; }
      data.tca = {
        available: true,
        asOf: (j && j.asOf) || null,
        basis: (j && j.basis) || null,
        overall: (j && j.overall) || {},
        byInstrument: (j && j.byInstrument) || {}
      };
      sources.add('forseti');
    }).catch(function () { /* tca stays null → empty state */ });

    return Promise.all([liveP, metricsApplyP, alphaP, portfolioP, validationP, equityP, servicesP,
                        researchP, featuresP, tcaP])
      .then(function () { return { data: data, sources: sources, demo: false }; })
      .catch(function () { return { data: data, sources: sources, demo: false }; });
  }

  // ====================================================================
  // apply — write whatever came back into the DOM; honest empty states for
  // anything genuinely unavailable.
  // ====================================================================
  var halted = false;
  var liveHaltReason = ''; // halt_reason from the live snapshot, surfaced when halted

  function regimeStyle(regime) {
    var map = {
      QUIET: { color: '#7787a0', bg: '#11192a', border: '#22324a' },
      TREND: { color: C.blue, bg: '#0e1a30', border: '#23436e' },
      'MEAN-REVERT': { color: C.amber, bg: '#1c1606', border: '#43381a' },
      VOLATILE: { color: C.red, bg: '#1f0c0e', border: '#4a2026' }
    };
    return map[regime] || map.QUIET;
  }

  function applyLive(L) {
    if (!L) {
      // No live trading data yet — neutral placeholders.
      setText('equity', DASH); setText('cash', DASH);
      setText('net-pnl', DASH); setColor('net-pnl', C.mut);
      setText('net-pnl-pct', DASH); setColor('net-pnl-pct', C.mut);
      setText('open-pos', DASH); setText('fills-today', DASH); setText('suppressed', DASH);
      var pb = $('positions-body'); if (pb) pb.innerHTML = emptyRow(5, 'connecting to huginn…');
      var ff = $('fills-feed'); if (ff) ff.innerHTML = emptyBlock('connecting to huginn…');
      var st = $('stat-tiles'); if (st) st.innerHTML = '';
      return;
    }

    var realized = isNum(L.realizedPnL) ? L.realizedPnL : 0;
    var unreal = isNum(L.unrealizedPnL) ? L.unrealizedPnL : 0;
    var netPnl = realized + unreal;
    var haveNet = isNum(L.realizedPnL) || isNum(L.unrealizedPnL);

    setText('equity', isNum(L.totalValue) ? usd(L.totalValue) : DASH);
    setText('cash', isNum(L.cash) ? usd(L.cash) : DASH);
    var netColor = netPnl >= 0 ? C.green : C.red;
    if (haveNet) {
      setText('net-pnl', signedUsd(netPnl)); setColor('net-pnl', netColor);
      if (isNum(L.totalValue) && (L.totalValue - netPnl) !== 0) {
        setText('net-pnl-pct', pct((netPnl / (L.totalValue - netPnl)) * 100, 3));
      } else { setText('net-pnl-pct', DASH); }
      setColor('net-pnl-pct', netColor);
    } else {
      setText('net-pnl', DASH); setColor('net-pnl', C.mut);
      setText('net-pnl-pct', DASH); setColor('net-pnl-pct', C.mut);
    }
    setText('open-pos', Array.isArray(L.positions) ? L.positions.length : DASH);
    setText('fills-today', isNum(L.totalFills) ? L.totalFills : DASH);
    setText('suppressed', isNum(L.ordersCostSuppressed) ? L.ordersCostSuppressed : DASH);

    // regime: only when the backend actually reports it.
    var rm = regimeStyle(L.regime);
    var pill = $('regime-pill');
    if (L.regime) {
      if (pill) { pill.style.background = rm.bg; pill.style.borderColor = rm.border; }
      var rdot = $('regime-dot'); if (rdot) rdot.style.background = rm.color;
      setText('regime', L.regime); setColor('regime', rm.color);
    } else {
      setText('regime', DASH); setColor('regime', C.dim);
      if (pill) { pill.style.background = '#11192a'; pill.style.borderColor = '#22324a'; }
    }

    // stat tiles
    var statTiles = [
      { label: 'REALIZED PnL', value: isNum(L.realizedPnL) ? signedUsd(L.realizedPnL) : DASH, color: isNum(L.realizedPnL) ? (L.realizedPnL >= 0 ? C.green : C.red) : C.dim, sub: 'booked today' },
      { label: 'UNREALIZED PnL', value: isNum(L.unrealizedPnL) ? signedUsd(L.unrealizedPnL) : DASH, color: isNum(L.unrealizedPnL) ? (L.unrealizedPnL >= 0 ? C.green : C.red) : C.dim, sub: 'open marks' },
      { label: 'FEES', value: isNum(L.fees) ? usd(L.fees) : DASH, color: C.mut, sub: 'taker + maker' },
      { label: 'NET PnL', value: haveNet ? signedUsd(netPnl) : DASH, color: haveNet ? (netPnl >= 0 ? C.green : C.red) : C.dim, sub: 'realized + unreal' },
      { label: 'SUPPRESSED', value: isNum(L.ordersCostSuppressed) ? String(L.ordersCostSuppressed) : DASH, color: C.amber, sub: 'cost-gated signals' }
    ];
    var tilesEl = $('stat-tiles');
    if (tilesEl) {
      tilesEl.innerHTML = statTiles.map(function (t) {
        return '<div style="background:#070d17;border:1px solid #16233a;border-radius:9px;padding:11px 12px;display:flex;flex-direction:column;gap:5px">' +
          '<span style="font-size:9.5px;color:#6b7c95;font-weight:600;letter-spacing:.05em">' + esc(t.label) + '</span>' +
          '<span style="font-family:\'JetBrains Mono\',monospace;font-size:18px;font-weight:600;color:' + t.color + ';line-height:1">' + esc(t.value) + '</span>' +
          '<span style="font-size:9.5px;color:#4f5e75">' + esc(t.sub) + '</span></div>';
      }).join('');
    }

    // positions
    var posBody = $('positions-body');
    if (posBody) {
      if (!L.positions || !L.positions.length) {
        posBody.innerHTML = emptyRow(5, 'no open positions');
      } else {
        posBody.innerHTML = L.positions.map(function (p) {
          var long = p.quantity >= 0;
          var priceDec = p.lastMarkPrice < 10 ? 4 : 2;
          var sideColor = long ? C.green : C.red;
          var sideBg = long ? '#0c1f1a' : '#1f0d10';
          var pnlColor = p.unrealizedPnL >= 0 ? C.green : C.red;
          return '<tr style="border-top:1px solid #111d2c;font-size:12.5px">' +
            '<td style="text-align:left;padding:10px 14px"><span style="display:inline-flex;align-items:center;gap:8px">' +
            '<span style="font-size:9px;font-weight:700;letter-spacing:.05em;color:' + sideColor + ';background:' + sideBg + ';padding:2px 5px;border-radius:4px;font-family:\'Public Sans\',sans-serif">' + (long ? 'LONG' : 'SHORT') + '</span>' +
            '<span style="color:#dbe4f0;font-weight:500">' + esc(p.instrument) + '</span></span></td>' +
            '<td style="text-align:right;padding:10px 10px;color:' + sideColor + '">' + (long ? '+' : '') + nf(p.quantity, 4) + '</td>' +
            '<td style="text-align:right;padding:10px 10px;color:#9fb0c8">' + nf(p.averageCost, priceDec) + '</td>' +
            '<td style="text-align:right;padding:10px 10px;color:#dbe4f0">' + nf(p.lastMarkPrice, priceDec) + '</td>' +
            '<td style="text-align:right;padding:10px 14px;color:' + pnlColor + ';font-weight:600">' + signedUsd(p.unrealizedPnL, 3) + '</td></tr>';
        }).join('');
      }
    }

    // fills
    var fillsEl = $('fills-feed');
    if (fillsEl) {
      if (!L.fills || !L.fills.length) {
        fillsEl.innerHTML = emptyBlock('no fills yet');
      } else {
        fillsEl.innerHTML = L.fills.map(function (f) {
          var buy = f.side === 'BUY';
          var pxDec = f.fillPrice < 10 ? 4 : 2;
          var qd = f.quantity < 1 ? 4 : (f.quantity < 100 ? 2 : 0);
          var sideColor = buy ? C.green : C.red;
          var sideBg = buy ? '#0c1f1a' : '#1f0d10';
          return '<div style="display:flex;align-items:center;justify-content:space-between;padding:7px 14px;border-top:1px solid #0f1a28;font-family:\'JetBrains Mono\',monospace;font-size:11.5px">' +
            '<span style="display:flex;align-items:center;gap:8px">' +
            '<span style="color:#5b6a82;font-size:10px">' + esc(f.timestamp) + '</span>' +
            '<span style="font-size:8.5px;font-weight:700;letter-spacing:.04em;color:' + sideColor + ';background:' + sideBg + ';padding:2px 5px;border-radius:4px;font-family:\'Public Sans\',sans-serif">' + esc(f.side) + '</span>' +
            '<span style="color:#c4d0e0">' + esc(f.instrument) + '</span></span>' +
            '<span style="display:flex;gap:10px"><span style="color:#7787a0">' + nf(f.quantity, qd) + '</span><span style="color:#dbe4f0">' + nf(f.fillPrice, pxDec) + '</span></span></div>';
        }).join('');
      }
    }

    // equity curve
    applyEquity(L.equitySeries);
  }

  function applyEquity(es) {
    var ep = $('equity-path'), ea = $('equity-area'), dot = $('equity-dot');
    if (!Array.isArray(es) || es.length < 2) {
      if (ep) ep.setAttribute('d', '');
      if (ea) ea.setAttribute('d', '');
      if (dot) { dot.setAttribute('cx', '-10'); dot.setAttribute('cy', '-10'); }
      setText('equity-start', DASH); setText('equity-end', DASH);
      return;
    }
    var W = 680, H = 168, pT = 14, pB = 18;
    var mn = Math.min.apply(null, es), mx = Math.max.apply(null, es), span = (mx - mn) || 1;
    var xs = function (i) { return (i / (es.length - 1)) * (W - 4) + 2; };
    var ys = function (v) { return pT + (1 - (v - mn) / span) * (H - pT - pB); };
    var line = '';
    es.forEach(function (v, i) { line += (i === 0 ? 'M ' : 'L ') + xs(i).toFixed(1) + ' ' + ys(v).toFixed(1) + ' '; });
    var area = line + 'L ' + xs(es.length - 1).toFixed(1) + ' ' + H + ' L ' + xs(0).toFixed(1) + ' ' + H + ' Z';
    if (ep) ep.setAttribute('d', line.trim());
    if (ea) ea.setAttribute('d', area);
    if (dot) { dot.setAttribute('cx', xs(es.length - 1).toFixed(1)); dot.setAttribute('cy', ys(es[es.length - 1]).toFixed(1)); }
    setText('equity-start', usd(es[0]));
    setText('equity-end', usd(es[es.length - 1]));
  }

  function applyAlpha(A) {
    var rows = $('alpha-rows');
    if (!A) {
      // honest empty: no active strategy / huginn unreachable
      setText('composite-score', DASH); setColor('composite-score', C.dim);
      setText('threshold', DASH);
      setText('blend', DASH); setText('alpha-count', DASH);
      var ci = $('comp-implies'); if (ci) { ci.textContent = DASH; ci.style.color = C.dim; ci.style.background = '#11192a'; }
      if (rows) rows.innerHTML = emptyBlock('no active strategy / huginn unreachable');
      // neutral gauge
      var cx0 = 140, cy0 = 132, R0 = 110;
      setAttr('gauge-track', 'd', arc(cx0, cy0, R0, -90, 90));
      setAttr('gauge-active', 'd', '');
      var ga0 = $('gauge-needle'); if (ga0) { ga0.setAttribute('x2', '140'); ga0.setAttribute('y2', '132'); }
      return;
    }

    var cx = 140, cy = 132, R = 110;
    var score = A.compositeScore, thr = A.entryThreshold;
    var haveScore = isNum(score), haveThr = isNum(thr);
    setAttr('gauge-track', 'd', arc(cx, cy, R, -90, 90));
    if (haveThr) {
      setAttr('gauge-band-pos', 'd', arc(cx, cy, R, thr * 90, 90));
      setAttr('gauge-band-neg', 'd', arc(cx, cy, R, -90, -thr * 90));
    }
    var compositeColor = !haveScore ? C.dim : (haveThr && Math.abs(score) < thr ? C.amber : (score > 0 ? C.green : C.red));
    if (haveScore) {
      setAttr('gauge-active', 'd', score >= 0 ? arc(cx, cy, R, 0, score * 90) : arc(cx, cy, R, score * 90, 0));
      var ga = $('gauge-active'); if (ga) ga.setAttribute('stroke', compositeColor);
      var needle = polar(cx, cy, R - 16, score * 90);
      var nEl = $('gauge-needle');
      if (nEl) { nEl.setAttribute('x2', needle.x.toFixed(1)); nEl.setAttribute('y2', needle.y.toFixed(1)); nEl.setAttribute('stroke', compositeColor); }
      var hub = $('gauge-hub'); if (hub) hub.setAttribute('fill', compositeColor);
      setText('composite-score', (score >= 0 ? '+' : '') + nf(score, 2)); setColor('composite-score', compositeColor);
    } else {
      setAttr('gauge-active', 'd', '');
      setText('composite-score', DASH); setColor('composite-score', C.dim);
    }
    setText('threshold', haveThr ? nf(thr, 2) : DASH);

    if (haveScore && haveThr) {
      var implies = Math.abs(score) < thr ? 'NO TRADE' : (score > 0 ? 'IMPLIES LONG' : 'IMPLIES SHORT');
      var impliesColor = Math.abs(score) < thr ? C.amber : (score > 0 ? C.green : C.red);
      var impliesBg = Math.abs(score) < thr ? '#1c1606' : (score > 0 ? '#0c1f1a' : '#1f0d10');
      var ci2 = $('comp-implies');
      if (ci2) { ci2.textContent = implies; ci2.style.color = impliesColor; ci2.style.background = impliesBg; }
    } else {
      var ci3 = $('comp-implies'); if (ci3) { ci3.textContent = DASH; ci3.style.color = C.dim; ci3.style.background = '#11192a'; }
    }
    setText('blend', A.blend || DASH);
    setText('alpha-count', Array.isArray(A.alphas) ? A.alphas.length : DASH);

    if (rows) {
      if (!A.alphas || !A.alphas.length) {
        rows.innerHTML = emptyBlock('no alphas registered');
      } else {
        rows.innerHTML = A.alphas.map(function (a) {
          var hasContrib = isNum(a.contribution);
          var cv = hasContrib ? a.contribution : 0;
          var frac = Math.min(Math.abs(cv) / 1, 1) * 50;
          var contribColor = !hasContrib ? C.dim : (cv > 0 ? C.green : (cv < 0 ? C.red : C.dim));
          var contribLeft = cv >= 0 ? 50 : 50 - frac;
          var contribWidth = hasContrib ? Math.max(frac, cv === 0 ? 0 : 1.2) : 0;
          var hasConf = isNum(a.confidence);
          var confColor = !hasConf ? '#46566e' : (a.confidence >= 0.65 ? C.blue : (a.confidence >= 0.45 ? C.amber : '#46566e'));
          var hasIC = Array.isArray(a.ic) && a.ic.length >= 2;
          var icSvg;
          if (hasIC) {
            var icMn = Math.min.apply(null, a.ic.concat([-0.005])), icMx = Math.max.apply(null, a.ic.concat([0.005])), icSpan = (icMx - icMn) || 1;
            var icPath = '';
            a.ic.forEach(function (v, i) { var x = (i / (a.ic.length - 1)) * 60; var y = 16 - ((v - icMn) / icSpan) * 14; icPath += (i === 0 ? 'M ' : 'L ') + x.toFixed(1) + ' ' + y.toFixed(1) + ' '; });
            var icLast = a.ic[a.ic.length - 1];
            var icColor = icLast >= 0 ? C.green : C.red;
            icSvg = '<svg viewBox="0 0 60 18" width="60" height="18" preserveAspectRatio="none" style="vertical-align:middle"><line x1="0" y1="9" x2="60" y2="9" stroke="#1a283c" stroke-width="1"/><path d="' + icPath.trim() + '" fill="none" stroke="' + icColor + '" stroke-width="1.4" stroke-linejoin="round"/></svg>';
          } else {
            icSvg = '<span style="font-family:\'JetBrains Mono\',monospace;font-size:11px;color:#46566e">' + DASH + '</span>';
          }
          var weightStr = isNum(a.weight) ? nf(a.weight, 2) : DASH;
          var contribStr = hasContrib ? ((cv >= 0 ? '+' : '') + nf(cv, 2)) : DASH;
          var confInner = hasConf ? ('<span style="position:absolute;left:0;top:0;bottom:0;width:' + (a.confidence * 100) + '%;background:' + confColor + '"></span>') : '';
          var confLabel = hasConf ? (Math.round(a.confidence * 100) + '%') : DASH;
          return '<div style="display:flex;align-items:center;padding:10px 14px;border-top:1px solid #111d2c">' +
            '<span style="flex:1.3;display:flex;flex-direction:column;gap:1px"><span style="font-size:12.5px;color:#dbe4f0;font-weight:500;font-family:\'JetBrains Mono\',monospace">' + esc(a.name) + '</span></span>' +
            '<span style="width:54px;text-align:right;font-family:\'JetBrains Mono\',monospace;font-size:11.5px;color:#9fb0c8">' + weightStr + '</span>' +
            '<span style="flex:1.6;padding:0 14px"><span style="position:relative;display:block;height:14px;background:#0c1726;border-radius:3px">' +
            '<span style="position:absolute;top:0;bottom:0;left:50%;width:1px;background:#2b3c55"></span>' +
            '<span style="position:absolute;top:2px;bottom:2px;left:' + contribLeft + '%;width:' + contribWidth + '%;background:' + contribColor + ';border-radius:2px"></span></span>' +
            '<span style="display:block;text-align:center;font-size:10px;font-family:\'JetBrains Mono\',monospace;color:' + contribColor + ';margin-top:3px">' + contribStr + '</span></span>' +
            '<span style="width:62px;text-align:right"><span style="display:inline-block;width:38px;height:5px;background:#0c1726;border-radius:3px;vertical-align:middle;position:relative;overflow:hidden">' + confInner + '</span>' +
            '<span style="font-size:10.5px;font-family:\'JetBrains Mono\',monospace;color:#9fb0c8;margin-left:5px">' + confLabel + '</span></span>' +
            '<span style="width:66px;text-align:right">' + icSvg + '</span></div>';
        }).join('');
      }
    }
  }

  function applyPortfolio(P) {
    var weightsEl = $('weights'), factorsEl = $('factors'), segsEl = $('donut-segs'), legEl = $('donut-legend');
    if (!P || P.available === false) {
      if (weightsEl) weightsEl.innerHTML = emptyBlock(P && P.available === false ? 'no portfolio run yet' : 'connecting to odin…');
      setText('net-exposure', DASH);
      if (factorsEl) factorsEl.innerHTML = emptyBlock('—');
      if (segsEl) segsEl.innerHTML = '';
      if (legEl) legEl.innerHTML = emptyBlock('—');
      return;
    }

    // weights
    var wEntries = Object.keys(P.weights || {}).map(function (k) { return [k, P.weights[k]]; });
    if (weightsEl) {
      if (!wEntries.length) {
        weightsEl.innerHTML = emptyBlock('no weights computed');
      } else {
        var wMax = Math.max.apply(null, wEntries.map(function (e) { return Math.abs(e[1]); })) * 1.08 || 1;
        weightsEl.innerHTML = wEntries.map(function (e) {
          var k = e[0], v = e[1];
          var frac = (Math.abs(v) / wMax) * 50;
          var color = v >= 0 ? C.green : C.red;
          var left = v >= 0 ? 50 : 50 - frac;
          var width = Math.max(frac, 0.8);
          return '<div style="display:flex;align-items:center;gap:10px">' +
            '<span style="width:44px;font-size:11px;font-weight:600;color:#c4d0e0;font-family:\'JetBrains Mono\',monospace">' + esc(k) + '</span>' +
            '<span style="position:relative;flex:1;height:18px;background:#0c1726;border-radius:3px"><span style="position:absolute;top:0;bottom:0;left:50%;width:1px;background:#2b3c55"></span>' +
            '<span style="position:absolute;top:3px;bottom:3px;left:' + left + '%;width:' + width + '%;background:' + color + ';border-radius:2px"></span></span>' +
            '<span style="width:54px;text-align:right;font-size:11.5px;font-family:\'JetBrains Mono\',monospace;color:' + color + '">' + (v >= 0 ? '+' : '') + nf(v, 2) + '</span></div>';
        }).join('');
      }
    }
    var netExp = wEntries.reduce(function (s, e) { return s + e[1]; }, 0);
    setText('net-exposure', wEntries.length ? ((netExp >= 0 ? '+' : '') + nf(netExp, 2) + ' (neutral)') : DASH);

    // factors
    var fEntries = Object.keys(P.factorExposures || {}).map(function (k) { return [k, P.factorExposures[k]]; });
    if (factorsEl) {
      if (!fEntries.length) {
        factorsEl.innerHTML = emptyBlock('no factor exposures');
      } else {
        var fMax = Math.max.apply(null, fEntries.map(function (e) { return Math.abs(e[1]); })) * 1.15 || 1;
        factorsEl.innerHTML = fEntries.map(function (e) {
          var k = e[0], v = e[1];
          var frac = (Math.abs(v) / fMax) * 50;
          var color = v >= 0 ? C.blue : C.amber;
          var left = v >= 0 ? 50 : 50 - frac;
          var width = Math.max(frac, 0.8);
          return '<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 14px;border-bottom:1px solid #0f1a28">' +
            '<span style="font-size:11.5px;color:#c4d0e0">' + esc(k) + '</span>' +
            '<span style="display:flex;align-items:center;gap:8px"><span style="position:relative;width:50px;height:5px;background:#0c1726;border-radius:3px">' +
            '<span style="position:absolute;top:0;bottom:0;left:50%;width:1px;background:#2b3c55"></span>' +
            '<span style="position:absolute;top:0;bottom:0;left:' + left + '%;width:' + width + '%;background:' + color + ';border-radius:2px"></span></span>' +
            '<span style="width:42px;text-align:right;font-size:11px;font-family:\'JetBrains Mono\',monospace;color:' + color + '">' + (v >= 0 ? '+' : '') + nf(v, 2) + '</span></span></div>';
        }).join('');
      }
    }

    // risk donut
    var rEntries = Object.keys(P.riskContributions || {}).map(function (k) { return [k, P.riskContributions[k]]; });
    var rTotal = rEntries.reduce(function (s, e) { return s + e[1]; }, 0) || 1;
    var palette = ['#5b8def', '#2bd4a4', '#f3b13c', '#a779f0', '#5f6f88'];
    var circ = 2 * Math.PI * 54;
    var cum = 0, segs = [], legend = [];
    rEntries.forEach(function (e, i) {
      var frac = e[1] / rTotal, len = frac * circ;
      segs.push({ color: palette[i % palette.length], dash: len.toFixed(1) + ' ' + (circ - len).toFixed(1), offset: (-cum * circ).toFixed(1) });
      legend.push({ label: e[0], color: palette[i % palette.length], pctStr: Math.round(frac * 100) + '%' });
      cum += frac;
    });
    if (segsEl) {
      segsEl.innerHTML = segs.map(function (s) {
        return '<circle cx="70" cy="70" r="54" fill="none" stroke="' + s.color + '" stroke-width="18" stroke-dasharray="' + s.dash + '" stroke-dashoffset="' + s.offset + '"/>';
      }).join('');
    }
    if (legEl) {
      legEl.innerHTML = legend.length ? legend.map(function (lg) {
        return '<div style="display:flex;align-items:center;gap:7px;font-size:11px">' +
          '<span style="width:8px;height:8px;border-radius:2px;background:' + lg.color + '"></span>' +
          '<span style="color:#c4d0e0;flex:1;font-family:\'JetBrains Mono\',monospace">' + esc(lg.label) + '</span>' +
          '<span style="color:#7787a0;font-family:\'JetBrains Mono\',monospace">' + lg.pctStr + '</span></div>';
      }).join('') : emptyBlock('no risk contributions');
    }
  }

  function applyValidation(V) {
    var foldsBody = $('folds-body');
    if (!V || V.available === false) {
      if (foldsBody) foldsBody.innerHTML = emptyRow(5, V && V.available === false ? 'no walk-forward run yet' : 'connecting to huginn…');
      setText('pbo', DASH);
      var marker0 = $('pbo-marker'); if (marker0) marker0.style.left = '0%';
      setText('dsharpe', DASH); setText('oos-profitable', DASH); setText('total-oos', DASH);
      return;
    }
    if (foldsBody) {
      foldsBody.innerHTML = (V.folds && V.folds.length) ? V.folds.map(function (f) {
        var isColor = f.isPnL >= 0 ? C.green : C.red;
        var oosColor = f.oosPnL >= 0 ? C.green : C.red;
        return '<tr style="border-top:1px solid #111d2c;font-size:12px">' +
          '<td style="text-align:left;padding:9px 14px;color:#c4d0e0">#' + f.fold + '</td>' +
          '<td style="text-align:right;padding:9px 8px;color:#7787a0">' + f.train + '</td>' +
          '<td style="text-align:right;padding:9px 8px;color:#7787a0">' + f.test + '</td>' +
          '<td style="text-align:right;padding:9px 8px;color:' + isColor + '">' + signedUsd(f.isPnL, 1) + '</td>' +
          '<td style="text-align:right;padding:9px 14px;color:' + oosColor + ';font-weight:600">' + signedUsd(f.oosPnL, 1) + '</td></tr>';
      }).join('') : emptyRow(5, 'no folds');
    }
    setText('pbo', isNum(V.pbo) ? nf(V.pbo, 2) : DASH);
    var marker = $('pbo-marker'); if (marker) marker.style.left = (isNum(V.pbo) ? V.pbo * 100 : 0) + '%';
    setText('dsharpe', isNum(V.deflatedSharpe) ? nf(V.deflatedSharpe, 2) : 'n/a');
    // huginn returns oosFoldsProfitable as a raw integer; the design shows it as
    // "N/total". Render the fraction when we have both numbers, else pass the
    // value through verbatim (it may already be a "N/total" string).
    var oosProf = DASH;
    if (isNum(V.oosFoldsProfitable) && V.folds && V.folds.length) {
      oosProf = V.oosFoldsProfitable + '/' + V.folds.length;
    } else if (V.oosFoldsProfitable != null) {
      oosProf = V.oosFoldsProfitable;
    }
    setText('oos-profitable', oosProf);
    setText('total-oos', isNum(V.totalOOSPnL) ? signedUsd(V.totalOOSPnL, 1) : DASH);
  }

  function applyServices(services) {
    var svcColor = { up: C.green, degraded: C.amber, down: C.red };
    var svcGlow = { up: '#2bd4a455', degraded: '#f3b13c55', down: '#ff626255' };
    var list = services || [];
    // Always render dots: unknown → dim.
    ['muninn', 'huginn', 'sleipnir', 'odin', 'redpanda'].forEach(function (name) {
      var dot = $('svc-' + name);
      if (!dot) return;
      var found = null;
      for (var i = 0; i < list.length; i++) { if (list[i].name === name) { found = list[i]; break; } }
      var status = found ? found.status : null;
      var col = status ? (svcColor[status] || C.dim) : C.dim;
      dot.style.background = col;
      dot.style.boxShadow = '0 0 6px ' + (status ? (svcGlow[status] || 'transparent') : 'transparent');
      var wrap = dot.parentElement; if (wrap) wrap.title = name + ' — ' + (status || 'unknown');
    });
  }

  // ---- (6) Research gateway --------------------------------------------
  function runStatusColor(status) {
    return status === 'done' ? C.green : (status === 'error' ? C.red : (status === 'running' ? C.blue : C.dim));
  }
  function applyResearch(R) {
    var recent = $('rg-recent'), foldsBody = $('rg-folds-body'), rstatus = $('rg-result-status');
    if (!R) {
      if (recent) recent.innerHTML = emptyBlock('connecting to research…');
      if (foldsBody) foldsBody.innerHTML = emptyRow(5, 'no walk-forward run yet');
      setText('rg-result-status', DASH); setColor('rg-result-status', C.dim);
      setText('rg-oos-profitable', DASH); setText('rg-total-oos', DASH);
      setText('rg-pbo', DASH); setText('rg-dsharpe', DASH);
      return;
    }
    // recent runs list
    var runs = R.runs || [];
    if (recent) {
      recent.innerHTML = runs.length ? runs.map(function (r) {
        var col = runStatusColor(r.status);
        var when = fmtTime(r.submittedAt);
        return '<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 14px;border-top:1px solid #0f1a28;font-family:\'JetBrains Mono\',monospace;font-size:11px">' +
          '<span style="display:flex;align-items:center;gap:8px">' +
          '<span style="width:6px;height:6px;border-radius:50%;background:' + col + '"></span>' +
          '<span style="color:#c4d0e0">' + esc(r.strategy || '—') + '</span>' +
          '<span style="color:#4f5e75;font-size:10px">' + esc(when) + '</span></span>' +
          '<span style="color:' + col + ';font-weight:600;letter-spacing:.04em">' + esc(String(r.status || '—').toUpperCase()) + '</span></div>';
      }).join('') : emptyBlock('no walk-forward run yet');
    }
    // most recent result
    var det = R.latest;
    if (!det) {
      if (foldsBody) foldsBody.innerHTML = emptyRow(5, 'no walk-forward run yet');
      setText('rg-result-status', DASH); setColor('rg-result-status', C.dim);
      setText('rg-oos-profitable', DASH); setText('rg-total-oos', DASH);
      setText('rg-pbo', DASH); setText('rg-dsharpe', DASH);
      return;
    }
    var sc = runStatusColor(det.status);
    var statusLabel = String(det.status || '—').toUpperCase();
    if (det.strategy) statusLabel += ' · ' + det.strategy;
    setText('rg-result-status', statusLabel); setColor('rg-result-status', sc);
    var res = det.result;
    if (det.status === 'running') {
      if (foldsBody) foldsBody.innerHTML = emptyRow(5, 'running walk-forward…');
      setText('rg-oos-profitable', DASH); setText('rg-total-oos', DASH);
      setText('rg-pbo', DASH); setText('rg-dsharpe', DASH);
      return;
    }
    if (det.status === 'error') {
      if (foldsBody) foldsBody.innerHTML = emptyRow(5, det.error ? ('error: ' + det.error) : 'run errored');
      setText('rg-oos-profitable', DASH); setText('rg-total-oos', DASH);
      setText('rg-pbo', DASH); setText('rg-dsharpe', DASH);
      return;
    }
    if (!res) {
      if (foldsBody) foldsBody.innerHTML = emptyRow(5, 'no result');
      setText('rg-oos-profitable', DASH); setText('rg-total-oos', DASH);
      setText('rg-pbo', DASH); setText('rg-dsharpe', DASH);
      return;
    }
    var folds = Array.isArray(res.folds) ? res.folds : [];
    if (foldsBody) {
      foldsBody.innerHTML = folds.length ? folds.map(function (f) {
        var pnl = f.test_pnl;
        var pnlColor = isNum(pnl) ? (pnl >= 0 ? C.green : C.red) : C.dim;
        var sh = f.sharpe;
        var shColor = isNum(sh) ? (sh >= 0 ? C.green : C.red) : C.dim;
        return '<tr style="border-top:1px solid #111d2c;font-size:12px">' +
          '<td style="text-align:left;padding:9px 14px;color:#c4d0e0">#' + esc(String(f.fold)) + '</td>' +
          '<td style="text-align:right;padding:9px 8px;color:#9fb0c8">' + (isNum(f.best_threshold) ? nf(f.best_threshold, 2) : DASH) + '</td>' +
          '<td style="text-align:right;padding:9px 8px;color:' + pnlColor + '">' + (isNum(pnl) ? signedUsd(pnl, 1) : DASH) + '</td>' +
          '<td style="text-align:right;padding:9px 8px;color:#7787a0">' + (isNum(f.test_fills) ? f.test_fills : DASH) + '</td>' +
          '<td style="text-align:right;padding:9px 14px;color:' + shColor + ';font-weight:600">' + (isNum(sh) ? nf(sh, 2) : DASH) + '</td></tr>';
      }).join('') : emptyRow(5, 'no folds');
    }
    var oosProf = DASH;
    if (isNum(res.oosFoldsProfitable)) {
      oosProf = res.oosFoldsProfitable + (folds.length ? '/' + folds.length : '');
    } else if (res.oosFoldsProfitable != null) {
      oosProf = res.oosFoldsProfitable;
    }
    setText('rg-oos-profitable', oosProf);
    setText('rg-total-oos', isNum(res.totalOOSPnL) ? signedUsd(res.totalOOSPnL, 1) : DASH);
    setText('rg-pbo', isNum(res.pbo) ? nf(res.pbo, 2) : 'n/a');
    setText('rg-dsharpe', isNum(res.deflatedSharpe) ? nf(res.deflatedSharpe, 2) : 'n/a');
  }

  // ---- (7) Feature store · Mimir ---------------------------------------
  function applyFeatures(F) {
    var srcBody = $('fs-sources-body'), featBody = $('fs-features-body');
    if (!F) {
      if (srcBody) srcBody.innerHTML = emptyRow(4, 'connecting to mimir…');
      if (featBody) featBody.innerHTML = emptyRow(3, 'connecting to mimir…');
      setText('fs-basis', DASH);
      return;
    }
    // freshness
    var srcs = F.sources || [];
    if (srcBody) {
      srcBody.innerHTML = srcs.length ? srcs.map(function (s) {
        var lag = s.max_ingest_lag_secs;
        var lagColor = !isNum(lag) ? C.dim : (lag > 60 ? C.amber : C.mut);
        return '<tr style="border-top:1px solid #111d2c;font-size:12px">' +
          '<td style="text-align:left;padding:9px 14px;color:#dbe4f0">' + esc(s.instrument || DASH) + '</td>' +
          '<td style="text-align:right;padding:9px 8px;color:#7787a0">' + (isNum(s.count) ? nf(s.count, 0) : DASH) + '</td>' +
          '<td style="text-align:right;padding:9px 8px;color:#9fb0c8">' + fmtTime(s.last_event_time) + '</td>' +
          '<td style="text-align:right;padding:9px 14px;color:' + lagColor + '">' + fmtLag(lag) + '</td></tr>';
      }).join('') : emptyRow(4, 'no sources registered');
    }
    // as-of features (event_time vs ingest_time → no-lookahead visible)
    var feats = F.features || [];
    if (featBody) {
      featBody.innerHTML = feats.length ? feats.map(function (r) {
        return '<tr style="border-top:1px solid #111d2c;font-size:12px">' +
          '<td style="text-align:left;padding:9px 14px;color:#dbe4f0">' + esc(r.instrument || DASH) + '</td>' +
          '<td style="text-align:right;padding:9px 8px;color:#9fb0c8">' + fmtTime(r.event_time) + '</td>' +
          '<td style="text-align:right;padding:9px 14px;color:#7787a0">' + fmtTime(r.ingest_time) + '</td></tr>';
      }).join('') : emptyRow(3, 'no features as of this instant');
    }
    setText('fs-basis', F.basis || 'point-in-time (event_time ≤ as_of AND ingest_time ≤ as_of)');
  }

  // ---- (8) Execution TCA · Forseti -------------------------------------
  function applyTCA(T) {
    var tilesEls = ['tca-slippage', 'tca-fees', 'tca-makertaker', 'tca-shortfall'];
    var byBody = $('tca-byinstrument-body');
    if (!T || T.available === false) {
      tilesEls.forEach(function (k) { setText(k, DASH); setColor(k, C.dim); });
      if (byBody) byBody.innerHTML = emptyRow(5, T && T.available === false ? 'no fills yet' : 'connecting to forseti…');
      setText('tca-basis', (T && T.basis) || (T && T.available === false ? 'fees + reported-slippage only' : DASH));
      return;
    }
    var o = T.overall || {};
    // avg slippage bps — null is an HONEST empty state (no arrival benchmark).
    if (isNum(o.avgSlippageBps)) {
      setText('tca-slippage', nf(o.avgSlippageBps, 2)); setColor('tca-slippage', o.avgSlippageBps <= 0 ? C.green : C.red);
    } else {
      setText('tca-slippage', 'n/a'); setColor('tca-slippage', C.dim);
    }
    setText('tca-fees', isNum(o.totalFees) ? usd(o.totalFees) : DASH); setColor('tca-fees', C.mut);
    setText('tca-makertaker', isNum(o.makerTakerRatio) ? nf(o.makerTakerRatio, 2) : DASH); setColor('tca-makertaker', C.mut);
    setText('tca-shortfall', isNum(o.totalImplementationShortfall) ? usd(o.totalImplementationShortfall) : DASH); setColor('tca-shortfall', C.mut);
    // basis: when slippage is unavailable, state the honest basis explicitly.
    setText('tca-basis', T.basis || (isNum(o.avgSlippageBps) ? '' : 'fees + reported-slippage only'));

    var entries = Object.keys(T.byInstrument || {}).map(function (k) { return [k, T.byInstrument[k]]; });
    if (byBody) {
      byBody.innerHTML = entries.length ? entries.map(function (e) {
        var k = e[0], m = e[1] || {};
        var slip = m.avgSlippageBps;
        var slipColor = !isNum(slip) ? C.dim : (slip <= 0 ? C.green : C.red);
        return '<tr style="border-top:1px solid #111d2c;font-size:12px">' +
          '<td style="text-align:left;padding:9px 14px;color:#dbe4f0">' + esc(k) + '</td>' +
          '<td style="text-align:right;padding:9px 8px;color:#7787a0">' + (isNum(m.totalFills) ? m.totalFills : DASH) + '</td>' +
          '<td style="text-align:right;padding:9px 8px;color:' + slipColor + '">' + (isNum(slip) ? nf(slip, 2) : 'n/a') + '</td>' +
          '<td style="text-align:right;padding:9px 8px;color:#9fb0c8">' + (isNum(m.totalFees) ? usd(m.totalFees) : DASH) + '</td>' +
          '<td style="text-align:right;padding:9px 14px;color:#9fb0c8">' + (isNum(m.totalNotional) ? usd(m.totalNotional, 0) : DASH) + '</td></tr>';
      }).join('') : emptyRow(5, 'no fills yet');
    }
  }

  function applyFooter(sources, demo) {
    var badge = $('status-badge'), dotF = $('status-dot'), textF = $('status-text');
    if (!badge || !dotF || !textF) return;
    if (demo) {
      textF.textContent = 'DEMO PREVIEW · ?demo=1 (not live)';
      badge.style.color = '#7e6a3a'; badge.style.background = '#211a0c'; badge.style.borderColor = '#3d3214';
      dotF.style.background = C.amber;
      return;
    }
    var n = sources ? sources.size : 0;
    if (n > 0) {
      textF.textContent = 'LIVE · ' + n + ' source' + (n === 1 ? '' : 's');
      badge.style.color = C.green; badge.style.background = '#0c1f1a'; badge.style.borderColor = '#1f6e54';
      dotF.style.background = C.green;
    } else {
      textF.textContent = 'BACKEND UNREACHABLE · no live sources';
      badge.style.color = '#ff8b8b'; badge.style.background = '#1f0d10'; badge.style.borderColor = '#6e2a30';
      dotF.style.background = C.red;
    }
  }

  function emptyRow(cols, msg) {
    return '<tr><td colspan="' + cols + '" style="padding:16px 14px;text-align:center;color:#4f5e75;font-size:11.5px;font-style:italic">' + esc(msg) + '</td></tr>';
  }
  function emptyBlock(msg) {
    return '<div style="padding:16px 14px;text-align:center;color:#4f5e75;font-size:11.5px;font-style:italic">' + esc(msg) + '</div>';
  }

  function apply(res) {
    var d = res.data || {};
    applyLive(d.live);
    applyAlpha(d.alpha);
    applyPortfolio(d.portfolio);
    applyValidation(d.validation);
    applyResearch(d.research);
    applyFeatures(d.features);
    applyTCA(d.tca);
    applyServices(d.services);
    // Drive the RUNNING/HALTED indicator from authoritative live breaker state
    // (huginn /api/snapshot `halted` + `halt_reason`), not the optimistic echo.
    // Only override when the snapshot actually landed (d.live present); a failed
    // poll leaves the last known state in place rather than flipping to RUNNING.
    // In ?demo=1 mode the button drives `halted` locally — don't clobber it.
    if (!res.demo && d.live) {
      halted = d.live.halted === true;
      liveHaltReason = d.live.haltReason || '';
    }
    applyHaltVisual();
    applyFooter(res.sources, res.demo);
  }

  function setAttr(key, attr, val) { var el = $(key); if (el) el.setAttribute(attr, val); }

  // ====================================================================
  // HALT / RESUME — wired to huginn breaker via the same-origin proxy.
  // ====================================================================
  function applyHaltVisual() {
    var btn = $('halt-btn'), label = $('halt-btn-label'), status = $('halt-status');
    var banner = $('halt-banner'), icon2 = $('halt-icon-second'), reason = $('halt-reason');
    if (label) label.textContent = halted ? 'RESUME' : 'HALT';
    if (status) { status.textContent = halted ? 'HALTED' : 'RUNNING'; status.style.color = halted ? C.red : C.green; }
    if (btn) {
      btn.style.background = halted ? '#0c1f1a' : '#1f0d10';
      btn.style.borderColor = halted ? '#1f6e54' : '#6e2a30';
      btn.style.color = halted ? C.green : '#ff8b8b';
    }
    if (icon2) icon2.style.display = halted ? 'none' : '';
    if (banner) banner.style.display = halted ? 'flex' : 'none';
    // Surface halt_reason from the live snapshot when present.
    if (reason) reason.textContent = (halted && liveHaltReason) ? ('· ' + liveHaltReason) : '';
  }

  // Breaker-auth notice — shown on-screen when a breaker POST is rejected.
  function showHaltNotice(msg) {
    var n = $('halt-notice');
    if (!n) return;
    n.textContent = msg;
    n.style.display = msg ? 'flex' : 'none';
  }

  function getToken() {
    if (window.NC_TOKEN) return window.NC_TOKEN;
    try { return localStorage.getItem('nc_token'); } catch (e) { return null; }
  }

  function onHaltClick() {
    if (DEMO_MODE) { halted = !halted; applyHaltVisual(); return; }
    var token = getToken();
    var target = !halted; // desired state after toggle
    var headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = 'Bearer ' + token;
    // POST same-origin; serve.py forwards to huginn /api/breaker/{trigger,reset}.
    fetchWithTimeout('/api/breaker', {
      method: 'POST', headers: headers, body: JSON.stringify({ halted: target })
    }).then(function (r) {
      if (r.ok) { halted = target; liveHaltReason = ''; showHaltNotice(''); applyHaltVisual(); }
      else if (r.status === 401 || r.status === 403) {
        showHaltNotice(token
          ? 'Breaker control rejected the token (' + r.status + '). Set a valid token via the TOKEN button.'
          : 'Breaker control requires a token (' + r.status + '). Click TOKEN to set one, then retry.');
        console.warn('[norse-console] breaker POST returned ' + r.status + (token ? '' : ' (no token set — control plane locked)'));
      } else {
        showHaltNotice('Breaker control returned ' + r.status + '.');
        console.warn('[norse-console] breaker POST returned ' + r.status);
      }
    }).catch(function (e) {
      showHaltNotice('Breaker control unreachable.');
      console.warn('[norse-console] breaker POST failed:', e && e.message);
    });
  }

  // Token entry — store/clear localStorage nc_token used as the breaker bearer token.
  function onTokenClick() {
    var current = '';
    try { current = localStorage.getItem('nc_token') || ''; } catch (e) {}
    var val = window.prompt('Breaker control token (stored in localStorage as nc_token; leave blank to clear):', current);
    if (val === null) return; // cancelled
    val = val.trim();
    try {
      if (val) { localStorage.setItem('nc_token', val); showHaltNotice('Token saved. The HALT/RESUME button is now authorized.'); }
      else { localStorage.removeItem('nc_token'); showHaltNotice('Token cleared.'); }
    } catch (e) {
      showHaltNotice('Could not access localStorage to store the token.');
    }
  }

  // ====================================================================
  // Research run submission — POST then poll the run by id until terminal.
  // ====================================================================
  var rgPolling = false;
  function onResearchRun() {
    if (rgPolling) return;
    var sel = $('rg-strategy');
    var strategy = sel ? sel.value : 'obi';
    var btn = $('rg-run-btn');
    if (DEMO_MODE) { setText('rg-run-state', 'demo mode — run disabled'); setColor('rg-run-state', C.amber); return; }
    rgPolling = true;
    if (btn) { btn.disabled = true; btn.style.opacity = '0.55'; btn.style.cursor = 'default'; }
    setText('rg-run-state', 'submitting ' + strategy + ' walk-forward…'); setColor('rg-run-state', C.blue);
    var body = JSON.stringify({ strategy: strategy, thresholds: [0.5, 0.6, 0.7, 0.8], folds: 4 });
    fetchWithTimeout('/api/research/runs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body
    }).then(function (r) {
      if (!r.ok) throw new Error('submit ' + r.status);
      return r.json();
    }).then(function (j) {
      var id = j && j.id;
      if (id == null) throw new Error('no run id');
      setText('rg-run-state', 'running ' + strategy + ' · ' + id); setColor('rg-run-state', C.blue);
      pollResearchRun(id, 0);
    }).catch(function (e) {
      setText('rg-run-state', 'run submit failed: ' + (e && e.message || 'error')); setColor('rg-run-state', C.red);
      finishResearchRun();
    });
  }
  function finishResearchRun() {
    rgPolling = false;
    var btn = $('rg-run-btn');
    if (btn) { btn.disabled = false; btn.style.opacity = '1'; btn.style.cursor = 'pointer'; }
  }
  function pollResearchRun(id, attempt) {
    if (attempt > 150) { // ~5 min ceiling at 2s
      setText('rg-run-state', 'still running — see recent runs'); setColor('rg-run-state', C.amber);
      finishResearchRun(); return;
    }
    getJSON('/api/research/runs/' + encodeURIComponent(id)).then(function (det) {
      if (det && det.status === 'running') {
        setTimeout(function () { pollResearchRun(id, attempt + 1); }, 2000);
        return;
      }
      // terminal: render immediately and let the next poll refresh the list.
      if (det && det.status === 'done') {
        var doneStrat = det.strategy || (det.request && det.request.strategy) || '';
        setText('rg-run-state', doneStrat ? ('done · ' + doneStrat) : 'done'); setColor('rg-run-state', C.green);
      }
      else if (det && det.status === 'error') { setText('rg-run-state', 'error: ' + (det.error || 'run failed')); setColor('rg-run-state', C.red); }
      else { setText('rg-run-state', String(det && det.status || 'finished')); setColor('rg-run-state', C.mut); }
      // Patch the latest-result panel directly so the operator sees it without
      // waiting for the next poll cadence.
      try { applyResearch({ runs: [{ id: id, status: det.status, strategy: det.strategy, submittedAt: det.submittedAt }], latest: det }); } catch (e) {}
      finishResearchRun();
    }).catch(function () {
      setTimeout(function () { pollResearchRun(id, attempt + 1); }, 2000);
    });
  }

  // ====================================================================
  // Bootstrap
  // ====================================================================
  function tick() {
    loadLive().then(function (res) {
      try { apply(res); }
      catch (e) { console.warn('[norse-console] apply error:', e && e.message); }
    }).catch(function () { /* never throw — keep last paint */ });
  }

  function neutralFirstPaint() {
    // Honest neutral placeholders before the first live response lands.
    setText('equity', DASH); setText('cash', DASH);
    setText('net-pnl', DASH); setText('net-pnl-pct', DASH);
    setText('open-pos', DASH); setText('fills-today', DASH); setText('suppressed', DASH);
    setText('composite-score', DASH); setText('threshold', DASH);
    setText('blend', DASH); setText('alpha-count', DASH);
    setText('pbo', DASH); setText('dsharpe', DASH);
    setText('oos-profitable', DASH); setText('total-oos', DASH);
    setText('equity-start', DASH); setText('equity-end', DASH);
    // new panels
    setText('rg-result-status', DASH);
    setText('rg-oos-profitable', DASH); setText('rg-total-oos', DASH);
    setText('rg-pbo', DASH); setText('rg-dsharpe', DASH);
    setText('fs-basis', DASH);
    setText('tca-slippage', DASH); setText('tca-fees', DASH);
    setText('tca-makertaker', DASH); setText('tca-shortfall', DASH);
    setText('tca-basis', DASH);
    var badge = $('status-text'); if (badge) badge.textContent = 'connecting…';
  }

  function init() {
    if (!DEMO_MODE) neutralFirstPaint();
    var btn = $('halt-btn');
    if (btn) btn.addEventListener('click', onHaltClick);
    var tokenBtn = $('token-btn');
    if (tokenBtn) tokenBtn.addEventListener('click', onTokenClick);
    // Research gateway: Run button kicks off a walk-forward + polls to terminal.
    var rgBtn = $('rg-run-btn');
    if (rgBtn) rgBtn.addEventListener('click', onResearchRun);
    // Feature store: default the as-of input to "now" and re-query on change.
    ensureAsOfDefault();
    var asOfEl = $('fs-asof');
    if (asOfEl) asOfEl.addEventListener('change', tick);
    tick();
    setInterval(tick, POLL_MS);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
