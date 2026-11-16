"""`uv run python -m tools.ops.hypothesis_cards …` の入口。"""

from __future__ import annotations

import sys

from tools.ops.hypothesis_cards.cli import main

sys.exit(main())
