"""Runtime paths for the published Scatter service.

SCATTER_DATA_DIR may point at the existing live graph directory. Source databases
remain owned by the RedNote mirror and Red Engine, not this package.
"""

from __future__ import annotations

import os
from pathlib import Path


PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parents[1]
DATA = Path(os.environ.get("SCATTER_DATA_DIR") or PACKAGE / "data").resolve()
RESEARCH = REPO / "engines" / "headless_research"
BRIDGE_KITS = REPO / "ui_prototypes" / "cinematic_graph" / "bridge_kits"
