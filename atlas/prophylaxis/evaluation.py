"""Vendor-neutral evaluation policies for normalized Profylaxia values."""

from atlas.prophylaxis.models import EvaluationOutcome, UptimeValues


SECONDS_PER_DAY = 24 * 60 * 60
UPTIME_TOO_SHORT_SECONDS = 3 * SECONDS_PER_DAY
UPTIME_TOO_LONG_SECONDS = 365 * SECONDS_PER_DAY


def evaluate_uptime(
    values: UptimeValues,
) -> tuple[EvaluationOutcome, str | None]:
    """Evaluate uptime without depending on a platform-specific collector."""
    if values.uptime_seconds < UPTIME_TOO_SHORT_SECONDS:
        return EvaluationOutcome.WARNING, "uptime_too_short"
    if values.uptime_seconds > UPTIME_TOO_LONG_SECONDS:
        return EvaluationOutcome.WARNING, "uptime_too_long"
    return EvaluationOutcome.PASS, None


__all__ = [
    "UPTIME_TOO_LONG_SECONDS",
    "UPTIME_TOO_SHORT_SECONDS",
    "evaluate_uptime",
]
