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
        for role in ("AUTHOR", "COMMITTER"):
            patch.setenv(f"GIT_{role}_NAME", "ledger test")
            patch.setenv(f"GIT_{role}_EMAIL", "test@norse-stack.invalid")
        yield


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A temporary bare remote and clone holding a synthetic registration, with a
    genesis entry (tests/ledger_fixtures.py). The harness refuses NORSE_* and RISK_*
    overrides, so none from the caller's shell reaches these tests."""
    from ledger_fixtures import World

    for key in list(os.environ):
        if key.startswith(("NORSE_", "RISK_")):
            monkeypatch.delenv(key)
    built = World(tmp_path)
    yield built
    built.close()
    shutil.rmtree(tmp_path / "work", ignore_errors=True)
