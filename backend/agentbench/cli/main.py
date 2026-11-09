"""``agentbench`` command line interface.

Covers the operational surface that should not require the web UI: schema
creation, benchmark and agent import, task validation, experiment launch,
synchronous execution, and report export.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from agentbench.config import get_settings
from agentbench.logging_setup import setup_logging

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="AgentBench Lab - evaluate, compare and debug AI coding agents.",
)
db_app = typer.Typer(no_args_is_help=True, help="Database schema management.")
bench_app = typer.Typer(no_args_is_help=True, help="Benchmark task registry.")
agent_app = typer.Typer(no_args_is_help=True, help="Agent configurations.")
exp_app = typer.Typer(no_args_is_help=True, help="Experiments.")
images_app = typer.Typer(no_args_is_help=True, help="Benchmark execution images.")
app.add_typer(db_app, name="db")
app.add_typer(bench_app, name="benchmark")
app.add_typer(agent_app, name="agent")
app.add_typer(exp_app, name="experiment")
app.add_typer(images_app, name="images")


@app.callback()
def _root(
    log_format: Annotated[str, typer.Option(help="json or console")] = "console",
    log_level: Annotated[str, typer.Option()] = "INFO",
) -> None:
    settings = get_settings()
    setup_logging(log_level, log_format, settings.secret_values())


def _echo(payload: object) -> None:
    typer.echo(json.dumps(payload, indent=2, default=str))


# --------------------------------------------------------------------------- #
@db_app.command("init")
def db_init() -> None:
    """Create all tables (SQLite-friendly; use Alembic for PostgreSQL)."""
    from agentbench.db.session import create_all

    create_all()
    typer.echo(f"schema created at {get_settings().database_url}")


@db_app.command("status")
def db_status() -> None:
    from sqlalchemy import func, select

    from agentbench.db.models import AgentConfigRecord, ExperimentRecord, RunRecord, TaskRecord
    from agentbench.db.session import session_scope

    with session_scope() as session:
        counts = {
            "tasks": session.execute(select(func.count()).select_from(TaskRecord)).scalar(),
            "agents": session.execute(select(func.count()).select_from(AgentConfigRecord)).scalar(),
            "experiments": session.execute(
                select(func.count()).select_from(ExperimentRecord)
            ).scalar(),
            "runs": session.execute(select(func.count()).select_from(RunRecord)).scalar(),
        }
    _echo({"database_url": get_settings().database_url, **counts})


# --------------------------------------------------------------------------- #
@bench_app.command("import")
def benchmark_import(
    path: Annotated[Path, typer.Argument(help="JSON or JSONL benchmark file")],
    overwrite: Annotated[
        bool, typer.Option(help="Allow replacing an existing task version")
    ] = False,
) -> None:
    from agentbench.db.session import session_scope
    from agentbench.registry.loader import load_benchmark_file
    from agentbench.registry.service import import_benchmark

    benchmark = load_benchmark_file(path)
    with session_scope() as session:
        report = import_benchmark(session, benchmark, allow_overwrite=overwrite)
    _echo(report.to_dict())
    if not report.ok:
        raise typer.Exit(code=1)


@bench_app.command("list")
def benchmark_list() -> None:
    from agentbench.db.session import session_scope
    from agentbench.registry.service import list_tasks

    with session_scope() as session:
        rows, total = list_tasks(session, limit=500)
        _echo(
            {
                "total": total,
                "tasks": [
                    {
                        "key": r.key,
                        "title": r.title,
                        "category": r.category,
                        "difficulty": r.difficulty,
                        "validation": r.validation_status,
                        "benchmark": f"{r.benchmark_id}@{r.benchmark_version}",
                    }
                    for r in rows
                ],
            }
        )


@bench_app.command("validate")
def benchmark_validate(
    task_key: Annotated[str | None, typer.Argument(help="task_id or task_id@version")] = None,
) -> None:
    """Build each task's real environment and check it is executable and falsifiable."""
    from agentbench.db.session import session_scope
    from agentbench.registry.service import (
        get_task_by_key,
        list_tasks,
        set_validation_result,
        spec_of,
    )
    from agentbench.registry.validator import validate_task

    with session_scope() as session:
        if task_key:
            record = get_task_by_key(session, task_key)
            if record is None:
                typer.echo(f"unknown task: {task_key}", err=True)
                raise typer.Exit(code=1)
            records = [record]
        else:
            records, _ = list_tasks(session, limit=500)

        results = []
        for record in records:
            report = validate_task(spec_of(record))
            set_validation_result(session, record, report.status, report.to_dict())
            results.append(report.to_dict())

    _echo({"validated": len(results), "results": results})
    if any(not r["ok"] for r in results):
        raise typer.Exit(code=1)


@bench_app.command("export")
def benchmark_export(
    benchmark_id: Annotated[str | None, typer.Option()] = None,
    fmt: Annotated[str, typer.Option("--format", help="json or jsonl")] = "json",
    out: Annotated[Path | None, typer.Option(help="Write to a file instead of stdout")] = None,
) -> None:
    from agentbench.db.session import session_scope
    from agentbench.registry.service import export_tasks

    with session_scope() as session:
        payload = export_tasks(session, benchmark_id=benchmark_id, fmt=fmt)
    if out:
        out.write_text(payload, encoding="utf-8")
        typer.echo(f"wrote {out}")
    else:
        typer.echo(payload)


# --------------------------------------------------------------------------- #
@agent_app.command("import")
def agent_import(
    path: Annotated[Path, typer.Argument(help="JSON file with an 'agents' array")],
    update: Annotated[bool, typer.Option(help="Allow updating existing name@version")] = False,
) -> None:
    from agentbench.db.session import session_scope
    from agentbench.registry.agents import register_agent

    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("agents") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        typer.echo("expected an object with an 'agents' array, or a bare array", err=True)
        raise typer.Exit(code=1)

    created = []
    with session_scope() as session:
        for entry in entries:
            record = register_agent(
                session,
                name=entry["name"],
                adapter=entry["adapter"],
                version=entry.get("version", "1.0"),
                description=entry.get("description", ""),
                provider=entry.get("provider"),
                model=entry.get("model"),
                prompt_version=entry.get("prompt_version", "1.0"),
                tools_version=entry.get("tools_version", "1.0"),
                config=entry.get("config"),
                sampling=entry.get("sampling"),
                allow_update=update,
            )
            created.append(record.label)
    _echo({"registered": created})


@agent_app.command("list")
def agent_list() -> None:
    from agentbench.db.session import session_scope
    from agentbench.registry.agents import describe, list_agents

    with session_scope() as session:
        rows, total = list_agents(session)
        _echo(
            {
                "total": total,
                "agents": [
                    {
                        "label": a["label"],
                        "adapter": a["adapter"],
                        "model": a["model"],
                        "simulated": a["is_simulated"],
                        "ready": a["readiness"]["ready"],
                        "detail": a["readiness"]["detail"],
                    }
                    for a in (describe(r) for r in rows)
                ],
            }
        )


@agent_app.command("adapters")
def agent_adapters() -> None:
    from agentbench.adapters.registry import list_capabilities

    _echo([c.to_dict() for c in list_capabilities()])


# --------------------------------------------------------------------------- #
@exp_app.command("create")
def experiment_create(
    name: Annotated[str, typer.Option()],
    agent: Annotated[str, typer.Option(help="agent name or name@version")],
    tasks: Annotated[
        str, typer.Option(help="comma-separated task keys, or 'all', or 'benchmark:<id>'")
    ] = "all",
    repetitions: Annotated[int, typer.Option()] = 1,
    concurrency: Annotated[int, typer.Option()] = 1,
    seed: Annotated[int | None, typer.Option()] = None,
    include_hints: Annotated[bool, typer.Option()] = False,
    launch: Annotated[bool, typer.Option(help="Queue it immediately")] = True,
) -> None:
    from agentbench.db.session import session_scope
    from agentbench.orchestrator import experiment as experiments
    from agentbench.registry.agents import get_agent_by_label
    from agentbench.registry.service import list_tasks

    with session_scope() as session:
        agent_record = get_agent_by_label(session, agent)
        if agent_record is None:
            typer.echo(f"unknown agent: {agent}", err=True)
            raise typer.Exit(code=1)

        if tasks == "all":
            rows, _ = list_tasks(session, limit=500, enabled_only=True)
            keys = [r.key for r in rows]
        elif tasks.startswith("benchmark:"):
            rows, _ = list_tasks(
                session, limit=500, enabled_only=True, benchmark_id=tasks.split(":", 1)[1]
            )
            keys = [r.key for r in rows]
        else:
            keys = [k.strip() for k in tasks.split(",") if k.strip()]

        experiment = experiments.create_experiment(
            session,
            name=name,
            agent_config_id=agent_record.id,
            task_keys=keys,
            repetitions=repetitions,
            concurrency=concurrency,
            seed=seed,
            include_hints=include_hints,
        )
        experiment_id = experiment.id
        if launch:
            experiments.launch(session, experiment_id)

    _echo({"experiment_id": experiment_id, "tasks": len(keys), "launched": launch})


@exp_app.command("run")
def experiment_run(
    experiment_id: Annotated[str | None, typer.Argument()] = None,
    max_seconds: Annotated[float, typer.Option()] = 3600.0,
) -> None:
    """Execute queued runs synchronously in this process until none remain."""
    from agentbench.db.session import session_scope
    from agentbench.orchestrator import experiment as experiments
    from agentbench.orchestrator.worker import Worker

    if experiment_id:
        with session_scope() as session:
            experiments.launch(session, experiment_id)

    reports = Worker(concurrency=1).drain(max_seconds=max_seconds)
    _echo(
        {
            "executed": len(reports),
            "runs": [
                {"run_id": r.run_id, "outcome": str(r.outcome), "reason": r.reason[:200]}
                for r in reports
            ],
        }
    )


@exp_app.command("status")
def experiment_status(experiment_id: Annotated[str, typer.Argument()]) -> None:
    from agentbench.db.session import session_scope
    from agentbench.metrics.compute import experiment_metrics

    with session_scope() as session:
        _echo(experiment_metrics(session, experiment_id).to_dict())


@exp_app.command("cancel")
def experiment_cancel(experiment_id: Annotated[str, typer.Argument()]) -> None:
    from agentbench.db.session import session_scope
    from agentbench.orchestrator import experiment as experiments

    with session_scope() as session:
        _echo(experiments.cancel(session, experiment_id).to_dict())


@exp_app.command("resume")
def experiment_resume(
    experiment_id: Annotated[str, typer.Argument()],
    force: Annotated[
        bool,
        typer.Option(
            help="Also reset runs whose worker lease is still live. Only use this when you "
            "know the worker is gone, otherwise work can be duplicated."
        ),
    ] = False,
) -> None:
    from agentbench.db.session import session_scope
    from agentbench.orchestrator import experiment as experiments

    with session_scope() as session:
        _echo(experiments.resume(session, experiment_id, force=force).to_dict())


@exp_app.command("report")
def experiment_report(
    experiment_id: Annotated[str, typer.Argument()],
    fmt: Annotated[str, typer.Option("--format", help="json, csv or markdown")] = "markdown",
    out: Annotated[Path | None, typer.Option()] = None,
) -> None:
    from agentbench.db.session import session_scope
    from agentbench.metrics.export import render_report

    with session_scope() as session:
        payload = render_report(session, experiment_id, fmt=fmt)
    if out:
        out.write_text(payload, encoding="utf-8")
        typer.echo(f"wrote {out}")
    else:
        typer.echo(payload)


@exp_app.command("compare")
def experiment_compare(
    baseline: Annotated[str, typer.Argument()],
    candidate: Annotated[str, typer.Argument()],
) -> None:
    from agentbench.db.session import session_scope
    from agentbench.metrics.compare import compare_experiments

    with session_scope() as session:
        _echo(compare_experiments(session, baseline, candidate).to_dict())


# --------------------------------------------------------------------------- #
@images_app.command("build")
def images_build(
    tag: Annotated[str, typer.Option()] = "agentbench/benchmark-python:3.12",
    dockerfile: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Build the benchmark execution image. Needs network; runs do not."""
    import subprocess

    root = Path(__file__).resolve().parents[3]
    dockerfile = dockerfile or root / "docker" / "benchmark-python3.12.Dockerfile"
    if not dockerfile.is_file():
        typer.echo(f"dockerfile not found: {dockerfile}", err=True)
        raise typer.Exit(code=1)
    argv = [
        get_settings().docker_binary,
        "build",
        "-f",
        str(dockerfile),
        "-t",
        tag,
        str(dockerfile.parent),
    ]
    typer.echo(" ".join(argv))
    raise typer.Exit(code=subprocess.run(argv, check=False).returncode)  # noqa: S603


@images_app.command("status")
def images_status() -> None:
    from agentbench.sandbox.docker_sandbox import image_present
    from agentbench.sandbox.factory import all_provider_statuses

    _echo(
        {
            "providers": [
                {
                    "name": s.name,
                    "available": s.available,
                    "detail": s.detail,
                    "isolation": s.isolation,
                }
                for s in all_provider_statuses()
            ],
            "default_image_present": image_present(get_settings().default_sandbox_image),
        }
    )


# --------------------------------------------------------------------------- #
@app.command("bootstrap")
def bootstrap() -> None:
    """Create the schema and import the bundled benchmark and demo agents."""
    from agentbench.db.session import create_all, session_scope
    from agentbench.registry.agents import AgentConflict, register_agent
    from agentbench.registry.loader import discover_benchmark_files, load_benchmark_file
    from agentbench.registry.service import import_benchmark

    settings = get_settings()
    create_all()

    imported: list[dict[str, object]] = []
    with session_scope() as session:
        for path in discover_benchmark_files():
            benchmark = load_benchmark_file(path)
            report = import_benchmark(session, benchmark)
            imported.append({"file": path.name, **report.to_dict()})

    agents_file = settings.benchmarks_dir / "agents" / "builtin.json"
    registered: list[str] = []
    skipped: list[str] = []
    if agents_file.is_file():
        entries = json.loads(agents_file.read_text(encoding="utf-8"))["agents"]
        with session_scope() as session:
            for entry in entries:
                try:
                    record = register_agent(
                        session,
                        name=entry["name"],
                        adapter=entry["adapter"],
                        version=entry.get("version", "1.0"),
                        description=entry.get("description", ""),
                        config=entry.get("config"),
                        model=entry.get("model"),
                        provider=entry.get("provider"),
                    )
                    registered.append(record.label)
                except AgentConflict:
                    skipped.append(f"{entry['name']}@{entry.get('version', '1.0')}")

    _echo(
        {
            "database_url": settings.database_url,
            "benchmarks": imported,
            "agents_registered": registered,
            "agents_already_present": skipped,
            "next": [
                "agentbench benchmark validate",
                "agentbench experiment create --name demo --agent reference-solution",
                "agentbench experiment run",
            ],
        }
    )


@app.command("serve")
def serve(
    host: Annotated[str | None, typer.Option()] = None,
    port: Annotated[int | None, typer.Option()] = None,
    reload: Annotated[bool, typer.Option()] = False,
) -> None:
    """Run the API server (and the in-process worker, unless disabled)."""
    import uvicorn

    settings = get_settings()
    host = host or settings.api_host
    port = port or settings.api_port

    is_loopback = host in {"127.0.0.1", "localhost", "::1"}
    if not is_loopback and not settings.api_token and not settings.allow_insecure_bind:
        typer.echo(
            f"refusing to bind {host} without AGENTBENCH_API_TOKEN set.\n"
            "This platform executes untrusted code; an unauthenticated non-loopback "
            "bind would let anyone on the network queue arbitrary benchmark runs.\n"
            "Set AGENTBENCH_API_TOKEN, or AGENTBENCH_ALLOW_INSECURE_BIND=true if you "
            "have another access control in front of it.",
            err=True,
        )
        raise typer.Exit(code=2)

    uvicorn.run("agentbench.api.main:app", host=host, port=port, reload=reload, log_config=None)


@app.command("worker")
def worker(
    concurrency: Annotated[int | None, typer.Option()] = None,
) -> None:
    """Run a standalone worker process."""
    import signal
    import time

    from agentbench.orchestrator.worker import Worker

    instance = Worker(concurrency=concurrency)
    instance.start()

    stopping = False

    def _handle(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)
    typer.echo(f"worker {instance.worker_id} started (concurrency={instance.concurrency})")
    try:
        while not stopping:
            time.sleep(0.5)
    finally:
        instance.stop()


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
