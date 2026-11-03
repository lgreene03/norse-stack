"""OBI decile test on muninn's recorded books: forward mid return by imbalance decile.

Usage: python3 -I scripts/research/obi_decile_test.py <venue_dir> <levels>
       (venue_dir comes from scripts/research/fetch-recorded-books.sh; levels is 10 for
       Binance depth20, 5 for OKX books5)

Reads muninn-raw JSONL.gz book snapshots (one venue), computes for every snapshot the
mid price and the top-N size imbalance (bid - ask) / (bid + ask), then the forward
mid-to-mid return in bps at several horizons, with the entry taken either at the
snapshot (latency 0) or 2 s later (huginn's measured decision latency). Reports the
mean forward return per OBI decile, and a t-statistic for the top and bottom deciles
computed on NON-OVERLAPPING samples only, so autocorrelated 100 ms rows cannot inflate it.
Gross of fees: the round-trip hurdle at retail tier is about 20 bps.
"""

import glob
import gzip
import json
import math
import os
import sys
from array import array
from bisect import bisect_left

HORIZONS = [1, 2, 5, 30, 60, 300, 900, 3600]
LATENCIES = [0.0, 2.0]
MAX_GAP = 5.0  # a forward point further than this past its target is a data gap: skip


def load(venue_dir, levels):
    files = sorted(glob.glob(os.path.join(venue_dir, "day=*", "hour=*", "*.jsonl.gz")))
    t, mid, obi = array("d"), array("d"), array("d")
    for path in files:
        with gzip.open(path, "rt") as fh:
            for line in fh:
                v = json.loads(line)["value"]
                bids, asks = v["bids"], v["asks"]
                if not bids or not asks:
                    continue
                b = sum(lv["size"] for lv in bids[:levels])
                a = sum(lv["size"] for lv in asks[:levels])
                if b + a <= 0:
                    continue
                t.append(float(v["eventTime"]))
                mid.append((bids[0]["price"] + asks[0]["price"]) / 2.0)
                obi.append((b - a) / (b + a))
    order = sorted(range(len(t)), key=t.__getitem__)  # files are in order; be certain
    return (
        array("d", (t[i] for i in order)),
        array("d", (mid[i] for i in order)),
        array("d", (obi[i] for i in order)),
        len(files),
    )


def mean_sd(xs):
    n = len(xs)
    if n < 2:
        return float("nan"), float("nan"), n
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return m, math.sqrt(var), n


def main():
    venue_dir, levels = sys.argv[1], int(sys.argv[2])
    t, mid, obi, nfiles = load(venue_dir, levels)
    n = len(t)
    span_h = (t[-1] - t[0]) / 3600 if n else 0
    gaps = sum(1 for i in range(1, n) if t[i] - t[i - 1] > MAX_GAP)
    print(
        f"venue={os.path.basename(venue_dir)} levels={levels} files={nfiles} snapshots={n} "
        f"span={span_h:.1f}h gaps>{MAX_GAP:.0f}s={gaps}"
    )
    srt = sorted(obi[::10])
    edges = [srt[int(len(srt) * k / 10)] for k in range(1, 10)]
    dec = array("b", (bisect_left(edges, x) for x in obi))

    for lat in LATENCIES:
        print(
            f"\n-- entry latency {lat:.0f}s: mean forward mid return (bps) by OBI decile "
            f"(D1 most ask-heavy, D10 most bid-heavy)"
        )
        print(
            "horizon  "
            + " ".join(f"{'D' + str(d + 1):>6}" for d in range(10))
            + "   D10-D1   t(D10)  t(D1)  nonoverlap_n"
        )
        for h in HORIZONS:
            sums, cnts = [0.0] * 10, [0] * 10
            j0 = j1 = 0
            nonover = {0: [], 9: []}
            next_free = -1e18
            for i in range(n):
                t_in, t_out = t[i] + lat, t[i] + lat + h
                while j0 < n and t[j0] < t_in:
                    j0 += 1
                while j1 < n and t[j1] < t_out:
                    j1 += 1
                if j1 >= n:
                    break
                if t[j0] - t_in > MAX_GAP or t[j1] - t_out > MAX_GAP:
                    continue
                r = (mid[j1] / mid[j0] - 1.0) * 1e4
                d = dec[i]
                sums[d] += r
                cnts[d] += 1
                if t[i] >= next_free:  # one sample per horizon window: independent-ish
                    if d in nonover:
                        nonover[d].append(r)
                    next_free = t[i] + lat + h
            means = [
                s / c if c else float("nan") for s, c in zip(sums, cnts, strict=True)
            ]
            m10, s10, n10 = mean_sd(nonover[9])
            m1, s1, n1 = mean_sd(nonover[0])
            t10 = m10 / (s10 / math.sqrt(n10)) if n10 > 1 and s10 > 0 else float("nan")
            t1 = m1 / (s1 / math.sqrt(n1)) if n1 > 1 and s1 > 0 else float("nan")
            print(
                f"{h:>6}s  "
                + " ".join(f"{m:6.2f}" for m in means)
                + f"   {means[9] - means[0]:6.2f}   {t10:6.2f} {t1:6.2f}  {n10}/{n1}"
            )


if __name__ == "__main__":
    main()
