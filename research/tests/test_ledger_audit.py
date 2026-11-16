"""Audit decisions around the ledger, tested offline (G1.5).

Threat model T10 (the force-push audit, against a recorded GitHub activity response),
T14 (the upstream manifest audit, with a fake fetcher) and T27 (the REST register).
"""

import copy
import hashlib
import json
from pathlib import Path

from ledger_fixtures import REAL_PREREG_JSON, Book
from norse_research.audit import force_push_findings, parse_activity, upstream_manifest_audit
from norse_research.binance_archive import DOWNLOAD_URL
from norse_research.formats import DatasetSpec
from norse_research.manifest import Manifest, build, write
from norse_research.periods import DEVELOPMENT
from norse_research.store import ArchiveStore
from synthetic_store import make_series, month_times, put

FIXTURES = Path(__file__).resolve().parent / "fixtures"
RESEARCH = Path(__file__).resolve().parent.parent

# Recorded on 2026-11-14 from GET /repos/lgreene03/norse-stack/activity
# ?activity_type=force_push: two force pushes, neither to the ledger or a tag.
RECORDED = FIXTURES / "github-activity-force-push-2026-11-14.json"


def test_force_push_audit_flags_a_force_push_to_the_ledger_ref():
    events = parse_activity(RECORDED.read_text())
    assert [e["activity_type"] for e in events] == ["force_push", "force_push"]
    assert force_push_findings(events) == []
    rewritten = copy.deepcopy(events)
    rewritten[0]["ref"] = "refs/heads/ledger"
    findings = force_push_findings(rewritten)
    assert len(findings) == 1
    assert "force_push on refs/heads/ledger by lgreene03" in findings[0]
    assert events[0]["before"] in findings[0] and events[0]["after"] in findings[0]
    # A deletion of the ledger branch, or a move of a protected tag, is flagged too.
    for ref, kind in (("refs/heads/ledger", "branch_deletion"),
                      ("refs/tags/prereg-v1", "force_push"),
                      ("refs/tags/ledger-freeze", "force_push")):
        event = {**events[1], "ref": ref, "activity_type": kind}
        assert force_push_findings([event]), (ref, kind)
    # An ordinary push to the ledger is not.
    assert force_push_findings([{**events[1], "ref": "refs/heads/ledger",
                                 "activity_type": "push"}]) == []
    # Only events at or after the audit's start count.
    assert force_push_findings(rewritten, since="2026-11-14T00:00:00Z") == []


def test_upstream_audit_detects_a_mirror_and_manifest_altered_together(tmp_path):
    store = ArchiveStore(tmp_path / "mirror")
    spec, months = make_series(store, "spot/klines/XUSDT/1d", "2025-11", "2025-12")
    published = {}
    for month in months:
        path = store.path(spec.key(month))
        published[spec.key(month)] = path.with_name(path.name + ".CHECKSUM").read_bytes()

    def fetch(url: str, max_bytes: int) -> bytes:  # Binance serving its published checksums
        assert url.startswith(DOWNLOAD_URL) and url.endswith(".CHECKSUM")
        return published[url[len(DOWNLOAD_URL) : -len(".CHECKSUM")]]

    manifest_path = tmp_path / "development.json"
    write(build(store, DEVELOPMENT, [(spec, months)]), manifest_path)
    assert upstream_manifest_audit(Manifest.load(manifest_path), fetch) == []
    # Alter a mirrored file and its stored CHECKSUM, then rebuild the manifest from the
    # mirror: the mirror, its checksums and the manifest all agree again.
    times = month_times(spec, "2025-12")
    put(store, spec, "2025-12", times[:-1])
    write(build(store, DEVELOPMENT, [(spec, months)]), manifest_path)
    rebuilt = Manifest.load(manifest_path)
    altered = store.read(spec.key("2025-12"))[1]
    assert rebuilt.datasets[spec.name]["files"][-1][1] == altered
    findings = upstream_manifest_audit(rebuilt, fetch)
    assert len(findings) == 1
    assert spec.key("2025-12") in findings[0] and altered in findings[0]
    assert DatasetSpec.from_name(spec.name).key("2025-11") not in findings[0]


def test_rest_register_names_the_registered_development_manifest():
    register = json.loads((RESEARCH / "manifests" / "rest-differences.json").read_text())
    registered = json.loads(REAL_PREREG_JSON.read_text())["data"]["development_manifest"]
    assert register["manifest_sha256"] == registered["sha256"]
    committed = (RESEARCH / "manifests" / "binance-vision-development.json").read_bytes()
    assert hashlib.sha256(committed).hexdigest() == registered["sha256"]
    # The genesis entry pins the same hash from the registration.
    book = Book(REAL_PREREG_JSON.read_bytes())
    assert book.entries[0].body["manifests"]["development"] == register["manifest_sha256"]
