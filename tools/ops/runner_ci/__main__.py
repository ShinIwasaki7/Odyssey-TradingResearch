"""`python -m tools.ops.runner_ci` の入口（`cli.main`）。"""

from __future__ import annotations

import sys

from tools.ops.runner_ci.cli import main

if __name__ == "__main__":
    sys.exit(main())
