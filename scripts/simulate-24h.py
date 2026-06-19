#!/usr/bin/env python3
"""
Norse Stack 24-hour market simulator.

Generates realistic BTC-USDT price action and feeds:
  1. Trade events → Muninn (HTTP API)
  2. OBI feature events → Huginn (Kafka via rpk)

Price model: geometric Brownian motion with regime switches
  - Trending regime: drift ±0.02%/tick, low vol
  - Mean-reverting regime: pulls toward VWAP, higher vol
  - Volatile regime: high vol bursts (news events)

OBI model: correlated with net buy/sell flow, mean-reverting around 0.

Usage:
  python3 scripts/simulate-24h.py                    # 24 hours
  python3 scripts/simulate-24h.py --hours 1           # 1 hour test
  python3 scripts/simulate-24h.py --speed 10           # 10x speed (trade every 0.5s)
  python3 scripts/simulate-24h.py --hours 1 --speed 60 # 1 hour in 1 minute
"""

import argparse
import json
import math
import os
import random
import signal
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone

MUNINN_URL = os.environ.get("MUNINN_URL", "http://localhost:8080")
REDPANDA_CONTAINER = os.environ.get("REDPANDA_CONTAINER", "norse-stack-redpanda-1")
OBI_TOPIC = "features.obi.v1"
INSTRUMENT = "BTC-USDT"

INITIAL_PRICE = 67500.0
TICK_INTERVAL = 5.0  # seconds between trades at 1x speed

# Regime parameters
REGIMES = {
    "trending_up":   {"drift": 0.0002, "vol": 0.0003, "obi_bias": 0.15,  "duration": (60, 300)},
    "trending_down": {"drift": -0.0002, "vol": 0.0003, "obi_bias": -0.15, "duration": (60, 300)},
    "mean_revert":   {"drift": 0.0, "vol": 0.0005, "obi_bias": 0.0,    "duration": (120, 600)},
    "volatile":      {"drift": 0.0, "vol": 0.0015, "obi_bias": 0.0,    "duration": (30, 120)},
    "quiet":         {"drift": 0.0, "vol": 0.0001, "obi_bias": 0.0,    "duration": (120, 600)},
}

shutdown = False

def handle_signal(signum, frame):
    global shutdown
    shutdown = True
    print("\nShutting down gracefully...", flush=True)


def send_trade(price, size, side, seq):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "eventId": str(uuid.uuid4()),
        "eventTime": now,
        "ingestTime": now,
        "source": "simulator",
        "instrument": {
            "symbol": INSTRUMENT,
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "exchange": {"id": "binance", "displayName": "Binance Spot", "timezone": "UTC"},
        },
        "sequenceNumber": seq,
        "schemaVersion": 1,
        "price": round(price, 2),
        "size": round(size, 6),
        "side": side,
        "exchangeTradeId": f"sim-{seq}",
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{MUNINN_URL}/api/v1/events/trade",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 201
    except Exception as e:
        print(f"  [WARN] Trade send failed: {e}", flush=True)
        return False


def send_obi(obi_value, seq):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    feature = {
        "eventId": str(uuid.uuid4()),
        "eventTime": now,
        "featureName": "obi",
        "featureVersion": "v1",
        "instrument": INSTRUMENT,
        "windowStart": now,
        "windowEnd": now,
        "values": {"obi": round(obi_value, 4)},
    }
    msg = json.dumps(feature)
    try:
        result = subprocess.run(
            ["docker", "exec", "-i", REDPANDA_CONTAINER,
             "rpk", "topic", "produce", OBI_TOPIC, "--key", INSTRUMENT],
            input=msg, capture_output=True, text=True, timeout=5,
        )
        return "Produced" in result.stdout
    except Exception as e:
        print(f"  [WARN] OBI send failed: {e}", flush=True)
        return False


def pick_regime():
    weights = {
        "trending_up": 0.2,
        "trending_down": 0.2,
        "mean_revert": 0.3,
        "volatile": 0.1,
        "quiet": 0.2,
    }
    names = list(weights.keys())
    return random.choices(names, weights=[weights[n] for n in names], k=1)[0]


def get_portfolio():
    try:
        req = urllib.request.Request("http://localhost:8083/api/snapshot")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def format_duration(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h{m:02d}m"


def main():
    parser = argparse.ArgumentParser(description="Norse Stack 24h market simulator")
    parser.add_argument("--hours", type=float, default=24, help="Duration in hours (default: 24)")
    parser.add_argument("--speed", type=float, default=1, help="Speed multiplier (default: 1x)")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    duration_secs = args.hours * 3600
    tick = TICK_INTERVAL / args.speed
    obi_interval = 12  # send OBI every N trades

    price = INITIAL_PRICE
    obi = 0.0
    vwap_accum = 0.0
    vol_accum = 0.0
    seq = 1
    trades_sent = 0
    obis_sent = 0
    signals_fired = 0
    start_time = time.time()

    regime_name = pick_regime()
    regime = REGIMES[regime_name]
    regime_end = start_time + random.uniform(*regime["duration"])

    print(f"=== Norse Stack 24h Simulator ===", flush=True)
    print(f"Duration: {args.hours}h at {args.speed}x speed (wall-clock: {format_duration(duration_secs / args.speed)})", flush=True)
    print(f"Tick interval: {tick:.1f}s | OBI every {obi_interval} trades", flush=True)
    print(f"Starting price: ${price:,.2f}", flush=True)
    print(f"Initial regime: {regime_name}", flush=True)
    print(f"PID: {os.getpid()} — kill with: kill {os.getpid()}", flush=True)
    print(f"Log: tail -f /tmp/norse-sim.log", flush=True)
    print("=" * 40, flush=True)

    log = open("/tmp/norse-sim.log", "w")

    last_status = start_time
    prev_fills = 0

    while not shutdown:
        elapsed = time.time() - start_time
        sim_elapsed = elapsed * args.speed
        if sim_elapsed >= duration_secs:
            break

        # Regime switch
        if time.time() > regime_end:
            regime_name = pick_regime()
            regime = REGIMES[regime_name]
            regime_end = time.time() + random.uniform(*regime["duration"]) / args.speed
            log.write(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Regime → {regime_name}\n")
            log.flush()

        # Price step (geometric Brownian motion)
        drift = regime["drift"]
        vol = regime["vol"]
        shock = random.gauss(0, 1)
        ret = drift + vol * shock
        price *= (1 + ret)
        price = max(price, 100)  # floor

        # Trade size: larger in volatile regimes
        base_size = 0.001 + random.expovariate(10)
        if regime_name == "volatile":
            base_size *= 3

        # Side: biased by regime
        buy_prob = 0.5 + regime["obi_bias"]
        side = "BUY" if random.random() < buy_prob else "SELL"

        # VWAP tracking
        vwap_accum += price * base_size
        vol_accum += base_size
        vwap = vwap_accum / vol_accum if vol_accum > 0 else price

        # Send trade to Muninn
        send_trade(price, base_size, side, seq)
        trades_sent += 1

        # OBI: mean-reverting process correlated with flow
        obi_shock = random.gauss(0, 0.08)
        flow_impact = 0.05 if side == "BUY" else -0.05
        obi = 0.92 * obi + flow_impact + obi_shock + regime["obi_bias"] * 0.02
        obi = max(-1.0, min(1.0, obi))  # clamp

        # Send OBI feature every N trades
        if seq % obi_interval == 0:
            send_obi(obi, seq)
            obis_sent += 1
            if abs(obi) > 0.7:
                signals_fired += 1
                log.write(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] OBI={obi:+.4f} → signal likely\n")
                log.flush()

        # Status every 60 seconds
        if time.time() - last_status > 60:
            pct = (sim_elapsed / duration_secs) * 100
            snap = get_portfolio()
            portfolio_line = ""
            if snap and snap.get("portfolio"):
                p = snap["portfolio"]
                total = p.get("TotalValue", 0)
                fills = p.get("TotalFills", 0)
                pnl = total - 100000
                new_fills = fills - prev_fills
                prev_fills = fills
                portfolio_line = f" | Portfolio: ${total:,.0f} (PnL: {'+' if pnl >= 0 else ''}{pnl:,.0f}) | Fills: {fills} (+{new_fills})"

            msg = (
                f"[{format_duration(sim_elapsed)}] {pct:.0f}% | "
                f"Price: ${price:,.2f} | VWAP: ${vwap:,.2f} | OBI: {obi:+.4f} | "
                f"Trades: {trades_sent} | Signals: {signals_fired}"
                f"{portfolio_line}"
            )
            print(msg, flush=True)
            log.write(msg + "\n")
            log.flush()
            last_status = time.time()

        seq += 1
        time.sleep(tick)

    # Final summary
    elapsed = time.time() - start_time
    snap = get_portfolio()
    print("\n" + "=" * 60, flush=True)
    print("=== SIMULATION COMPLETE ===", flush=True)
    print(f"Wall clock: {format_duration(elapsed)}", flush=True)
    print(f"Simulated:  {format_duration(elapsed * args.speed)}", flush=True)
    print(f"Trades sent: {trades_sent}", flush=True)
    print(f"OBI features sent: {obis_sent}", flush=True)
    print(f"Signals triggered: {signals_fired}", flush=True)
    print(f"Final price: ${price:,.2f} (started ${INITIAL_PRICE:,.2f}, {((price/INITIAL_PRICE)-1)*100:+.2f}%)", flush=True)

    if snap and snap.get("portfolio"):
        p = snap["portfolio"]
        total = p.get("TotalValue", 0)
        fills = p.get("TotalFills", 0)
        pnl = total - 100000
        costs = p.get("TotalCosts", 0)
        print(f"\nPortfolio:", flush=True)
        print(f"  Starting capital: $100,000", flush=True)
        print(f"  Final value:      ${total:,.2f}", flush=True)
        print(f"  P&L:              {'+' if pnl >= 0 else ''}${pnl:,.2f} ({pnl/1000:.2f}%)", flush=True)
        print(f"  Total fills:      {fills}", flush=True)
        print(f"  Total costs:      ${costs:,.2f}", flush=True)
        positions = p.get("Positions", {})
        if positions:
            print(f"  Open positions:", flush=True)
            for inst, pos in positions.items():
                qty = pos.get("Quantity", 0)
                if qty != 0:
                    avg = pos.get("AverageCost", 0)
                    upnl = pos.get("UnrealizedPnL", 0)
                    print(f"    {inst}: {qty:.6f} @ ${avg:,.2f} (unrealized: {'+' if upnl >= 0 else ''}${upnl:,.2f})", flush=True)

        if snap.get("fills"):
            recent = snap["fills"][-5:]
            print(f"\n  Last {len(recent)} fills:", flush=True)
            for f in recent:
                s = "BUY" if f.get("Side") == 0 else "SELL"
                print(f"    {s} {f.get('Quantity', 0):.4f} @ ${f.get('FillPrice', 0):,.2f} [{f.get('OrderID', '')[-12:]}]", flush=True)

    print("=" * 60, flush=True)
    print(f"Full log: /tmp/norse-sim.log", flush=True)
    log.close()


if __name__ == "__main__":
    main()
