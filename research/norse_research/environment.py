"""What the process around a ledger write may and may not carry (G1.5; T15, review M5, N10).

A variable can change a result, or what git reads and writes, with no file at all. The
harness, the genesis step, the milestone tags and the seal all refuse:

* ``NORSE_*`` (except ``NORSE_DATASETS_DIR``, which only says where the verified files
  are) and ``RISK_*``;
* every ``GIT_*`` outside a short allowlist (identity, dates, the global configuration
  file, prompts and the editor);
* ``PYTHONPATH``, ``PYTHONHOME``, ``PYTHONSTARTUP``, ``PYTHONHASHSEED``,
  ``PYTHONUSERBASE`` and ``PYTHONOPTIMIZE``, and an interpreter started with ``-O``,
  which strips assertions;
* a ``git`` found inside the repository, ahead of the real one on ``PATH``.

The strategy itself runs in a fresh interpreter with an empty environment.

**The ledger's git** (security review of #65, M-3 and L-6; review of #70, L-6). Every git
command the ledger, the seal and the seal's launcher run is ``GIT_PROGRAM``,
``/usr/bin/git``, never one found on ``PATH``, and only if it, its exec path, the
osxkeychain credential helper there and every folder above them belong to root and are
writable by no group or other user (``git_program``), so the owner's user, and with it any
agent, cannot replace them. On the owner's Mac ``/usr/bin/git`` is Apple's SIP-protected
launcher for the Command Line Tools' git, whose exec path holds Apple's own osxkeychain
helper. Each command runs with ``HARDENED`` settings first, ``PINNED_CONFIGURATION`` (no
global or system configuration file, no replacement objects) and an environment built
from ``PASSED_ENVIRONMENT`` alone (``git_environment``). These live here, not in
``gitledger``, so the seal's launcher (``relaunch``) can run git before it imports any
module of the seal or of data loading.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

# The one git binary every ledger and seal command runs (review of #70, L-6).
GIT_PROGRAM = "/usr/bin/git"
# Every git command runs with these first: hooks and the file system monitor would run a
# program the clone chooses inside a ledger command (security review of #65, H-1).
HARDENED = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")
# The configuration every ledger git command runs with, whatever the caller's environment
# says: no global or system configuration file (review of #65, M-3) and no replacement
# objects (M5). Nothing a caller passes can override these.
PINNED_CONFIGURATION = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
}
# The only variables a ledger git command inherits, besides PATH, which is rebuilt
# (git_environment): the home folder (for ssh and the keychain), the locale, the temporary
# folder, an ssh agent's socket and the switch that turns git's own prompt off. Nothing
# that moves the configuration (XDG_CONFIG_HOME), routes the network (HTTPS_PROXY,
# ALL_PROXY), changes trust (SSL_CERT_FILE, CURL_CA_BUNDLE), loads code
# (DYLD_INSERT_LIBRARIES) or chooses the developer tools (DEVELOPER_DIR) reaches git
# (review of #65, L-6).
PASSED_ENVIRONMENT = (
    "HOME", "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "TMPDIR", "SSH_AUTH_SOCK",
    "GIT_TERMINAL_PROMPT",
)
_HELPER = "git-credential-osxkeychain"

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


def _writable_by_others(path: Path) -> str | None:
    """Why ``path``, or a folder above it, could be changed by someone other than root,
    or None: each must belong to root and grant no group or other write permission."""
    for part in (path, *path.parents):
        info = os.stat(part)
        if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return f"{part} is not root's alone"
    return None


def _clean_environment(environ: Mapping[str, str], program: str) -> dict[str, str]:
    passed = {name: environ[name] for name in PASSED_ENVIRONMENT if name in environ}
    path = os.pathsep.join([str(Path(program).parent), os.defpath])
    return {**passed, "PATH": path, **PINNED_CONFIGURATION}


@lru_cache(maxsize=4)
def git_program(program: str = GIT_PROGRAM) -> str:
    """``program``, after checking that neither it, nor the exec path it reports, nor the
    osxkeychain credential helper there, nor any folder above them, can be changed by the
    owner's user: each belongs to root and is writable by no group or other user. Checked
    once per process. Raises ``EnvironmentRefused`` otherwise (review of #70, L-6)."""
    path = Path(program)
    if not path.is_absolute() or not path.is_file():
        raise EnvironmentRefused(f"the ledger's git, {program}, does not exist")
    problem = _writable_by_others(path.resolve())
    if problem is None:
        proc = subprocess.run(
            [program, "--exec-path"], capture_output=True, text=True, check=False,
            env=_clean_environment(os.environ, program),
        )
        exec_path = Path(proc.stdout.strip())
        if proc.returncode or not exec_path.is_absolute() or not exec_path.is_dir():
            problem = f"it reports no exec path ({proc.stderr.strip()[:200]})"
        else:
            helper = exec_path / _HELPER
            problem = _writable_by_others(exec_path.resolve()) or (
                _writable_by_others(helper.resolve()) if helper.exists() else None
            )
    if problem:
        raise EnvironmentRefused(
            f"the ledger's git, {program}, is refused: {problem}, so the owner's user, and "
            "any agent, could replace what runs inside the ledger's commands (review of #70, "
            "L-6)"
        )
    return program


def git_environment(environ: Mapping[str, str] | None = None) -> tuple[str, dict[str, str]]:
    """The git every ledger command runs, ``git_program()``, never one found on ``PATH``,
    and the environment it runs with: ``PASSED_ENVIRONMENT`` from the caller, ``PATH``
    rebuilt as that git's folder followed by the system's default, and
    ``PINNED_CONFIGURATION`` (security review of #65, M-3 and L-6; review of #70, L-6)."""
    program = git_program()
    return program, _clean_environment(os.environ if environ is None else environ, program)


@lru_cache(maxsize=1)
def git_version() -> str:
    """Which git runs, recorded in every open entry (its version, not its local path):
    the ledger's own git, ``git_program()``."""
    program, environ = git_environment()
    return subprocess.run(
        [program, "--version"], capture_output=True, text=True, check=False, env=environ,
    ).stdout.strip()
