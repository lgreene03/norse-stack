"""Strategies the research harness runs (H1 and H2 arrive with G2.2 and G2.3).

Every module in this package is parsed by a test and by the harness before each run:
it may import only ``norse_research.harness.ALLOWED_IMPORTS`` and this package, and
may not call ``open``, ``eval``, ``exec`` or ``__import__`` (threat model T13). A
strategy receives the frozen resolved configuration and a ``DataView`` that loads
rows through the registered loader; it gets no store, path, environment or network,
and the harness's tripwire refuses file and socket access while it runs.
"""
