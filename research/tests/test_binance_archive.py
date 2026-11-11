import hashlib

import pytest
from norse_research import binance_archive as ba

NS = 'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"'


def listing_xml(keys, prefixes=(), truncated=False, next_marker=None):
    contents = "".join(f"<Contents><Key>{k}</Key><Size>{n}</Size></Contents>" for k, n in keys)
    common = "".join(f"<CommonPrefixes><Prefix>{p}</Prefix></CommonPrefixes>" for p in prefixes)
    marker = f"<NextMarker>{next_marker}</NextMarker>" if next_marker else ""
    flag = "true" if truncated else "false"
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><ListBucketResult {NS}>'
        f"<IsTruncated>{flag}</IsTruncated>{marker}{contents}{common}</ListBucketResult>"
    ).encode()


def test_parse_listing_reads_keys_sizes_and_prefixes():
    page = ba.parse_listing(listing_xml([("a/x.zip", 10), ("a/x.zip.CHECKSUM", 88)], ["a/b/"]))
    assert page.objects == (ba.ArchiveObject("a/x.zip", 10), ba.ArchiveObject("a/x.zip.CHECKSUM", 88))
    assert page.prefixes == ("a/b/",)
    assert not page.truncated
    assert [o.key for o in ba.iter_archives(list(page.objects))] == ["a/x.zip"]


def test_list_prefix_follows_markers_across_pages():
    pages = {
        None: listing_xml([("p/1.zip", 1)], truncated=True, next_marker="p/1.zip"),
        "p/1.zip": listing_xml([("p/2.zip", 2)], ["p/sub/"], truncated=True),
        "p/sub/": listing_xml([("p/3.zip", 3)]),
    }
    seen = []

    def fetch(url, max_bytes):
        assert max_bytes == ba.LISTING_MAX_BYTES
        marker = url.split("marker=")[1].replace("%2F", "/") if "marker=" in url else None
        seen.append(marker)
        return pages[marker]

    objects, prefixes = ba.list_prefix("p/", fetch)
    assert [o.key for o in objects] == ["p/1.zip", "p/2.zip", "p/3.zip"]
    assert prefixes == ["p/sub/"]
    assert seen == [None, "p/1.zip", "p/sub/"]


def test_list_prefix_refuses_a_listing_that_does_not_advance():
    def fetch(url, max_bytes):
        return listing_xml([("p/1.zip", 1)], truncated=True, next_marker="p/1.zip")

    with pytest.raises(ba.ArchiveError, match="not advancing"):
        ba.list_prefix("p/", fetch)


def test_parse_checksum_checks_format_and_name():
    digest = "a" * 64
    assert ba.parse_checksum(f"{digest}  X-1d-2024-01.zip\n".encode(), "X-1d-2024-01.zip") == digest
    with pytest.raises(ba.ArchiveError, match="names"):
        ba.parse_checksum(f"{digest}  Y.zip".encode(), "X-1d-2024-01.zip")
    with pytest.raises(ba.ArchiveError, match="unparseable"):
        ba.parse_checksum(b"not a checksum", "X.zip")


def fake_archive(files):
    def fetch(url, max_bytes):
        key = url.removeprefix(ba.DOWNLOAD_URL)
        if key not in files:
            raise ba.ArchiveNotFound(url)
        return files[key]

    return fetch


def test_download_verified_accepts_matching_and_refuses_mismatched_files():
    data = b"zip bytes"
    good = hashlib.sha256(data).hexdigest()
    files = {"d/X.zip": data, "d/X.zip.CHECKSUM": f"{good}  X.zip".encode()}
    assert ba.download_verified("d/X.zip", fake_archive(files), 100) == (data, good)
    files["d/X.zip"] = b"tampered"
    with pytest.raises(ba.ChecksumMismatch):
        ba.download_verified("d/X.zip", fake_archive(files), 100)
    with pytest.raises(ba.ArchiveNotFound):
        ba.download_verified("d/missing.zip", fake_archive(files), 100)


class FakeResponse:
    def __init__(self, status, body, length=None, will_close=False):
        self.status = status
        self._body = body
        self._length = length
        self.will_close = will_close

    def getheader(self, name):
        return str(self._length) if name == "Content-Length" and self._length is not None else None

    def read(self, n):
        return self._body[:n]


class FakeConnection:
    def __init__(self, script, log):
        self.script = script
        self.log = log
        self.closed = False

    def request(self, method, path, headers):
        self.log.append((method, path, headers["User-Agent"]))

    def getresponse(self):
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed = True


def make_fetcher(script, clock_values=None):
    log, sleeps = [], []
    times = iter(clock_values or [0.0] * 100)
    fetcher = ba.HttpFetcher(
        min_interval=1.0,
        retries=2,
        backoff=3.0,
        sleep=sleeps.append,
        clock=lambda: next(times),
        connect=lambda host, timeout: FakeConnection(script, log),
    )
    return fetcher, log, sleeps


def test_fetcher_refuses_hosts_outside_the_archive():
    fetcher, _, _ = make_fetcher([])
    for url in ("https://api.binance.com/api/v3/order", "http://data.binance.vision/x"):
        with pytest.raises(ba.ArchiveError, match="refusing"):
            fetcher(url, 10)


def test_fetcher_returns_body_and_counts_it():
    fetcher, log, _ = make_fetcher([FakeResponse(200, b"abc", 3)])
    assert fetcher("https://data.binance.vision/data/x.zip", 10) == b"abc"
    assert fetcher.requests == 1 and fetcher.bytes_received == 3
    assert log[0][:2] == ("GET", "/data/x.zip")


def test_fetcher_percent_encodes_non_ascii_keys():
    fetcher, log, _ = make_fetcher([FakeResponse(200, b"a")])
    fetcher("https://data.binance.vision/data/币安人生USDT.zip", 10)
    assert log[0][1] == "/data/%E5%B8%81%E5%AE%89%E4%BA%BA%E7%94%9FUSDT.zip"


def test_fetcher_enforces_the_size_cap_declared_or_not():
    fetcher, _, _ = make_fetcher([FakeResponse(200, b"x" * 20, 20), FakeResponse(200, b"x" * 20)])
    with pytest.raises(ba.SizeCapExceeded):
        fetcher("https://data.binance.vision/a", 10)
    with pytest.raises(ba.SizeCapExceeded):
        fetcher("https://data.binance.vision/a", 10)


def test_fetcher_does_not_retry_a_missing_file():
    fetcher, log, _ = make_fetcher([FakeResponse(404, b""), FakeResponse(200, b"late")])
    with pytest.raises(ba.ArchiveNotFound):
        fetcher("https://data.binance.vision/a", 10)
    assert len(log) == 1


def test_fetcher_retries_server_errors_and_dropped_connections_then_gives_up():
    script = [FakeResponse(503, b""), OSError("reset"), FakeResponse(200, b"ok")]
    fetcher, log, sleeps = make_fetcher(script)
    assert fetcher("https://data.binance.vision/a", 10) == b"ok"
    assert len(log) == 3
    assert 3.0 in sleeps and 6.0 in sleeps
    fetcher, _, _ = make_fetcher([FakeResponse(500, b"")] * 3)
    with pytest.raises(ba.ArchiveError, match="HTTP 500"):
        fetcher("https://data.binance.vision/a", 10)


def test_fetcher_paces_consecutive_requests():
    script = [FakeResponse(200, b"a"), FakeResponse(200, b"b")]
    fetcher, _, sleeps = make_fetcher(script, clock_values=[10.0, 10.25, 11.0])
    fetcher("https://data.binance.vision/a", 10)
    fetcher("https://data.binance.vision/b", 10)
    assert sleeps == [pytest.approx(0.75)]
