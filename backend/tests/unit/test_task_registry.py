"""Task schema validation and benchmark import/export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentbench.registry.loader import (
    BenchmarkLoadError,
    export_benchmark_json,
    export_benchmark_jsonl,
    load_benchmark_text,
    parse_benchmark_payload,
    parse_task_dict,
)
from agentbench.schemas.task import TASK_SCHEMA_VERSION, TaskSpec
from tests.conftest import minirepo_task_dict


class TestTaskSchema:
    def test_valid_task_parses(self) -> None:
        spec = parse_task_dict(minirepo_task_dict())
        assert spec.key == "minirepo-add-bug@1.0"
        assert spec.schema_version == TASK_SCHEMA_VERSION

    @pytest.mark.parametrize(
        ("field", "value", "fragment"),
        [
            ("task_id", "Has Capitals", "lowercase"),
            ("task_id", "ab", "lowercase"),
            ("version", "v1", "MAJOR.MINOR"),
            ("problem_statement", "short", "at least 10"),
            ("title", "x", "at least 3"),
        ],
    )
    def test_rejects_malformed_fields(self, field: str, value: str, fragment: str) -> None:
        with pytest.raises(BenchmarkLoadError) as exc:
            parse_task_dict(minirepo_task_dict(**{field: value}))
        assert fragment in str(exc.value)

    def test_rejects_unknown_field(self) -> None:
        with pytest.raises(BenchmarkLoadError) as exc:
            parse_task_dict(minirepo_task_dict(surprise="value"))
        assert "extra" in str(exc.value).lower()

    def test_requires_at_least_one_fail_to_pass_test(self) -> None:
        payload = minirepo_task_dict()
        payload["tests"]["fail_to_pass"] = []
        with pytest.raises(BenchmarkLoadError) as exc:
            parse_task_dict(payload)
        assert "at least 1" in str(exc.value)

    def test_rejects_overlapping_test_groups(self) -> None:
        payload = minirepo_task_dict()
        payload["tests"]["pass_to_pass"] = payload["tests"]["fail_to_pass"][:1]
        with pytest.raises(BenchmarkLoadError) as exc:
            parse_task_dict(payload)
        assert "overlap" in str(exc.value)

    def test_pytest_command_must_declare_selection_placeholder(self) -> None:
        payload = minirepo_task_dict()
        payload["tests"]["command"] = "python -m pytest -q"
        with pytest.raises(BenchmarkLoadError) as exc:
            parse_task_dict(payload)
        assert "{selection}" in str(exc.value)

    def test_rejects_credentials_in_environment(self) -> None:
        payload = minirepo_task_dict()
        payload["environment"]["env"] = {"OPENAI_API_KEY": "sk-whatever"}
        with pytest.raises(BenchmarkLoadError) as exc:
            parse_task_dict(payload)
        assert "credentials" in str(exc.value)

    def test_rejects_path_traversal_in_repo_path(self) -> None:
        payload = minirepo_task_dict()
        payload["repo"] = {"kind": "local_path", "path": "../../etc", "revision": "local"}
        with pytest.raises(BenchmarkLoadError) as exc:
            parse_task_dict(payload)
        assert ".." in str(exc.value)

    def test_rejects_absolute_repo_path(self) -> None:
        payload = minirepo_task_dict()
        payload["repo"] = {"kind": "local_path", "path": "/etc", "revision": "local"}
        with pytest.raises(BenchmarkLoadError):
            parse_task_dict(payload)

    def test_rejects_traversal_in_acceptance_test_path(self) -> None:
        payload = minirepo_task_dict()
        payload["grading"]["acceptance_tests"] = {"../../evil.py": "print('x')"}
        with pytest.raises(BenchmarkLoadError):
            parse_task_dict(payload)

    def test_workdir_must_be_absolute(self) -> None:
        payload = minirepo_task_dict()
        payload["environment"]["workdir"] = "relative/path"
        with pytest.raises(BenchmarkLoadError) as exc:
            parse_task_dict(payload)
        assert "absolute" in str(exc.value)

    def test_rejects_unsupported_schema_major_version(self) -> None:
        with pytest.raises(BenchmarkLoadError) as exc:
            parse_task_dict(minirepo_task_dict(schema_version="99.0"))
        assert "schema" in str(exc.value).lower()


class TestHiddenTestProtection:
    def test_acceptance_test_paths_are_added_to_forbidden_paths(self) -> None:
        payload = minirepo_task_dict()
        payload["grading"]["forbidden_paths"] = []
        spec = parse_task_dict(payload)
        assert "tests/test_acceptance_add.py" in spec.grading.forbidden_paths

    def test_agent_payload_excludes_hints_by_default(self) -> None:
        spec = parse_task_dict(minirepo_task_dict())
        assert spec.hints
        assert "hints" not in spec.agent_visible_payload()
        assert "hints" in spec.agent_visible_payload(include_hints=True)

    def test_agent_payload_never_contains_acceptance_tests_or_node_ids(self) -> None:
        spec = parse_task_dict(minirepo_task_dict())
        serialized = json.dumps(spec.agent_visible_payload())
        assert "test_add_two_positives" not in serialized
        assert "assert add(2, 3)" not in serialized

    def test_agent_payload_lists_forbidden_paths_so_the_agent_can_comply(self) -> None:
        spec = parse_task_dict(minirepo_task_dict())
        assert spec.agent_visible_payload()["forbidden_paths"]


class TestFileInclusion:
    def test_dollar_file_is_inlined_at_import_time(self, minirepo_spec: TaskSpec) -> None:
        content = minirepo_spec.grading.acceptance_tests["tests/test_acceptance_add.py"]
        assert "def test_add_two_positives" in content

    def test_dollar_file_cannot_escape_the_benchmarks_directory(self) -> None:
        payload = minirepo_task_dict()
        payload["grading"]["acceptance_tests"] = {"tests/x.py": {"$file": "../../../etc/passwd"}}
        with pytest.raises(BenchmarkLoadError) as exc:
            parse_task_dict(payload)
        assert ".." in str(exc.value)

    def test_dollar_file_rejects_absolute_paths(self) -> None:
        payload = minirepo_task_dict()
        payload["grading"]["acceptance_tests"] = {"tests/x.py": {"$file": "/etc/passwd"}}
        with pytest.raises(BenchmarkLoadError) as exc:
            parse_task_dict(payload)
        assert "relative" in str(exc.value)

    def test_missing_dollar_file_target_is_reported(self) -> None:
        payload = minirepo_task_dict()
        payload["grading"]["acceptance_tests"] = {"tests/x.py": {"$file": "nope/missing.py"}}
        with pytest.raises(BenchmarkLoadError) as exc:
            parse_task_dict(payload)
        assert "not found" in str(exc.value)


class TestBenchmarkFileFormats:
    def test_envelope_form(self) -> None:
        payload = {
            "benchmark_id": "demo",
            "benchmark_version": "2.0",
            "tasks": [minirepo_task_dict()],
        }
        benchmark = parse_benchmark_payload(payload)
        assert benchmark.benchmark_id == "demo"
        assert benchmark.benchmark_version == "2.0"
        assert len(benchmark.tasks) == 1

    def test_bare_list_form(self) -> None:
        assert len(parse_benchmark_payload([minirepo_task_dict()]).tasks) == 1

    def test_single_object_form(self) -> None:
        assert len(parse_benchmark_payload(minirepo_task_dict()).tasks) == 1

    def test_jsonl_form(self) -> None:
        second = minirepo_task_dict(task_id="minirepo-second")
        text = "\n".join(json.dumps(t) for t in (minirepo_task_dict(), second))
        benchmark = load_benchmark_text(text, filename="pack.jsonl")
        assert [t.task_id for t in benchmark.tasks] == ["minirepo-add-bug", "minirepo-second"]

    def test_duplicate_task_keys_are_rejected(self) -> None:
        with pytest.raises(BenchmarkLoadError) as exc:
            parse_benchmark_payload([minirepo_task_dict(), minirepo_task_dict()])
        assert "duplicate" in str(exc.value)

    def test_empty_file_is_rejected(self) -> None:
        with pytest.raises(BenchmarkLoadError) as exc:
            load_benchmark_text("   ")
        assert "empty" in str(exc.value)

    def test_malformed_json_reports_line_number(self) -> None:
        with pytest.raises(BenchmarkLoadError) as exc:
            load_benchmark_text('{"tasks": [')
        assert "line" in str(exc.value)

    def test_malformed_jsonl_reports_the_offending_line(self) -> None:
        text = json.dumps(minirepo_task_dict()) + "\n{not json}\n"
        with pytest.raises(BenchmarkLoadError) as exc:
            load_benchmark_text(text, filename="pack.jsonl")
        assert "line 2" in str(exc.value)


class TestRoundTrip:
    def test_json_export_reimports_identically(self, minirepo_spec: TaskSpec) -> None:
        exported = export_benchmark_json(
            [minirepo_spec], benchmark_id="demo", benchmark_version="1.0"
        )
        reimported = load_benchmark_text(exported, filename="demo.json")
        assert reimported.tasks[0].model_dump(mode="json") == minirepo_spec.model_dump(mode="json")

    def test_jsonl_export_reimports_identically(self, minirepo_spec: TaskSpec) -> None:
        reimported = load_benchmark_text(
            export_benchmark_jsonl([minirepo_spec]), filename="demo.jsonl"
        )
        assert reimported.tasks[0].model_dump(mode="json") == minirepo_spec.model_dump(mode="json")

    def test_exported_spec_is_self_contained(self, minirepo_spec: TaskSpec, tmp_path: Path) -> None:
        """A `$file` reference must be inlined, not carried through to the export."""
        exported = export_benchmark_json(
            [minirepo_spec], benchmark_id="demo", benchmark_version="1.0"
        )
        assert "$file" not in exported
        assert "def test_add_two_positives" in exported
