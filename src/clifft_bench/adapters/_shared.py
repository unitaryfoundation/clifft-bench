"""Source verification shared by adapters."""

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
