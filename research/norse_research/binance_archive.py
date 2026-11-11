"""Read-only client for Binance's public data archive, data.binance.vision.

Public market data only: no API key, no account, no trading endpoint. The archive is
an S3 bucket fronted by a CDN. Its listing is the bucket's anonymous ListObjects (v1)
XML, and every archive file has a sibling ``.CHECKSUM`` file holding its SHA-256.

The fetcher is deliberately gentle: kept-alive connections, a global minimum interval
between request starts (at most ten a second by default, whatever the number of
threads), a hard size cap on every response and a bounded retry. Most archive files
are cache misses that the CDN fetches from Tokyo, so each takes about a third of a
second; a few threads hide that latency without raising the request rate above the
pace set here.
"""

from __future__ import annotations

import hashlib
import http.client
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from dataclasses import dataclass

LISTING_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
DOWNLOAD_URL = "https://data.binance.vision/"
USER_AGENT = "norse-research/1 (public data; github.com/lgreene03/norse-stack)"
ALLOWED_HOSTS = frozenset({"data.binance.vision", "s3-ap-northeast-1.amazonaws.com"})

# A listing page is at most 1,000 keys, about 300 KB; a CHECKSUM file is under 200
# bytes. Archive files have their own caps, set by the caller per data type.
LISTING_MAX_BYTES = 2_000_000
CHECKSUM_MAX_BYTES = 1_024

_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_CHECKSUM_RE = re.compile(r"^([0-9a-f]{64})\s+\*?(\S+)\s*$")

Fetch = Callable[[str, int], bytes]


class ArchiveError(RuntimeError):
    """The archive returned something this client will not accept."""


class ArchiveNotFound(ArchiveError):
    """The archive has no such file (HTTP 404 or 403 from the bucket)."""


class SizeCapExceeded(ArchiveError):
    """A response was larger than the cap set for it."""


class ChecksumMismatch(ArchiveError):
    """A downloaded file does not match its published SHA-256."""


@dataclass(frozen=True)
class ArchiveObject:
    key: str
    size: int


@dataclass(frozen=True)
class ListingPage:
    objects: tuple[ArchiveObject, ...]
    prefixes: tuple[str, ...]
    truncated: bool
    next_marker: str | None


def _default_connect(host: str, timeout: float) -> http.client.HTTPConnection:
    return http.client.HTTPSConnection(host, timeout=timeout)


class HttpFetcher:
    """Rate-limited HTTPS GET with a size cap and bounded retries, safe across threads.

    ``min_interval`` seconds separate the start of consecutive requests however many
    threads share the fetcher; each thread keeps its own connection per host. Retries
    cover transient failures only (connection errors and 5xx). A 404 or any other
    4xx is raised at once, because a missing archive file must fail a load rather
    than be retried into silence.
    """

    def __init__(
        self,
        min_interval: float = 0.1,
        timeout: float = 60.0,
        retries: int = 3,
        backoff: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        connect: Callable[[str, float], http.client.HTTPConnection] = _default_connect,
    ) -> None:
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self._sleep = sleep
        self._clock = clock
        self._connect = connect
        self._pace_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._local = threading.local()
        self._all_conns: list[http.client.HTTPConnection] = []
        self._next_slot: float | None = None
        self.requests = 0
        self.bytes_received = 0

    def close(self) -> None:
        with self._stats_lock:
            for conn in self._all_conns:
                conn.close()
            self._all_conns.clear()

    def _pace(self) -> None:
        """Reserve the next start slot under the lock, then wait for it outside it."""
        with self._pace_lock:
            now = self._clock()
            slot = now if self._next_slot is None else max(now, self._next_slot)
            self._next_slot = slot + self.min_interval
        if slot > now:
            self._sleep(slot - now)

    @property
    def _conns(self) -> dict[str, http.client.HTTPConnection]:
        if not hasattr(self._local, "conns"):
            self._local.conns = {}
        return self._local.conns

    def _drop(self, host: str) -> None:
        conn = self._conns.pop(host, None)
        if conn is not None:
            conn.close()

    def _get_once(self, host: str, path: str, url: str, max_bytes: int) -> tuple[int, bytes]:
        conn = self._conns.get(host)
        if conn is None:
            conn = self._connect(host, self.timeout)
            self._conns[host] = conn
            with self._stats_lock:
                self._all_conns.append(conn)
        conn.request("GET", path, headers={"User-Agent": USER_AGENT})
        response = conn.getresponse()
        declared = response.getheader("Content-Length")
        if declared is not None and int(declared) > max_bytes:
            self._drop(host)
            raise SizeCapExceeded(f"{url}: {declared} bytes exceeds cap {max_bytes}")
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            self._drop(host)
            raise SizeCapExceeded(f"{url}: body exceeds cap {max_bytes}")
        if response.will_close:
            self._drop(host)
        return response.status, body

    def __call__(self, url: str, max_bytes: int) -> bytes:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
            raise ArchiveError(f"refusing to fetch outside the public archive: {url}")
        # Keys can hold non-ASCII symbols (the archive lists pairs named in Chinese),
        # which the request line must carry percent-encoded.
        path = urllib.parse.quote(parts.path) + (f"?{parts.query}" if parts.query else "")
        host = parts.hostname
        for attempt in range(1, self.retries + 2):
            self._pace()
            try:
                status, body = self._get_once(host, path, url, max_bytes)
            except (http.client.HTTPException, OSError) as exc:
                self._drop(host)
                if attempt > self.retries:
                    raise ArchiveError(f"{url}: {exc!r} after {attempt} attempts") from exc
                self._sleep(self.backoff * attempt)
                continue
            if status == 200:
                with self._stats_lock:
                    self.requests += 1
                    self.bytes_received += len(body)
                return body
            if status in (403, 404):
                raise ArchiveNotFound(f"{url}: HTTP {status}")
            if status < 500 or attempt > self.retries:
                raise ArchiveError(f"{url}: HTTP {status}")
            self._sleep(self.backoff * attempt)
        raise ArchiveError(f"{url}: retries exhausted")  # pragma: no cover


def parse_listing(xml_bytes: bytes) -> ListingPage:
    """Parse one page of the bucket's ListObjects (v1) XML.

    The listing comes from AWS over TLS; Python's expat refuses external entities and
    caps entity expansion, so a hostile page cannot read local files or explode.
    """
    root = ET.fromstring(xml_bytes)
    objects = tuple(
        ArchiveObject(
            key=node.findtext(f"{_S3_NS}Key", ""),
            size=int(node.findtext(f"{_S3_NS}Size", "0")),
        )
        for node in root.iter(f"{_S3_NS}Contents")
    )
    prefixes = tuple(
        node.findtext(f"{_S3_NS}Prefix", "") for node in root.iter(f"{_S3_NS}CommonPrefixes")
    )
    truncated = root.findtext(f"{_S3_NS}IsTruncated", "false") == "true"
    next_marker = root.findtext(f"{_S3_NS}NextMarker")
    if truncated and not next_marker:
        seen = [o.key for o in objects] + list(prefixes)
        next_marker = max(seen) if seen else None
    if truncated and not next_marker:
        raise ArchiveError("truncated listing with no marker to continue from")
    return ListingPage(objects, prefixes, truncated, next_marker)


def listing_url(prefix: str, marker: str | None = None) -> str:
    params = {"delimiter": "/", "prefix": prefix}
    if marker:
        params["marker"] = marker
    return f"{LISTING_URL}?{urllib.parse.urlencode(params)}"


def list_prefix(prefix: str, fetch: Fetch) -> tuple[list[ArchiveObject], list[str]]:
    """List every object and sub-prefix directly under ``prefix``, across pages."""
    objects: list[ArchiveObject] = []
    prefixes: list[str] = []
    marker: str | None = None
    for _ in range(10_000):
        page = parse_listing(fetch(listing_url(prefix, marker), LISTING_MAX_BYTES))
        objects.extend(page.objects)
        prefixes.extend(page.prefixes)
        if not page.truncated:
            return objects, prefixes
        if page.next_marker == marker:
            raise ArchiveError(f"listing of {prefix} is not advancing past {marker}")
        marker = page.next_marker
    raise ArchiveError(f"listing of {prefix} did not terminate")


def iter_archives(objects: list[ArchiveObject]) -> Iterator[ArchiveObject]:
    """The data files in a listing, without their CHECKSUM siblings."""
    for obj in objects:
        if obj.key.endswith(".zip"):
            yield obj


def parse_checksum(text: bytes, expected_name: str) -> str:
    """Return the SHA-256 from a ``.CHECKSUM`` file, checking it names the file."""
    # UTF-8, not ASCII: pairs named in Chinese carry their name into the CHECKSUM.
    line = text.decode("utf-8", errors="strict").strip()
    match = _CHECKSUM_RE.match(line)
    if not match:
        raise ArchiveError(f"unparseable CHECKSUM for {expected_name}: {line[:120]!r}")
    digest, name = match.groups()
    if name != expected_name:
        raise ArchiveError(f"CHECKSUM names {name}, expected {expected_name}")
    return digest


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download_verified(key: str, fetch: Fetch, max_bytes: int) -> tuple[bytes, str]:
    """Download one archive file and verify it against its published CHECKSUM.

    Returns the bytes and their SHA-256. Raises ChecksumMismatch on any difference,
    and ArchiveNotFound if either file is missing: a missing file is an error.
    """
    name = key.rsplit("/", 1)[-1]
    published = parse_checksum(fetch(DOWNLOAD_URL + key + ".CHECKSUM", CHECKSUM_MAX_BYTES), name)
    data = fetch(DOWNLOAD_URL + key, max_bytes)
    actual = sha256_hex(data)
    if actual != published:
        raise ChecksumMismatch(f"{key}: sha256 {actual} != published {published}")
    return data, actual
