"""The threat model's adversarial tests exist and run (G1.5, threat model T28).

docs/research/ledger-threat-model.md names, under "Adversarial test inventory", the
tests G1.5 and G1.6 implement. For G1.5 and G1.6, which this package delivers, and for
every goal docs/ITERATION_GOALS.md marks done, each named test must exist, be collected
by pytest, and be neither skipped nor expected to fail. The two sensitivity tests that
section 15, item 11 of docs/PREREGISTRATION.md assigns to G1.5 are held to the same
rule, and each must have run and passed, so a skip by a fixture or a hook, or a
deselection, fails it too. A pull request that removes a test can also edit the table;
review sees both.
"""

import ast
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree

TESTS = Path(__file__).resolve().parent
RESEARCH = TESTS.parent
DOCS = RESEARCH.parent / "docs"
THREAT_MODEL = DOCS / "research" / "ledger-threat-model.md"
GOALS = DOCS / "ITERATION_GOALS.md"
ALWAYS = {"G1.5", "G1.6"}  # delivered here, whatever the register says yet
PREREGISTRATION_TESTS = {
    "G1.5": {
        "test_sensitivity_run_before_its_hypothesis_is_complete_is_refused",
        "test_sensitivity_run_of_a_non_candidate_is_refused",
    }
}
SKIPPING = {"skip", "skipif", "xfail", "importorskip"}


def inventory() -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    goal = None
    for line in THREAT_MODEL.read_text().splitlines():
        heading = re.match(r"### (G\d+\.\d+),", line)
        if heading:
            goal = heading.group(1)
            continue
        if line.startswith("### ") or line.startswith("## "):
            goal = None
        row = re.match(r"\| `(test_[a-z0-9_]+)` \|", line)
        if goal and row:
            tables.setdefault(goal, set()).add(row.group(1))
    return tables


def done_goals() -> set[str]:
    done = set()
    for line in GOALS.read_text().splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 5 and re.fullmatch(r"G\d+\.\d+", cells[1]) and cells[5].startswith("Done"):
            done.add(cells[1])
    return done


def collected() -> set[str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "--rootdir", str(RESEARCH), str(TESTS)],
        capture_output=True, text=True, check=False, cwd=RESEARCH,
    )
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    names = set()
    for line in proc.stdout.splitlines():
        if "::" in line:
            names.add(line.rsplit("::", 1)[1].split("[", 1)[0])
    return names


def _skips(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr in SKIPPING:
            return True
        if isinstance(sub, ast.Name) and sub.id in SKIPPING:
            return True
    return False


def skipped_or_expected_to_fail() -> set[str]:
    """Test functions that carry, or call, a skip or an expected failure."""
    found = set()
    for path in sorted(TESTS.glob("test_*.py")):
        tree = ast.parse(path.read_text())
        module_marked = any(
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets)
            and _skips(node.value)
            for node in tree.body
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                if module_marked or any(_skips(d) for d in node.decorator_list) or any(
                    _skips(statement) for statement in node.body
                ):
                    found.add(node.name)
    return found


def outcomes_of(names: set[str]) -> dict[str, set[str]]:
    """How each named test fared: from this session when it ran here (the full suite,
    as CI runs it), otherwise from a separate run of just those tests. A skip by a
    fixture, a hook or a deselection shows here, where no parse of the source sees it
    (review L6)."""
    from conftest import OUTCOMES

    found = {n: OUTCOMES[n] for n in names if n in OUTCOMES}
    missing = set(names) - set(found)
    if missing:
        found.update(run_and_report(TESTS, RESEARCH, missing))
    return found


def run_and_report(tests: Path, rootdir: Path, names: set[str]) -> dict[str, set[str]]:
    """Run the named tests in a separate pytest and read each outcome from its report."""
    found: dict[str, set[str]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.xml"
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             "--rootdir", str(rootdir), f"--junitxml={report}", "-k", " or ".join(sorted(names)),
             str(tests)],
            capture_output=True, text=True, check=False, cwd=rootdir,
        )
        for case in ElementTree.parse(report).getroot().iter("testcase"):
            name = case.get("name", "").split("[", 1)[0]
            if name in names:
                kinds = {child.tag for child in case} & {"skipped", "failure", "error"}
                found.setdefault(name, set()).add(sorted(kinds)[0] if kinds else "passed")
    return found


def test_every_threat_model_test_exists_and_is_not_skipped():
    tables = inventory()
    # G1.6's table holds 22 names since security review round 2 added check-key's test.
    assert len(tables.get("G1.5", ())) >= 50 and len(tables.get("G1.6", ())) >= 22
    enforced = ALWAYS | (done_goals() & set(tables))
    required = set()
    for goal in enforced:
        required |= tables[goal] | PREREGISTRATION_TESTS.get(goal, set())
    names = collected()
    missing = sorted(required - names)
    assert not missing, f"named in the threat model but not collected: {missing}"
    skipped = sorted(required & skipped_or_expected_to_fail())
    assert not skipped, f"skipped or expected to fail: {skipped}"
    others = required - {"test_every_threat_model_test_exists_and_is_not_skipped"}
    results = outcomes_of(others)
    not_passed = sorted(n for n in others if results.get(n) != {"passed"})
    assert not not_passed, f"did not run and pass: {[(n, results.get(n)) for n in not_passed]}"


def test_the_inventory_check_sees_a_skip():
    source = (
        "import pytest\n"
        "@pytest.mark.skip\ndef test_a(): pass\n"
        "def test_b():\n    pytest.skip('later')\n"
        "@pytest.mark.xfail(reason='x')\ndef test_c(): pass\n"
        "def test_d(): pass\n"
    )
    tree = ast.parse(source)
    flagged = {
        n.name for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and (any(_skips(d) for d in n.decorator_list) or any(_skips(s) for s in n.body))
    }
    assert flagged == {"test_a", "test_b", "test_c"}


def test_the_inventory_check_sees_a_skip_by_a_fixture(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_example.py").write_text(
        "import pytest\n\n\n@pytest.fixture\ndef unavailable():\n    pytest.skip('later')\n\n\n"
        "def test_skipped_by_a_fixture(unavailable):\n    pass\n\n\n"
        "def test_that_fails():\n    raise AssertionError\n\n\n"
        "def test_that_passes():\n    pass\n"
    )
    names = {"test_skipped_by_a_fixture", "test_that_fails", "test_that_passes"}
    assert run_and_report(suite, tmp_path, names) == {
        "test_skipped_by_a_fixture": {"skipped"},
        "test_that_fails": {"failure"},
        "test_that_passes": {"passed"},
    }
