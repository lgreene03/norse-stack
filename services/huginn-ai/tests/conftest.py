"""Test bootstrap for Huginn-AI.

huginn_ai.py imports `kafka` at module import time. The ML logic under test
(ModelManager, feature extraction, FIFO labeling) does not touch Kafka, so we
install lightweight stub modules for `kafka` / `kafka.errors` before importing
huginn_ai. This mirrors odin's conftest and lets the unit tests run wherever
Python + numpy/sklearn/xgboost are available (locally and in CI) without
requiring the kafka-python wheel, while the real module is still used in the
running service.

We also point MODEL_DIR at a throwaway temp directory so the persistence tests
never write into a real volume.
"""

import os
import sys
import tempfile
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

# Isolate persistence to a temp dir before huginn_ai reads MODEL_DIR at import.
os.environ.setdefault(
    "MODEL_DIR", os.path.join(tempfile.gettempdir(), "huginn-ai-test")
)

# Make services/huginn-ai importable as `huginn_ai`.
_HUGINN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HUGINN_DIR not in sys.path:
    sys.path.insert(0, _HUGINN_DIR)
