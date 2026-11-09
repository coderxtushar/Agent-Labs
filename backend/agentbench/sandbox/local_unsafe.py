"""Host-local execution provider. **Not a sandbox.**

This provider runs task commands as ordinary child processes in a temporary
directory on the host.  It provides *no* isolation: no filesystem confinement,
no network restriction, no resource limits beyond a wall-clock timeout.

It exists for two legitimate uses:

* running the platform's own test suite where a Docker daemon is unavailable;
* evaluating a repository you wrote yourself and fully trust.

It refuses to instantiate unless ``AGENTBENCH_ALLOW_LOCAL_UNSAFE_SANDBOX=true``
is set, and every run executed through it is tagged so results can never be
confused with sandboxed ones.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Mapping
from pathlib import Path

from agentbench.config import get_settings
from agentbench.logging_setup import get_logger
from agentbench.sandbox.base import (
    ExecResult,
    Sandbox,
    SandboxError,
    SandboxSpec,
    clamp_spec,
    truncate_output,
)

log = get_logger(__name__)


class LocalUnsafeSandbox(Sandbox):
    provider = "local_unsafe"

    def __init__(self, spec: SandboxSpec, *, run_id: str | None = None) -> None:
        self._settings = get_settings()
        if not self._settings.allow_local_unsafe_sandbox:
            raise SandboxError(
                "the local_unsafe provider gives no isolation and is disabled. "
                "Set AGENTBENCH_ALLOW_LOCAL_UNSAFE_SANDBOX=true to enable it, and only "
                "for repositories you fully trust."
            )
        self._spec = clamp_spec(spec)
        self._run_id = run_id or uuid.uuid4().hex
        self._root: Path | None = None
        self._destroyed = False

    @property
    def sandbox_id(self) -> str:
        return f"local:{self._root}" if self._root else f"local:{self._run_id}"

    @property
    def _workdir(self) -> Path:
        assert self._root is not None
        # The container-style absolute workdir is remapped under the temp root.
        return self._root / self._spec.workdir.lstrip("/")

    def start(self) -> None:
        if self._root is not None:
            raise SandboxError("sandbox already started")
        self._root = Path(tempfile.mkdtemp(prefix=f"agentbench-local-{self._run_id[:8]}-"))
        self._workdir.mkdir(parents=True, exist_ok=True)
        log.warning(
            "sandbox.local_unsafe_started",
            extra={"run_id": self._run_id, "root": str(self._root)},
        )

    def destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        if self._root and self._root.exists():
            shutil.rmtree(self._root, ignore_errors=True)

    def _resolve(self, path: str) -> Path:
        """Map a sandbox-absolute path into the temp root, refusing escapes."""
        assert self._root is not None
        candidate = (self._root / path.lstrip("/")).resolve()
        root = self._root.resolve()
        if candidate != root and root not in candidate.parents:
            raise SandboxError(f"path escapes sandbox root: {path}")
        return candidate

    def exec(
        self,
        command: str,
        *,
        timeout_s: int,
        workdir: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        if self._root is None:
            raise SandboxError("sandbox not started")
        cwd = self._resolve(workdir) if workdir else self._workdir
        cwd.mkdir(parents=True, exist_ok=True)

        child_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self._root),
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        child_env.update(self._spec.env)
        child_env.update(env or {})

        t0 = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(  # noqa: S602 - documented unsafe provider
                ["sh", "-s"],
                input=f"set -e\n{command}\n",
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=cwd,
                env=child_env,
                check=False,
            )
            exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = 124
            stdout = (
                exc.stdout
                if isinstance(exc.stdout, str)
                else (exc.stdout or b"").decode(errors="replace")
            )
            stderr = (
                exc.stderr
                if isinstance(exc.stderr, str)
                else (exc.stderr or b"").decode(errors="replace")
            )

        limit = self._settings.log_tail_max_chars
        stdout, t1 = truncate_output(stdout, limit)
        stderr, t2 = truncate_output(stderr, limit)
        return ExecResult(
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_s=time.monotonic() - t0,
            timed_out=timed_out,
            output_truncated=t1 or t2,
        )

    def write_file(self, path: str, content: str) -> None:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def read_file(self, path: str, max_bytes: int = 1_000_000) -> str:
        target = self._resolve(path)
        if not target.exists():
            raise FileNotFoundError(path)
        return target.read_bytes()[:max_bytes].decode("utf-8", errors="replace")

    def copy_in(self, local_path: Path, dest_path: str) -> None:
        target = self._resolve(dest_path)
        if local_path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            shutil.copytree(local_path, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_path, target)

    def copy_out(self, path: str, local_path: Path) -> None:
        source = self._resolve(path)
        if not source.exists():
            raise SandboxError(f"sandbox path missing: {path}")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            if local_path.exists():
                shutil.rmtree(local_path)
            shutil.copytree(source, local_path)
        else:
            shutil.copy2(source, local_path)
