"""Strategies the research harness runs (H1 and H2 arrive with G2.2 and G2.3).

Every module in this package is parsed by a test and by the harness before each run:
it may import only ``norse_research.harness.ALLOWED_IMPORTS`` and this package, and
may not name ``open``, ``eval``, ``exec``, ``__import__``, ``setattr``, ``type`` and
their kind, called or not (threat model T13). A strategy receives the frozen resolved
configuration and a ``DataView`` that loads rows through the registered loader; it gets
no store, path, environment or network, and the harness's tripwire refuses file and
socket access while it runs. The engine it imports is sealed for the run: it may
construct engine objects and call engine functions, but never change an engine
module, class or table.
"""
