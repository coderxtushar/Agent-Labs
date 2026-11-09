"""Parsing of test-runner reports.

Per-test outcomes come from pytest's built-in JUnit XML writer rather than from
scraping stdout: the summary line cannot tell us *which* of the declared
``fail_to_pass`` nodes passed, and that distinction is the whole grading signal.

An exit code alone is never trusted.  pytest exits 4 on a usage error (for
example a node ID that does not exist), which is a grading infrastructure
problem, not a failed solution; :func:`classify_pytest_exit` keeps those apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from xml.etree import ElementTree


class TestStatus(StrEnum):
    __test__ = False

    PASSED = "passed"
    FAILED = "failed"
    ERRORED = "errored"
    SKIPPED = "skipped"
    #: The node was requested but does not appear in the report at all.
    MISSING = "missing"


@dataclass(slots=True)
class TestOutcome:
    __test__ = False
    node_id: str
    status: TestStatus
    duration_s: float = 0.0
    message: str = ""


@dataclass(slots=True)
class TestReport:
    __test__ = False
    outcomes: dict[str, TestOutcome] = field(default_factory=dict)
    passed: int = 0
    failed: int = 0
    errored: int = 0
    skipped: int = 0
    parse_error: str | None = None

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errored + self.skipped

    def status_of(self, node_id: str) -> TestStatus:
        hit = self.outcomes.get(node_id)
        if hit is not None:
            return hit.status
        # Parametrized selectors: `file.py::test_x` selects `test_x[1]`, `test_x[2]`.
        matches = [o for key, o in self.outcomes.items() if key.startswith(f"{node_id}[")]
        if matches:
            if any(o.status is TestStatus.ERRORED for o in matches):
                return TestStatus.ERRORED
            if any(o.status is TestStatus.FAILED for o in matches):
                return TestStatus.FAILED
            if all(o.status is TestStatus.SKIPPED for o in matches):
                return TestStatus.SKIPPED
            return TestStatus.PASSED
        return TestStatus.MISSING

    def message_of(self, node_id: str) -> str:
        hit = self.outcomes.get(node_id)
        if hit:
            return hit.message
        return "; ".join(
            o.message
            for key, o in self.outcomes.items()
            if key.startswith(f"{node_id}[") and o.message
        )


def _node_id(testcase: ElementTree.Element) -> str:
    name = testcase.get("name", "")
    file_attr = testcase.get("file")
    classname = testcase.get("classname", "")

    if file_attr:
        # pytest sets classname to the dotted module path, plus the class name
        # for class-based tests. Recover the class segment if present.
        module_dotted = file_attr.removesuffix(".py").replace("/", ".")
        if classname and classname != module_dotted and classname.startswith(f"{module_dotted}."):
            class_segment = classname[len(module_dotted) + 1 :]
            return f"{file_attr}::{class_segment.replace('.', '::')}::{name}"
        return f"{file_attr}::{name}"

    # No `file` attribute (non-pytest JUnit producers): best effort.
    if classname:
        return f"{classname.replace('.', '/')}.py::{name}"
    return name


def parse_junit_xml(xml_text: str) -> TestReport:
    report = TestReport()
    if not xml_text.strip():
        report.parse_error = "report file was empty"
        return report
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        report.parse_error = f"malformed JUnit XML: {exc}"
        return report

    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    for suite in suites:
        for case in suite.iter("testcase"):
            node_id = _node_id(case)
            try:
                duration = float(case.get("time", "0") or 0)
            except ValueError:
                duration = 0.0

            failure = case.find("failure")
            error = case.find("error")
            skipped = case.find("skipped")

            if error is not None:
                status = TestStatus.ERRORED
                message = (error.get("message") or error.text or "").strip()
                report.errored += 1
            elif failure is not None:
                status = TestStatus.FAILED
                message = (failure.get("message") or failure.text or "").strip()
                report.failed += 1
            elif skipped is not None:
                status = TestStatus.SKIPPED
                message = (skipped.get("message") or "").strip()
                report.skipped += 1
            else:
                status = TestStatus.PASSED
                message = ""
                report.passed += 1

            report.outcomes[node_id] = TestOutcome(node_id, status, duration, message[:4000])
    return report


class PytestExit(StrEnum):
    ALL_PASSED = "all_passed"
    TESTS_FAILED = "tests_failed"
    INTERRUPTED = "interrupted"
    INTERNAL_ERROR = "internal_error"
    USAGE_ERROR = "usage_error"
    NO_TESTS_COLLECTED = "no_tests_collected"
    TIMED_OUT = "timed_out"
    UNKNOWN = "unknown"

    @property
    def is_infrastructure_problem(self) -> bool:
        """True when the exit says nothing about the quality of the patch."""
        return self in {
            PytestExit.INTERNAL_ERROR,
            PytestExit.USAGE_ERROR,
            PytestExit.NO_TESTS_COLLECTED,
            PytestExit.INTERRUPTED,
            PytestExit.UNKNOWN,
        }


_PYTEST_EXIT_MAP = {
    0: PytestExit.ALL_PASSED,
    1: PytestExit.TESTS_FAILED,
    2: PytestExit.INTERRUPTED,
    3: PytestExit.INTERNAL_ERROR,
    4: PytestExit.USAGE_ERROR,
    5: PytestExit.NO_TESTS_COLLECTED,
}


def classify_pytest_exit(exit_code: int, timed_out: bool = False) -> PytestExit:
    if timed_out:
        return PytestExit.TIMED_OUT
    return _PYTEST_EXIT_MAP.get(exit_code, PytestExit.UNKNOWN)


_COLLECT_ERROR_RE = re.compile(r"ERROR\s+(\S+)|ImportError while importing", re.MULTILINE)
_MISSING_DEP_RE = re.compile(
    r"(ModuleNotFoundError: No module named ['\"]([\w.\-]+)['\"]"
    r"|ImportError: cannot import name ['\"]([\w.\-]+)['\"]"
    r"|No module named ([\w.\-]+))"
)


def detect_missing_dependency(output: str) -> str | None:
    """Extract a missing module name from runner output, if one is reported."""
    match = _MISSING_DEP_RE.search(output or "")
    if not match:
        return None
    for group in match.groups()[1:]:
        if group:
            return group
    return None


def has_collection_error(output: str) -> bool:
    return bool(_COLLECT_ERROR_RE.search(output or ""))
