/* Norse Console — live data layer (vanilla JS, no dependencies)
 *
 * Renders over the static demo markup in index.html. Each data section
 * (live / alpha / portfolio / validation / services) is fetched independently;
 * any section whose endpoint is unreachable silently keeps its DEMO values.
 *
 * Config (set BEFORE this script loads, e.g. via an inline <script>):
 *   window.NC_BASE  — base URL for the services (default "http://localhost")
 *   window.NC_TOKEN — bearer token for the HALT/RESUME breaker endpoint
 *   localStorage 'nc_token' — alternative source for the bearer token
 */
(function () {
  'use strict';

  // ---- palette (matches the design) ----
  var C = { green: '#2bd4a4', red: '#ff6262', blue: '#5b8def', amber: '#f3b13c', mut: '#9fb0c8', dim: '#6b7c95' };

  var BASE = (window.NC_BASE || 'http://localhost').replace(/\/$/, '');
  var POLL_MS = 3000;
  var FETCH_TIMEOUT_MS = 2500;

  // ====================================================================
  // DEMO — exact object from defaultData() in norse-console.dc.html
  // ====================================================================
  var DEMO = {
    // huginn /api/snapshot + /metrics
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
        { instrument: 'SOL-USDT', side: 'BUY', quantity: 0.04, fillPrice: 142.18, timestamp: '14:55:31' },
        { instrument: 'ETH-USDT', side: 'BUY', quantity: 0.002, fillPrice: 1726.40, timestamp: '14:54:48' },
        { instrument: 'BTC-USDT', side: 'SELL', quantity: 0.0005, fillPrice: 64255.1, timestamp: '14:54:12' },
        { instrument: 'XRP-USDT', side: 'SELL', quantity: 2.0, fillPrice: 0.5123, timestamp: '14:53:40' },
        { instrument: 'DOGE-USDT', side: 'BUY', quantity: 30, fillPrice: 0.1287, timestamp: '14:53:02' },
        { instrument: 'BTC-USDT', side: 'BUY', quantity: 0.001, fillPrice: 64180.9, timestamp: '14:52:19' }
      ],
      equitySeries: [998.4, 998.2, 998.6, 998.9, 998.7, 999.1, 999.0, 999.4, 999.2, 999.6, 999.9, 999.7, 1000.1, 999.9, 1000.3, 1000.6, 1000.2, 1000.5, 1000.8, 1000.4, 1000.1, 1000.5, 1000.9, 1001.1, 1000.7, 1000.4, 1000.6, 1000.9, 1000.5, 1000.2, 1000.0, 1000.4, 1000.7, 1000.5, 1000.3, 1000.3]
    },
    // huginn alpha factory
    alpha: {
      compositeScore: 0.42, entryThreshold: 0.30, blend: 'weighted-sum',
      alphas: [
        { name: 'imbalance', weight: 0.30, contribution: 0.51, confidence: 0.80, ic: [0.02, -0.01, 0.03, 0.0, -0.02] },
        { name: 'momentum', weight: 0.25, contribution: -0.12, confidence: 0.60, ic: [-0.01, 0.0, -0.02, 0.01, -0.03] },
        { name: 'mean_reversion', weight: 0.20, contribution: 0.33, confidence: 0.70, ic: [0.01, 0.02, 0.0, 0.03, 0.02] },
        { name: 'funding_rate', weight: 0.15, contribution: 0.10, confidence: 0.40, ic: [0.0, 0.01, -0.01, 0.0, 0.01] },
        { name: 'vol_regime', weight: 0.10, contribution: 0.0, confidence: 0.0, ic: [0.0, 0.0, -0.01, 0.0, 0.0] }
      ]
    },
    // muninn-py factor / portfolio
    portfolio: {
      weights: { BTC: 0.32, ETH: -0.18, SOL: 0.10, XRP: -0.14, DOGE: -0.10 },
      factorExposures: { market: 0.05, momentum: 0.21, volatility: -0.12 },
      riskContributions: { BTC: 0.22, ETH: 0.19, SOL: 0.20, XRP: 0.21, DOGE: 0.18 }
    },
    // validation — honest committed walk-forward numbers
    validation: {
      folds: [
        { fold: 1, train: 288, test: 288, isPnL: -45.3, oosPnL: -57.8 },
        { fold: 2, train: 576, test: 288, isPnL: -99.6, oosPnL: -0.1 },
        { fold: 3, train: 864, test: 288, isPnL: -99.0, oosPnL: -20.8 },
        { fold: 4, train: 1152, test: 288, isPnL: -219.9, oosPnL: -65.4 }
      ],
      oosFoldsProfitable: '0/4', totalOOSPnL: -146.1, pbo: 1.0, deflatedSharpe: null
    },
    services: [
      { name: 'muninn', status: 'up' },
      { name: 'huginn', status: 'up' },
      { name: 'sleipnir', status: 'degraded' },
      { name: 'odin', status: 'up' },
      { name: 'redpanda', status: 'up' }
    ]
  };

  // ====================================================================
  // Formatting helpers — copied verbatim from the DC Component
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
    var o = { method: opts.method || 'GET', signal: ctl.signal };
    if (opts.headers) o.headers = opts.headers;
    if (opts.body != null) o.body = opts.body;
    return fetch(url, o).finally(function () { clearTimeout(t); });
  }

  // Parse Prometheus text exposition into [{name, labels:{}, value}]
  function parseMetrics(text) {
    var out = [];
    var lines = text.split('\n');
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i].trim();
      if (!line || line.charAt(0) === '#') continue;
      // name{labels} value   OR   name value
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
  // loadLive — fetch every source, map to the data shape, merge over DEMO
  // ====================================================================
  function loadLive() {
    // Deep-clone DEMO so we never mutate the fallback baseline.
    var data = JSON.parse(JSON.stringify(DEMO));
    var liveSections = new Set();

    // shared metrics text (huginn /metrics) used by both `live` and `alpha`
    var metricsP = fetchWithTimeout(BASE + ':8083/metrics')
      .then(function (r) { return r.ok ? r.text() : null; })
      .catch(function () { return null; });

    // ---- live  ← :8083/api/snapshot (+fills, +metrics suppressed, +odin equity) ----
    var liveP = fetchWithTimeout(BASE + ':8083/api/snapshot')
      .then(function (r) { if (!r.ok) throw new Error('snapshot ' + r.status); return r.json(); })
      .then(function (j) {
        var L = data.live;
        var p = j.portfolio || j.Portfolio || {};
        if (p.Cash != null) L.cash = p.Cash;
        if (p.TotalValue != null) L.totalValue = p.TotalValue;
        if (p.RealizedPnL != null) L.realizedPnL = p.RealizedPnL;
        if (p.UnrealizedPnL != null) L.unrealizedPnL = p.UnrealizedPnL;
        if (p.TotalCosts != null) L.fees = p.TotalCosts;
        if (p.TotalFills != null) L.totalFills = p.TotalFills;
        if (Array.isArray(p.Positions)) {
          L.positions = p.Positions.map(function (pos) {
            return {
              instrument: pos.Instrument,
              quantity: pos.Quantity,
              averageCost: pos.AverageCost,
              lastMarkPrice: pos.LastMarkPrice,
              unrealizedPnL: pos.UnrealizedPnL
            };
          });
        }
        // top-level fills[] — Side 0=BUY / 1=SELL, timestamp -> HH:MM:SS
        if (Array.isArray(j.fills)) {
          L.fills = j.fills.map(function (f) {
            var side = f.Side === 1 || f.Side === 'SELL' ? 'SELL' : 'BUY';
            var ts = String(f.Timestamp == null ? '' : f.Timestamp);
            var hm = ts.match(/(\d{2}:\d{2}:\d{2})/);
            return {
              instrument: f.Instrument,
              side: side,
              quantity: f.Quantity,
              fillPrice: f.FillPrice,
              timestamp: hm ? hm[1] : ts
            };
          });
        }
        liveSections.add('live');
      })
      .catch(function () { /* keep DEMO live */ });

    // ordersCostSuppressed ← sum of all huginn_orders_cost_suppressed_total samples
    var suppressedP = metricsP.then(function (text) {
      if (!text) return;
      var samples = parseMetrics(text);
      var sum = 0, found = false;
      for (var i = 0; i < samples.length; i++) {
        if (samples[i].name === 'huginn_orders_cost_suppressed_total') { sum += samples[i].value; found = true; }
      }
      if (found) { data.live.ordersCostSuppressed = sum; liveSections.add('live'); }
    }).catch(function () {});

    // equitySeries ← optionally :8086/api/equity (else keep DEMO)
    var equityP = fetchWithTimeout(BASE + ':8086/api/equity')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j) return;
        // accept either an array of numbers or array of {equity|value|TotalValue}
        var arr = Array.isArray(j) ? j : (j.equity || j.series || j.points || null);
        if (!Array.isArray(arr) || !arr.length) return;
        var series = arr.map(function (x) {
          if (typeof x === 'number') return x;
          return x.equity != null ? x.equity : (x.value != null ? x.value : x.TotalValue);
        }).filter(function (n) { return typeof n === 'number' && !isNaN(n); });
        if (series.length) { data.live.equitySeries = series; liveSections.add('live'); }
      })
      .catch(function () { /* keep DEMO equity */ });
    // regime ← keep DEMO (not trivially available)

    // ---- alpha  ← parse :8083/metrics ----
    var alphaP = metricsP.then(function (text) {
      if (!text) return;
      var samples = parseMetrics(text);
      var touched = false;
      for (var i = 0; i < samples.length; i++) {
        var s = samples[i];
        if (s.name === 'huginn_composite_score') { data.alpha.compositeScore = s.value; touched = true; }
        if (s.name === 'huginn_alpha_contribution' && s.labels.alpha) {
          for (var a = 0; a < data.alpha.alphas.length; a++) {
            if (data.alpha.alphas[a].name === s.labels.alpha) { data.alpha.alphas[a].contribution = s.value; touched = true; }
          }
        }
      }
      // keep DEMO weight / confidence / ic (not exposed in metrics)
      if (touched) liveSections.add('alpha');
    }).catch(function () {});

    // ---- portfolio ← keep DEMO ----
    // TODO: wire to a future muninn-py optimizer endpoint (factor / portfolio weights).

    // ---- validation ← keep DEMO ----
    // These are the honest committed walk-forward numbers (0/4, PBO 1.00); not served live.

    // ---- services ← health-check each ----
    function health(name, path, fallbackPath) {
      return fetchWithTimeout(BASE + path)
        .then(function (r) {
          if (r.ok) return 'up';
          if (fallbackPath) {
            return fetchWithTimeout(BASE + fallbackPath)
              .then(function (r2) { return r2.ok ? 'up' : 'down'; })
              .catch(function () { return 'down'; });
          }
          return 'down';
        })
        .catch(function () {
          if (fallbackPath) {
            return fetchWithTimeout(BASE + fallbackPath)
              .then(function (r2) { return r2.ok ? 'up' : 'down'; })
              .catch(function () { return 'down'; });
          }
          return 'down';
        })
        .then(function (status) { return { name: name, status: status }; });
    }
    var svcChecks = [
      health('huginn', ':8083/healthz'),
      health('sleipnir', ':8085/healthz'),
      health('odin', ':8086/healthz'),
      health('muninn', ':8080/actuator/health', ':8080/healthz'),
      // redpanda-console reachability on :8088
      health('redpanda', ':8088', ':8088/public/v1/admin/health')
    ];
    var servicesP = Promise.all(svcChecks).then(function (results) {
      var anyUp = false;
      var byName = {};
      results.forEach(function (r) { byName[r.name] = r.status; if (r.status === 'up') anyUp = true; });
      // Map onto DEMO service order; only overwrite when we got a definite signal.
      data.services = data.services.map(function (s) {
        return { name: s.name, status: byName[s.name] != null ? byName[s.name] : s.status };
      });
      if (anyUp) liveSections.add('services');
    }).catch(function () {});

    return Promise.all([liveP, suppressedP, equityP, alphaP, servicesP])
      .then(function () { return { data: data, liveSections: liveSections }; })
      .catch(function () { return { data: data, liveSections: liveSections }; });
  }

  // ====================================================================
  // apply — write the data shape into the tagged DOM elements
  // ====================================================================
  // local halt state (visual-only when no token / POST unavailable)
  var halted = false;

  function regimeStyle(regime) {
    var map = {
      QUIET: { color: '#7787a0', bg: '#11192a', border: '#22324a' },
      TREND: { color: C.blue, bg: '#0e1a30', border: '#23436e' },
      'MEAN-REVERT': { color: C.amber, bg: '#1c1606', border: '#43381a' },
      VOLATILE: { color: C.red, bg: '#1f0c0e', border: '#4a2026' }
    };
    return map[regime] || map.QUIET;
  }

  function apply(data, liveSections) {
    var L = data.live, A = data.alpha, P = data.portfolio, V = data.validation;

    // ---------- TOP STRIP ----------
    var netPnl = L.realizedPnL + L.unrealizedPnL;
    var netPct = (netPnl / (L.totalValue - netPnl)) * 100;
    setText('equity', usd(L.totalValue));
    setText('cash', usd(L.cash));
    var netColor = netPnl >= 0 ? C.green : C.red;
    setText('net-pnl', signedUsd(netPnl)); setColor('net-pnl', netColor);
    setText('net-pnl-pct', pct(netPct, 3)); setColor('net-pnl-pct', netColor);
    setText('open-pos', L.positions.length);
    setText('fills-today', L.totalFills);
    setText('suppressed', L.ordersCostSuppressed);

    var rm = regimeStyle(L.regime);
    var pill = $('regime-pill');
    if (pill) { pill.style.background = rm.bg; pill.style.borderColor = rm.border; }
    var rdot = $('regime-dot'); if (rdot) rdot.style.background = rm.color;
    setText('regime', L.regime); setColor('regime', rm.color);

    // ---------- SERVICES ----------
    var svcColor = { up: C.green, degraded: C.amber, down: C.red };
    var svcGlow = { up: '#2bd4a455', degraded: '#f3b13c55', down: '#ff626255' };
    data.services.forEach(function (s) {
      var dot = $('svc-' + s.name);
      if (!dot) return;
      var col = svcColor[s.status] || C.dim;
      dot.style.background = col;
      dot.style.boxShadow = '0 0 6px ' + (svcGlow[s.status] || 'transparent');
      var wrap = dot.parentElement; if (wrap) wrap.title = s.name + ' — ' + s.status;
    });

    // ---------- HALT BUTTON ----------
    applyHaltVisual();

    // ---------- EQUITY CURVE ----------
    var es = L.equitySeries, W = 680, H = 168, pT = 14, pB = 18;
    var mn = Math.min.apply(null, es), mx = Math.max.apply(null, es), span = (mx - mn) || 1;
    var xs = function (i) { return (i / (es.length - 1)) * (W - 4) + 2; };
    var ys = function (v) { return pT + (1 - (v - mn) / span) * (H - pT - pB); };
    var line = '';
    es.forEach(function (v, i) { line += (i === 0 ? 'M ' : 'L ') + xs(i).toFixed(1) + ' ' + ys(v).toFixed(1) + ' '; });
    var area = line + 'L ' + xs(es.length - 1).toFixed(1) + ' ' + H + ' L ' + xs(0).toFixed(1) + ' ' + H + ' Z';
    var ep = $('equity-path'); if (ep) ep.setAttribute('d', line.trim());
    var ea = $('equity-area'); if (ea) ea.setAttribute('d', area);
    var dot = $('equity-dot');
    if (dot) { dot.setAttribute('cx', xs(es.length - 1).toFixed(1)); dot.setAttribute('cy', ys(es[es.length - 1]).toFixed(1)); }
    setText('equity-start', usd(es[0]));
    setText('equity-end', usd(es[es.length - 1]));

    // ---------- STAT TILES ----------
    var statTiles = [
      { label: 'REALIZED PnL', value: signedUsd(L.realizedPnL), color: L.realizedPnL >= 0 ? C.green : C.red, sub: 'booked today' },
      { label: 'UNREALIZED PnL', value: signedUsd(L.unrealizedPnL), color: L.unrealizedPnL >= 0 ? C.green : C.red, sub: 'open marks' },
      { label: 'FEES', value: usd(L.fees), color: C.mut, sub: 'taker + maker' },
      { label: 'NET PnL', value: signedUsd(netPnl), color: netPnl >= 0 ? C.green : C.red, sub: 'realized + unreal' },
      { label: 'SUPPRESSED', value: String(L.ordersCostSuppressed), color: C.amber, sub: 'cost-gated signals' }
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

    // ---------- POSITIONS ----------
    var posBody = $('positions-body');
    if (posBody) {
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

    // ---------- FILLS ----------
    var fillsEl = $('fills-feed');
    if (fillsEl) {
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

    // ---------- ALPHA GAUGE ----------
    var cx = 140, cy = 132, R = 110, score = A.compositeScore, thr = A.entryThreshold;
    var compositeColor = Math.abs(score) < thr ? C.amber : (score > 0 ? C.green : C.red);
    setAttr('gauge-track', 'd', arc(cx, cy, R, -90, 90));
    setAttr('gauge-band-pos', 'd', arc(cx, cy, R, thr * 90, 90));
    setAttr('gauge-band-neg', 'd', arc(cx, cy, R, -90, -thr * 90));
    setAttr('gauge-active', 'd', score >= 0 ? arc(cx, cy, R, 0, score * 90) : arc(cx, cy, R, score * 90, 0));
    var ga = $('gauge-active'); if (ga) ga.setAttribute('stroke', compositeColor);
    var needle = polar(cx, cy, R - 16, score * 90);
    var nEl = $('gauge-needle');
    if (nEl) { nEl.setAttribute('x2', needle.x.toFixed(1)); nEl.setAttribute('y2', needle.y.toFixed(1)); nEl.setAttribute('stroke', compositeColor); }
    var hub = $('gauge-hub'); if (hub) hub.setAttribute('fill', compositeColor);
    var tA_o = polar(cx, cy, R + 8, thr * 90), tA_i = polar(cx, cy, R - 10, thr * 90);
    var tB_o = polar(cx, cy, R + 8, -thr * 90), tB_i = polar(cx, cy, R - 10, -thr * 90);
    var tickA = $('gauge-tick-a');
    if (tickA) { tickA.setAttribute('x1', tA_i.x.toFixed(1)); tickA.setAttribute('y1', tA_i.y.toFixed(1)); tickA.setAttribute('x2', tA_o.x.toFixed(1)); tickA.setAttribute('y2', tA_o.y.toFixed(1)); }
    var tickB = $('gauge-tick-b');
    if (tickB) { tickB.setAttribute('x1', tB_i.x.toFixed(1)); tickB.setAttribute('y1', tB_i.y.toFixed(1)); tickB.setAttribute('x2', tB_o.x.toFixed(1)); tickB.setAttribute('y2', tB_o.y.toFixed(1)); }
    setText('composite-score', (score >= 0 ? '+' : '') + nf(score, 2)); setColor('composite-score', compositeColor);
    setText('threshold', nf(thr, 2));
    var implies = Math.abs(score) < thr ? 'NO TRADE' : (score > 0 ? 'IMPLIES LONG' : 'IMPLIES SHORT');
    var impliesColor = Math.abs(score) < thr ? C.amber : (score > 0 ? C.green : C.red);
    var impliesBg = Math.abs(score) < thr ? '#1c1606' : (score > 0 ? '#0c1f1a' : '#1f0d10');
    var ci = $('comp-implies');
    if (ci) { ci.textContent = implies; ci.style.color = impliesColor; ci.style.background = impliesBg; }
    setText('blend', A.blend);
    setText('alpha-count', A.alphas.length);

    // ---------- ALPHA LIST ----------
    var rows = $('alpha-rows');
    if (rows) {
      rows.innerHTML = A.alphas.map(function (a) {
        var cv = a.contribution, frac = Math.min(Math.abs(cv) / 1, 1) * 50;
        var contribColor = cv > 0 ? C.green : (cv < 0 ? C.red : C.dim);
        var contribLeft = cv >= 0 ? 50 : 50 - frac;
        var contribWidth = Math.max(frac, cv === 0 ? 0 : 1.2);
        var confColor = a.confidence >= 0.65 ? C.blue : (a.confidence >= 0.45 ? C.amber : '#46566e');
        var icMn = Math.min.apply(null, a.ic.concat([-0.005])), icMx = Math.max.apply(null, a.ic.concat([0.005])), icSpan = (icMx - icMn) || 1;
        var icPath = '';
        a.ic.forEach(function (v, i) { var x = (i / (a.ic.length - 1)) * 60; var y = 16 - ((v - icMn) / icSpan) * 14; icPath += (i === 0 ? 'M ' : 'L ') + x.toFixed(1) + ' ' + y.toFixed(1) + ' '; });
        var icLast = a.ic[a.ic.length - 1];
        var icColor = icLast >= 0 ? C.green : C.red;
        return '<div style="display:flex;align-items:center;padding:10px 14px;border-top:1px solid #111d2c">' +
          '<span style="flex:1.3;display:flex;flex-direction:column;gap:1px"><span style="font-size:12.5px;color:#dbe4f0;font-weight:500;font-family:\'JetBrains Mono\',monospace">' + esc(a.name) + '</span></span>' +
          '<span style="width:54px;text-align:right;font-family:\'JetBrains Mono\',monospace;font-size:11.5px;color:#9fb0c8">' + nf(a.weight, 2) + '</span>' +
          '<span style="flex:1.6;padding:0 14px"><span style="position:relative;display:block;height:14px;background:#0c1726;border-radius:3px">' +
          '<span style="position:absolute;top:0;bottom:0;left:50%;width:1px;background:#2b3c55"></span>' +
          '<span style="position:absolute;top:2px;bottom:2px;left:' + contribLeft + '%;width:' + contribWidth + '%;background:' + contribColor + ';border-radius:2px"></span></span>' +
          '<span style="display:block;text-align:center;font-size:10px;font-family:\'JetBrains Mono\',monospace;color:' + contribColor + ';margin-top:3px">' + (cv >= 0 ? '+' : '') + nf(cv, 2) + '</span></span>' +
          '<span style="width:62px;text-align:right"><span style="display:inline-block;width:38px;height:5px;background:#0c1726;border-radius:3px;vertical-align:middle;position:relative;overflow:hidden"><span style="position:absolute;left:0;top:0;bottom:0;width:' + (a.confidence * 100) + '%;background:' + confColor + '"></span></span>' +
          '<span style="font-size:10.5px;font-family:\'JetBrains Mono\',monospace;color:#9fb0c8;margin-left:5px">' + Math.round(a.confidence * 100) + '%</span></span>' +
          '<span style="width:66px;text-align:right"><svg viewBox="0 0 60 18" width="60" height="18" preserveAspectRatio="none" style="vertical-align:middle"><line x1="0" y1="9" x2="60" y2="9" stroke="#1a283c" stroke-width="1"/><path d="' + icPath.trim() + '" fill="none" stroke="' + icColor + '" stroke-width="1.4" stroke-linejoin="round"/></svg></span></div>';
      }).join('');
    }

    // ---------- PORTFOLIO WEIGHTS ----------
    var wEntries = Object.keys(P.weights).map(function (k) { return [k, P.weights[k]]; });
    var wMax = Math.max.apply(null, wEntries.map(function (e) { return Math.abs(e[1]); })) * 1.08 || 1;
    var weightsEl = $('weights');
    if (weightsEl) {
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
    var netExp = wEntries.reduce(function (s, e) { return s + e[1]; }, 0);
    setText('net-exposure', (netExp >= 0 ? '+' : '') + nf(netExp, 2) + ' (neutral)');

    // ---------- FACTORS ----------
    var fEntries = Object.keys(P.factorExposures).map(function (k) { return [k, P.factorExposures[k]]; });
    var fMax = Math.max.apply(null, fEntries.map(function (e) { return Math.abs(e[1]); })) * 1.15 || 1;
    var factorsEl = $('factors');
    if (factorsEl) {
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

    // ---------- RISK DONUT ----------
    var rEntries = Object.keys(P.riskContributions).map(function (k) { return [k, P.riskContributions[k]]; });
    var rTotal = rEntries.reduce(function (s, e) { return s + e[1]; }, 0) || 1;
    var palette = ['#5b8def', '#2bd4a4', '#f3b13c', '#a779f0', '#5f6f88'];
    var circ = 2 * Math.PI * 54;
    var cum = 0;
    var segs = [], legend = [];
    rEntries.forEach(function (e, i) {
      var frac = e[1] / rTotal, len = frac * circ;
      segs.push({ color: palette[i % palette.length], dash: len.toFixed(1) + ' ' + (circ - len).toFixed(1), offset: (-cum * circ).toFixed(1) });
      legend.push({ label: e[0], color: palette[i % palette.length], pctStr: Math.round(frac * 100) + '%' });
      cum += frac;
    });
    var segsEl = $('donut-segs');
    if (segsEl) {
      segsEl.innerHTML = segs.map(function (s) {
        return '<circle cx="70" cy="70" r="54" fill="none" stroke="' + s.color + '" stroke-width="18" stroke-dasharray="' + s.dash + '" stroke-dashoffset="' + s.offset + '"/>';
      }).join('');
    }
    var legEl = $('donut-legend');
    if (legEl) {
      legEl.innerHTML = legend.map(function (lg) {
        return '<div style="display:flex;align-items:center;gap:7px;font-size:11px">' +
          '<span style="width:8px;height:8px;border-radius:2px;background:' + lg.color + '"></span>' +
          '<span style="color:#c4d0e0;flex:1;font-family:\'JetBrains Mono\',monospace">' + esc(lg.label) + '</span>' +
          '<span style="color:#7787a0;font-family:\'JetBrains Mono\',monospace">' + lg.pctStr + '</span></div>';
      }).join('');
    }

    // ---------- VALIDATION ----------
    var foldsBody = $('folds-body');
    if (foldsBody) {
      foldsBody.innerHTML = V.folds.map(function (f) {
        var isColor = f.isPnL >= 0 ? C.green : C.red;
        var oosColor = f.oosPnL >= 0 ? C.green : C.red;
        return '<tr style="border-top:1px solid #111d2c;font-size:12px">' +
          '<td style="text-align:left;padding:9px 14px;color:#c4d0e0">#' + f.fold + '</td>' +
          '<td style="text-align:right;padding:9px 8px;color:#7787a0">' + f.train + '</td>' +
          '<td style="text-align:right;padding:9px 8px;color:#7787a0">' + f.test + '</td>' +
          '<td style="text-align:right;padding:9px 8px;color:' + isColor + '">' + signedUsd(f.isPnL, 1) + '</td>' +
          '<td style="text-align:right;padding:9px 14px;color:' + oosColor + ';font-weight:600">' + signedUsd(f.oosPnL, 1) + '</td></tr>';
      }).join('');
    }
    setText('pbo', nf(V.pbo, 2));
    var marker = $('pbo-marker'); if (marker) marker.style.left = (V.pbo * 100) + '%';
    setText('dsharpe', V.deflatedSharpe == null ? 'undefined' : nf(V.deflatedSharpe, 2));
    setText('oos-profitable', V.oosFoldsProfitable);
    setText('total-oos', signedUsd(V.totalOOSPnL, 1));

    // ---------- FOOTER BADGE ----------
    var badge = $('status-badge'), dotF = $('status-dot'), textF = $('status-text');
    if (badge && dotF && textF) {
      if (liveSections.size > 0) {
        textF.textContent = 'LIVE · ' + liveSections.size + ' source' + (liveSections.size === 1 ? '' : 's');
        badge.style.color = C.green; badge.style.background = '#0c1f1a'; badge.style.borderColor = '#1f6e54';
        dotF.style.background = C.green;
      } else {
        textF.textContent = 'DEMO DATA · not connected to live endpoints';
        badge.style.color = '#7e6a3a'; badge.style.background = '#211a0c'; badge.style.borderColor = '#3d3214';
        dotF.style.background = C.amber;
      }
    }
  }

  function setAttr(key, attr, val) { var el = $(key); if (el) el.setAttribute(attr, val); }

  // ====================================================================
  // HALT / RESUME
  // ====================================================================
  function applyHaltVisual() {
    var btn = $('halt-btn');
    var label = $('halt-btn-label');
    var status = $('halt-status');
    var banner = $('halt-banner');
    var icon2 = $('halt-icon-second');
    if (label) label.textContent = halted ? 'RESUME' : 'HALT';
    if (status) { status.textContent = halted ? 'HALTED' : 'RUNNING'; status.style.color = halted ? C.red : C.green; }
    if (btn) {
      btn.style.background = halted ? '#0c1f1a' : '#1f0d10';
      btn.style.borderColor = halted ? '#1f6e54' : '#6e2a30';
      btn.style.color = halted ? C.green : '#ff8b8b';
    }
    if (icon2) icon2.style.display = halted ? 'none' : '';
    if (banner) banner.style.display = halted ? 'flex' : 'none';
  }

  function getToken() {
    if (window.NC_TOKEN) return window.NC_TOKEN;
    try { return localStorage.getItem('nc_token'); } catch (e) { return null; }
  }

  function onHaltClick() {
    var token = getToken();
    var target = !halted; // desired state after toggle
    if (token) {
      // huginn breaker endpoints live under /api/breaker/*
      fetchWithTimeout(BASE + ':8083/api/breaker', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
        body: JSON.stringify({ halted: target })
      }).then(function (r) {
        if (r.ok) { halted = target; applyHaltVisual(); }
        else { console.warn('[norse-console] breaker POST returned ' + r.status); }
      }).catch(function (e) {
        console.warn('[norse-console] breaker POST failed:', e && e.message);
      });
    } else {
      // No token — local visual toggle only.
      halted = target;
      applyHaltVisual();
      console.warn('[norse-console] no token set (window.NC_TOKEN / localStorage "nc_token"); halt toggled locally only — live order routing NOT changed.');
    }
  }

  // ====================================================================
  // Bootstrap
  // ====================================================================
  function tick() {
    loadLive().then(function (res) {
      try { apply(res.data, res.liveSections); }
      catch (e) { console.warn('[norse-console] apply error:', e && e.message); }
    }).catch(function () { /* never throw — stay on whatever is rendered */ });
  }

  function init() {
    var btn = $('halt-btn');
    if (btn) btn.addEventListener('click', onHaltClick);
    tick();
    setInterval(tick, POLL_MS);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
