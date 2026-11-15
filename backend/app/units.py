"""Shared whole-unit ceilings for quantity fields.

``ceil_units`` is the tolerant ceiling every whole-unit quantity field
shares: it rounds to 9 decimal places before the ceiling, so a float
sum that lands within 1e-9 of an integer — the overshoot of repeated
floating-point addition, ``sum([0.7] * 10)`` — is treated as that
integer, while every other value behaves exactly as ``math.ceil``.
"""

from __future__ import annotations

import math


def ceil_units(x: float) -> int:
    """The whole-unit ceiling of *x*, tolerant of float-sum noise.

    ``math.ceil(round(x, 9))``: a value within 1e-9 of an integer
    rounds to that integer before the ceiling; every other value
    behaves exactly as ``math.ceil``.
    """
    return math.ceil(round(x, 9))
