"""Run state machine, metric calculations and failure classification."""

from __future__ import annotations

import pytest

from agentbench.metrics.compute import Rate, make_rate, pass_at_k, wilson_interval
from agentbench.schemas.enums import (
    RUN_TRANSITIONS,
    IllegalTransition,
    RunOutcome,
    RunState,
    assert_transition,
)


class TestRunStateMachine:
    def test_happy_path_is_legal(self) -> None:
        path = [
            RunState.PENDING,
            RunState.CLAIMED,
            RunState.PREPARING,
            RunState.AGENT_RUNNING,
            RunState.COLLECTING,
            RunState.GRADING,
            RunState.FINALIZING,
            RunState.COMPLETED,
        ]
        for src, dst in zip(path[:-1], path[1:], strict=True):
            assert_transition(src, dst)

    @pytest.mark.parametrize(
        ("src", "dst"),
        [
            (RunState.PENDING, RunState.GRADING),
            (RunState.PENDING, RunState.COMPLETED),
            (RunState.COMPLETED, RunState.PENDING),
            (RunState.COMPLETED, RunState.CLAIMED),
            (RunState.CANCELLED, RunState.COMPLETED),
            (RunState.GRADING, RunState.AGENT_RUNNING),
        ],
    )
    def test_illegal_transitions_are_rejected(self, src: RunState, dst: RunState) -> None:
        with pytest.raises(IllegalTransition):
            assert_transition(src, dst)

    def test_terminal_states_have_no_outgoing_transitions(self) -> None:
        assert RUN_TRANSITIONS[RunState.COMPLETED] == frozenset()
        assert RUN_TRANSITIONS[RunState.CANCELLED] == frozenset()

    def test_every_active_state_can_reach_completed(self) -> None:
        """A run must always be able to terminate, whatever phase it fails in."""
        for state in RunState:
            if state.is_active:
                assert RunState.COMPLETED in RUN_TRANSITIONS[state]

    def test_every_non_terminal_state_can_be_cancelled(self) -> None:
        for state in RunState:
            if not state.is_terminal:
                assert RunState.CANCELLED in RUN_TRANSITIONS[state]

    def test_retryable_phases_can_return_to_pending_for_an_infra_retry(self) -> None:
        retryable = (
            RunState.CLAIMED,
            RunState.PREPARING,
            RunState.AGENT_RUNNING,
            RunState.COLLECTING,
            RunState.GRADING,
        )
        for state in retryable:
            assert RunState.PENDING in RUN_TRANSITIONS[state]

    def test_finalizing_cannot_be_retried(self) -> None:
        """Once grading has concluded, re-running the work would discard a
        verdict that was already reached."""
        assert RunState.PENDING not in RUN_TRANSITIONS[RunState.FINALIZING]

    def test_terminal_and_active_classification_is_disjoint(self) -> None:
        for state in RunState:
            assert not (state.is_terminal and state.is_active)


class TestOutcomeSemantics:
    def test_only_graded_outcomes_count_toward_the_success_rate(self) -> None:
        counting = {o for o in RunOutcome if o.counts_toward_success_rate}
        assert counting == {RunOutcome.SOLVED, RunOutcome.UNSOLVED}

    def test_infrastructure_failures_are_not_attributable_to_the_agent(self) -> None:
        for outcome in (
            RunOutcome.INFRA_ERROR,
            RunOutcome.INVALID_TASK,
            RunOutcome.GRADING_ERROR,
            RunOutcome.CANCELLED,
        ):
            assert not outcome.is_attributable_to_agent

    def test_timeout_and_agent_error_are_attributable_to_the_agent(self) -> None:
        assert RunOutcome.TIMEOUT.is_attributable_to_agent
        assert RunOutcome.AGENT_ERROR.is_attributable_to_agent


class TestRates:
    def test_zero_denominator_yields_none_not_zero(self) -> None:
        rate = make_rate(0, 0)
        assert rate.value is None
        payload = rate.to_dict()
        assert payload["value"] is None
        assert payload["percent"] is None
        assert payload["ci_method"] is None

    def test_denominator_is_always_exposed(self) -> None:
        payload = make_rate(3, 7).to_dict()
        assert payload["numerator"] == 3
        assert payload["denominator"] == 7
        assert payload["percent"] == pytest.approx(42.86, abs=0.01)

    def test_small_sample_carries_a_warning(self) -> None:
        payload = make_rate(1, 3).to_dict()
        assert payload["small_sample"] is True
        assert "indicative" in payload["interpretation_warning"]

    def test_large_sample_has_no_warning(self) -> None:
        payload = make_rate(50, 100).to_dict()
        assert payload["small_sample"] is False
        assert payload["interpretation_warning"] is None

    def test_confidence_interval_brackets_the_point_estimate(self) -> None:
        rate = make_rate(7, 10)
        assert rate.ci_low is not None and rate.ci_high is not None
        assert rate.ci_low <= 0.7 <= rate.ci_high

    def test_interval_stays_inside_zero_and_one_at_the_extremes(self) -> None:
        low, high = wilson_interval(0, 5)
        assert low == 0.0
        assert 0.0 < high < 1.0
        low, high = wilson_interval(5, 5)
        assert 0.0 < low < 1.0
        assert high == 1.0

    def test_wider_interval_for_a_smaller_sample(self) -> None:
        narrow = make_rate(50, 100)
        wide = make_rate(5, 10)
        assert (wide.ci_high - wide.ci_low) > (narrow.ci_high - narrow.ci_low)

    def test_empty_trials_interval_is_degenerate(self) -> None:
        assert wilson_interval(0, 0) == (0.0, 0.0)


class TestPassAtK:
    def test_pass_at_1_is_the_plain_success_rate(self) -> None:
        assert pass_at_k(4, 1, 1) == pytest.approx(0.25)
        assert pass_at_k(4, 4, 1) == pytest.approx(1.0)
        assert pass_at_k(4, 0, 1) == pytest.approx(0.0)

    def test_returns_none_when_fewer_samples_than_k(self) -> None:
        assert pass_at_k(2, 1, 5) is None
        assert pass_at_k(0, 0, 1) is None

    def test_zero_successes_gives_zero(self) -> None:
        assert pass_at_k(10, 0, 3) == 0.0

    def test_all_successes_gives_one(self) -> None:
        assert pass_at_k(10, 10, 3) == 1.0

    def test_matches_the_unbiased_estimator_by_hand(self) -> None:
        """n=5, c=2, k=2: 1 - C(3,2)/C(5,2) = 1 - 3/10 = 0.7."""
        assert pass_at_k(5, 2, 2) == pytest.approx(0.7)

    def test_is_monotonically_non_decreasing_in_k(self) -> None:
        values = [pass_at_k(10, 3, k) for k in (1, 2, 3, 4, 5)]
        assert all(value is not None for value in values)
        assert values == sorted(values)

    def test_rejects_non_positive_k(self) -> None:
        assert pass_at_k(5, 2, 0) is None


class TestRateDataclass:
    def test_value_is_none_without_trials(self) -> None:
        assert Rate(0, 0).value is None

    def test_small_sample_requires_a_nonzero_denominator(self) -> None:
        assert Rate(0, 0).small_sample is False
