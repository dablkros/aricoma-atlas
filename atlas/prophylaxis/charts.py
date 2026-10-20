"""Small, dependency-free SVG charts for Profylaxia run artifacts."""

from datetime import datetime, timezone
from html import escape
from math import isfinite

from atlas.services.zabbix import MetricPoint


MAX_RENDERED_POINTS = 600


def _sample(points: list[MetricPoint]) -> list[MetricPoint]:
    if len(points) <= MAX_RENDERED_POINTS:
        return points
    last = len(points) - 1
    indexes = {
        round(index * last / (MAX_RENDERED_POINTS - 1))
        for index in range(MAX_RENDERED_POINTS)
    }
    return [points[index] for index in sorted(indexes)]


def render_metric_svg(
    *,
    device_name: str,
    metric: str,
    unit: str,
    window: str,
    points: list[MetricPoint],
) -> str:
    """Render numeric Zabbix history as a self-contained, script-free SVG."""
    clean = [
        point
        for point in sorted(points, key=lambda item: item.timestamp)
        if isfinite(point.value)
    ]
    if not clean:
        raise ValueError("metric_points_unavailable")
    clean = _sample(clean)

    width, height = 960, 360
    left, right, top, bottom = 72, 28, 56, 54
    plot_width = width - left - right
    plot_height = height - top - bottom
    first_time = clean[0].timestamp
    last_time = clean[-1].timestamp
    time_span = max(1, last_time - first_time)

    values = [point.value for point in clean]
    if metric == "cpu" or unit == "%":
        y_min, y_max = 0.0, 100.0
    else:
        y_min, y_max = min(values), max(values)
        if y_min == y_max:
            padding = max(abs(y_min) * 0.05, 1.0)
            y_min -= padding
            y_max += padding
    value_span = max(y_max - y_min, 1.0)

    coordinates = []
    for point in clean:
        x = left + ((point.timestamp - first_time) / time_span) * plot_width
        bounded = min(y_max, max(y_min, point.value))
        y = top + (1 - ((bounded - y_min) / value_span)) * plot_height
        coordinates.append(f"{x:.2f},{y:.2f}")

    metric_label = (
        "CPU utilization"
        if metric == "cpu"
        else metric.replace("_", " ").title()
    )
    title = escape(f"{device_name} — {metric_label} ({window})")
    unit_label = escape(unit or "value")
    start_label = datetime.fromtimestamp(first_time, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )
    end_label = datetime.fromtimestamp(last_time, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )
    current_label = escape(f"Current: {clean[-1].value:.2f}{unit}")

    grid = []
    for index in range(5):
        ratio = index / 4
        y = top + ratio * plot_height
        label = y_max - ratio * value_span
        grid.append(
            f'<line x1="{left}" y1="{y:.2f}" x2="{width - right}" y2="{y:.2f}" '
            'stroke="#d8e1ee" stroke-width="1"/>'
            f'<text x="{left - 12}" y="{y + 4:.2f}" text-anchor="end" '
            f'font-size="12" fill="#52627a">{label:.1f}</text>'
        )

    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-label="{title}">',
            f'<title>{title}</title>',
            '<rect width="100%" height="100%" rx="16" fill="#ffffff"/>',
            f'<text x="{left}" y="30" font-family="system-ui, sans-serif" font-size="19" '
            f'font-weight="700" fill="#13233b">{title}</text>',
            f'<text x="{width - right}" y="30" text-anchor="end" '
            f'font-family="system-ui, sans-serif" font-size="13" fill="#52627a">{current_label}</text>',
            *grid,
            f'<polyline points="{" ".join(coordinates)}" fill="none" stroke="#174a7e" '
            'stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>',
            f'<text x="{left}" y="{height - 18}" font-family="system-ui, sans-serif" '
            f'font-size="12" fill="#52627a">{escape(start_label)}</text>',
            f'<text x="{width - right}" y="{height - 18}" text-anchor="end" '
            f'font-family="system-ui, sans-serif" font-size="12" fill="#52627a">{escape(end_label)}</text>',
            f'<text x="18" y="{top + plot_height / 2:.2f}" transform="rotate(-90 18 '
            f'{top + plot_height / 2:.2f})" text-anchor="middle" '
            f'font-family="system-ui, sans-serif" font-size="12" fill="#52627a">{unit_label}</text>',
            '</svg>',
            '',
        ]
    )


__all__ = ["MAX_RENDERED_POINTS", "render_metric_svg"]
