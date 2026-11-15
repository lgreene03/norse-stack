"""What the process around a ledger write may and may not carry (G1.5; T15, review M5, N10).

A variable can change a result, or what git reads and writes, with no file at all. The
harness, the genesis step, the milestone tags and the freeze all refuse:

* ``NORSE_*`` (except ``NORSE_DATASETS_DIR``, which only says where the verified files
  are) and ``RISK_*``;
* every ``GIT_*`` outside a short allowlist (identity, dates, the global configuration
  file, prompts and the editor);
* ``PYTHONPATH``, ``PYTHONHOME``, ``PYTHONSTARTUP``, ``PYTHONHASHSEED``,
  ``PYTHONUSERBASE`` and ``PYTHONOPTIMIZE``, and an interpreter started with ``-O``,
  which strips assertions;
* a ``git`` found inside the repository, ahead of the real one on ``PATH``.

The strategy itself runs in a fresh interpreter with an empty environment.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

ENV_ALLOWED = frozenset(
    {
        "NORSE_DATASETS_DIR",
        "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_TERMINAL_PROMPT", "GIT_ASKPASS",
        "GIT_EDITOR", "GIT_PAGER", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE",
        "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE",
    }
)
ENV_REFUSED = re.compile(
    r"(NORSE|RISK|GIT)_|PYTHON(PATH|HOME|STARTUP|HASHSEED|USERBASE|OPTIMIZE)$"
)


class EnvironmentRefused(PermissionError):
    """The process carries an override that could change a result or a ledger write."""


def check_environment(
    environ: Mapping[str, str] | None = None, repo: Path | None = None
) -> None:
    environ = os.environ if environ is None else environ
    bad = sorted(k for k in environ if ENV_REFUSED.match(k) and k not in ENV_ALLOWED)
    if bad:
        raise EnvironmentRefused(f"environment overrides {bad} could change a result; unset them")
    if sys.flags.optimize:
        raise EnvironmentRefused("the interpreter runs with -O, which strips assertions")
    git = shutil.which("git", path=environ.get("PATH"))
    if git is None:
        raise EnvironmentRefused("no git on PATH")
    if repo is not None and Path(git).resolve().is_relative_to(Path(repo).resolve()):
        raise EnvironmentRefused(f"the git found on PATH, {git}, is inside the repository")


@lru_cache(maxsize=1)
def git_version() -> str:
    """Which git runs, recorded in every open entry (its version, not its local path)."""
    return subprocess.run(
        ["git", "--version"], capture_output=True, text=True, check=False
    ).stdout.strip()
