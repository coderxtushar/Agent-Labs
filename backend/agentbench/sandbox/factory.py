"""Sandbox provider selection.

Adding a stronger isolation backend (Firecracker microVM, gVisor, a remote
sandbox service) means implementing :class:`agentbench.sandbox.base.Sandbox` and
registering it here.  Nothing above this module knows which provider is in use.
No such backend ships today - the only providers that exist are ``docker`` and
the explicitly unsafe ``local_unsafe``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agentbench.config import get_settings
from agentbench.sandbox.base import Sandbox, SandboxSpec, SandboxUnavailable
from agentbench.sandbox.docker_sandbox import DockerSandbox, docker_available, image_present
from agentbench.sandbox.local_unsafe import LocalUnsafeSandbox

SandboxFactory = Callable[[SandboxSpec, str], Sandbox]

_PROVIDERS: dict[str, SandboxFactory] = {
    "docker": lambda spec, run_id: DockerSandbox(spec, run_id=run_id),
    "local_unsafe": lambda spec, run_id: LocalUnsafeSandbox(spec, run_id=run_id),
}


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    name: str
    available: bool
    detail: str
    isolation: str


def create_sandbox(spec: SandboxSpec, run_id: str, provider: str | None = None) -> Sandbox:
    provider = provider or get_settings().sandbox_provider
    factory = _PROVIDERS.get(provider)
    if factory is None:
        raise SandboxUnavailable(f"unknown sandbox provider: {provider}")
    return factory(spec, run_id)


def provider_status(provider: str | None = None) -> ProviderStatus:
    settings = get_settings()
    provider = provider or settings.sandbox_provider
    if provider == "docker":
        ok, detail = docker_available()
        return ProviderStatus(
            name="docker",
            available=ok,
            detail=f"server {detail}" if ok else detail,
            isolation="container (shared kernel; not a VM boundary)",
        )
    if provider == "local_unsafe":
        return ProviderStatus(
            name="local_unsafe",
            available=settings.allow_local_unsafe_sandbox,
            detail=(
                "enabled - NO ISOLATION"
                if settings.allow_local_unsafe_sandbox
                else "disabled (set AGENTBENCH_ALLOW_LOCAL_UNSAFE_SANDBOX=true)"
            ),
            isolation="none",
        )
    return ProviderStatus(provider, False, "unknown provider", "unknown")


def all_provider_statuses() -> list[ProviderStatus]:
    return [provider_status(name) for name in _PROVIDERS]


__all__ = [
    "ProviderStatus",
    "Sandbox",
    "SandboxSpec",
    "all_provider_statuses",
    "create_sandbox",
    "docker_available",
    "image_present",
    "provider_status",
]
