"""Balanced serial schedules for temporally paired comparisons."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScheduledSample:
    case_id: str
    repetition: int
    sequence_index: int


def balanced_schedule(case_ids: list[str], repetitions: int) -> list[ScheduledSample]:
    """Alternate forward and reverse orders (A/B then B/A for a pair)."""
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    if not case_ids:
        return []
    schedule = []
    sequence_index = 0
    for repetition in range(repetitions):
        ordered = case_ids if repetition % 2 == 0 else list(reversed(case_ids))
        for case_id in ordered:
            schedule.append(ScheduledSample(case_id, repetition, sequence_index))
            sequence_index += 1
    return schedule
