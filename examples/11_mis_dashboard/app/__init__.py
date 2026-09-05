"""MIS dashboard application package (layered FastAPI structure)."""

from __future__ import annotations

import sys
from pathlib import Path

# Make the nzpy_extended driver importable when the example is run from its
# own directory (same convention as examples/10_query_app).
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))