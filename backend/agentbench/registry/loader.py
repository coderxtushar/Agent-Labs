"""Benchmark file loading, ``$file`` inclusion, and import/export.

Task files are JSON (single task, list of tasks, or a
:class:`~agentbench.schemas.task.TaskSpecFile` envelope) or JSONL (one task per
line).

``$file`` inclusion
-------------------
Hidden acceptance tests would be unreadable if pasted into JSON as escaped
strings, so a value may be written as ``{"$file": "relative/path.py"}`` and the
loader inlines the file's contents.  Paths resolve against the benchmarks
directory and may not escape it.  Inlining happens at *import* time, so what
lands in the database is fully self-contained and exportable - the benchmark
stays reproducible even if the source tree later changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agentbench.config import get_settings
from agentbench.schemas.task import TASK_SCHEMA_VERSION, TaskSpec, TaskSpecFile


class BenchmarkLoadError(ValueError):
    """A benchmark file could not be parsed or failed schema validation."""

    def __init__(self, message: str, *, errors: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


@dataclass(slots=True)
class LoadedBenchmark:
    benchmark_id: str
    benchmark_version: str
    description: str = ""
    tasks: list[TaskSpec] = field(default_factory=list)


def _resolve_under(root: Path, relative: str) -> Path:
    if relative.startswith(("/", "~")):
        raise BenchmarkLoadError(f"$file path must be relative: {relative}")
    if ".." in relative.replace("\\", "/").split("/"):
        raise BenchmarkLoadError(f"$file path must not contain '..': {relative}")
    candidate = (root / relative).resolve()
    root_resolved = root.resolve()
    if root_resolved not in candidate.parents and candidate != root_resolved:
        raise BenchmarkLoadError(f"$file path escapes the benchmarks directory: {relative}")
    if not candidate.is_file():
        raise BenchmarkLoadError(f"$file target not found: {relative}")
    return candidate


def inline_file_refs(value: Any, root: Path) -> Any:
    """Recursively replace ``{"$file": "..."}`` nodes with the file's text."""
    if isinstance(value, dict):
        if set(value.keys()) == {"$file"} and isinstance(value["$file"], str):
            return _resolve_under(root, value["$file"]).read_text(encoding="utf-8")
        return {key: inline_file_refs(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [inline_file_refs(item, root) for item in value]
    return value


def _as_load_error(exc: ValidationError, context: str) -> BenchmarkLoadError:
    errors = [
        {
            "location": ".".join(str(part) for part in err["loc"]),
            "message": err["msg"],
            "type": err["type"],
        }
        for err in exc.errors()
    ]
    summary = "; ".join(f"{e['location']}: {e['message']}" for e in errors[:6])
    return BenchmarkLoadError(f"{context}: {summary}", errors=errors)


def parse_task_dict(
    data: dict[str, Any], *, root: Path | None = None, context: str = "task"
) -> TaskSpec:
    """Validate a single task mapping into a :class:`TaskSpec`."""
    root = root or get_settings().benchmarks_dir
    resolved = inline_file_refs(data, root)
    if not isinstance(resolved, dict):
        raise BenchmarkLoadError(f"{context}: expected a JSON object")
    try:
        return TaskSpec.model_validate(resolved)
    except ValidationError as exc:
        raise _as_load_error(exc, context) from exc


def parse_benchmark_payload(
    payload: Any, *, root: Path | None = None, default_id: str = "custom"
) -> LoadedBenchmark:
    """Accept the three supported JSON shapes and normalise them."""
    root = root or get_settings().benchmarks_dir

    if isinstance(payload, list):
        tasks = [
            parse_task_dict(item, root=root, context=f"tasks[{i}]")
            for i, item in enumerate(payload)
        ]
        bench = LoadedBenchmark(default_id, "1.0", tasks=tasks)
    elif isinstance(payload, dict) and "tasks" in payload:
        resolved = inline_file_refs(payload, root)
        try:
            envelope = TaskSpecFile.model_validate(resolved)
        except ValidationError as exc:
            raise _as_load_error(exc, "benchmark file") from exc
        bench = LoadedBenchmark(
            envelope.benchmark_id,
            envelope.benchmark_version,
            envelope.description,
            list(envelope.tasks),
        )
    elif isinstance(payload, dict):
        bench = LoadedBenchmark(default_id, "1.0", tasks=[parse_task_dict(payload, root=root)])
    else:
        raise BenchmarkLoadError("benchmark payload must be an object or a list of objects")

    seen: set[str] = set()
    for task in bench.tasks:
        if task.key in seen:
            raise BenchmarkLoadError(f"duplicate task key: {task.key}")
        seen.add(task.key)
    if not bench.tasks:
        raise BenchmarkLoadError("benchmark contains no tasks")
    return bench


def load_benchmark_text(
    text: str, *, filename: str = "benchmark.json", root: Path | None = None
) -> LoadedBenchmark:
    """Parse benchmark text as JSON or JSONL, chosen by extension then content."""
    stripped = text.strip()
    if not stripped:
        raise BenchmarkLoadError("benchmark file is empty")

    default_id = Path(filename).stem or "custom"

    if filename.endswith(".jsonl"):
        tasks: list[TaskSpec] = []
        for lineno, line in enumerate(stripped.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BenchmarkLoadError(f"line {lineno}: invalid JSON ({exc.msg})") from exc
            tasks.append(parse_task_dict(item, root=root, context=f"line {lineno}"))
        if not tasks:
            raise BenchmarkLoadError("JSONL benchmark contains no tasks")
        return LoadedBenchmark(default_id, "1.0", tasks=tasks)

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise BenchmarkLoadError(f"invalid JSON: {exc.msg} (line {exc.lineno})") from exc
    return parse_benchmark_payload(payload, root=root, default_id=default_id)


def load_benchmark_file(path: Path, root: Path | None = None) -> LoadedBenchmark:
    if not path.is_file():
        raise BenchmarkLoadError(f"benchmark file not found: {path}")
    return load_benchmark_text(
        path.read_text(encoding="utf-8"),
        filename=path.name,
        root=root or get_settings().benchmarks_dir,
    )


def discover_benchmark_files(directory: Path | None = None) -> list[Path]:
    directory = directory or (get_settings().benchmarks_dir / "tasks")
    if not directory.is_dir():
        return []
    return sorted(
        p
        for p in directory.iterdir()
        if p.suffix in {".json", ".jsonl"} and not p.name.startswith("_")
    )


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #
def export_benchmark_json(
    tasks: list[TaskSpec], *, benchmark_id: str, benchmark_version: str, description: str = ""
) -> str:
    envelope = TaskSpecFile(
        schema_version=TASK_SCHEMA_VERSION,
        benchmark_id=benchmark_id,
        benchmark_version=benchmark_version,
        description=description,
        tasks=tasks,
    )
    return json.dumps(envelope.model_dump(mode="json"), indent=2)


def export_benchmark_jsonl(tasks: list[TaskSpec]) -> str:
    return "\n".join(json.dumps(t.model_dump(mode="json")) for t in tasks) + "\n"
