"""Structural invariant checks for the optional historical speedup renderer."""

from __future__ import annotations

import importlib.util
import math
import statistics
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "render_history", ROOT / "scripts/render_history.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_history_series_invariants() -> None:
    mod = _load_module()
    series = mod.build_history_series(ROOT)

    assert len(series.versions) == len(series.medians) == len(series.sources)
    assert len(set(series.versions)) == len(series.versions)
    assert set(series.speedups) == set(mod.CORE_WORKLOADS)

    for values in series.speedups.values():
        assert len(values) == len(series.versions)
        assert math.isclose(values[0], 1.0)
        assert all(math.isfinite(v) and v > 0 for v in values)

    for index, median in enumerate(series.medians):
        expected = statistics.median(series.speedups[w][index] for w in mod.CORE_WORKLOADS)
        assert math.isclose(median, expected)


def test_history_figures_render(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    mod = _load_module()
    series = mod.build_history_series(ROOT)
    outputs = mod.render_figures(series, tmp_path)
    assert {p.name for p in outputs} == {
        "performance-over-time-light.png",
        "performance-over-time-dark.png",
    }
    assert all(p.stat().st_size > 1000 for p in outputs)
