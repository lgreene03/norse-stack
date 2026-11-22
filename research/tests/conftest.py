import io
import os
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def make_zip(name: str, text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


@pytest.fixture
def zip_of():
    return make_zip


@pytest.fixture(autouse=True, scope="session")
def hermetic_git(tmp_path_factory):
    """Git without the user's or the system's configuration and with a test identity,
    so the ledger's tests behave the same on any machine and in CI."""
    empty = tmp_path_factory.mktemp("gitconfig") / "config"
    empty.write_text("")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("GIT_CONFIG_GLOBAL", str(empty))
        patch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        patch.setenv("GIT_TERMINAL_PROMPT", "0")
        for program in ("GIT_ASKPASS", "SSH_ASKPASS"):  # an editor's terminal may set them
            patch.delenv(program, raising=False)
        for role in ("AUTHOR", "COMMITTER"):
            patch.setenv(f"GIT_{role}_NAME", "ledger test")
            patch.setenv(f"GIT_{role}_EMAIL", "test@norse-stack.invalid")
        yield


def clear_overrides(monkeypatch) -> None:
    """Remove every variable the harness refuses (NORSE_*, RISK_*, PYTHON* overrides and
    GIT_* outside its allowlist) that the caller's shell may hold."""
    from norse_research.harness import ENV_ALLOWED, ENV_REFUSED

    for key in list(os.environ):
        if ENV_REFUSED.match(key) and key not in ENV_ALLOWED:
            monkeypatch.delenv(key)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A temporary bare remote and clone holding a synthetic registration, with a
    genesis entry (tests/ledger_fixtures.py), and none of the overrides the harness
    refuses in the environment."""
    from ledger_fixtures import World

    clear_overrides(monkeypatch)
    built = World(tmp_path)
    yield built
    built.close()
    shutil.rmtree(tmp_path / "work", ignore_errors=True)


# --- outcomes, for the threat model's inventory test (T28, review L6) -------------------

OUTCOMES: dict[str, set[str]] = {}


def pytest_runtest_logreport(report):
    """Record each test function's outcome, whatever skipped it: a marker, a fixture or
    a hook. A parametrised test passes only if every case passed."""
    name = report.nodeid.rsplit("::", 1)[-1].split("[", 1)[0]
    outcome = report.outcome
    if hasattr(report, "wasxfail"):
        outcome = "xfailed"
    if report.when == "call" or outcome != "passed":
        OUTCOMES.setdefault(name, set()).add(outcome)
