"""Enumerations shared by the persistence layer, orchestrator and API.

The split between :class:`RunState` (where a run is in its lifecycle) and
:class:`RunOutcome` (what the result *means*) is deliberate: it is what lets the
platform distinguish "the agent produced a wrong patch" from "our Docker daemon
died", which are not comparable signals.
"""

from __future__ import annotations

from enum import StrEnum


class RunState(StrEnum):
    """Lifecycle position of a single agent run. Only two states are terminal."""

    PENDING = "pending"
    CLAIMED = "claimed"
    PREPARING = "preparing"
    AGENT_RUNNING = "agent_running"
    COLLECTING = "collecting"
    GRADING = "grading"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_RUN_STATES

    @property
    def is_active(self) -> bool:
        return self in _ACTIVE_RUN_STATES


_TERMINAL_RUN_STATES = frozenset({RunState.COMPLETED, RunState.CANCELLED})
_ACTIVE_RUN_STATES = frozenset(
    {
        RunState.CLAIMED,
        RunState.PREPARING,
        RunState.AGENT_RUNNING,
        RunState.COLLECTING,
        RunState.GRADING,
        RunState.FINALIZING,
    }
)

#: Legal state transitions. Enforced by :func:`assert_transition`.
RUN_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.PENDING: frozenset({RunState.CLAIMED, RunState.CANCELLED}),
    RunState.CLAIMED: frozenset(
        {RunState.PREPARING, RunState.PENDING, RunState.COMPLETED, RunState.CANCELLED}
    ),
    RunState.PREPARING: frozenset(
        {RunState.AGENT_RUNNING, RunState.PENDING, RunState.COMPLETED, RunState.CANCELLED}
    ),
    RunState.AGENT_RUNNING: frozenset(
        {RunState.COLLECTING, RunState.PENDING, RunState.COMPLETED, RunState.CANCELLED}
    ),
    RunState.COLLECTING: frozenset(
        {RunState.GRADING, RunState.PENDING, RunState.COMPLETED, RunState.CANCELLED}
    ),
    RunState.GRADING: frozenset(
        {RunState.FINALIZING, RunState.PENDING, RunState.COMPLETED, RunState.CANCELLED}
    ),
    RunState.FINALIZING: frozenset({RunState.COMPLETED, RunState.CANCELLED}),
    RunState.COMPLETED: frozenset(),
    RunState.CANCELLED: frozenset(),
}


class IllegalTransition(RuntimeError):
    def __init__(self, src: RunState, dst: RunState) -> None:
        super().__init__(f"illegal run state transition: {src} -> {dst}")
        self.src = src
        self.dst = dst


def assert_transition(src: RunState, dst: RunState) -> None:
    if dst not in RUN_TRANSITIONS[src]:
        raise IllegalTransition(src, dst)


class RunOutcome(StrEnum):
    """Meaning of a terminated run.

    ``SOLVED`` / ``UNSOLVED`` are the only two outcomes that count towards
    success-rate metrics.  Everything else is excluded from the denominator and
    reported separately, because a crashed container says nothing about agent
    capability.
    """

    SOLVED = "solved"
    UNSOLVED = "unsolved"
    #: The agent process itself errored out (non-zero exit, protocol violation).
    AGENT_ERROR = "agent_error"
    #: The agent exceeded its wall-clock budget.
    TIMEOUT = "timeout"
    #: Sandbox/daemon/transport failure. Not attributable to the agent.
    INFRA_ERROR = "infra_error"
    #: The task's own environment or test baseline is broken.
    INVALID_TASK = "invalid_task"
    #: Grading could not produce a verdict (e.g. test runner crashed).
    GRADING_ERROR = "grading_error"
    CANCELLED = "cancelled"

    @property
    def counts_toward_success_rate(self) -> bool:
        return self in {RunOutcome.SOLVED, RunOutcome.UNSOLVED}

    @property
    def is_attributable_to_agent(self) -> bool:
        return self in {
            RunOutcome.SOLVED,
            RunOutcome.UNSOLVED,
            RunOutcome.AGENT_ERROR,
            RunOutcome.TIMEOUT,
        }


class ExperimentState(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {ExperimentState.COMPLETED, ExperimentState.CANCELLED}


class EventType(StrEnum):
    """Observable events recorded for every run.

    This stream intentionally records *observable* behaviour only: tool calls,
    commands, exit codes, provider-visible message metadata.  Hidden model
    reasoning is not available to this platform and is never claimed.
    """

    RUN_CREATED = "run.created"
    RUN_CLAIMED = "run.claimed"
    SANDBOX_CREATE = "sandbox.create"
    SANDBOX_READY = "sandbox.ready"
    SANDBOX_DESTROY = "sandbox.destroy"
    ENV_SETUP_START = "env.setup_start"
    ENV_SETUP_END = "env.setup_end"
    BASELINE_START = "baseline.start"
    BASELINE_END = "baseline.end"
    TASK_CONTEXT_SUPPLIED = "task.context_supplied"
    AGENT_START = "agent.start"
    MODEL_REQUEST = "model.request"
    MODEL_RESPONSE = "model.response"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    FILE_READ = "file.read"
    FILE_WRITE = "file.write"
    COMMAND_EXEC = "command.exec"
    CONTEXT_COMPACTION = "context.compaction"
    AGENT_MESSAGE = "agent.message"
    AGENT_END = "agent.end"
    RETRY = "retry"
    ERROR = "error"
    PATCH_GENERATED = "patch.generated"
    EVAL_START = "eval.start"
    EVAL_LAYER = "eval.layer"
    EVAL_END = "eval.end"
    RUN_FINALIZED = "run.finalized"
    RUN_TERMINATED = "run.terminated"


class FailureCategory(StrEnum):
    """Evidence-backed failure buckets. ``UNKNOWN`` is used rather than guessing."""

    INCORRECT_IMPLEMENTATION = "incorrect_implementation"
    TESTS_FAILING_AFTER_PATCH = "tests_failing_after_patch"
    EXISTING_REGRESSION = "existing_regression"
    NO_PATCH_PRODUCED = "no_patch_produced"
    FORBIDDEN_FILES_MODIFIED = "forbidden_files_modified"
    MISSING_DEPENDENCY = "missing_dependency"
    ENVIRONMENT_SETUP_FAILURE = "environment_setup_failure"
    TOOL_INVOCATION_ERROR = "tool_invocation_error"
    CONTEXT_OR_INSTRUCTION_ISSUE = "context_or_instruction_issue"
    TIMEOUT = "timeout"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    GRADING_FAILURE = "grading_failure"
    INVALID_TASK = "invalid_task"
    UNKNOWN = "unknown"


class ClassificationSource(StrEnum):
    RULE = "rule"
    MODEL = "model"
    HUMAN = "human"


class GradingLayerStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERRORED = "errored"


class TaskValidationStatus(StrEnum):
    UNVALIDATED = "unvalidated"
    VALIDATING = "validating"
    VALID = "valid"
    INVALID = "invalid"
    ERROR = "error"
