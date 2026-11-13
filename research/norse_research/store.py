"""A verified local mirror of data.binance.vision files.

Every file is stored at ``<root>/data.binance.vision/<archive key>`` beside the
``.CHECKSUM`` file it was verified against when it was downloaded. Reading a file
re-verifies it, so a file corrupted or edited on disk is refused rather than used.
The data file is written before its CHECKSUM, each by an atomic rename, so the
presence of a CHECKSUM means the download completed.

The store downloads development files only. The holdout's files are kept off disk
until the unseal (G1.6), which adds the one permitted re-fetch; until then a file
dated 2026-01 or later is refused before anything is requested.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from .binance_archive import (
    CHECKSUM_MAX_BYTES,
    DOWNLOAD_URL,
    ArchiveObject,
    ChecksumMismatch,
    Fetch,
    list_prefix,
    parse_checksum,
    sha256_hex,
)
from .periods import LAST_DEVELOPMENT_MONTH, HoldoutSealed

# The month of a monthly (``-2024-01.zip``) or daily (``-2024-01-31.zip``) archive file.
FILE_MONTH = re.compile(r"-(\d{4}-\d{2})(?:-\d{2})?\.zip$")

MIRROR_DIR = "data.binance.vision"
LISTINGS_DIR = "listings"
DATASETS_ENV = "NORSE_DATASETS_DIR"


REPO_ROOT = Path(__file__).resolve().parents[2]


def _from_dotenv(path: Path) -> str:
    """``NORSE_DATASETS_DIR`` from a KEY=VALUE file such as norse-stack's .env, if set."""
    if not path.is_file():
        return ""
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == DATASETS_ENV:
            return value.strip().strip('"').strip("'")
    return ""


def datasets_root(env: dict[str, str] | None = None, dotenv: Path | None = None) -> Path:
    """The datasets directory: ``NORSE_DATASETS_DIR`` from the environment, else from
    the repository's git-ignored .env. Never a guessed default, so data cannot land
    somewhere unintended."""
    env = os.environ if env is None else env
    value = env.get(DATASETS_ENV, "").strip().strip('"')
    if not value:
        value = _from_dotenv(REPO_ROOT / ".env" if dotenv is None else dotenv)
    if not value:
        raise RuntimeError(
            f"{DATASETS_ENV} is not set. Point it at the datasets folder, in the "
            "environment or in norse-stack's git-ignored .env (see .env.example)."
        )
    root = Path(value)
    if not root.is_dir():
        raise RuntimeError(f"{DATASETS_ENV}={value} is not an existing directory")
    return root


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class ArchiveStore:
    def __init__(self, root: Path, fetch: Fetch | None = None) -> None:
        self.root = Path(root)
        self.fetch = fetch
        self.downloaded_bytes = 0
        self.downloaded_files = 0
        self._lock = threading.Lock()

    def mirror(
        self,
        items: Iterable[tuple[str, int]],
        workers: int = 6,
        progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """Mirror many ``(key, max_bytes)`` files, a few at a time; the fetcher sets
        the pace. The first failure stops the run and is raised."""
        todo = [(key, cap) for key, cap in items if not self.has(key)]
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(self.get, key, cap) for key, cap in todo]
            try:
                for future in as_completed(futures):
                    future.result()
                    done += 1
                    if progress and done % 500 == 0:
                        progress(done, len(todo))
            except BaseException:
                for future in futures:
                    future.cancel()
                raise

    def path(self, key: str) -> Path:
        if ".." in key.split("/") or key.startswith("/"):
            raise ValueError(f"unsafe archive key {key!r}")
        return self.root / MIRROR_DIR / key

    def has(self, key: str) -> bool:
        path = self.path(key)
        return path.is_file() and path.with_name(path.name + ".CHECKSUM").is_file()

    def read(self, key: str) -> tuple[bytes, str]:
        """Read a mirrored file, re-verifying it against its stored CHECKSUM."""
        path = self.path(key)
        published = parse_checksum(
            path.with_name(path.name + ".CHECKSUM").read_bytes(), path.name
        )
        data = path.read_bytes()
        actual = sha256_hex(data)
        if actual != published:
            raise ChecksumMismatch(f"{path}: sha256 {actual} != published {published}")
        return data, actual

    def get(self, key: str, max_bytes: int) -> tuple[bytes, str]:
        """Return a verified file, downloading it first if it is not mirrored yet."""
        if self.has(key):
            return self.read(key)
        if self.fetch is None:
            raise FileNotFoundError(f"{key} is not in the local mirror and fetching is off")
        month = FILE_MONTH.search(key)
        if month is None:
            raise ValueError(f"{key} is not a dated archive file")
        if month.group(1) > LAST_DEVELOPMENT_MONTH:
            raise HoldoutSealed(
                f"{key} is dated after the development period; the holdout's files stay "
                "off disk until the unseal (G1.6)"
            )
        path = self.path(key)
        checksum = self.fetch(DOWNLOAD_URL + key + ".CHECKSUM", CHECKSUM_MAX_BYTES)
        published = parse_checksum(checksum, path.name)
        data = self.fetch(DOWNLOAD_URL + key, max_bytes)
        actual = sha256_hex(data)
        if actual != published:
            raise ChecksumMismatch(f"{key}: sha256 {actual} != published {published}")
        _atomic_write(path, data)
        _atomic_write(path.with_name(path.name + ".CHECKSUM"), checksum)
        with self._lock:
            self.downloaded_bytes += len(data) + len(checksum)
            self.downloaded_files += 1
        return data, actual

    def _cached_listing(self, prefix: str, refresh: bool) -> dict:
        name = prefix.strip("/").replace("/", "__") + ".json"
        path = self.root / LISTINGS_DIR / name
        if path.is_file() and not refresh:
            return json.loads(path.read_text())
        if self.fetch is None:
            raise FileNotFoundError(f"no cached listing for {prefix} and fetching is off")
        objects, prefixes = list_prefix(prefix, self.fetch)
        record = {
            "prefix": prefix,
            "listed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "objects": [{"key": o.key, "size": o.size} for o in objects],
            "prefixes": prefixes,
        }
        _atomic_write(path, json.dumps(record, indent=0).encode())
        return record

    def listing(self, prefix: str, refresh: bool = False) -> list[ArchiveObject]:
        """The objects directly under ``prefix``, cached on disk with the date listed."""
        record = self._cached_listing(prefix, refresh)
        return [ArchiveObject(o["key"], o["size"]) for o in record["objects"]]

    def subdirectories(self, prefix: str, refresh: bool = False) -> list[str]:
        """The names of the sub-prefixes directly under ``prefix``, cached likewise."""
        record = self._cached_listing(prefix, refresh)
        return sorted(p[len(prefix) :].rstrip("/") for p in record["prefixes"])

    def prefetch_listings(self, prefixes: Iterable[str], workers: int = 4) -> None:
        """Fill the listing cache for many prefixes, a few at a time."""
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for _ in pool.map(lambda p: self._cached_listing(p, False), prefixes):
                pass

    def listed_at(self, prefix: str) -> str:
        return self._cached_listing(prefix, refresh=False)["listed_at"]
