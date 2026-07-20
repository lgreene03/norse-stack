"""Test bootstrap for Heimdall.

heimdall.py imports `kafka` at module import time. The HMM math and the regime
tracker under test never touch Kafka, so we install lightweight stub modules for
`kafka` / `kafka.errors` before importing heimdall. This lets the unit tests run
wherever Python + numpy are available (locally and in CI) without requiring the
kafka-python wheel, while the real module is still used in the running service.
"""

import os
import sys
import types

if "kafka" not in sys.modules:
    kafka_mod = types.ModuleType("kafka")

    class _KafkaConsumer:  # pragma: no cover - never instantiated in tests
        def __init__(self, *args, **kwargs):
            raise RuntimeError("KafkaConsumer should not be used in unit tests")

    kafka_mod.KafkaConsumer = _KafkaConsumer

    errors_mod = types.ModuleType("kafka.errors")

    class _KafkaConnectionError(Exception):
        pass

    errors_mod.KafkaConnectionError = _KafkaConnectionError
    kafka_mod.errors = errors_mod

    sys.modules["kafka"] = kafka_mod
    sys.modules["kafka.errors"] = errors_mod

# Make services/heimdall importable as `heimdall`.
_HEIMDALL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HEIMDALL_DIR not in sys.path:
    sys.path.insert(0, _HEIMDALL_DIR)
