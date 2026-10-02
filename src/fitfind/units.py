"""Lengths as people write them, turned into inches.

Everything inside fitfind is stored in inches. Size charts arrive in
centimetres as often as inches, and heights arrive as ``6'1"`` or
``185 cm``, so every number that comes from outside passes through here
once and is never converted again.
"""

from __future__ import annotations

import re
from typing import Optional

CM_PER_INCH = 2.54

_FEET_INCHES = re.compile(
    r"""^\s*(?P<ft>\d+)\s*(?:'|’|ft|feet)\s*(?:(?P<in>\d+(?:\.\d+)?)\s*(?:"|”|''|in|inches)?)?\s*$""",
    re.IGNORECASE,
)
_NUMBER_UNIT = re.compile(
    r"^\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>cm|centimet(?:er|re)s?|mm|in|inch|inches|\"|”)?\s*$",
    re.IGNORECASE,
)


def to_inches(value: float, unit: str) -> float:
    """Convert a number in ``in`` or ``cm`` to inches."""
    unit = unit.strip().lower()
    if unit in ("in", "inch", "inches", '"'):
        return float(value)
    if unit in ("cm", "centimeter", "centimeters", "centimetre", "centimetres"):
        return float(value) / CM_PER_INCH
    if unit == "mm":
        return float(value) / (CM_PER_INCH * 10)
    raise ValueError(f"unknown unit {unit!r}; use in or cm")


def parse_length(text: str, default_unit: str = "in") -> float:
    """Parse ``40``, ``40 in``, ``101.5 cm`` or ``6'1"`` into inches.

    A bare number takes ``default_unit``. That is the one place a guess
    is made, so callers pass the unit they asked the person for.
    """
    match = _FEET_INCHES.match(text)
    if match:
        inches = float(match.group("in") or 0)
        return int(match.group("ft")) * 12 + inches
    match = _NUMBER_UNIT.match(text)
    if match:
        return to_inches(float(match.group("num")), match.group("unit") or default_unit)
    raise ValueError(f"cannot read {text!r} as a length; try 40, 40in, 102cm or 6'1\"")


def format_inches(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:.1f} in"


def format_height(value: Optional[float]) -> str:
    if value is None:
        return "-"
    feet, inches = divmod(round(value), 12)
    return f"{feet}'{inches}\""
