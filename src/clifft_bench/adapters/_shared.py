"""Source verification and packed-record reduction shared by adapters."""

from __future__ import annotations

import json
from importlib.metadata import distribution
from typing import Any


def verify_source_installation(
    package: str, *, expected_commit: str, source_url: str
) -> dict[str, Any]:
    direct_url = json.loads(distribution(package).read_text("direct_url.json") or "{}")
    vcs = direct_url.get("vcs_info", {})
    installed_url = str(direct_url.get("url", "")).rstrip("/").removesuffix(".git")
    expected_url = source_url.rstrip("/").removesuffix(".git")
    if (
        vcs.get("vcs") != "git"
        or vcs.get("commit_id") != expected_commit
        or installed_url != expected_url
    ):
        raise RuntimeError(
            f"{package} source identity mismatch: expected {expected_url}@{expected_commit}, "
            f"installed {installed_url or 'no VCS metadata'}@{vcs.get('commit_id')}; "
            "install the manifest's pinned Git requirement, not a same-version PyPI wheel"
        )
    return {"installed_source_commit": vcs["commit_id"], "installed_direct_url": direct_url}


class PackedCountsReducer:
    """Convert little-endian packed flips to raw-parity aggregate counts."""

    def __init__(self, det_ref: Any, obs_ref: Any, *, observable: int, postselect: bool):
        import numpy as np

        self._np = np
        self._det_ref = det_ref if np.any(det_ref) else None
        self._obs_ref = obs_ref if np.any(obs_ref) else None
        self._observable = observable
        self._postselect = postselect

    def count(self, detectors: Any, observables: Any) -> tuple[int, int]:
        """Return accepted shots and logical errors; modify supplied records in place."""
        np = self._np
        if self._det_ref is not None:
            detectors ^= self._det_ref
        if self._obs_ref is not None:
            observables ^= self._obs_ref
        keep = (
            ~np.any(detectors, axis=1)
            if self._postselect else np.ones(len(detectors), dtype=bool)
        )
        logical = (observables[:, self._observable // 8] >> (self._observable % 8)) & 1
        return int(np.count_nonzero(keep)), int(np.count_nonzero(logical[keep]))
