"""版管理の窓の設定（`configs/acquisition/oanda_mt5_m1_sealed_window.yaml`）の値の照合（ADR-0037 第4項の 4）。

確かめること: `allowed_start` が改定文の値と一致する。`acquisition_start` が merge のコミットの時刻から
ADR-0037 第2項の 2 の規則で計算した値と一致する。merge のコミットとその時刻が ADR-0037 に記録した値
（git の履歴があれば git の値）と一致する。設定ファイルと ADR を読むだけで、市場データは読まない。
"""

from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.app.config.sealed_window import (
    SEALED_WINDOW_PATH,
    SEALED_WINDOW_SYMBOLS,
    load_sealed_window,
)
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.access import (
    SEALED_RESEARCH_UNTIL,
    acquisition_start_after,
    is_trading_week_start,
)

ROOT = Path(__file__).resolve().parents[3]
ADR_0037 = ROOT / "docs" / "decisions" / "0037-revised-constraint-on-data-from-2024.md"
T = UtcTime.parse


def _adr_merge_record() -> tuple[str, UtcTime]:
    """ADR-0037 の状態の行に記録した merge の時刻とコミットの短い識別。"""
    text = ADR_0037.read_text(encoding="utf-8")
    status = next(line for line in text.splitlines() if line.startswith("- 状態:"))
    found = re.search(
        r"\*\*承認・発効\*\*（(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d)。PR #108 の merge の"
        r"コミット `([0-9a-f]{7,40})`",
        status,
    )
    assert found is not None, "ADR-0037 の状態の行に merge の時刻とコミットが無い"
    moment = datetime.fromisoformat(found.group(1))
    return found.group(2), UtcTime(moment.astimezone(UTC))


def test_the_window_holds_the_revised_constraint_and_the_derived_acquisition_start() -> None:
    window = load_sealed_window(ROOT / SEALED_WINDOW_PATH)
    assert window.allowed_start == SEALED_RESEARCH_UNTIL == T("2024-01-01T00:00:00Z")
    assert window.symbols == SEALED_WINDOW_SYMBOLS and len(window.symbols) == 8
    assert window.merge_commit_time is not None
    assert window.acquisition_start == acquisition_start_after(window.merge_commit_time)
    assert window.acquisition_start == T("2026-11-01T22:00:00Z")
    assert window.boundaries.acquisition_start == window.acquisition_start
    assert is_trading_week_start(window.acquisition_start)


def test_the_merge_commit_and_its_time_match_the_record_in_adr_0037() -> None:
    window = load_sealed_window(ROOT / SEALED_WINDOW_PATH)
    short, moment = _adr_merge_record()
    assert window.merge_commit is not None and window.merge_commit.startswith(short)
    assert window.merge_commit_time == moment  # 2026-10-29T09:37:55+09:00 = 00:37:55Z


def test_the_merge_commit_time_matches_git_when_the_history_is_available() -> None:
    window = load_sealed_window(ROOT / SEALED_WINDOW_PATH)
    assert window.merge_commit is not None
    try:
        shown = subprocess.run(
            ["git", "-C", str(ROOT), "log", "-1", "--format=%cI", window.merge_commit],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git の履歴に merge のコミットが無い（浅い取得）。ADR の記録との照合で足りる")
    assert window.merge_commit_time == UtcTime(datetime.fromisoformat(shown).astimezone(UTC))


@pytest.mark.parametrize(
    ("merged", "expected"),
    [
        # 改定文の merge（水曜の夜。夏時間の終わりの日曜の 17:00 は標準時で 22:00Z）。
        ("2026-10-29T00:37:55Z", "2026-11-01T22:00:00Z"),
        # ちょうど週の始まりなら、その次の週の始まり。
        ("2026-11-01T22:00:00Z", "2026-11-08T22:00:00Z"),
        # 週の始まりの 1 秒前なら、その週の始まり。
        ("2026-11-01T21:59:59Z", "2026-11-01T22:00:00Z"),
        # 夏時間の間（17:00 は 21:00Z）。
        ("2027-06-02T12:00:00Z", "2027-06-06T21:00:00Z"),
    ],
)
def test_the_acquisition_start_rule(merged: str, expected: str) -> None:
    assert acquisition_start_after(T(merged)) == T(expected)


def test_a_hand_edited_acquisition_start_that_breaks_the_rule_is_refused(tmp_path: Path) -> None:
    text = (ROOT / SEALED_WINDOW_PATH).read_text(encoding="utf-8")
    edited = text.replace(
        'acquisition_start: "2026-11-01T22:00:00Z"', 'acquisition_start: "2026-11-08T22:00:00Z"'
    )
    assert edited != text
    path = tmp_path / "window.yaml"
    path.write_text(edited, encoding="utf-8")
    with pytest.raises(ConfigError, match="計算した値"):
        load_sealed_window(path)
