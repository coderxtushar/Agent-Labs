"""Test-report parsing, secret redaction and the trajectory recorder."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from agentbench.grading.reports import (
    PytestExit,
    TestStatus,
    classify_pytest_exit,
    detect_missing_dependency,
    parse_junit_xml,
)
from agentbench.schemas.enums import EventType
from agentbench.trajectory.recorder import EventRecorder, count_events, read_events
from agentbench.trajectory.redaction import MASK, redact_text, redact_value

PASSING_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="2" failures="0" errors="0" skipped="0">
<testcase classname="tests.test_calc" name="test_mul" file="tests/test_calc.py" time="0.01"/>
<testcase classname="tests.test_calc" name="test_add" file="tests/test_calc.py" time="0.02"/>
</testsuite></testsuites>
"""

MIXED_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="4" failures="1" errors="1" skipped="1">
<testcase classname="tests.test_calc" name="test_ok" file="tests/test_calc.py" time="0.01"/>
<testcase classname="tests.test_calc" name="test_bad" file="tests/test_calc.py" time="0.01">
  <failure message="assert 1 == 2">AssertionError</failure>
</testcase>
<testcase classname="tests.test_calc" name="test_boom" file="tests/test_calc.py" time="0.01">
  <error message="ModuleNotFoundError: No module named 'absent'">traceback</error>
</testcase>
<testcase classname="tests.test_calc" name="test_skip" file="tests/test_calc.py" time="0.0">
  <skipped message="needs network"/>
</testcase>
</testsuite></testsuites>
"""

PARAMETRIZED_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="2">
<testcase classname="tests.test_p" name="test_x[1]" file="tests/test_p.py" time="0.01"/>
<testcase classname="tests.test_p" name="test_x[2]" file="tests/test_p.py" time="0.01">
  <failure message="boom">AssertionError</failure>
</testcase>
</testsuite>
"""

CLASS_BASED_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="1">
<testcase classname="tests.test_cls.TestThing" name="test_method" file="tests/test_cls.py"/>
</testsuite>
"""


class TestJUnitParsing:
    def test_node_ids_are_reconstructed_from_file_and_name(self) -> None:
        report = parse_junit_xml(PASSING_XML)
        assert report.status_of("tests/test_calc.py::test_mul") is TestStatus.PASSED
        assert report.passed == 2

    def test_each_status_is_counted_separately(self) -> None:
        report = parse_junit_xml(MIXED_XML)
        assert (report.passed, report.failed, report.errored, report.skipped) == (1, 1, 1, 1)
        assert report.status_of("tests/test_calc.py::test_bad") is TestStatus.FAILED
        assert report.status_of("tests/test_calc.py::test_boom") is TestStatus.ERRORED
        assert report.status_of("tests/test_calc.py::test_skip") is TestStatus.SKIPPED

    def test_unknown_node_is_missing_not_failed(self) -> None:
        report = parse_junit_xml(PASSING_XML)
        assert report.status_of("tests/test_calc.py::test_nope") is TestStatus.MISSING

    def test_parametrized_selector_aggregates_to_worst_status(self) -> None:
        report = parse_junit_xml(PARAMETRIZED_XML)
        assert report.status_of("tests/test_p.py::test_x") is TestStatus.FAILED

    def test_class_based_node_id_includes_the_class(self) -> None:
        report = parse_junit_xml(CLASS_BASED_XML)
        assert report.status_of("tests/test_cls.py::TestThing::test_method") is TestStatus.PASSED

    def test_malformed_xml_reports_a_parse_error(self) -> None:
        report = parse_junit_xml("<testsuite><bad")
        assert report.parse_error is not None

    def test_empty_report_reports_a_parse_error(self) -> None:
        assert parse_junit_xml("").parse_error is not None

    def test_failure_message_is_retrievable(self) -> None:
        report = parse_junit_xml(MIXED_XML)
        assert "assert 1 == 2" in report.message_of("tests/test_calc.py::test_bad")


class TestExitClassification:
    @pytest.mark.parametrize(
        ("code", "expected", "is_infra"),
        [
            (0, PytestExit.ALL_PASSED, False),
            (1, PytestExit.TESTS_FAILED, False),
            (2, PytestExit.INTERRUPTED, True),
            (3, PytestExit.INTERNAL_ERROR, True),
            (4, PytestExit.USAGE_ERROR, True),
            (5, PytestExit.NO_TESTS_COLLECTED, True),
            (99, PytestExit.UNKNOWN, True),
        ],
    )
    def test_exit_codes_map_to_kinds(self, code: int, expected: PytestExit, is_infra: bool) -> None:
        kind = classify_pytest_exit(code)
        assert kind is expected
        assert kind.is_infrastructure_problem is is_infra

    def test_timeout_overrides_the_exit_code(self) -> None:
        assert classify_pytest_exit(0, timed_out=True) is PytestExit.TIMED_OUT

    def test_a_failed_test_is_not_an_infrastructure_problem(self) -> None:
        assert not classify_pytest_exit(1).is_infrastructure_problem

    def test_missing_dependency_is_extracted(self) -> None:
        assert detect_missing_dependency("ModuleNotFoundError: No module named 'numpy'") == "numpy"

    def test_missing_dependency_absent_returns_none(self) -> None:
        assert detect_missing_dependency("assert 1 == 2") is None


class TestRedaction:
    @pytest.mark.parametrize(
        "secret",
        [
            "sk-abcdefghijklmnopqrstuvwxyz123456",
            "sk-ant-api03-abcdefghijklmnop",
            "ghp_abcdefghijklmnopqrstuvwxyz1234",
            "xoxb-1234567890-abcdefghij",
            "AKIAIOSFODNN7EXAMPLE",
        ],
    )
    def test_credential_shaped_values_are_masked(self, secret: str) -> None:
        assert secret not in redact_text(f"token is {secret} ok")
        assert MASK in redact_text(f"token is {secret} ok")

    def test_authorization_headers_are_masked(self) -> None:
        assert "abcdef1234567890" not in redact_text("Authorization: Bearer abcdef1234567890")

    def test_key_value_assignment_is_masked(self) -> None:
        assert "hunter2hunter2" not in redact_text("MY_SECRET=hunter2hunter2")

    def test_pem_private_key_is_masked(self) -> None:
        pem = "-----BEGIN RSA PRIVATE KEY-----\nabc\ndef\n-----END RSA PRIVATE KEY-----"
        assert "abc" not in redact_text(pem)

    def test_known_secret_is_masked_even_without_a_recognisable_shape(self) -> None:
        assert redact_text("value=zzzqqqwww", ["zzzqqqwww"]) == f"value={MASK}"

    def test_sensitive_mapping_keys_mask_the_whole_value(self) -> None:
        result = redact_value({"api_key": {"nested": "anything"}, "model": "gpt-4o-mini"})
        assert result["api_key"] == MASK
        assert result["model"] == "gpt-4o-mini"

    def test_nested_structures_are_walked(self) -> None:
        payload = {"a": [{"password": "x" * 20}, "sk-" + "y" * 20]}
        result = redact_value(payload)
        assert result["a"][0]["password"] == MASK
        assert MASK in result["a"][1]

    def test_ordinary_text_is_untouched(self) -> None:
        assert redact_text("all 12 tests passed in 0.3s") == "all 12 tests passed in 0.3s"

    def test_depth_limit_prevents_runaway_recursion(self) -> None:
        payload: dict = {}
        node = payload
        for _ in range(30):
            node["next"] = {}
            node = node["next"]
        assert "DEPTH_LIMIT" in str(redact_value(payload))


class TestEventRecorder:
    """Uses the `seeded_run` fixture: foreign keys are enforced, so a run must
    be attached to a real experiment, agent and task."""

    def test_sequence_numbers_start_at_one_and_are_monotonic(
        self, db: Session, seeded_run: object
    ) -> None:
        run_id = seeded_run.id
        recorder = EventRecorder(db, run_id, commit=False)
        seqs = [recorder.record(EventType.TOOL_CALL, {"i": i}).seq for i in range(5)]
        assert seqs == [1, 2, 3, 4, 5]

    def test_a_second_recorder_continues_the_sequence(
        self, db: Session, seeded_run: object
    ) -> None:
        run_id = seeded_run.id
        EventRecorder(db, run_id, commit=False).record(EventType.AGENT_START)
        assert EventRecorder(db, run_id, commit=False).record(EventType.AGENT_END).seq == 2

    def test_events_read_back_in_sequence_order(self, db: Session, seeded_run: object) -> None:
        run_id = seeded_run.id
        recorder = EventRecorder(db, run_id, commit=False)
        for i in range(4):
            recorder.record(EventType.COMMAND_EXEC, {"i": i})
        rows = read_events(db, run_id)
        assert [row.seq for row in rows] == [1, 2, 3, 4]
        assert [row.payload["i"] for row in rows] == [0, 1, 2, 3]

    def test_after_seq_cursor_returns_only_newer_events(
        self, db: Session, seeded_run: object
    ) -> None:
        run_id = seeded_run.id
        recorder = EventRecorder(db, run_id, commit=False)
        for _ in range(5):
            recorder.record(EventType.TOOL_CALL)
        assert [row.seq for row in read_events(db, run_id, after_seq=3)] == [4, 5]

    def test_secrets_in_payloads_are_redacted_before_storage(
        self, db: Session, seeded_run: object
    ) -> None:
        run_id = seeded_run.id
        recorder = EventRecorder(db, run_id, commit=False)
        recorder.record(EventType.MODEL_REQUEST, {"headers": {"api_key": "sk-" + "z" * 24}})
        payload = read_events(db, run_id)[0].payload
        assert "sk-" not in str(payload)

    def test_oversized_payload_is_truncated_and_flagged(
        self, db: Session, seeded_run: object, settings: object
    ) -> None:
        run_id = seeded_run.id
        oversized = "x" * (settings.event_payload_max_chars * 3)
        recorder = EventRecorder(db, run_id, commit=False)
        recorder.record(EventType.COMMAND_EXEC, {"stdout_tail": oversized})
        row = read_events(db, run_id)[0]
        assert row.truncated is True
        assert len(str(row.payload)) < len(oversized)

    def test_unserialisable_payload_is_recorded_rather_than_lost(
        self, db: Session, seeded_run: object
    ) -> None:
        run_id = seeded_run.id
        recorder = EventRecorder(db, run_id, commit=False)
        recorder.record(EventType.ERROR, {"obj": {1, 2, 3}})
        assert count_events(db, run_id) == 1

    def test_unavailable_helper_distinguishes_missing_from_empty(
        self, db: Session, seeded_run: object
    ) -> None:
        run_id = seeded_run.id
        recorder = EventRecorder(db, run_id, commit=False)
        payload = recorder.unavailable("token_count")
        assert payload == {"token_count": None, "token_count_available": False}

    def test_event_type_filter(self, db: Session, seeded_run: object) -> None:
        run_id = seeded_run.id
        recorder = EventRecorder(db, run_id, commit=False)
        recorder.record(EventType.TOOL_CALL)
        recorder.record(EventType.AGENT_END)
        rows = read_events(db, run_id, event_types=[str(EventType.AGENT_END)])
        assert [row.event_type for row in rows] == [str(EventType.AGENT_END)]
