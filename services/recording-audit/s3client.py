"""A minimal S3 client for MinIO: list, get and (for tests) put, signed with SigV4.

Standard library only. The audit needs two calls, ListObjectsV2 and GetObject, and a
dependency the size of boto3 for those two would be most of the image and most of
the supply chain to audit. The signer is checked against the worked examples in the
AWS documentation (tests/test_s3client.py) and, in CI, against a real S3 server
(tests/test_s3_contract.py).

Credentials are read from files (``S3_ACCESS_KEY_FILE``, ``S3_SECRET_KEY_FILE``) or,
failing that, from the environment (``S3_ACCESS_KEY``, ``S3_SECRET_KEY``), never
from a command line. They are never logged and never included in an exception.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
# RFC 3986 unreserved characters; everything else is percent-encoded when signing.
_UNRESERVED = "-_.~"


class ConfigError(ValueError):
    """A required setting is missing or unreadable."""


class S3Error(RuntimeError):
    """An S3 request failed. Carries the HTTP status and S3 error code, never a secret."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"S3 request failed: HTTP {status} {code}: {message}")
        self.status = status
        self.code = code


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    etag: str


@dataclass(frozen=True)
class Credentials:
    access_key: str
    secret_key: str

    def __repr__(self) -> str:  # never print the secret, even by accident
        return "Credentials(access_key=<redacted>, secret_key=<redacted>)"


def load_credentials(env: Mapping[str, str]) -> Credentials:
    """Read the access and secret keys, preferring ``*_FILE`` over a plain variable."""
    values = {}
    for name in ("S3_ACCESS_KEY", "S3_SECRET_KEY"):
        path = env.get(f"{name}_FILE", "").strip()
        if path:
            try:
                value = Path(path).read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ConfigError(f"{name}_FILE could not be read ({exc.strerror})") from None
        else:
            value = env.get(name, "").strip()
        if not value:
            raise ConfigError(f"set {name}_FILE (preferred) or {name}")
        values[name] = value
    return Credentials(values["S3_ACCESS_KEY"], values["S3_SECRET_KEY"])


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def canonical_uri(path: str) -> str:
    """Percent-encode each segment of an object path as S3's SigV4 expects."""
    return urllib.parse.quote(path, safe="/" + _UNRESERVED)


def canonical_query(params: list[tuple[str, str]]) -> str:
    pairs = sorted(
        (urllib.parse.quote(k, safe=_UNRESERVED), urllib.parse.quote(v, safe=_UNRESERVED))
        for k, v in params
    )
    return "&".join(f"{k}={v}" for k, v in pairs)


def sign(
    *,
    method: str,
    uri: str,
    query: str,
    headers: Mapping[str, str],
    payload_sha256: str,
    credentials: Credentials,
    region: str,
    amz_date: str,
    service: str = "s3",
) -> str:
    """Return the Authorization header value for one request (AWS Signature Version 4).

    ``uri`` and ``query`` must already be canonical (see canonical_uri and
    canonical_query), and ``headers`` must include every header to be signed,
    including host, x-amz-date and x-amz-content-sha256.
    """
    lowered = {k.lower(): " ".join(str(v).split()) for k, v in headers.items()}
    names = sorted(lowered)
    canonical_headers = "".join(f"{n}:{lowered[n]}\n" for n in names)
    signed_headers = ";".join(names)
    canonical_request = "\n".join(
        [method, uri, query, canonical_headers, signed_headers, payload_sha256]
    )
    date = amz_date[:8]
    scope = f"{date}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    key = _hmac(f"AWS4{credentials.secret_key}".encode(), date)
    for part in (region, service, "aws4_request"):
        key = _hmac(key, part)
    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return (
        f"AWS4-HMAC-SHA256 Credential={credentials.access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )


class S3Client:
    """Path-style S3 access to one bucket, as MinIO serves it."""

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        credentials: Credentials,
        region: str = "us-east-1",
        timeout: float = 30.0,
        retries: int = 3,
    ):
        parts = urllib.parse.urlsplit(endpoint)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ConfigError("S3_ENDPOINT must be an http(s) URL such as http://minio:9000")
        self._base = f"{parts.scheme}://{parts.netloc}"
        self._host = parts.netloc
        self.bucket = bucket
        self._credentials = credentials
        self._region = region
        self._timeout = timeout
        self._retries = retries

    def _request(
        self,
        method: str,
        path: str,
        params: list[tuple[str, str]] | None = None,
        body: bytes = b"",
    ) -> bytes:
        uri = canonical_uri(path)
        query = canonical_query(params or [])
        payload = hashlib.sha256(body).hexdigest() if body else EMPTY_SHA256
        url = self._base + uri + (f"?{query}" if query else "")
        attempt = 0
        while True:
            amz_date = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
            headers = {
                "host": self._host,
                "x-amz-date": amz_date,
                "x-amz-content-sha256": payload,
            }
            auth = sign(
                method=method,
                uri=uri,
                query=query,
                headers=headers,
                payload_sha256=payload,
                credentials=self._credentials,
                region=self._region,
                amz_date=amz_date,
            )
            request = urllib.request.Request(url, data=body or None, method=method)
            for name, value in headers.items():
                if name != "host":  # urllib sets Host itself, to the same netloc
                    request.add_header(name, value)
            request.add_header("Authorization", auth)
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                error = _s3_error(exc)
                if exc.code < 500 or attempt >= self._retries:
                    raise error from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt >= self._retries:
                    reason = getattr(exc, "reason", exc)
                    raise S3Error(0, "Unreachable", f"{self._base}: {reason}") from None
            attempt += 1
            time.sleep(min(2**attempt * 0.25, 4.0))

    def list_objects(self, prefix: str, page_size: int | None = None) -> Iterator[StoredObject]:
        """Every object under ``prefix``, following continuation tokens."""
        token = None
        while True:
            params = [("list-type", "2"), ("prefix", prefix)]
            if page_size:
                params.append(("max-keys", str(page_size)))
            if token:
                params.append(("continuation-token", token))
            root = ET.fromstring(self._request("GET", f"/{self.bucket}", params))
            for item in root.iter(f"{_S3_NS}Contents"):
                yield StoredObject(
                    key=item.findtext(f"{_S3_NS}Key", ""),
                    size=int(item.findtext(f"{_S3_NS}Size", "0")),
                    etag=item.findtext(f"{_S3_NS}ETag", "").strip('"'),
                )
            truncated = root.findtext(f"{_S3_NS}IsTruncated", "false") == "true"
            token = root.findtext(f"{_S3_NS}NextContinuationToken")
            if not truncated or not token:
                return

    def get_object(self, key: str) -> bytes:
        return self._request("GET", f"/{self.bucket}/{key}")

    # The two calls below exist for the contract test, which builds its own bucket.
    # The service never writes to the store.
    def create_bucket(self) -> None:
        try:
            self._request("PUT", f"/{self.bucket}")
        except S3Error as exc:
            if exc.code != "BucketAlreadyOwnedByYou":
                raise

    def put_object(self, key: str, body: bytes) -> None:
        self._request("PUT", f"/{self.bucket}/{key}", body=body)


def _s3_error(exc: urllib.error.HTTPError) -> S3Error:
    code, message = "HTTPError", exc.reason or ""
    try:
        root = ET.fromstring(exc.read())
        code = root.findtext("Code", code)
        message = root.findtext("Message", message)
    except (ET.ParseError, OSError):
        pass
    return S3Error(exc.code, str(code), str(message))
