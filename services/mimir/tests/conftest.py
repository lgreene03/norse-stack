"""Test bootstrap for Mimir.

mimir.py imports `kafka` at module import time. The feature-store logic under
test (FeatureStore) does not touch Kafka, so we install lightweight stub
modules for `kafka` / `kafka.errors` before importing mimir. This lets the unit
tests run wherever Python is available (locally and in CI) without requiring
the kafka-python wheel, while the real module is still used in the running
service.

Mirrors services/odin/tests/conftest.py.
"""

import os
import sys
import types

# Stub the kafka package so `from kafka import KafkaConsumer` succeeds.
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

# Make services/mimir importable as `mimir`.
_MIMIR_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _MIMIR_DIR not in sys.path:
    sys.path.insert(0, _MIMIR_DIR)
