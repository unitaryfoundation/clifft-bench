"""Simulator adapters loaded inside isolated benchmark workers."""

from __future__ import annotations

from clifft_bench.adapters.base import Adapter


def load_adapter(name: str, version: str | None = None) -> Adapter:
    """Select an implementation by version. Unversioned Clifft uses the legacy options schema."""
    if name == "clifft":
        from clifft_bench.adapters.clifft import ClifftAdapter, ClifftPlanAdapter

        if version is None or version == "0.13.0":
            return ClifftAdapter()
        return ClifftPlanAdapter()
    if name == "symft":
        from clifft_bench.adapters.symft import SymftAdapter

        return SymftAdapter()
    if name == "stim":
        from clifft_bench.adapters.stim import StimAdapter

        return StimAdapter()
    if name == "xtim":
        from clifft_bench.adapters.xtim import XtimAdapter

        return XtimAdapter()
    if name == "tsim":
        from clifft_bench.adapters.tsim import TsimAdapter

        return TsimAdapter()
    if name == "fixture":
        from clifft_bench.adapters.fixture import FixtureAdapter

        return FixtureAdapter()
    raise ValueError(f"unknown adapter {name!r}")
