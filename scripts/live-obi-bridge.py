#!/usr/bin/env python3
"""
Live OBI Bridge — computes Order Book Imbalance from real Binance data.

Connects to Binance's public WebSocket for 20-level order book snapshots,
computes OBI = (bid_vol - ask_vol) / (bid_vol + ask_vol), and publishes
to the features.obi.v1 Kafka topic for Huginn to consume.

No API key required — uses unauthenticated public market data streams.

Usage:
  python3 scripts/live-obi-bridge.py                       # BTC-USDT, 5s intervals
  python3 scripts/live-obi-bridge.py --symbol ethusdt       # ETH-USDT
  python3 scripts/live-obi-bridge.py --interval 10          # publish every 10s
  python3 scripts/live-obi-bridge.py --levels 5             # top 5 levels only
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import uuid
import websocket
import threading
from datetime import datetime, timezone

REDPANDA_CONTAINER = os.environ.get("REDPANDA_CONTAINER", "norse-stack-redpanda-1")
OBI_TOPIC = "features.obi.v1"

shutdown = False
latest_obi = None
latest_book = None
lock = threading.Lock()
stats = {"messages": 0, "published": 0, "errors": 0, "signals": 0}


def handle_signal(signum, frame):
    global shutdown
    shutdown = True
    print("\nShutting down...", flush=True)


def compute_obi(bids, asks, levels):
    bid_vol = sum(float(b[1]) for b in bids[:levels])
    ask_vol = sum(float(a[1]) for a in asks[:levels])
    total = bid_vol + ask_vol
    if total == 0:
        return 0.0, bid_vol, ask_vol
    return (bid_vol - ask_vol) / total, bid_vol, ask_vol


def symbol_to_instrument(symbol):
    s = symbol.upper()
    if s.endswith("USDT"):
        return f"{s[:-4]}-USDT"
    elif s.endswith("USD"):
        return f"{s[:-3]}-USD"
    elif s.endswith("BTC"):
        return f"{s[:-3]}-BTC"
    return s


def publish_obi(instrument, obi_value, bid_vol, ask_vol, levels):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    feature = {
        "eventId": str(uuid.uuid4()),
        "eventTime": now,
        "featureName": "obi",
        "featureVersion": "v1",
        "instrument": instrument,
        "windowStart": now,
        "windowEnd": now,
        "values": {
            "obi": round(obi_value, 6),
            "bidVolume": round(bid_vol, 4),
            "askVolume": round(ask_vol, 4),
            "levels": levels,
        },
    }
    msg = json.dumps(feature)
    try:
        result = subprocess.run(
            ["docker", "exec", "-i", REDPANDA_CONTAINER,
             "rpk", "topic", "produce", OBI_TOPIC, "--key", instrument],
            input=msg, capture_output=True, text=True, timeout=5,
        )
        return "Produced" in result.stdout
    except Exception as e:
        print(f"  [ERR] Kafka publish failed: {e}", flush=True)
        return False


def on_message(ws, message):
    global latest_book
    stats["messages"] += 1
    try:
        data = json.loads(message)
        if "data" in data:
            data = data["data"]
        if "bids" in data and "asks" in data:
            with lock:
                latest_book = data
    except Exception:
        pass


def on_error(ws, error):
    print(f"  [WS ERR] {error}", flush=True)


def on_close(ws, close_status_code, close_msg):
    print(f"  [WS] Connection closed: {close_status_code} {close_msg}", flush=True)


def on_open(ws):
    print("  [WS] Connected to Binance", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Live OBI bridge from Binance")
    parser.add_argument("--symbol", default="btcusdt", help="Binance symbol (default: btcusdt)")
    parser.add_argument("--interval", type=float, default=5, help="Publish interval in seconds (default: 5)")
    parser.add_argument("--levels", type=int, default=10, help="Book levels for OBI (default: 10, max 20)")
    parser.add_argument("--threshold", type=float, default=0.7, help="Signal alert threshold (default: 0.7)")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    instrument = symbol_to_instrument(args.symbol)
    levels = min(args.levels, 20)
    url = f"wss://stream.binance.com:9443/ws/{args.symbol.lower()}@depth20@100ms"

    print("=" * 60, flush=True)
    print("  LIVE OBI BRIDGE — Real Binance Order Book Data", flush=True)
    print("=" * 60, flush=True)
    print(f"  Symbol:     {args.symbol.upper()} → {instrument}", flush=True)
    print(f"  Stream:     {url}", flush=True)
    print(f"  OBI levels: top {levels}", flush=True)
    print(f"  Interval:   {args.interval}s", flush=True)
    print(f"  Threshold:  ±{args.threshold}", flush=True)
    print(f"  Topic:      {OBI_TOPIC}", flush=True)
    print(f"  PID:        {os.getpid()}", flush=True)
    print("=" * 60, flush=True)

    ws = websocket.WebSocketApp(
        url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )
    ws_thread = threading.Thread(target=ws.run_forever, daemon=True)
    ws_thread.start()

    time.sleep(2)
    if not ws_thread.is_alive():
        print("[FATAL] WebSocket failed to connect", flush=True)
        sys.exit(1)

    print("\n  Waiting for first book snapshot...", flush=True)
    waited = 0
    while latest_book is None and waited < 15:
        time.sleep(0.5)
        waited += 0.5

    if latest_book is None:
        print("[FATAL] No book data received in 15s", flush=True)
        sys.exit(1)

    print("  ✓ Receiving live data. Publishing OBI to Huginn...\n", flush=True)

    start_time = time.time()
    seq = 0

    while not shutdown:
        with lock:
            book = latest_book

        if book:
            obi, bid_vol, ask_vol = compute_obi(
                book.get("bids", []), book.get("asks", []), levels
            )

            ok = publish_obi(instrument, obi, bid_vol, ask_vol, levels)
            seq += 1

            if ok:
                stats["published"] += 1
            else:
                stats["errors"] += 1

            signal_marker = ""
            if abs(obi) > args.threshold:
                stats["signals"] += 1
                direction = "SELL signal (buy pressure)" if obi > 0 else "BUY signal (sell pressure)"
                signal_marker = f"  ← {direction}"

            elapsed = time.time() - start_time
            best_bid = float(book["bids"][0][0]) if book.get("bids") else 0
            best_ask = float(book["asks"][0][0]) if book.get("asks") else 0
            spread = best_ask - best_bid

            print(
                f"  [{seq:>4}] OBI: {obi:+.4f} | "
                f"Bid: ${best_bid:,.2f} Ask: ${best_ask:,.2f} (spread ${spread:.2f}) | "
                f"Vol B:{bid_vol:.2f} A:{ask_vol:.2f} | "
                f"WS msgs: {stats['messages']}{signal_marker}",
                flush=True,
            )

        time.sleep(args.interval)

    ws.close()
    elapsed = time.time() - start_time
    print(f"\n{'=' * 60}", flush=True)
    print(f"  Session: {elapsed:.0f}s", flush=True)
    print(f"  Published: {stats['published']} OBI features", flush=True)
    print(f"  Errors: {stats['errors']}", flush=True)
    print(f"  Signals (|OBI| > {args.threshold}): {stats['signals']}", flush=True)
    print(f"  WS messages received: {stats['messages']}", flush=True)
    print(f"{'=' * 60}", flush=True)


if __name__ == "__main__":
    main()
