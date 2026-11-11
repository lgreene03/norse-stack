import hashlib

import pytest
from norse_research.binance_archive import DOWNLOAD_URL, ChecksumMismatch
from norse_research.store import ArchiveStore, datasets_root

KEY = "data/spot/monthly/klines/X/1d/X-1d-2024-01.zip"


def archive_with(data: bytes, published: bytes | None = None):
    digest = hashlib.sha256(published if published is not None else data).hexdigest()
    files = {KEY: data, KEY + ".CHECKSUM": f"{digest}  X-1d-2024-01.zip\n".encode()}
    calls = []

    def fetch(url, max_bytes):
        calls.append(url)
        return files[url.removeprefix(DOWNLOAD_URL)]

    return fetch, calls


def test_get_downloads_verifies_and_then_serves_from_disk(tmp_path):
    fetch, calls = archive_with(b"payload")
    store = ArchiveStore(tmp_path, fetch)
    data, digest = store.get(KEY, 100)
    assert data == b"payload" and digest == hashlib.sha256(b"payload").hexdigest()
    assert store.has(KEY) and store.downloaded_files == 1
    assert store.get(KEY, 100) == (data, digest)
    assert len(calls) == 2  # the CHECKSUM and the file, once


def test_get_refuses_a_download_that_fails_its_checksum(tmp_path):
    fetch, _ = archive_with(b"payload", published=b"other")
    store = ArchiveStore(tmp_path, fetch)
    with pytest.raises(ChecksumMismatch):
        store.get(KEY, 100)
    assert not store.has(KEY)


def test_read_refuses_a_file_changed_on_disk(tmp_path):
    fetch, _ = archive_with(b"payload")
    store = ArchiveStore(tmp_path, fetch)
    store.get(KEY, 100)
    store.path(KEY).write_bytes(b"edited")
    with pytest.raises(ChecksumMismatch):
        store.read(KEY)


def test_offline_store_never_fetches(tmp_path):
    with pytest.raises(FileNotFoundError):
        ArchiveStore(tmp_path).get(KEY, 100)


def test_store_refuses_keys_that_escape_the_mirror(tmp_path):
    store = ArchiveStore(tmp_path)
    for key in ("../x.zip", "/etc/passwd", "data/../../x.zip"):
        with pytest.raises(ValueError):
            store.path(key)


def test_datasets_root_comes_from_env_then_dotenv_and_is_never_guessed(tmp_path):
    folder = tmp_path / "datasets"
    folder.mkdir()
    assert datasets_root({"NORSE_DATASETS_DIR": str(folder)}, tmp_path / "none") == folder
    dotenv = tmp_path / ".env"
    dotenv.write_text(f'MINIO_DATA_DIR=/x\nNORSE_DATASETS_DIR="{folder}"\n')
    assert datasets_root({}, dotenv) == folder
    with pytest.raises(RuntimeError, match="not set"):
        datasets_root({}, tmp_path / "none")
    with pytest.raises(RuntimeError, match="not an existing directory"):
        datasets_root({"NORSE_DATASETS_DIR": str(tmp_path / "absent")}, tmp_path / "none")
