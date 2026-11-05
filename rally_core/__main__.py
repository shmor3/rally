"""Direct package execution entrypoint for python -m rally_core."""

from __future__ import annotations

import sys
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
