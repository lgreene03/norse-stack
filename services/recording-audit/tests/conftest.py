"""Make services/recording-audit importable from its tests. Stdlib only: nothing to stub."""

import os
import sys

_SERVICE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_DIR not in sys.path:
    sys.path.insert(0, _SERVICE_DIR)
