"""Real container isolation, resource limits and cleanup.

Skipped when no Docker daemon is reachable, because these assertions are only
meaningful against the actual provider.  They cover the properties the platform
*claims*: no network, no host mounts, enforced timeouts, dropped capabilities,
and deterministic teardown.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from agentbench.config import get_settings
from agentbench.sandbox.base import SandboxError, SandboxSpec, SandboxUnavailable
from agentbench.sandbox.docker_sandbox import (
    LABEL_NAMESPACE,
    DockerSandbox,
    cleanup_orphans,
    docker_available,
    image_present,
)
from agentbench.schemas.task import NetworkPolicy

pytestmark = pytest.mark.docker

IMAGE = "agentbench/benchmark-python:3.12"


def _skip_unless_ready() -> None:
    available, detail = docker_available(use_cache=False)
    if not available:
        pytest.skip(f"no Docker daemon: {detail}")
    if not image_present(IMAGE):
        pytest.skip(f"benchmark image '{IMAGE}' is not built; run `agentbench images build`")


@pytest.fixture
def sandbox() -> Any:
    _skip_unless_ready()
    instance = DockerSandbox(
        SandboxSpec(image=IMAGE, workdir="/workspace/repo", memory_mb=512, cpus=1.0),
        run_id="pytest",
    )
    instance.start()
    try:
        yield instance
    finally:
        instance.destroy()


def containers_with_label() -> list[str]:
    proc = subprocess.run(  # noqa: S603
        [
            get_settings().docker_binary,
            "ps",
            "-a",
            "--filter",
            f"label={LABEL_NAMESPACE}.managed=true",
            "--format",
            "{{.ID}}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return [line for line in proc.stdout.strip().splitlines() if line]


class TestExecution:
    def test_commands_run_and_report_their_exit_code(self, sandbox: Any) -> None:
        assert sandbox.exec("exit 0", timeout_s=30).exit_code == 0
        assert sandbox.exec("exit 7", timeout_s=30).exit_code == 7

    def test_stdout_and_stderr_are_captured_separately(self, sandbox: Any) -> None:
        result = sandbox.exec("echo out; echo err >&2", timeout_s=30)
        assert "out" in result.stdout
        assert "err" in result.stderr

    def test_shell_metacharacters_in_content_are_not_evaluated(self, sandbox: Any) -> None:
        """Task and agent content reaches the container on stdin, never as a
        host argv element, so injection through a filename cannot happen."""
        payload = "$(id) `whoami` ; rm -rf / && echo pwned"
        sandbox.write_file("/workspace/repo/payload.txt", payload)
        assert sandbox.read_file("/workspace/repo/payload.txt") == payload

    def test_working_directory_is_honoured(self, sandbox: Any) -> None:
        sandbox.exec("mkdir -p /workspace/repo/sub", timeout_s=30)
        result = sandbox.exec("pwd", timeout_s=30, workdir="/workspace/repo/sub")
        assert result.stdout.strip() == "/workspace/repo/sub"

    def test_environment_variables_are_passed_through(self, sandbox: Any) -> None:
        result = sandbox.exec("echo $MY_VAR", timeout_s=30, env={"MY_VAR": "hello"})
        assert result.stdout.strip() == "hello"


class TestIsolation:
    def test_network_is_unreachable_by_default(self, sandbox: Any) -> None:
        result = sandbox.exec(
            "python -c \"import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)\"",
            timeout_s=30,
        )
        assert not result.ok

    def test_dns_resolution_fails_without_network(self, sandbox: Any) -> None:
        result = sandbox.exec(
            "python -c \"import socket; socket.gethostbyname('example.com')\"", timeout_s=30
        )
        assert not result.ok

    def test_no_host_filesystem_is_mounted(self, sandbox: Any) -> None:
        marker = str(Path(__file__).resolve())
        result = sandbox.exec(f"test -e '{marker}'", timeout_s=30)
        assert result.exit_code != 0

    def test_the_docker_socket_is_not_exposed(self, sandbox: Any) -> None:
        assert sandbox.exec("test -S /var/run/docker.sock", timeout_s=30).exit_code != 0

    def test_runs_as_a_non_root_user(self, sandbox: Any) -> None:
        assert sandbox.exec("id -u", timeout_s=30).stdout.strip() != "0"

    def test_capabilities_are_dropped(self, sandbox: Any) -> None:
        """A dropped-capability container cannot change file ownership."""
        sandbox.write_file("/workspace/repo/f.txt", "x")
        assert sandbox.exec("chown 0:0 /workspace/repo/f.txt", timeout_s=30).exit_code != 0

    def test_the_container_declares_the_expected_hardening(self, sandbox: Any) -> None:
        proc = subprocess.run(  # noqa: S603
            [
                get_settings().docker_binary,
                "inspect",
                sandbox.sandbox_id,
                "--format",
                "{{.HostConfig.NetworkMode}}|{{.HostConfig.CapDrop}}|"
                "{{.HostConfig.SecurityOpt}}|{{.HostConfig.Memory}}|{{.HostConfig.PidsLimit}}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        network, cap_drop, security, memory, pids = proc.stdout.strip().split("|")
        assert network == "none"
        assert "ALL" in cap_drop
        assert "no-new-privileges" in security
        assert int(memory) == 512 * 1024 * 1024
        assert int(pids) > 0


class TestLimits:
    def test_a_command_exceeding_its_budget_is_killed(self, sandbox: Any) -> None:
        result = sandbox.exec("sleep 60", timeout_s=2)
        assert result.timed_out is True
        assert result.exit_code == 124
        assert result.duration_s < 20

    def test_memory_limit_is_enforced(self, sandbox: Any) -> None:
        result = sandbox.exec('python -c "x = bytearray(2 * 1024 * 1024 * 1024)"', timeout_s=60)
        assert not result.ok

    def test_requested_limits_are_clamped_to_the_global_ceiling(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _skip_unless_ready()
        from agentbench.config import reset_settings_cache

        monkeypatch.setenv("AGENTBENCH_MAX_MEMORY_MB", "256")
        reset_settings_cache()
        instance = DockerSandbox(SandboxSpec(image=IMAGE, memory_mb=99_999), run_id="clamp")
        assert instance.spec.memory_mb == 256


class TestFileTransfer:
    def test_a_directory_round_trips(self, sandbox: Any, tmp_path: Path) -> None:
        source = tmp_path / "src"
        (source / "nested").mkdir(parents=True)
        (source / "a.txt").write_text("alpha", encoding="utf-8")
        (source / "nested" / "b.txt").write_text("beta", encoding="utf-8")

        sandbox.copy_in(source, "/workspace/repo")
        out = tmp_path / "out"
        sandbox.copy_out("/workspace/repo", out)

        assert (out / "a.txt").read_text(encoding="utf-8") == "alpha"
        assert (out / "nested" / "b.txt").read_text(encoding="utf-8") == "beta"

    def test_reading_from_a_tmpfs_mount_works(self, sandbox: Any) -> None:
        """`/tmp` is a tmpfs, which `docker cp` refuses to read; the provider
        streams instead, and this is the regression guard for that."""
        sandbox.exec("printf 'tmpfs-content' > /tmp/probe.txt", timeout_s=30)
        assert sandbox.read_file("/tmp/probe.txt") == "tmpfs-content"

    def test_reading_a_missing_file_raises_file_not_found(self, sandbox: Any) -> None:
        with pytest.raises(FileNotFoundError):
            sandbox.read_file("/workspace/repo/absent.txt")

    def test_unicode_content_survives_the_round_trip(self, sandbox: Any) -> None:
        payload = "héllo wörld ✅ 日本語\n"
        sandbox.write_file("/workspace/repo/u.txt", payload)
        assert sandbox.read_file("/workspace/repo/u.txt") == payload


class TestLifecycle:
    def test_the_container_is_removed_on_destroy(self) -> None:
        _skip_unless_ready()
        instance = DockerSandbox(SandboxSpec(image=IMAGE), run_id="teardown")
        instance.start()
        container_id = instance.sandbox_id
        assert container_id[:12] in "".join(containers_with_label())

        instance.destroy()
        assert container_id[:12] not in "".join(containers_with_label())

    def test_destroy_is_idempotent(self) -> None:
        _skip_unless_ready()
        instance = DockerSandbox(SandboxSpec(image=IMAGE), run_id="idempotent")
        instance.start()
        instance.destroy()
        instance.destroy()

    def test_destroy_is_safe_after_a_failed_start(self) -> None:
        _skip_unless_ready()
        instance = DockerSandbox(SandboxSpec(image="agentbench/not-a-real-image:0"), run_id="x")
        with pytest.raises(SandboxUnavailable):
            instance.start()
        instance.destroy()

    def test_context_manager_tears_down_on_exception(self) -> None:
        _skip_unless_ready()
        instance = DockerSandbox(SandboxSpec(image=IMAGE), run_id="ctx")
        with pytest.raises(RuntimeError), instance:
            raise RuntimeError("boom")
        assert instance.sandbox_id[:12] not in "".join(containers_with_label())

    def test_executing_before_start_is_refused(self) -> None:
        _skip_unless_ready()
        with pytest.raises(SandboxError):
            DockerSandbox(SandboxSpec(image=IMAGE), run_id="nostart").exec("echo", timeout_s=5)

    def test_a_missing_image_is_reported_as_unavailable_not_pulled(self) -> None:
        """Implicit pulls would make a benchmark run depend on the network and
        on whatever the registry serves today."""
        _skip_unless_ready()
        instance = DockerSandbox(
            SandboxSpec(image="agentbench/definitely-absent:0"), run_id="nopull"
        )
        with pytest.raises(SandboxUnavailable) as exc:
            instance.start()
        assert "not present locally" in str(exc.value)

    def test_orphan_cleanup_only_touches_labelled_containers(self) -> None:
        _skip_unless_ready()
        instance = DockerSandbox(SandboxSpec(image=IMAGE), run_id="orphan")
        instance.start()
        container_id = instance.sandbox_id

        removed = cleanup_orphans()

        assert any(
            container_id.startswith(entry) or entry.startswith(container_id[:12])
            for entry in removed
        )
        assert containers_with_label() == []


class TestNetworkPolicy:
    def test_model_only_policy_leaves_the_network_reachable(self) -> None:
        """Recorded for auditing: this policy is weaker isolation, and the
        platform says so rather than implying per-destination firewalling."""
        _skip_unless_ready()
        instance = DockerSandbox(
            SandboxSpec(image=IMAGE, network=NetworkPolicy.MODEL_ONLY), run_id="netpolicy"
        )
        instance.start()
        try:
            proc = subprocess.run(  # noqa: S603
                [
                    get_settings().docker_binary,
                    "inspect",
                    instance.sandbox_id,
                    "--format",
                    "{{.HostConfig.NetworkMode}}",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            assert proc.stdout.strip() != "none"
        finally:
            instance.destroy()
