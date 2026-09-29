"""Explicit, reproducible Clifft compiler options."""

from __future__ import annotations

import math
from typing import Any

SCHEDULER_DEFAULTS = {
    "beam_width": 8,
    "search_budget": 16.0,
    "noise_transparent": True,
    "sink_neutral_rotations": True,
}


def scheduler_configuration(execution: dict[str, Any]) -> dict[str, Any]:
    requested = execution.get("clifft_scheduler", {"enabled": False})
    if not requested["enabled"]:
        if set(requested) != {"enabled"}:
            raise ValueError("disabled clifft_scheduler must not specify search options")
        return {"enabled": False}
    options = {**SCHEDULER_DEFAULTS, **requested}
    budget = options["search_budget"]
    if budget is not None and (not math.isfinite(budget) or budget < 0):
        raise ValueError("clifft_scheduler search_budget must be finite and nonnegative or null")
    return options
