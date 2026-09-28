"""Blackboard identifier conversion shared by course and attempt readers."""

import re


_BB_ID = re.compile(r"^_(\d+)_(\d+)$")


def numeric_id(value) -> str:
    """Return the numeric part of ``_123_1`` without trimming ID digits."""
    raw = str(value)
    match = _BB_ID.fullmatch(raw)
    return match.group(1) if match else raw
