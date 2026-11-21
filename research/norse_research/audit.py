"""Audit checks around the ledger whose decisions are unit-tested offline (G1.5).

Both need the network to run for real, so they are steps of the G2.7 ledger audit before
the unseal and the G3.4 attestation; here they take what they read as arguments.

* ``force_push_findings`` reads GitHub's repository activity record
  (``GET /repos/{owner}/{repo}/activity``), which keeps force pushes and branch
  deletions server side with the actor and the before and after commits. A force
  push or deletion on the ledger branch, or on a ``ledger-*`` or ``prereg-*`` tag,
  is a finding (threat model T10).
* ``upstream_manifest_audit`` compares every SHA-256 a manifest pins with the
  ``.CHECKSUM`` fetched afresh from data.binance.vision. A mirror file altered with its
  stored CHECKSUM and a manifest rebuilt to match all agree with each other; only
  the published checksum shows the change (T14). Fetching a CHECKSUM reads a hash
  and a file name, never a row.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

from .binance_archive import CHECKSUM_MAX_BYTES, DOWNLOAD_URL, Fetch, parse_checksum
from .formats import DatasetSpec
from .gitledger import LEDGER_BRANCH
from .manifest import Manifest

WATCHED_TAG_PREFIXES = ("refs/tags/ledger-", "refs/tags/prereg-")
REWRITES = frozenset({"force_push", "branch_deletion"})


def parse_activity(text: str | bytes) -> list[dict]:
    events = json.loads(text)
    if not isinstance(events, list):
        raise ValueError("an activity response is a list of events")
    return events


def force_push_findings(
    events: Iterable[Mapping], ledger_branch: str = LEDGER_BRANCH, since: str | None = None
) -> list[str]:
    """Force pushes and deletions of the ledger ref or a protected tag, at or after
    ``since`` (an ISO timestamp), from a recorded activity response."""
    ledger_ref = f"refs/heads/{ledger_branch}"
    findings = []
    for event in events:
        ref = event.get("ref") or ""
        kind = event.get("activity_type")
        when = event.get("timestamp") or ""
        if since is not None and when < since:
            continue
        if kind in REWRITES and (ref == ledger_ref or ref.startswith(WATCHED_TAG_PREFIXES)):
            actor = (event.get("actor") or {}).get("login", "unknown")
            findings.append(
                f"{when} {kind} on {ref} by {actor}: {event.get('before')} -> {event.get('after')}"
            )
    return findings


def upstream_manifest_audit(manifest: Manifest, fetch: Fetch) -> list[str]:
    """Every file whose pinned SHA-256 differs from its published CHECKSUM."""
    mismatches = []
    for name, entry in sorted(manifest.datasets.items()):
        spec = DatasetSpec.from_name(name)
        for month, digest, *_ in entry["files"]:
            key = spec.key(month)
            raw = fetch(DOWNLOAD_URL + key + ".CHECKSUM", CHECKSUM_MAX_BYTES)
            published = parse_checksum(raw, key.rsplit("/", 1)[-1])
            if published != digest:
                mismatches.append(f"{key}: the manifest pins {digest}, Binance publishes {published}")
    return mismatches
