"""Python port of src/domain/petState.ts.

Implements the same state-decision logic, counter rules, and transition notes as the
TypeScript original. Constants, initial state, and every state-transition rule must
stay in sync with petState.ts (see .kiro/steering/structure.md).

The checker Lambda imports this module directly. It must not redefine any of the rules
here; use this module as the single Python source of truth for status decisions.

Shared identifiers (STATUSES, status strings) are imported from ghostpet.model so the
reader and writer cannot drift apart (design.md section 8.1).
"""

from __future__ import annotations

from typing import TypedDict

from ghostpet.model import STATUSES  # noqa: F401 — re-exported for callers

# ---------------------------------------------------------------------------
# Thresholds — mirrors the constants at the top of src/domain/petState.ts.
# Change them here and in petState.ts together; never inline magic numbers.
# ---------------------------------------------------------------------------
TIMEOUT_MS: int = 5000
SLOW_THRESHOLD_MS: int = 2000  # responseMs <= this is "fast"
SLOW_CHECKS_NEEDED: int = 2
FAST_CHECKS_NEEDED: int = 2
FAILURES_TO_DOWN: int = 3

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Status = str  # one of the five values in STATUSES ("unknown"|"healthy"|"slow"|"down"|"recovering")


class CheckResult(TypedDict):
    """Result of one HTTP health check."""

    statusCode: int | None   # None when the connection failed outright
    responseMs: int
    timedOut: bool


class Tracker(TypedDict):
    """Everything the decision logic remembers between checks.

    Persisted per service in DynamoDB. The checker reads this before every decision.
    """

    status: Status
    consecutiveFailures: int
    consecutiveSlow: int
    consecutiveFast: int


class Transition(TypedDict):
    """A status change produced by one check."""

    from_status: str   # "from" is a Python keyword; callers writing to DynamoDB use the key "from"
    to: str
    note: str


class Decision(TypedDict, total=False):
    """Result of decide(). ``transition`` is absent when the status does not change."""

    tracker: Tracker
    transition: Transition  # optional


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

INITIAL_TRACKER: Tracker = {
    "status": "unknown",
    "consecutiveFailures": 0,
    "consecutiveSlow": 0,
    "consecutiveFast": 0,
}

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

CheckClass = str  # "failure" | "fast" | "slow"


def is_failure(check: CheckResult) -> bool:
    """Failure: timeout, connection error (statusCode is None), or 4xx/5xx.

    Everything else — including 3xx — is a success (mirrors petState.ts isFailure).
    """
    return check["timedOut"] or check["statusCode"] is None or check["statusCode"] >= 400


def classify(check: CheckResult) -> CheckClass:
    """Return "failure", "fast", or "slow" for a single check result."""
    if is_failure(check):
        return "failure"
    return "fast" if check["responseMs"] <= SLOW_THRESHOLD_MS else "slow"


# ---------------------------------------------------------------------------
# Counter update (applied before status rules, same as TypeScript)
# ---------------------------------------------------------------------------

def _update_counters(prev: Tracker, kind: CheckClass) -> dict:
    """Return updated counter values only (not status). Mirrors updateCounters() in petState.ts."""
    return {
        "consecutiveFailures": prev["consecutiveFailures"] + 1 if kind == "failure" else 0,
        "consecutiveSlow":     prev["consecutiveSlow"]    + 1 if kind == "slow"    else 0,
        "consecutiveFast":     prev["consecutiveFast"]    + 1 if kind == "fast"    else 0,
    }


# ---------------------------------------------------------------------------
# Core decision function
# ---------------------------------------------------------------------------

def decide(prev: Tracker, check: CheckResult) -> Decision:
    """Pure function: same input → same output. No I/O, no clock.

    Returns a Decision dict with:
    - ``tracker``: the new Tracker (always present)
    - ``transition``: only present when the status changes
    """
    kind = classify(check)
    counters = _update_counters(prev, kind)

    failed_enough = counters["consecutiveFailures"] >= FAILURES_TO_DOWN
    slow_enough   = counters["consecutiveSlow"]    >= SLOW_CHECKS_NEEDED
    fast_enough   = counters["consecutiveFast"]    >= FAST_CHECKS_NEEDED

    def stay() -> Decision:
        return {"tracker": Tracker(status=prev["status"], **counters)}

    def move(to: str, note: str) -> Decision:
        return {
            "tracker": Tracker(status=to, **counters),
            "transition": Transition(from_status=prev["status"], to=to, note=note),
        }

    def to_down() -> Decision:
        return move("down", f"{FAILURES_TO_DOWN} failed checks in a row")

    def to_slow(note: str = "response over 2s twice in a row") -> Decision:
        return move("slow", note)

    current = prev["status"]

    if current == "unknown":
        if kind == "fast":
            return move("healthy", "first fast check")
        if slow_enough:
            return to_slow()
        if failed_enough:
            return to_down()
        return stay()

    if current == "healthy":
        if failed_enough:
            return to_down()
        if slow_enough:
            return to_slow()
        return stay()

    if current == "slow":
        if failed_enough:
            return to_down()
        if fast_enough:
            return move("healthy", "response at or under 2s twice in a row")
        return stay()

    if current == "down":
        if kind == "failure":
            return stay()
        return move("recovering", "first successful check after outage")

    if current == "recovering":
        if kind == "failure":
            return move("down", "failed again while recovering")
        if fast_enough:
            return move("healthy", "two fast checks in a row after outage")
        if slow_enough:
            return to_slow("still slow after outage")
        return stay()

    # Unreachable for valid input, but keeps type checkers happy.
    return stay()  # pragma: no cover


# ---------------------------------------------------------------------------
# Batch helper
# ---------------------------------------------------------------------------

class IndexedTransition(TypedDict):
    index: int
    from_status: str   # DynamoDB key is "from"; callers map this themselves
    to: str
    note: str


def run_checks(
    checks: list[CheckResult],
    start: Tracker | None = None,
) -> dict:
    """Fold a sequence of checks into a final tracker plus all transitions, oldest first.

    Returns ``{"tracker": Tracker, "transitions": list[IndexedTransition]}``.
    Mirrors runChecks() in petState.ts.
    """
    tracker: Tracker = start if start is not None else {**INITIAL_TRACKER}
    transitions: list[IndexedTransition] = []
    for index, check in enumerate(checks):
        d = decide(tracker, check)
        tracker = d["tracker"]
        if "transition" in d:
            t = d["transition"]
            transitions.append(
                IndexedTransition(index=index, from_status=t["from_status"], to=t["to"], note=t["note"])
            )
    return {"tracker": tracker, "transitions": transitions}


__all__ = [
    # Constants
    "TIMEOUT_MS",
    "SLOW_THRESHOLD_MS",
    "SLOW_CHECKS_NEEDED",
    "FAST_CHECKS_NEEDED",
    "FAILURES_TO_DOWN",
    # Types
    "CheckResult",
    "Tracker",
    "Transition",
    "Decision",
    "IndexedTransition",
    # State
    "INITIAL_TRACKER",
    # Functions
    "is_failure",
    "classify",
    "decide",
    "run_checks",
]
