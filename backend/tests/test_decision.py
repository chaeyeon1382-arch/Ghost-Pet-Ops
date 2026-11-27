"""Tests for ghostpet/decision.py.

Each test is mapped to its counterpart in src/domain/petState.test.ts (TypeScript).
The numbering follows the "agreed scenarios" table in .kiro/specs/mvp/design.md and the
test labels in petState.test.ts so that the two suites can be compared line by line.

Helper conventions mirror the TypeScript helpers:
  res(status_code, response_ms)  -> CheckResult with timedOut=False
  ok(response_ms)                -> 200 OK
  fast                           -> ok(150)
  slow                           -> ok(2300)
  timeout                        -> CheckResult with timedOut=True, responseMs=5000
  err503                         -> res(503, 90)
  from_status(s)                 -> Tracker in status s, all counters 0
  status_after(checks, start)    -> final status string after running all checks
  path(checks, start)            -> status after each individual check (list of strings)
"""

from __future__ import annotations

import pytest

from ghostpet.decision import (
    FAILURES_TO_DOWN,
    FAST_CHECKS_NEEDED,
    INITIAL_TRACKER,
    SLOW_CHECKS_NEEDED,
    SLOW_THRESHOLD_MS,
    CheckResult,
    Tracker,
    classify,
    decide,
    is_failure,
    run_checks,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def res(status_code: int | None, response_ms: int) -> CheckResult:
    return CheckResult(statusCode=status_code, responseMs=response_ms, timedOut=False)


def ok(response_ms: int) -> CheckResult:
    return res(200, response_ms)


fast: CheckResult = ok(150)
slow_check: CheckResult = ok(2300)
timeout: CheckResult = CheckResult(statusCode=None, responseMs=5000, timedOut=True)
err503: CheckResult = res(503, 90)


def from_status(status: str) -> Tracker:
    return Tracker(
        status=status,
        consecutiveFailures=0,
        consecutiveSlow=0,
        consecutiveFast=0,
    )


def status_after(checks: list[CheckResult], start: Tracker | None = None) -> str:
    return run_checks(checks, start)["tracker"]["status"]


def path(checks: list[CheckResult], start: Tracker | None = None) -> list[str]:
    """Status after each check, to verify intermediate steps."""
    return [status_after(checks[: i + 1], start) for i in range(len(checks))]


# ---------------------------------------------------------------------------
# Check classification
# ---------------------------------------------------------------------------


class TestClassification:
    def test_timeout_is_failure(self):
        assert is_failure(timeout) is True

    def test_connection_error_is_failure(self):
        # statusCode=None, timedOut=False (connection refused, etc.)
        assert is_failure(res(None, 30)) is True

    def test_4xx_is_failure(self):
        assert is_failure(res(404, 30)) is True
        assert is_failure(res(400, 30)) is True

    def test_5xx_is_failure(self):
        assert is_failure(err503) is True
        assert is_failure(res(599, 30)) is True

    def test_16_3xx_is_success(self):
        """TS test 16: 3xx counts as success."""
        assert classify(res(301, 100)) == "fast"
        assert classify(res(304, 100)) == "fast"
        assert classify(res(302, 2500)) == "slow"
        assert status_after([res(301, 100)]) == "healthy"

    def test_8_boundary_2000ms_is_fast_2001ms_is_slow(self):
        """TS test 8: exactly 2000 ms is fast, 2001 ms is slow."""
        assert classify(ok(2000)) == "fast"
        assert classify(ok(2001)) == "slow"
        assert status_after([ok(2000), ok(2000)], from_status("healthy")) == "healthy"
        assert status_after([ok(2001), ok(2001)], from_status("healthy")) == "slow"

    def test_2xx_fast(self):
        assert classify(fast) == "fast"

    def test_2xx_slow(self):
        assert classify(slow_check) == "slow"


# ---------------------------------------------------------------------------
# Counter updates
# ---------------------------------------------------------------------------


class TestCounters:
    def test_failure_increments_failures_and_resets_slow_and_fast(self):
        start = Tracker(status="healthy", consecutiveFailures=0, consecutiveSlow=1, consecutiveFast=4)
        d = decide(start, timeout)
        t = d["tracker"]
        assert t == Tracker(status="healthy", consecutiveFailures=1, consecutiveSlow=0, consecutiveFast=0)

    def test_fast_increments_fast_and_resets_failures_and_slow(self):
        start = Tracker(status="healthy", consecutiveFailures=2, consecutiveSlow=1, consecutiveFast=0)
        d = decide(start, fast)
        t = d["tracker"]
        assert t == Tracker(status="healthy", consecutiveFailures=0, consecutiveSlow=0, consecutiveFast=1)

    def test_slow_increments_slow_and_resets_failures_and_fast(self):
        start = Tracker(status="healthy", consecutiveFailures=2, consecutiveSlow=0, consecutiveFast=3)
        d = decide(start, slow_check)
        t = d["tracker"]
        assert t == Tracker(status="healthy", consecutiveFailures=0, consecutiveSlow=1, consecutiveFast=0)

    def test_10_consecutive_failures_resets_after_success(self):
        """TS test 10: consecutiveFailures resets after a success, intermediate status unchanged."""
        r = run_checks([timeout, timeout, fast, timeout, timeout], from_status("healthy"))
        assert r["tracker"]["consecutiveFailures"] == 2
        assert r["tracker"]["status"] == "healthy"
        assert r["transitions"] == []

    def test_11_consecutive_slow_resets_after_fast(self):
        """TS test 11: consecutiveSlow resets after a fast success."""
        r = run_checks([slow_check, fast, slow_check], from_status("healthy"))
        assert r["tracker"]["consecutiveSlow"] == 1
        assert r["tracker"]["status"] == "healthy"


# ---------------------------------------------------------------------------
# Agreed scenarios 1–9
# ---------------------------------------------------------------------------


class TestAgreedScenarios:
    def test_1_from_unknown_fast_to_healthy(self):
        """TS test 1: from unknown: 200 at 150 ms -> healthy."""
        assert status_after([fast]) == "healthy"

    def test_2_from_healthy_one_slow_stays_healthy(self):
        """TS test 2: from healthy: one 2300 ms check -> stays healthy."""
        assert status_after([slow_check], from_status("healthy")) == "healthy"

    def test_3_from_healthy_two_slow_to_slow(self):
        """TS test 3: from healthy: two 2300 ms checks in a row -> slow (with event)."""
        r = run_checks([slow_check, slow_check], from_status("healthy"))
        assert r["tracker"]["status"] == "slow"
        assert len(r["transitions"]) == 1
        t = r["transitions"][0]
        assert t["index"] == 1
        assert t["from_status"] == "healthy"
        assert t["to"] == "slow"
        assert t["note"] == "response over 2s twice in a row"

    def test_4_from_healthy_one_timeout_unchanged_no_event(self):
        """TS test 4: from healthy: one timeout -> unchanged, no event."""
        d = decide(from_status("healthy"), timeout)
        assert d["tracker"]["status"] == "healthy"
        assert "transition" not in d

    def test_5_from_healthy_three_failures_to_down(self):
        """TS test 5: from healthy: three timeouts, 5xx, or a mix -> down."""
        assert path([timeout, timeout, timeout], from_status("healthy")) == ["healthy", "healthy", "down"]
        assert status_after([err503, err503, err503], from_status("healthy")) == "down"
        assert status_after([timeout, err503, res(None, 10)], from_status("healthy")) == "down"

    def test_6_from_down_one_success_to_recovering(self):
        """TS test 6: from down: one success (fast or slow) -> recovering."""
        assert status_after([fast], from_status("down")) == "recovering"
        assert status_after([slow_check], from_status("down")) == "recovering"

    def test_7_from_down_fast_fast_to_recovering_then_healthy(self):
        """TS test 7: from down: fast, fast -> recovering, then healthy."""
        assert path([fast, fast], from_status("down")) == ["recovering", "healthy"]

    def test_9_from_recovering_one_failure_to_down(self):
        """TS test 9: from recovering: one failure -> down."""
        start = Tracker(status="recovering", consecutiveFailures=0, consecutiveSlow=0, consecutiveFast=1)
        d = decide(start, timeout)
        assert d["tracker"]["status"] == "down"
        assert d["transition"]["from_status"] == "recovering"
        assert d["transition"]["to"] == "down"


# ---------------------------------------------------------------------------
# Unknown state (TS "unknown" describe block)
# ---------------------------------------------------------------------------


class TestUnknown:
    def test_initial_tracker_shape(self):
        """Starts as unknown with zero counters."""
        assert INITIAL_TRACKER == {
            "status": "unknown",
            "consecutiveFailures": 0,
            "consecutiveSlow": 0,
            "consecutiveFast": 0,
        }
        assert status_after([]) == "unknown"

    def test_12_one_fast_success_to_healthy_with_event(self):
        """TS test 12: one fast success -> healthy, unknown->healthy event."""
        r = run_checks([fast])
        assert len(r["transitions"]) == 1
        t = r["transitions"][0]
        assert t["index"] == 0
        assert t["from_status"] == "unknown"
        assert t["to"] == "healthy"
        assert t["note"] == "first fast check"

    def test_13_two_slow_to_slow_stays_unknown_after_first(self):
        """TS test 13: two slow successes -> slow, stays unknown after the first."""
        assert path([slow_check, slow_check]) == ["unknown", "slow"]
        t = run_checks([slow_check, slow_check])["transitions"][0]
        assert t["from_status"] == "unknown"
        assert t["to"] == "slow"

    def test_14_three_failures_to_down_stays_unknown_after_one_or_two(self):
        """TS test 14: three failures -> down, stays unknown after one or two."""
        assert path([timeout, err503, timeout]) == ["unknown", "unknown", "down"]
        t = run_checks([timeout, err503, timeout])["transitions"][0]
        assert t["from_status"] == "unknown"
        assert t["to"] == "down"

    def test_fast_after_one_slow_to_healthy(self):
        assert path([slow_check, fast]) == ["unknown", "healthy"]

    def test_fast_after_two_failures_to_healthy(self):
        assert status_after([timeout, timeout, fast]) == "healthy"


# ---------------------------------------------------------------------------
# Recovering state (TS "recovering" describe block)
# ---------------------------------------------------------------------------


class TestRecovering:
    def test_15_from_down_fast_slow_stays_recovering_fast_reset(self):
        """TS test 15: from down: fast, slow -> stays recovering with consecutiveFast reset."""
        r = run_checks([fast, slow_check], from_status("down"))
        assert r["tracker"]["status"] == "recovering"
        assert r["tracker"]["consecutiveFast"] == 0

    def test_18_from_down_slow_slow_to_recovering_then_slow(self):
        """TS test 18: from down: slow, slow -> recovering, then slow."""
        assert path([slow_check, slow_check], from_status("down")) == ["recovering", "slow"]
        t = run_checks([slow_check, slow_check], from_status("down"))["transitions"][1]
        assert t["from_status"] == "recovering"
        assert t["to"] == "slow"
        assert t["note"] == "still slow after outage"

    def test_19_from_recovering_slow_slow_to_slow(self):
        """TS test 19: from recovering: slow, slow -> slow."""
        assert path([slow_check, slow_check], from_status("recovering")) == ["recovering", "slow"]

    def test_20_from_down_slow_fast_slow_stays_recovering(self):
        """TS test 20: from down: slow, fast, slow -> stays recovering (counter resets)."""
        assert path([slow_check, fast, slow_check], from_status("down")) == [
            "recovering",
            "recovering",
            "recovering",
        ]

    def test_from_down_slow_fast_fast_to_healthy(self):
        assert path([slow_check, fast, fast], from_status("down")) == ["recovering", "recovering", "healthy"]

    def test_from_down_fast_slow_fast_stays_recovering(self):
        assert status_after([fast, slow_check, fast], from_status("down")) == "recovering"


# ---------------------------------------------------------------------------
# Other transitions
# ---------------------------------------------------------------------------


class TestOtherTransitions:
    def test_slow_to_healthy_needs_two_fast_successes(self):
        assert path([fast, fast], from_status("slow")) == ["slow", "healthy"]

    def test_slow_one_or_two_failures_keep_slow_three_to_down(self):
        assert path([timeout, timeout, timeout], from_status("slow")) == ["slow", "slow", "down"]

    def test_down_stays_down_on_failure_no_event(self):
        start = Tracker(status="down", consecutiveFailures=3, consecutiveSlow=0, consecutiveFast=0)
        d = decide(start, timeout)
        assert d["tracker"]["status"] == "down"
        assert d["tracker"]["consecutiveFailures"] == 4
        assert "transition" not in d

    def test_full_transition_sequence(self):
        """Records only real status changes in the right order."""
        r = run_checks([fast, fast, slow_check, slow_check, slow_check, timeout, timeout, timeout, timeout, fast, fast])
        sequence = [f"{t['from_status']}>{t['to']}" for t in r["transitions"]]
        assert sequence == [
            "unknown>healthy",
            "healthy>slow",
            "slow>down",
            "down>recovering",
            "recovering>healthy",
        ]

    def test_does_not_mutate_input_tracker(self):
        start = from_status("healthy")
        copy = {**start}
        decide(start, slow_check)
        assert start == copy


# ---------------------------------------------------------------------------
# Constants match TypeScript
# ---------------------------------------------------------------------------


class TestConstants:
    def test_timeout_ms(self):
        from ghostpet.decision import TIMEOUT_MS
        assert TIMEOUT_MS == 5000

    def test_slow_threshold_ms(self):
        assert SLOW_THRESHOLD_MS == 2000

    def test_slow_checks_needed(self):
        assert SLOW_CHECKS_NEEDED == 2

    def test_fast_checks_needed(self):
        assert FAST_CHECKS_NEEDED == 2

    def test_failures_to_down(self):
        assert FAILURES_TO_DOWN == 3


# ---------------------------------------------------------------------------
# Boundary / edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_exactly_2000ms_is_fast(self):
        assert classify(ok(2000)) == "fast"

    def test_2001ms_is_slow(self):
        assert classify(ok(2001)) == "slow"

    def test_1xx_is_success(self):
        # 1xx is not failure (statusCode < 400)
        assert is_failure(res(100, 50)) is False
        assert classify(res(100, 50)) == "fast"

    def test_399_is_success(self):
        assert is_failure(res(399, 50)) is False

    def test_400_is_failure(self):
        assert is_failure(res(400, 50)) is True

    def test_empty_checks_returns_initial(self):
        r = run_checks([])
        assert r["tracker"] == INITIAL_TRACKER
        assert r["transitions"] == []

    def test_run_checks_default_start_is_initial(self):
        r1 = run_checks([fast])
        r2 = run_checks([fast], None)
        assert r1["tracker"] == r2["tracker"]

    def test_run_checks_does_not_mutate_start(self):
        start = from_status("healthy")
        copy = {**start}
        run_checks([fast, fast], start)
        assert start == copy

    def test_recovering_slow_note(self):
        """'still slow after outage' note appears on recovering -> slow."""
        r = run_checks([slow_check, slow_check], from_status("down"))
        t = r["transitions"][1]
        assert t["note"] == "still slow after outage"

    def test_healthy_slow_note(self):
        """Standard slow note from healthy."""
        d = decide(
            Tracker(status="healthy", consecutiveFailures=0, consecutiveSlow=1, consecutiveFast=0),
            slow_check,
        )
        assert d["transition"]["note"] == "response over 2s twice in a row"

    def test_recovering_to_down_note(self):
        d = decide(from_status("recovering"), timeout)
        assert d["transition"]["note"] == "failed again while recovering"

    def test_down_to_recovering_note(self):
        d = decide(from_status("down"), fast)
        assert d["transition"]["note"] == "first successful check after outage"

    def test_unknown_to_down_note(self):
        d = decide(
            Tracker(status="unknown", consecutiveFailures=2, consecutiveSlow=0, consecutiveFast=0),
            timeout,
        )
        assert d["transition"]["note"] == f"{FAILURES_TO_DOWN} failed checks in a row"

    def test_slow_to_healthy_note(self):
        d = decide(
            Tracker(status="slow", consecutiveFailures=0, consecutiveSlow=0, consecutiveFast=1),
            fast,
        )
        assert d["transition"]["note"] == "response at or under 2s twice in a row"

    def test_transition_index_is_check_index(self):
        """Transition index matches the position of the check that caused it."""
        r = run_checks([fast, slow_check, slow_check], from_status("unknown"))
        # index 0: unknown->healthy, index 2: healthy->slow
        assert r["transitions"][0]["index"] == 0
        assert r["transitions"][1]["index"] == 2

    def test_down_no_event_on_repeated_failure(self):
        """Consecutive failures while down accumulate but never emit an event."""
        start = Tracker(status="down", consecutiveFailures=5, consecutiveSlow=0, consecutiveFast=0)
        for _ in range(10):
            d = decide(start, timeout)
            assert "transition" not in d
            start = d["tracker"]
        assert start["consecutiveFailures"] == 15

    def test_multiple_transitions_in_one_run(self):
        """run_checks collects all transitions, not just the last."""
        r = run_checks([fast, timeout, timeout, timeout, fast], from_status("unknown"))
        statuses = [t["to"] for t in r["transitions"]]
        assert statuses == ["healthy", "down", "recovering"]
