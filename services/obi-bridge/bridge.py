#!/usr/bin/env python3
"""
Live OBI Bridge — computes Order Book Imbalance from real Binance market data,
enriched with multi-timeframe momentum, volatility, sentiment, funding rate,
open interest cascade detection, ML confidence, and news sentiment.

Data layers:
  1. OBI — order book imbalance from 20-level depth snapshots
  2. Multi-timeframe momentum — 1m, 5m, 15m EMA crossovers
  3. Volatility — ATR from recent candles (regime detection)
  4. Volume context — volume ratio and spike detection
  5. Sentiment — Fear & Greed Index (crowd positioning)
  6. Funding rate — perpetual futures funding (leverage indicator)
  7. Open interest — OI change for liquidation cascade detection
  8. ML signal quality — XGBoost confidence from Huginn AI
  9. News sentiment — LLM-classified headline sentiment

No API key required — uses unauthenticated public endpoints.
"""

import json
import logging
import os
import signal
import sys
import threading
import time
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone

from kafka import KafkaProducer
from kafka.errors import KafkaConnectionError

KAFKA_BROKERS = os.environ.get("KAFKA_BROKERS", "redpanda:29092")
OBI_TOPIC = os.environ.get("OBI_TOPIC", "features.obi.v1")
SYMBOLS = os.environ.get("SYMBOLS", "BTCUSDT").split(",")
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "5"))
BOOK_LEVELS = int(os.environ.get("BOOK_LEVELS", "10"))
OBI_THRESHOLD = float(os.environ.get("OBI_THRESHOLD", "0.7"))
BINANCE_BASE = os.environ.get("BINANCE_BASE", "https://api.binance.com")
BINANCE_FAPI = os.environ.get("BINANCE_FAPI", "https://fapi.binance.com")
HUGINN_AI_URL = os.environ.get("HUGINN_AI_URL", "http://huginn-ai:8091")
NEWS_SENTINEL_URL = os.environ.get("NEWS_SENTINEL_URL", "http://news-sentinel:8089")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("obi-bridge")

shutdown = False


def handle_signal(signum, frame):
    global shutdown
    shutdown = True
    log.info("Shutdown signal received")


def symbol_to_instrument(symbol):
    s = symbol.upper()
    if s.endswith("USDT"):
        return f"{s[:-4]}-USDT"
    if s.endswith("USD"):
        return f"{s[:-3]}-USD"
    if s.endswith("BTC"):
        return f"{s[:-3]}-BTC"
    if s.endswith("ETH"):
        return f"{s[:-3]}-ETH"
    return s


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "NorseStack/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


# ── Spot API ─────────────────────────────────────────────────────────────

def fetch_orderbook(symbol, limit=20):
    return fetch_json(f"{BINANCE_BASE}/api/v3/depth?symbol={symbol}&limit={limit}")


def fetch_klines(symbol, interval="5m", limit=30):
    return fetch_json(
        f"{BINANCE_BASE}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    )


def fetch_ticker_24h(symbol):
    return fetch_json(f"{BINANCE_BASE}/api/v3/ticker/24hr?symbol={symbol}")


# ── Futures API (funding rate, open interest) ────────────────────────────

def fetch_funding_rate(symbol):
    try:
        data = fetch_json(f"{BINANCE_FAPI}/fapi/v1/premiumIndex?symbol={symbol}")
        rate = float(data.get("lastFundingRate", 0))
        return rate
    except Exception:
        return 0.0


def fetch_open_interest(symbol):
    try:
        data = fetch_json(f"{BINANCE_FAPI}/fapi/v1/openInterest?symbol={symbol}")
        return float(data.get("openInterest", 0))
    except Exception:
        return 0.0


# ── Internal services ────────────────────────────────────────────────────

def fetch_ml_score(instrument):
    try:
        data = fetch_json(
            f"{HUGINN_AI_URL}/api/predict?instrument={instrument}"
        )
        return data.get("confidence", 0.5), data.get("model_ready", False)
    except Exception:
        return 0.5, False


def fetch_news_sentiment(instrument):
    try:
        data = fetch_json(f"{NEWS_SENTINEL_URL}/api/sentiment")
        entry = data.get(instrument, {})
        return entry.get("score", 0.0)
    except Exception:
        return 0.0


def fetch_fear_greed():
    try:
        data = fetch_json("https://api.alternative.me/fng/?limit=1")
        return int(data["data"][0]["value"])
    except Exception:
        return 50


# ── Computations ─────────────────────────────────────────────────────────

def compute_ema(prices, period):
    if len(prices) < period:
        return prices[-1] if prices else 0
    multiplier = 2.0 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def compute_momentum(klines):
    closes = [float(k[4]) for k in klines]
    if len(closes) < 26:
        return {"momentum": 0.0, "emaFast": 0.0, "emaSlow": 0.0, "trend": "neutral"}

    ema12 = compute_ema(closes, 12)
    ema26 = compute_ema(closes, 26)

    if ema26 > 0:
        momentum = (ema12 - ema26) / ema26
    else:
        momentum = 0.0

    if momentum > 0.0005:
        trend = "bullish"
    elif momentum < -0.0005:
        trend = "bearish"
    else:
        trend = "neutral"

    return {
        "momentum": round(momentum, 6),
        "emaFast": round(ema12, 2),
        "emaSlow": round(ema26, 2),
        "trend": trend,
    }


def compute_volatility(klines):
    if len(klines) < 5:
        return {"volatility": 0.0, "atr": 0.0, "regime": "unknown"}

    ranges = []
    for i, k in enumerate(klines[-14:]):
        high, low, close = float(k[2]), float(k[3]), float(k[4])
        if i == 0:
            tr = high - low
        else:
            prev_close = float(klines[max(0, len(klines) - 14 + i - 1)][4])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        ranges.append(tr)

    atr = sum(ranges) / len(ranges) if ranges else 0
    last_close = float(klines[-1][4])
    vol_pct = (atr / last_close) if last_close > 0 else 0

    if vol_pct > 0.015:
        regime = "high"
    elif vol_pct > 0.005:
        regime = "medium"
    else:
        regime = "low"

    return {
        "volatility": round(vol_pct, 6),
        "atr": round(atr, 2),
        "regime": regime,
    }


def compute_volume_context(klines, ticker):
    if not klines or not ticker:
        return {"volumeRatio": 1.0, "volumeSpike": False}

    recent_vols = [float(k[5]) for k in klines[-5:]]
    avg_recent = sum(recent_vols) / len(recent_vols) if recent_vols else 1
    older_vols = [float(k[5]) for k in klines[:-5]]
    avg_older = sum(older_vols) / len(older_vols) if older_vols else avg_recent

    ratio = avg_recent / avg_older if avg_older > 0 else 1.0

    return {
        "volumeRatio": round(ratio, 3),
        "volumeSpike": ratio > 2.0,
    }


def compute_obi(book, levels):
    bids = book.get("bids", [])[:levels]
    asks = book.get("asks", [])[:levels]
    bid_vol = sum(float(b[1]) for b in bids)
    ask_vol = sum(float(a[1]) for a in asks)
    total = bid_vol + ask_vol
    obi = (bid_vol - ask_vol) / total if total > 0 else 0.0

    best_bid = float(bids[0][0]) if bids else 0
    best_ask = float(asks[0][0]) if asks else 0
    spread = best_ask - best_bid
    mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0

    return {
        "obi": round(obi, 6),
        "bid_vol": round(bid_vol, 4),
        "ask_vol": round(ask_vol, 4),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": round(spread, 2),
        "mid": round(mid, 2),
        "levels": min(len(bids), len(asks), levels),
    }


class RegimeDetector:
    """Rolling regime classifier using return distribution statistics.

    Classifies market state into four regimes:
      - trending:       strong directional move (|mean return| > 1.5 std)
      - mean_reverting: oscillating within range (low autocorrelation, moderate vol)
      - volatile:       high dispersion without clear direction
      - quiet:          low vol, low activity

    Uses 60-sample rolling window of log returns, computing:
      - Realized volatility (annualized std of returns)
      - Hurst exponent proxy via rescaled range (R/S)
      - Return autocorrelation (lag-1)
    """

    def __init__(self, window=60):
        self.window = window
        self.prices = {}  # symbol -> deque of closes

    def update(self, symbol, price):
        import math
        from collections import deque
        if symbol not in self.prices:
            self.prices[symbol] = deque(maxlen=self.window + 1)
        self.prices[symbol].append(price)

    def classify(self, symbol):
        import math
        prices = self.prices.get(symbol, [])
        if len(prices) < 20:
            return {"regime": "unknown", "volatility_ann": 0, "hurst_proxy": 0.5,
                    "autocorr_lag1": 0, "regime_confidence": 0}

        returns = []
        p = list(prices)
        for i in range(1, len(p)):
            if p[i - 1] > 0 and p[i] > 0:
                returns.append(math.log(p[i] / p[i - 1]))

        if len(returns) < 10:
            return {"regime": "unknown", "volatility_ann": 0, "hurst_proxy": 0.5,
                    "autocorr_lag1": 0, "regime_confidence": 0}

        mean_r = sum(returns) / len(returns)
        var_r = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        std_r = math.sqrt(var_r) if var_r > 0 else 1e-10
        vol_ann = std_r * math.sqrt(252 * 24 * 12)  # 5-min bars

        # Hurst exponent proxy via rescaled range
        deviations = [sum(returns[:i+1]) - mean_r * (i + 1) for i in range(len(returns))]
        r_range = max(deviations) - min(deviations)
        hurst = math.log(r_range / std_r + 1e-10) / math.log(len(returns)) if std_r > 0 else 0.5

        # Lag-1 autocorrelation
        if len(returns) > 2:
            mean1 = sum(returns[:-1]) / (len(returns) - 1)
            mean2 = sum(returns[1:]) / (len(returns) - 1)
            cov = sum((returns[i] - mean1) * (returns[i+1] - mean2) for i in range(len(returns) - 1)) / (len(returns) - 1)
            var1 = sum((r - mean1) ** 2 for r in returns[:-1]) / (len(returns) - 1)
            var2 = sum((r - mean2) ** 2 for r in returns[1:]) / (len(returns) - 1)
            denom = math.sqrt(var1 * var2) if var1 > 0 and var2 > 0 else 1
            autocorr = cov / denom
        else:
            autocorr = 0

        # Classification
        abs_drift = abs(mean_r / std_r) if std_r > 0 else 0  # signal-to-noise

        if vol_ann > 0.8 and abs_drift < 0.5:
            regime = "volatile"
            confidence = min(vol_ann / 1.2, 1.0)
        elif abs_drift > 1.0 and hurst > 0.55:
            regime = "trending"
            confidence = min(abs_drift / 2.0, 1.0)
        elif autocorr < -0.15 and vol_ann < 0.6:
            regime = "mean_reverting"
            confidence = min(abs(autocorr) / 0.3, 1.0)
        elif vol_ann < 0.3:
            regime = "quiet"
            confidence = 1.0 - vol_ann / 0.3
        else:
            regime = "mean_reverting"
            confidence = 0.4

        return {
            "regime": regime,
            "volatility_ann": round(vol_ann, 4),
            "hurst_proxy": round(hurst, 3),
            "autocorr_lag1": round(autocorr, 4),
            "regime_confidence": round(confidence, 3),
        }


def compute_oi_change(current_oi, prev_oi):
    if prev_oi <= 0:
        return 0.0
    return (current_oi - prev_oi) / prev_oi


def build_feature_event(instrument, metrics, mom_5m, mom_1m, mom_15m,
                        volatility, volume, fear_greed, funding_rate,
                        oi_change, ml_score, ml_ready, news_sentiment,
                        regime_info=None):
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    epoch_ms = int(now.timestamp() * 1000)

    regime = regime_info or {}

    return {
        "eventId": str(uuid.uuid4()),
        "eventTime": now_str,
        "featureName": "obi",
        "featureVersion": "v1",
        "instrument": instrument,
        "windowStart": now_str,
        "windowEnd": now_str,
        "signalTimeMs": epoch_ms,
        "values": {
            # Core OBI
            "obi": metrics["obi"],
            "bidVolume": metrics["bid_vol"],
            "askVolume": metrics["ask_vol"],
            "spread": metrics["spread"],
            "midPrice": metrics["mid"],
            "levels": metrics["levels"],
            # Multi-timeframe momentum
            "momentum": mom_5m["momentum"],
            "momentum1m": mom_1m["momentum"],
            "momentum15m": mom_15m["momentum"],
            "emaFast": mom_5m["emaFast"],
            "emaSlow": mom_5m["emaSlow"],
            # Volatility (ATR-based)
            "volatility": volatility["volatility"],
            "atr": volatility["atr"],
            # Volume context
            "volumeRatio": volume["volumeRatio"],
            # Market sentiment
            "fearGreed": float(fear_greed),
            # Funding rate (per 8h period)
            "fundingRate": funding_rate,
            # Open interest change (% change since last poll)
            "oiChange": oi_change,
            # ML signal quality
            "mlScore": ml_score,
            "mlReady": 1.0 if ml_ready else 0.0,
            # News sentiment
            "newsSentiment": news_sentiment,
            # Regime detection
            "regimeVolAnn": regime.get("volatility_ann", 0),
            "regimeHurst": regime.get("hurst_proxy", 0.5),
            "regimeAutocorr": regime.get("autocorr_lag1", 0),
            "regimeConfidence": regime.get("regime_confidence", 0),
        },
    }


def connect_kafka(retries=30, delay=2):
    for attempt in range(1, retries + 1):
        try:
            producer = KafkaProducer(
                bootstrap_servers=KAFKA_BROKERS,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8"),
                acks="all",
                retries=3,
                retry_backoff_ms=500,
            )
            log.info("Connected to Kafka at %s", KAFKA_BROKERS)
            return producer
        except KafkaConnectionError:
            log.warning(
                "Kafka not ready (attempt %d/%d), retrying in %ds...",
                attempt, retries, delay,
            )
            time.sleep(delay)
    log.error("Failed to connect to Kafka after %d attempts", retries)
    sys.exit(1)


PRICE_TOPIC = os.environ.get("PRICE_TOPIC", "prices.realtime.v1")
WS_ENABLED = os.environ.get("WS_ENABLED", "true").lower() == "true"


def ws_price_feed(instruments_map, kafka_producer):
    """WebSocket thread: streams real-time trade prices from Binance.

    Connects to the combined stream endpoint for all symbols,
    publishes price ticks to a dedicated Kafka topic for sub-second
    exit monitoring by Huginn.
    """
    try:
        import websocket
    except ImportError:
        log.warning("websocket-client not installed, WS feed disabled")
        return

    streams = "/".join(f"{s.lower()}@aggTrade" for s in instruments_map)
    url = f"wss://stream.binance.com:9443/stream?streams={streams}"

    log.info("WebSocket connecting: %d streams", len(instruments_map))

    def on_message(ws, message):
        try:
            data = json.loads(message)
            payload = data.get("data", {})
            symbol = payload.get("s", "")
            instrument = instruments_map.get(symbol, "")
            if not instrument:
                return

            tick = {
                "type": "price_tick",
                "instrument": instrument,
                "price": float(payload.get("p", 0)),
                "quantity": float(payload.get("q", 0)),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trade_time": payload.get("T", 0),
                "is_buyer_maker": payload.get("m", False),
            }

            kafka_producer.send(
                PRICE_TOPIC,
                key=instrument.encode("utf-8"),
                value=json.dumps(tick).encode("utf-8"),
            )
        except Exception as e:
            log.debug("WS tick error: %s", e)

    def on_error(ws, error):
        log.warning("WebSocket error: %s", error)

    def on_close(ws, close_status, close_msg):
        log.info("WebSocket closed: %s %s", close_status, close_msg)

    def on_open(ws):
        log.info("WebSocket connected, streaming %d symbols", len(instruments_map))

    while not shutdown:
        try:
            ws = websocket.WebSocketApp(
                url,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
                on_open=on_open,
            )
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            log.warning("WebSocket reconnecting after error: %s", e)

        if not shutdown:
            time.sleep(5)

    log.info("WebSocket feed stopped")


def main():
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    instruments = {s: symbol_to_instrument(s) for s in SYMBOLS}

    log.info("=" * 60)
    log.info("  LIVE OBI BRIDGE — Multi-Layer Market Intelligence")
    log.info("=" * 60)
    log.info("  Symbols:    %s", ", ".join(f"{s} -> {i}" for s, i in instruments.items()))
    log.info("  Interval:   %ss", POLL_INTERVAL)
    log.info("  Levels:     top %d", BOOK_LEVELS)
    log.info("  Threshold:  +/-%s", OBI_THRESHOLD)
    log.info("  Layers:     OBI + MTF Momentum + Vol + Funding + OI + F&G + ML + News")
    log.info("  Topic:      %s", OBI_TOPIC)
    log.info("  WS feed:    %s -> %s", "enabled" if WS_ENABLED else "disabled", PRICE_TOPIC)
    log.info("  Kafka:      %s", KAFKA_BROKERS)
    log.info("=" * 60)

    producer = connect_kafka()

    if WS_ENABLED:
        ws_thread = threading.Thread(
            target=ws_price_feed, args=(instruments, producer), daemon=True
        )
        ws_thread.start()

    regime_detector = RegimeDetector(window=60)

    seq = 0
    stats = {s: {"published": 0, "errors": 0, "signals": 0} for s in SYMBOLS}
    start_time = time.time()

    fear_greed = 50
    last_fg_fetch = 0
    FG_INTERVAL = 600

    # Pre-fetch open interest baseline so first cycle has real OI change
    prev_oi = {}
    for symbol in SYMBOLS:
        try:
            prev_oi[symbol] = fetch_open_interest(symbol)
            log.info("  OI baseline %s: %.2f", symbol, prev_oi[symbol])
        except Exception:
            prev_oi[symbol] = 0.0

    while not shutdown:
        now = time.time()
        if now - last_fg_fetch > FG_INTERVAL:
            try:
                fear_greed = fetch_fear_greed()
                log.info("Fear & Greed Index: %d", fear_greed)
                last_fg_fetch = now
            except Exception as e:
                log.warning("Failed to fetch Fear & Greed: %s", e)

        for symbol, instrument in instruments.items():
            try:
                # Layer 1: Order book imbalance
                book = fetch_orderbook(symbol, limit=20)
                metrics = compute_obi(book, BOOK_LEVELS)

                # Layer 2: Multi-timeframe momentum
                klines_5m = fetch_klines(symbol, interval="5m", limit=30)
                klines_1m = fetch_klines(symbol, interval="1m", limit=30)
                klines_15m = fetch_klines(symbol, interval="15m", limit=30)
                mom_5m = compute_momentum(klines_5m)
                mom_1m = compute_momentum(klines_1m)
                mom_15m = compute_momentum(klines_15m)

                # Layer 3: Volatility regime
                volatility = compute_volatility(klines_5m)

                # Layer 4: Volume context
                ticker = fetch_ticker_24h(symbol)
                volume = compute_volume_context(klines_5m, ticker)

                # Layer 5: Funding rate (perpetual futures)
                funding_rate = fetch_funding_rate(symbol)

                # Layer 6: Open interest cascade detection
                current_oi = fetch_open_interest(symbol)
                oi_change = compute_oi_change(current_oi, prev_oi[symbol])
                prev_oi[symbol] = current_oi

                # Layer 7: ML signal quality prediction
                ml_score, ml_ready = fetch_ml_score(instrument)

                # Layer 8: News sentiment
                news_sentiment = fetch_news_sentiment(instrument)

                # Layer 9: Regime detection (rolling return statistics)
                regime_detector.update(symbol, metrics["mid"])
                regime_info = regime_detector.classify(symbol)

                event = build_feature_event(
                    instrument, metrics, mom_5m, mom_1m, mom_15m,
                    volatility, volume, fear_greed, funding_rate,
                    oi_change, ml_score, ml_ready, news_sentiment,
                    regime_info,
                )

                producer.send(OBI_TOPIC, key=instrument, value=event)
                producer.flush()
                stats[symbol]["published"] += 1
                seq += 1

                signal_marker = ""
                if abs(metrics["obi"]) > OBI_THRESHOLD:
                    stats[symbol]["signals"] += 1
                    direction = "SELL" if metrics["obi"] > 0 else "BUY"
                    signal_marker = f" <- {direction} SIGNAL"

                ml_tag = f"ML:{ml_score:.2f}" if ml_ready else "ML:--"
                fr_tag = f"FR:{funding_rate*100:+.4f}%"
                oi_tag = f"OI:{oi_change*100:+.1f}%"
                rg_tag = regime_info["regime"][:4].upper()
                log.info(
                    "[%4d] %s OBI:%+.4f M1:%+.4f M5:%+.4f M15:%+.4f "
                    "Vol:%s F&G:%d VR:%.1f %s %s %s News:%+.2f R:%s%s",
                    seq, instrument, metrics["obi"],
                    mom_1m["momentum"], mom_5m["momentum"], mom_15m["momentum"],
                    volatility["regime"], fear_greed, volume["volumeRatio"],
                    fr_tag, oi_tag, ml_tag, news_sentiment, rg_tag,
                    signal_marker,
                )
            except urllib.error.URLError as e:
                stats[symbol]["errors"] += 1
                log.warning("[%s] API error: %s", symbol, e)
            except Exception as e:
                stats[symbol]["errors"] += 1
                log.error("[%s] Unexpected error: %s", symbol, e)

        time.sleep(POLL_INTERVAL)

    producer.close()
    elapsed = time.time() - start_time
    log.info("=" * 60)
    log.info("  Session duration: %.0fs", elapsed)
    for symbol, s in stats.items():
        log.info(
            "  %s: published=%d errors=%d signals=%d",
            symbol, s["published"], s["errors"], s["signals"],
        )
    log.info("=" * 60)


if __name__ == "__main__":
    main()
