"""Human-readable formatting for Profylaxia presentation layers."""


def _slovak_unit(value: int, one: str, few: str, many: str) -> str:
    if value == 1:
        label = one
    elif 2 <= value <= 4:
        label = few
    else:
        label = many
    return f"{value} {label}"


def format_uptime_seconds(total_seconds: int) -> str:
    """Render a non-negative uptime using stable Slovak duration units."""
    if (
        isinstance(total_seconds, bool)
        or not isinstance(total_seconds, int)
        or total_seconds < 0
    ):
        raise ValueError("uptime seconds must be a non-negative integer")

    remaining = total_seconds
    years, remaining = divmod(remaining, 365 * 24 * 60 * 60)
    days, remaining = divmod(remaining, 24 * 60 * 60)
    hours, remaining = divmod(remaining, 60 * 60)
    minutes, seconds = divmod(remaining, 60)

    values = (
        (years, "rok", "roky", "rokov"),
        (days, "deň", "dni", "dní"),
        (hours, "hodina", "hodiny", "hodín"),
        (minutes, "minúta", "minúty", "minút"),
        (seconds, "sekunda", "sekundy", "sekúnd"),
    )
    parts = [
        _slovak_unit(value, one, few, many)
        for value, one, few, many in values
        if value
    ]
    return " ".join(parts) if parts else "0 sekúnd"


__all__ = ["format_uptime_seconds"]
