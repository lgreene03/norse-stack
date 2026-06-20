"""Test bootstrap for News Sentinel.

sentinel.py imports `feedparser` at module import time. The logic under test
(HeadlineStore, Ollama response parsing, liveness) does not touch feedparser,
so we install a lightweight stub before importing sentinel. This mirrors odin's
kafka stub and lets the unit tests run wherever Python is available without the
feedparser wheel, while the real module is still used in the running service.
"""

import os
import sys
import types

if "feedparser" not in sys.modules:
    fp = types.ModuleType("feedparser")

    def _parse(*args, **kwargs):  # pragma: no cover - never called in tests
        raise RuntimeError("feedparser.parse should not be used in unit tests")

    fp.parse = _parse
    sys.modules["feedparser"] = fp

# Make services/news-sentinel importable as `sentinel`.
_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)
