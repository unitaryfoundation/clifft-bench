"""Whole-instance QEC aggregate throughput, independent of release campaigns."""

import sys
from pathlib import Path

# Reuse the checked-in Stim adapter and count/metadata helpers without installing
# a second copy of the repository into this experiment's environment.
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
