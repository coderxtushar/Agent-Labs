"""Execution sandbox abstraction.

Everything the platform runs on behalf of a task - repository setup commands,
the agent itself, and the grading test suite - goes through this interface.  No
module above this one is permitted to call :mod:`subprocess` on task-controlled
input.

Threat model
------------
Benchmark repositories, task setup commands, agent-generated code and test
commands are all treated as **untrusted**.  The Docker provider therefore drops
all capabilities, disables privilege escalation, disables networking by default,
and mounts nothing from the host.  It does *not* provide a security boundary
equivalent to a virtual machine: a container escape or a kernel vulnerability
would reach the host.  See ``docs/security.md``.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentbench.schemas.task import NetworkPolicy


class SandboxError(RuntimeError):
    """Infrastructure-level sandbox failure.

    Raising this (rather than returning a failed :class:`ExecResult`) is the
    signal that a problem is *ours*, not the agent's, and the run should be
    recorded as ``INFRA_ERROR``.
    """


class SandboxUnavailable(SandboxError):
    """The provider's backing service is not usable (e.g. no Docker daemon)."""


@dataclass(slots=True)
class ExecResult:
    """Outcome of a single command execution inside a sandbox."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    #: True when the command was killed for exceeding its time budget. In that
    #: case ``exit_code`` is synthetic and must not be interpreted as a real
    #: process status.
    timed_out: bool = False
    #: True when captured output was truncated for retention reasons.
    output_truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def summary(self, tail: int = 2000) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "duration_s": round(self.duration_s, 3),
            "stdout_tail": self.stdout[-tail:],
            "stderr_tail": self.stderr[-tail:],
            "output_truncated": self.output_truncated,
        }


@dataclass(slots=True)
class SandboxSpec:
    """Requested sandbox shape. Hard ceilings from settings are applied on top."""

    image: str
    workdir: str = "/workspace/repo"
    memory_mb: int = 2048
    cpus: float = 2.0
    disk_mb: int = 4096
    pids_limit: int = 512
    network: NetworkPolicy = NetworkPolicy.NONE
    env: dict[str, str] = field(default_factory=dict)
    #: Labels applied to the container for traceability and orphan cleanup.
    labels: dict[str, str] = field(default_factory=dict)


class Sandbox(ABC):
    """A disposable, isolated execution environment.

    Instances are single-use.  :meth:`destroy` is idempotent and must be safe to
    call after a failed start.
    """

    provider: str = "abstract"

    @property
    @abstractmethod
    def sandbox_id(self) -> str:
        """Provider-specific identifier, recorded on the run for traceability."""

    @abstractmethod
    def start(self) -> None:
        """Create and start the environment. Raises :class:`SandboxError`."""

    @abstractmethod
    def exec(
        self,
        command: str,
        *,
        timeout_s: int,
        workdir: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        """Run ``command`` through ``sh`` and capture its output.

        Never raises on a non-zero exit code; that is the caller's business.
        Raises :class:`SandboxError` only for sandbox-level faults.
        """

    @abstractmethod
    def write_file(self, path: str, content: str) -> None:
        """Write a UTF-8 text file inside the sandbox, creating parent dirs."""

    @abstractmethod
    def read_file(self, path: str, max_bytes: int = 1_000_000) -> str:
        """Read a UTF-8 text file. Raises :class:`FileNotFoundError` if absent."""

    @abstractmethod
    def copy_in(self, local_path: Path, dest_path: str) -> None:
        """Recursively copy a host directory (or file) into the sandbox."""

    @abstractmethod
    def copy_out(self, path: str, local_path: Path) -> None:
        """Copy a sandbox path out to the host."""

    @abstractmethod
    def destroy(self) -> None:
        """Tear down all resources. Idempotent; must not raise."""

    # -- convenience -------------------------------------------------------
    def exec_checked(
        self, command: str, *, timeout_s: int, workdir: str | None = None
    ) -> ExecResult:
        result = self.exec(command, timeout_s=timeout_s, workdir=workdir)
        if not result.ok:
            raise SandboxError(
                f"command failed ({result.exit_code}, timed_out={result.timed_out}): {command}\n"
                f"{result.stderr[-2000:]}"
            )
        return result

    def __enter__(self) -> Sandbox:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.destroy()


def sh_quote(value: str) -> str:
    """Single-quote a value for safe use inside a container-side ``sh`` script.

    Used for every path, test node ID and task-supplied string that reaches a
    command line, so that shell metacharacters in task data cannot change the
    structure of the command.
    """
    return "'" + value.replace("'", "'\\''") + "'"


def clamp_spec(spec: SandboxSpec) -> SandboxSpec:
    """Apply global hard ceilings. A task may request less, never more."""
    from agentbench.config import get_settings

    s = get_settings()
    spec.memory_mb = min(spec.memory_mb, s.max_memory_mb)
    spec.cpus = min(spec.cpus, s.max_cpus)
    return spec


def clamp_timeout(requested: int, ceiling: int) -> int:
    return max(1, min(int(requested), int(ceiling)))


def truncate_output(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    head = limit // 2
    tail = limit - head
    return (
        f"{text[:head]}\n...[{len(text) - limit} characters truncated]...\n{text[-tail:]}",
        True,
    )


class _Timer:
    def __enter__(self) -> _Timer:
        self._t0 = time.monotonic()
        return self

    def __exit__(self, *exc: object) -> None:
        self.elapsed = time.monotonic() - self._t0

    elapsed: float = 0.0
