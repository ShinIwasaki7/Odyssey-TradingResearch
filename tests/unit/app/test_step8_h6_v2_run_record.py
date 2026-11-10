"""順 8 の版 2 の実行記録の確認（D19 §2.1 の手順 5。司令塔の呼び方では手順 4 の後半）。

改善後の実験（研究仮説 H6 の版 2）の実行記録を確かめる。

- 注記 `docs/data/eurusd_step8_research_loop/README.md` の第 4 節（改善後の実験の実行記録）に、
  判定の値（4 値・2 軸・fold の判定）の識別子と表示の語が無い（D14 §7.1 (a)。決定 53。
  数値入りのレポートは版管理の外）。
- 第 4 節の成果物の表と試行ごとの束の SHA-256 の列は 64 桁の小文字の 16 進。

**run はしない**。テストは版管理されたファイルだけを読み、snapshot・成果物・戦略の成績を読まない。
台帳の行は人間がコミットする（D14-11 (a)。決定 57）ので、本テストは台帳を読まない。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from odyssey_fx.evaluation.adapters.search_report import (
    _EVIDENCE_NAMES,
    _FOLD_VERDICT_NAMES,
    _PROFITABILITY_NAMES,
    _VERDICT_NAMES,
)
from odyssey_fx.evaluation.domain.search import (
    EvidenceAxis,
    FoldVerdict,
    ProfitabilityAxis,
    SearchVerdict,
)

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
README: Final = REPO_ROOT / "docs/data/eurusd_step8_research_loop/README.md"


def _section_4() -> str:
    text = README.read_text(encoding="utf-8")
    start = text.index("\n## 4. ")
    end = text.find("\n## 5. ", start)
    return text[start:] if end < 0 else text[start:end]


def test_section_4_carries_no_verdict_values() -> None:
    """第 4 節に判定の値の識別子と表示の語が無い（D14 §7.1 (a)）。"""
    section = _section_4()
    identifiers = {
        member.value
        for enum in (SearchVerdict, ProfitabilityAxis, EvidenceAxis, FoldVerdict)
        for member in enum
    }
    for identifier in identifiers:
        assert re.search(rf"(?<![A-Z_]){identifier}(?![A-Z_])", section) is None, identifier
    labels = {
        *_VERDICT_NAMES.values(),
        *_FOLD_VERDICT_NAMES.values(),
        *_PROFITABILITY_NAMES.values(),
        *_EVIDENCE_NAMES.values(),
    }
    for label in labels:
        assert label not in section, label


def test_every_artifact_hash_in_section_4_is_a_full_sha256() -> None:
    """第 4 節の成果物の表（`runs/` の置き場の行）の SHA-256 の列は 64 桁の小文字の 16 進。

    版 2 の分析メモは版 2 の手順 3 で作るので、まだ値が無い（「まだ無い」）。
    """
    rows = [
        line
        for line in _section_4().splitlines()
        if line.startswith("| ") and line.count("|") == 4 and "`runs/" in line
    ]
    assert len(rows) == 4
    for row in rows:
        assert "/v2/" in row, row
        cell = row.rstrip(" |").rsplit("|", 1)[1].strip()
        if "analysis_memo.md" in row:
            assert cell.startswith("まだ無い"), row
            continue
        assert re.fullmatch(r"`[0-9a-f]{64}`", cell), row


def test_section_4_hashes_of_the_trials_are_full_sha256() -> None:
    """第 4.2 節の試行ごとの束は 4 行で、どれも 64 桁の小文字の 16 進。"""
    rows = [line for line in _section_4().splitlines() if re.match(r"\| [0-3] \| N ", line)]
    assert len(rows) == 4
    for row in rows:
        cell = row.rstrip(" |").rsplit("|", 1)[1].strip()
        assert re.fullmatch(r"`[0-9a-f]{64}`", cell), row
