# Benchmark execution image.
#
# Benchmark runs execute with networking disabled, so everything the task needs
# must already be in the image.  This image is built once, with network access,
# as an explicit setup step (`agentbench images build`); it is never built or
# pulled implicitly during a run, which keeps runs reproducible and offline.
#
# Pinned versions are part of the experiment's environment identity: changing
# them changes what results mean, so bump the image tag when you bump them.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Test runner and the linters used by optional static-check grading layers.
RUN pip install --no-cache-dir \
        "pytest==8.3.4" \
        "pytest-timeout==2.3.1" \
        "ruff==0.8.4" \
    && python -m pytest --version \
    && ruff --version

# Runs never need root. The workspace is owned by this user.
RUN useradd --create-home --uid 10001 agent \
    && mkdir -p /workspace \
    && chown -R agent:agent /workspace

USER agent
WORKDIR /workspace/repo

LABEL io.agentbench.image="benchmark-python" \
      io.agentbench.python="3.12" \
      io.agentbench.pytest="8.3.4"
