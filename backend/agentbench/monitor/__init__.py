"""Read-only host observation: resources, Docker daemon and local Ollama.

Nothing in this package mutates the host. Every probe is a short, bounded,
argv-only subprocess or a loopback HTTP GET; a metric the platform cannot read
is reported as ``None`` with a reason rather than guessed.
"""
