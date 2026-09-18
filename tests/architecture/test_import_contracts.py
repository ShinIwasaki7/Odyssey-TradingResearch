"""依存規則の機械検査（ADR-0004、全体計画書 §4.3）。

`import-linter` の契約（pyproject.toml の `[tool.importlinter]`）が満たされることを
CI とローカルの両方で検査する。
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_import_contracts_are_satisfied() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "importlinter.cli", "lint-imports"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"lint-imports failed (exit {result.returncode})\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
