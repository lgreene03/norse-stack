"""Test bootstrap for Bragi.

bragi.py imports `kafka` at module import time. The decision-log logic under
test (DecisionLog, dedup, liveness) does not touch Kafka, so we install
lightweight stub modules for `kafka` / `kafka.errors` before importing bragi.
This mirrors odin's conftest and lets the unit tests run wherever Python is
available without requiring the kafka-python wheel, while the real module is
still used in the running service.
"""

import os
import sys
import types

if "kafka" not in sys.modules:
    kafka_mod = types.ModuleType("kafka")

    class _KafkaConsumer:  # pragma: no cover - never instantiated in tests
        def __init__(self, *args, **kwargs):
            raise RuntimeError("KafkaConsumer should not be used in unit tests")

    class _KafkaProducer:  # pragma: no cover - never instantiated in tests
        def __init__(self, *args, **kwargs):
            raise RuntimeError("KafkaProducer should not be used in unit tests")

    kafka_mod.KafkaConsumer = _KafkaConsumer
    kafka_mod.KafkaProducer = _KafkaProducer

    errors_mod = types.ModuleType("kafka.errors")

    class _KafkaConnectionError(Exception):
        pass

    errors_mod.KafkaConnectionError = _KafkaConnectionError
    kafka_mod.errors = errors_mod

    sys.modules["kafka"] = kafka_mod
    sys.modules["kafka.errors"] = errors_mod

# Make services/bragi importable as `bragi`.
_BRAGI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BRAGI_DIR not in sys.path:
    sys.path.insert(0, _BRAGI_DIR)
