"""Minimal dependency-free 5-field cron matcher.

Fields (standard order): minute hour day-of-month month day-of-week.
Supports ``*``, lists (``1,15``), ranges (``1-5``), steps (``*/15`` and
``0-30/10``), and day-of-week 0 or 7 = Sunday. This is intentionally small —
enough to schedule workflow runs at minute resolution without a third-party
dependency; it is not a full crontab implementation (no @hourly aliases, no
``L``/``#`` qualifiers).
"""

from __future__ import annotations

from datetime import datetime

_FIELD_BOUNDS = [
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day of month
    (1, 12),  # month
    (0, 6),  # day of week (0=Sun)
]


def _parse_field(field: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for part in str(field).split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            base, _, step_str = part.partition("/")
            step = int(step_str)
            if step <= 0:
                raise ValueError("cron step must be positive")
        else:
            base = part
        if base in ("*", ""):
            start, end = low, high
        elif "-" in base:
            start_str, _, end_str = base.partition("-")
            start, end = int(start_str), int(end_str)
        else:
            start = end = int(base)
        if start < low or end > high or start > end:
            raise ValueError(f"cron field out of range: {part}")
        values.update(range(start, end + 1, step))
    if not values:
        raise ValueError("cron field produced no values")
    return values


def is_valid_cron(expression: str) -> bool:
    try:
        parse_cron(expression)
        return True
    except (ValueError, AttributeError):
        return False


def parse_cron(expression: str) -> list[set[int]]:
    fields = str(expression or "").split()
    if len(fields) != 5:
        raise ValueError("cron expression must have exactly 5 fields")
    return [
        _parse_field(field, low, high)
        for field, (low, high) in zip(fields, _FIELD_BOUNDS, strict=True)
    ]


def cron_matches(expression: str, moment: datetime) -> bool:
    """True when ``moment`` (minute resolution) satisfies the cron expression.

    Day-of-month and day-of-week use cron's OR semantics when both are
    restricted (matches if either matches), matching Vixie cron behavior.
    """
    minute_f, hour_f, dom_f, month_f, dow_f = parse_cron(expression)
    if moment.minute not in minute_f or moment.hour not in hour_f:
        return False
    if moment.month not in month_f:
        return False
    # Python weekday(): Mon=0..Sun=6 -> cron dow: Sun=0..Sat=6.
    cron_dow = (moment.weekday() + 1) % 7
    dow_normalized = {0 if value == 7 else value for value in dow_f}
    dom_restricted = dom_f != set(range(1, 32))
    dow_restricted = dow_normalized != set(range(0, 7))
    dom_hit = moment.day in dom_f
    dow_hit = cron_dow in dow_normalized
    if dom_restricted and dow_restricted:
        return dom_hit or dow_hit
    return dom_hit and dow_hit
