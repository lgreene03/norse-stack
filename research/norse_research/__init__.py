"""Norse research code: public dataset coverage, loaders and manifests, and the
evaluation gate's statistics (docs/research/gate-statistics.md).

Everything here reads public market data only. There is no API key, no account and
no trading endpoint anywhere in this package.

``python -m norse_research.seal unseal`` and ``... candidate`` run the code of the commit
``--candidate-commit`` names, not HEAD's, or refuse: before Python imports ``seal.py`` or any
module of the seal or of data loading, the launcher below checks that commit against the
remote's ledger and runs them with its package (``relaunch``; security review of #70, H-3 and
round 2, H-4). For every other command line it does nothing.
"""


def _seal_launcher() -> None:
    import sys

    argv = getattr(sys, "orig_argv", ())
    if "norse_research.seal" not in argv and "-mnorse_research.seal" not in argv:
        return
    from .relaunch import relaunch_from_main

    relaunch_from_main(argv)


_seal_launcher()
del _seal_launcher
