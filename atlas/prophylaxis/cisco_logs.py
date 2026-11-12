"""Conservative IOS/IOS-XE local-buffer log normalization."""

from __future__ import annotations

from calendar import month_abbr
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Iterable

from atlas.prophylaxis.cisco_raw import CiscoRawCommandResult


_MONTHS = {name.casefold(): index for index, name in enumerate(month_abbr) if name}
_FULL_TIMESTAMP = re.compile(
    r"^\*?(?P<month>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})\s+"
    r"(?:(?P<year>\d{4})\s+)?(?P<time>\d{2}:\d{2}:\d{2}(?:\.\d+)?)"
    r"(?:\s+(?P<timezone>[A-Za-z0-9:+-]+))?"
)
_SMB_TIMESTAMP = re.compile(
    r"^(?P<day>\d{1,2})-(?P<month>[A-Z][a-z]{2})-(?P<year>\d{4})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2})"
)


def _zone(value: object) -> timezone | None:
    text = str(value or "").strip().upper()
    if text in {"UTC", "GMT", "Z"}:
        return timezone.utc
    match = re.fullmatch(r"(?:UTC|GMT)?([+-])(\d{1,2})(?::?(\d{2}))?", text)
    if not match:
        return None
    offset = timedelta(
        hours=int(match.group(2)), minutes=int(match.group(3) or 0)
    )
    return timezone(offset if match.group(1) == "+" else -offset)


def _device_reference(results: Iterable[CiscoRawCommandResult]) -> datetime | None:
    for result in results:
        if result.command_id != "device_clock" or not result.structured_data:
            continue
        row = result.structured_data[0]
        zone = _zone(row.get("timezone"))
        if zone is None:
            return None
        try:
            stamp = datetime.strptime(
                f"{row['month']} {row['day']} {row['year']} {row['time']}",
                "%b %d %Y %H:%M:%S.%f",
            )
        except (KeyError, TypeError, ValueError):
            return None
        return stamp.replace(tzinfo=zone)
    return None


def _event_timestamp(row: dict[str, Any], reference: datetime) -> datetime | None:
    month = str(row.get("month") or "").strip().casefold()
    day = str(row.get("day") or "").strip()
    clock = str(row.get("time") or "").strip()
    if month not in _MONTHS or not day or not clock:
        raw = str(row.get("raw_line") or "")
        match = _FULL_TIMESTAMP.match(raw) or _SMB_TIMESTAMP.match(raw)
        if not match:
            return None
        month, day, clock = (
            match.group("month").casefold(), match.group("day"), match.group("time")
        )
        year_text = match.groupdict().get("year")
        zone = _zone(match.groupdict().get("timezone")) or reference.tzinfo
    else:
        year_text = str(row.get("year") or "").strip() or None
        zone = _zone(row.get("timezone")) or reference.tzinfo
    if zone is None:
        return None
    try:
        hour, minute, seconds = clock.split(":")
        second = int(float(seconds))
        microsecond = int((float(seconds) - second) * 1_000_000)
        candidate = datetime(
            int(year_text) if year_text else reference.year,
            _MONTHS[month], int(day), int(hour), int(minute), second, microsecond,
            tzinfo=zone,
        )
    except (TypeError, ValueError):
        return None
    if not year_text and candidate > reference + timedelta(days=1):
        candidate = candidate.replace(year=candidate.year - 1)
    return candidate


def enrich_cisco_log_results(
    results: Iterable[CiscoRawCommandResult], *, requested_window_days: int = 7,
) -> tuple[CiscoRawCommandResult, ...]:
    """Attach a strict, timestamp-aware window to parsed Cisco log results."""
    items = tuple(results)
    reference = _device_reference(items)
    enriched: list[CiscoRawCommandResult] = []
    for result in items:
        if result.command_id != "log_events" or result.parsing_status != "success":
            enriched.append(result)
            continue
        reliable: list[dict[str, Any]] = []
        undated: list[dict[str, Any]] = []
        for source in result.structured_data or []:
            row = dict(source)
            timestamp = _event_timestamp(row, reference) if reference else None
            event = {
                "timestamp": timestamp.astimezone(timezone.utc).isoformat() if timestamp else None,
                "facility": row.get("facility") or None,
                "severity": row.get("severity") or None,
                "mnemonic": row.get("mnemonic") or None,
                "message": row.get("message") or None,
                "raw_line": row.get("raw_line") or None,
            }
            (reliable if timestamp else undated).append(event)
        end = reference.astimezone(timezone.utc) if reference else None
        start = end - timedelta(days=requested_window_days) if end else None
        filtered = [
            event for event in reliable
            if start <= datetime.fromisoformat(event["timestamp"]) <= end
        ] if start and end else []
        dated = [datetime.fromisoformat(event["timestamp"]) for event in reliable]
        oldest = min(dated) if dated else None
        newest = max(dated) if dated else None
        if reference is None or not reliable:
            coverage = "unknown"
        elif result.truncated or undated or oldest > start:
            coverage = "partial"
        else:
            coverage = "complete"
        normalized = {
            "category": "logs",
            "requested_window_days": requested_window_days,
            "window_start": start.isoformat() if start else None,
            "window_end": end.isoformat() if end else None,
            "oldest_available_event": oldest.isoformat() if oldest else None,
            "newest_available_event": newest.isoformat() if newest else None,
            "coverage_status": coverage,
            "total_events": len(reliable) + len(undated),
            "filtered_events": len(filtered),
            "undated_events": len(undated),
            "truncated": result.truncated,
            "events": filtered,
            "ambiguous_or_undated": undated,
        }
        enriched.append(result.model_copy(update={"normalized_data": normalized}))
    return tuple(enriched)


__all__ = ["enrich_cisco_log_results"]
