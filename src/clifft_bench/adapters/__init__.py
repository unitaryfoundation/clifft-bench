"""Simulator adapters loaded inside isolated benchmark workers."""

from __future__ import annotations

from clifft_bench.adapters.base import Adapter


def load_adapter(name: str) -> Adapter:
    if name == "clifft":
        from clifft_bench.adapters.clifft import ClifftAdapter

        return ClifftAdapter()
    if name == "symft":
        from clifft_bench.adapters.symft import SymftAdapter

        return SymftAdapter()
    if name == "tsim":
        from clifft_bench.adapters.tsim import TsimAdapter

        return TsimAdapter()
    if name == "fixture":
        from clifft_bench.adapters.fixture import FixtureAdapter

        return FixtureAdapter()
    raise ValueError(f"unknown adapter {name!r}")
