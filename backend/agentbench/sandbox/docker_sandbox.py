"""Docker-backed sandbox provider.

Implemented against the ``docker`` CLI rather than the SDK so that every
privileged operation is a single, auditable argument vector that is logged
verbatim.  Task-controlled strings are never interpolated into a host shell:
commands are piped to ``sh -s`` on the container's stdin, and files move through
``docker cp``.

Hardening applied to every container
------------------------------------
``--network none`` (unless the task explicitly opts in), ``--cap-drop ALL``,
``--security-opt no-new-privileges``, a pids limit, memory and CPU limits, a
size-capped ``tmpfs`` on ``/tmp``, and no host bind mounts of any kind.  The
Docker socket, SSH keys and host environment are never exposed.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path

from agentbench.config import get_settings
from agentbench.logging_setup import get_logger
from agentbench.sandbox.base import (
    ExecResult,
    Sandbox,
    SandboxError,
    SandboxSpec,
    SandboxUnavailable,
    clamp_spec,
    truncate_output,
)
from agentbench.schemas.task import NetworkPolicy

log = get_logger(__name__)

LABEL_NAMESPACE = "io.agentbench"
_DAEMON_ERROR_MARKERS = (
    "cannot connect to the docker daemon",
    "is the docker daemon running",
    "error during connect",
    "docker daemon is not running",
)


def _run_host(
    argv: Sequence[str], timeout_s: int, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a docker CLI invocation on the host. Never takes a shell string."""
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            list(argv),
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SandboxUnavailable(f"docker binary not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SandboxError(
            f"docker CLI call timed out after {timeout_s}s: {' '.join(argv[:3])}"
        ) from exc


#: Probing the daemon costs a subprocess round trip (seconds when it is down),
#: and status endpoints call it on every request. A short TTL keeps the answer
#: fresh enough to be useful without making the API feel broken.
_PROBE_TTL_S = 10.0
_probe_cache: tuple[float, bool, str] | None = None


def docker_available(binary: str | None = None, *, use_cache: bool = True) -> tuple[bool, str]:
    """Probe the daemon. Returns ``(available, detail)``."""
    global _probe_cache
    binary = binary or get_settings().docker_binary

    if use_cache and _probe_cache is not None:
        stamp, available, detail = _probe_cache
        if time.monotonic() - stamp < _PROBE_TTL_S:
            return available, detail

    try:
        proc = _run_host([binary, "version", "--format", "{{.Server.Version}}"], timeout_s=20)
    except SandboxError as exc:
        _probe_cache = (time.monotonic(), False, str(exc))
        return False, str(exc)

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[:500]
        _probe_cache = (time.monotonic(), False, detail)
        return False, detail

    detail = proc.stdout.strip()
    _probe_cache = (time.monotonic(), True, detail)
    return True, detail


def image_present(image: str, binary: str | None = None) -> bool:
    binary = binary or get_settings().docker_binary
    proc = _run_host([binary, "image", "inspect", image], timeout_s=30)
    return proc.returncode == 0


class DockerSandbox(Sandbox):
    provider = "docker"

    def __init__(self, spec: SandboxSpec, *, run_id: str | None = None) -> None:
        self._settings = get_settings()
        self._spec = clamp_spec(spec)
        self._run_id = run_id or uuid.uuid4().hex
        self._name = f"agentbench-{self._run_id[:12]}-{uuid.uuid4().hex[:6]}"
        self._container_id: str | None = None
        self._destroyed = False
        self._binary = self._settings.docker_binary

    # -- identity ----------------------------------------------------------
    @property
    def sandbox_id(self) -> str:
        return self._container_id or self._name

    @property
    def spec(self) -> SandboxSpec:
        return self._spec

    # -- lifecycle ---------------------------------------------------------
    def _create_argv(self) -> list[str]:
        s = self._spec
        argv = [
            self._binary,
            "create",
            "--name",
            self._name,
            "--label",
            f"{LABEL_NAMESPACE}.managed=true",
            "--label",
            f"{LABEL_NAMESPACE}.run_id={self._run_id}",
            "--memory",
            f"{s.memory_mb}m",
            # Prevent the OOM killer from silently swapping instead of failing.
            "--memory-swap",
            f"{s.memory_mb}m",
            "--cpus",
            str(s.cpus),
            "--pids-limit",
            str(s.pids_limit),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            f"/tmp:rw,nosuid,nodev,size={min(s.disk_mb, 2048)}m",
            "--workdir",
            s.workdir,
        ]
        for key, value in s.labels.items():
            argv += ["--label", f"{LABEL_NAMESPACE}.{key}={value}"]

        if s.network is NetworkPolicy.NONE:
            argv += ["--network", "none"]
        # MODEL_ONLY and FULL both use the default bridge today; the difference
        # is recorded for auditing and is enforced at the policy layer, not by
        # per-destination firewalling. See docs/security.md.

        for key, value in s.env.items():
            argv += ["--env", f"{key}={value}"]

        argv += [s.image, "sh", "-c", "mkdir -p /workspace && exec sleep infinity"]
        return argv

    def start(self) -> None:
        if self._container_id is not None:
            raise SandboxError("sandbox already started")

        available, detail = docker_available(self._binary)
        if not available:
            raise SandboxUnavailable(f"docker daemon unavailable: {detail}")

        if not image_present(self._spec.image, self._binary):
            raise SandboxUnavailable(
                f"image '{self._spec.image}' is not present locally. "
                f"Pull it first (`docker pull {self._spec.image}`); image pulls are not "
                "performed implicitly during a benchmark run so that runs stay reproducible "
                "and offline-safe."
            )

        argv = self._create_argv()
        log.info("sandbox.create", extra={"run_id": self._run_id, "argv": argv})
        proc = _run_host(argv, timeout_s=120)
        if proc.returncode != 0:
            raise SandboxError(
                f"docker create failed: {(proc.stderr or proc.stdout).strip()[:1000]}"
            )
        self._container_id = proc.stdout.strip()

        proc = _run_host([self._binary, "start", self._container_id], timeout_s=120)
        if proc.returncode != 0:
            self.destroy()
            raise SandboxError(
                f"docker start failed: {(proc.stderr or proc.stdout).strip()[:1000]}"
            )

        # Prove the container is actually executing before handing it work.
        probe = self.exec("printf ready", timeout_s=30)
        if not probe.ok or "ready" not in probe.stdout:
            self.destroy()
            raise SandboxError(f"sandbox failed readiness probe: {probe.summary()}")

    def destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        target = self._container_id or self._name
        try:
            proc = _run_host([self._binary, "rm", "-f", "-v", target], timeout_s=120)
            if proc.returncode != 0 and "no such container" not in (proc.stderr or "").lower():
                log.warning(
                    "sandbox.destroy_failed",
                    extra={"run_id": self._run_id, "target": target, "stderr": proc.stderr[:500]},
                )
            else:
                log.info("sandbox.destroy", extra={"run_id": self._run_id, "target": target})
        except SandboxError as exc:  # never raise from teardown
            log.warning("sandbox.destroy_error", extra={"run_id": self._run_id, "error": str(exc)})

    # -- execution ---------------------------------------------------------
    def exec(
        self,
        command: str,
        *,
        timeout_s: int,
        workdir: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        if self._container_id is None:
            raise SandboxError("sandbox not started")

        argv = [self._binary, "exec", "-i"]
        if workdir:
            argv += ["--workdir", workdir]
        for key, value in (env or {}).items():
            argv += ["--env", f"{key}={value}"]
        # `sh -s` reads the program from stdin, so no task-controlled text ever
        # becomes a host argv element or passes through a host shell.
        argv += [self._container_id, "sh", "-s"]

        script = f"set -e\n{command}\n"
        t0 = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, input on stdin
                argv,
                input=script,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
            exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = 124  # conventional `timeout(1)` status
            stdout = (
                exc.stdout.decode(errors="replace")
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "")
            )
            stderr = (
                exc.stderr.decode(errors="replace")
                if isinstance(exc.stderr, bytes)
                else (exc.stderr or "")
            )
            # The process inside the container survives the client kill, so stop
            # the container to guarantee the budget is actually enforced.
            self._force_stop()
        except FileNotFoundError as exc:
            raise SandboxUnavailable(f"docker binary not found: {self._binary}") from exc

        duration = time.monotonic() - t0
        limit = self._settings.log_tail_max_chars
        stdout, t1 = truncate_output(stdout, limit)
        stderr, t2 = truncate_output(stderr, limit)
        return ExecResult(
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_s=duration,
            timed_out=timed_out,
            output_truncated=t1 or t2,
        )

    def _force_stop(self) -> None:
        if self._container_id is None:
            return
        with contextlib.suppress(SandboxError):
            _run_host([self._binary, "kill", self._container_id], timeout_s=60)

    # -- files -------------------------------------------------------------
    def write_file(self, path: str, content: str) -> None:
        if self._container_id is None:
            raise SandboxError("sandbox not started")
        parent = os.path.dirname(path) or "/"
        mk = self.exec(f"mkdir -p {_shq(parent)}", timeout_s=60)
        if not mk.ok:
            raise SandboxError(f"could not create {parent}: {mk.stderr[:500]}")

        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "payload"
            local.write_text(content, encoding="utf-8")
            proc = _run_host(
                [self._binary, "cp", str(local), f"{self._container_id}:{path}"], timeout_s=180
            )
        if proc.returncode != 0:
            raise SandboxError(f"docker cp into sandbox failed: {(proc.stderr or '')[:500]}")

    def read_file(self, path: str, max_bytes: int = 1_000_000) -> str:
        if self._container_id is None:
            raise SandboxError("sandbox not started")
        # `docker cp` refuses to read from tmpfs and volume mounts, and the
        # grading report lives on the size-capped tmpfs at /tmp. Streaming the
        # file through base64 on stdout works for every mount type and keeps
        # the content byte-exact, so it is used for all reads.
        script = (
            f"if [ ! -f {_shq(path)} ]; then printf '__AGENTBENCH_MISSING__'; exit 0; fi\n"
            f"base64 < {_shq(path)} | tr -d '\\n'\n"
        )
        proc = _run_host(
            [self._binary, "exec", "-i", self._container_id, "sh", "-s"],
            timeout_s=300,
            stdin=script,
        )
        if proc.returncode != 0:
            raise SandboxError(
                f"could not read '{path}' from the sandbox: {(proc.stderr or '')[:500]}"
            )
        payload = proc.stdout.strip()
        if payload == "__AGENTBENCH_MISSING__" or not payload:
            if payload == "__AGENTBENCH_MISSING__":
                raise FileNotFoundError(path)
            return ""
        try:
            data = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SandboxError(f"corrupt read of '{path}' from the sandbox: {exc}") from exc
        return data[:max_bytes].decode("utf-8", errors="replace")

    def copy_in(self, local_path: Path, dest_path: str) -> None:
        if self._container_id is None:
            raise SandboxError("sandbox not started")
        if not local_path.exists():
            raise SandboxError(f"host path does not exist: {local_path}")
        mk = self.exec(f"mkdir -p {_shq(dest_path)}", timeout_s=60)
        if not mk.ok:
            raise SandboxError(f"could not create {dest_path}: {mk.stderr[:500]}")
        src = f"{local_path}/." if local_path.is_dir() else str(local_path)
        proc = _run_host(
            [self._binary, "cp", src, f"{self._container_id}:{dest_path}"], timeout_s=600
        )
        if proc.returncode != 0:
            raise SandboxError(f"docker cp -> sandbox failed: {(proc.stderr or '')[:800]}")

    def copy_out(self, path: str, local_path: Path) -> None:
        if self._container_id is None:
            raise SandboxError("sandbox not started")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        if local_path.exists() and local_path.is_dir():
            shutil.rmtree(local_path)
        proc = _run_host(
            [self._binary, "cp", f"{self._container_id}:{path}", str(local_path)], timeout_s=600
        )
        if proc.returncode != 0:
            raise SandboxError(f"docker cp <- sandbox failed: {(proc.stderr or '')[:800]}")


def _shq(value: str) -> str:
    """Single-quote a value for use inside a container-side ``sh`` script."""
    return "'" + value.replace("'", "'\\''") + "'"


def cleanup_orphans(older_than_s: int = 0) -> list[str]:
    """Remove containers this platform created but did not tear down.

    Called at worker startup to recover from a hard crash.  Only containers
    carrying the platform's own label are touched.
    """
    settings = get_settings()
    binary = settings.docker_binary
    available, _ = docker_available(binary)
    if not available:
        return []
    proc = _run_host(
        [
            binary,
            "ps",
            "-a",
            "--filter",
            f"label={LABEL_NAMESPACE}.managed=true",
            "--format",
            "{{.ID}} {{.Names}}",
        ],
        timeout_s=60,
    )
    if proc.returncode != 0:
        return []
    removed: list[str] = []
    for line in proc.stdout.strip().splitlines():
        cid = line.split()[0] if line.split() else ""
        if not cid:
            continue
        rm = _run_host([binary, "rm", "-f", "-v", cid], timeout_s=120)
        if rm.returncode == 0:
            removed.append(cid)
    if removed:
        log.info("sandbox.orphans_removed", extra={"count": len(removed)})
    return removed
