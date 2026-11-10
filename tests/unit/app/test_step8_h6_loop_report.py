"""順 8 の最後の PR の記録の確認（D19 §2.1 の手順 6「一巡の報告」、§3 の関門 N2・N5）。

- 版管理された試行台帳に、元の実験（研究仮説 H6 の版 1）と改善後の実験（版 2）の開始の行と結末の行が
  各 1 行あり、版 2 の系譜が版 1 の実行番号 1 を指し、変更の種類が `PARAMETERS` の 1 つで、
  理由の文のダイジェストが版 2 の実験設定の理由の文と一致する（関門 N2。D14 §7.2 の E2、
  D09 §10.10）。
- 注記 `docs/data/eurusd_step8_research_loop/README.md` の第 5 節（一巡の報告）に、関門 N1〜N5 の
  行、予想と実際の行（第 3.3 節の予想と同じ対象・同じ順）、人の手の注記 (1)〜(3)（関門 N5）がある。
- 第 5 節に判定の値（4 値・2 軸・fold の判定）の識別子と表示の語が無い（D14 §7.1 (a)。決定 53・64。
  数値入りの比較表と分析メモは版管理の外）。

**run はしない**。テストは版管理されたファイルだけを読み、snapshot・成果物・戦略の成績を読まない
（台帳の結末の行の判定の値は検査しない）。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Final

import pytest

from odyssey_fx.app.config.experiment_v2 import load_experiment_v2
from odyssey_fx.evaluation.adapters.fs_store import TRIAL_LEDGER_PATH, read_trial_ledger_file
from odyssey_fx.evaluation.adapters.search_report import (
    _EVIDENCE_NAMES,
    _FOLD_VERDICT_NAMES,
    _PROFITABILITY_NAMES,
    _VERDICT_NAMES,
)
from odyssey_fx.evaluation.application.ports import TrialLedgerContents
from odyssey_fx.evaluation.domain.metrics import METRIC_SET_VERSIONS
from odyssey_fx.evaluation.domain.search import (
    ChangeKind,
    EvidenceAxis,
    FoldVerdict,
    ProfitabilityAxis,
    SearchVerdict,
    TrialLedgerEvent,
    TrialLedgerLine,
)
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
EXPERIMENT_NAME: Final = "h6_h1_breakout_m15_ema_confirm_eurusd"
EXPERIMENT_V2: Final = REPO_ROOT / f"configs/experiments/{EXPERIMENT_NAME}_v2.yaml"
README: Final = REPO_ROOT / "docs/data/eurusd_step8_research_loop/README.md"
#: 版 1・版 2 の記録票の識別子（README §2.1・§4.1）。
V1_EXPERIMENT_ID: Final = "9cc0dd26abf6e16e65ec267fa8d5ab1e03d3f71f0285f5ff6d11e4f7e697fb13"
V2_EXPERIMENT_ID: Final = "14d0f8c076b542898819396c895ab287a500535328dd56796543f7b8eb5b3de4"
#: 1 巡目の 4 行と版 1・版 2 の 4 行（README §4.3）。
LEDGER_LINES_THROUGH_V2: Final = 8


def _lines() -> tuple[TrialLedgerLine, ...]:
    contents = read_trial_ledger_file(REPO_ROOT / TRIAL_LEDGER_PATH)
    assert isinstance(contents, TrialLedgerContents), contents
    return tuple(contents.lines)


def _text() -> str:
    return README.read_text(encoding="utf-8")


def _section(number: int) -> str:
    text = _text()
    start = text.index(f"\n## {number}. ")
    end = text.find(f"\n## {number + 1}. ", start)
    return text[start:] if end < 0 else text[start:end]


def _line_of(event: TrialLedgerEvent, experiment_id: str) -> TrialLedgerLine:
    matches = [
        line
        for line in _lines()
        if line.entry.event is event
        and line.entry.experiment_id.digest.hex == experiment_id
        and line.entry.execution == 1
    ]
    assert len(matches) == 1, matches
    return matches[0]


@pytest.mark.parametrize("event", [TrialLedgerEvent.STARTED, TrialLedgerEvent.FINISHED])
def test_version_1_lines_are_in_the_ledger_without_lineage(event: TrialLedgerEvent) -> None:
    """N2: 版 1 の開始・結末の行が 1 行ずつあり、系譜を持たない（元の実験）。"""
    entry = _line_of(event, V1_EXPERIMENT_ID).entry
    assert (entry.experiment_name, entry.experiment_version) == (EXPERIMENT_NAME, 1)
    assert entry.lineage is None


@pytest.mark.parametrize("event", [TrialLedgerEvent.STARTED, TrialLedgerEvent.FINISHED])
def test_version_2_lines_carry_the_lineage_to_version_1(event: TrialLedgerEvent) -> None:
    """N2: 版 2 の開始・結末の行の系譜が版 1 の実行番号 1 を指し、変更の種類は 1 つ。"""
    entry = _line_of(event, V2_EXPERIMENT_ID).entry
    assert (entry.experiment_name, entry.experiment_version) == (EXPERIMENT_NAME, 2)
    lineage = entry.lineage
    assert lineage is not None and lineage.derived_from is not None
    assert lineage.derived_from.experiment_id.digest.hex == V1_EXPERIMENT_ID
    assert lineage.derived_from.execution == 1
    assert lineage.change_kinds == (ChangeKind.PARAMETERS,)
    # 版 2 が「既に見た」区間は、版 1 の検証区間そのもの（D09 §10.10）。
    source = _line_of(TrialLedgerEvent.STARTED, V1_EXPERIMENT_ID).entry
    assert lineage.viewed_before == source.validation_intervals


def test_the_note_digest_is_the_digest_of_the_version_2_note() -> None:
    """N2: 理由の文のダイジェストがあり、版 2 の実験設定の `lineage.note` の SHA-256 と一致する。"""
    loaded = load_experiment_v2(
        EXPERIMENT_V2,
        repo_root=REPO_ROOT,
        registry=INITIAL_CATALOG,
        metric_set_versions=METRIC_SET_VERSIONS,
    )
    assert loaded.search is not None and loaded.search.lineage is not None
    expected = hashlib.sha256(loaded.search.lineage.note.encode("utf-8")).hexdigest()
    for event in (TrialLedgerEvent.STARTED, TrialLedgerEvent.FINISHED):
        lineage = _line_of(event, V2_EXPERIMENT_ID).entry.lineage
        assert lineage is not None and lineage.note_digest is not None
        assert lineage.note_digest.hex == expected


def test_the_readme_records_the_sha256_of_the_ledger_through_version_2() -> None:
    """README §4.3 のファイルの SHA-256 と末尾の行のダイジェストは、台帳の版 2 までの 8 行のもの。

    後で行が足されても、先頭の 8 行だけを比べるので左右されない。
    """
    raw = (REPO_ROOT / TRIAL_LEDGER_PATH).read_bytes()
    first = b"".join(raw.splitlines(keepends=True)[:LEDGER_LINES_THROUGH_V2])
    section = _section(4)
    assert f"| 追記の後のファイルの SHA-256 | `{hashlib.sha256(first).hexdigest()}` |" in section
    tail = _lines()[LEDGER_LINES_THROUGH_V2 - 1]
    assert tail.entry.experiment_id.digest.hex == V2_EXPERIMENT_ID
    assert tail.digest.hex in section


def test_every_hash_in_section_5_is_a_full_sha256() -> None:
    """第 5.4 節の置き場の表は 5 行で、SHA-256 の列は 64 桁の小文字の 16 進。"""
    rows = [
        line
        for line in _section(5).splitlines()
        if line.startswith("| ") and line.count("|") == 4 and "`runs/" in line
    ]
    assert len(rows) == 5
    for row in rows:
        cell = row.rstrip(" |").rsplit("|", 1)[1].strip()
        assert re.fullmatch(r"`[0-9a-f]{64}`", cell), row


def test_the_version_1_memo_hash_matches_decision_66() -> None:
    """第 5.4 節の版 1 の分析メモの SHA-256 は、第 3.1 節の決定 66 の値と同じ。"""
    decided = re.search(r"メモの SHA-256: ([0-9a-f]{64})", _section(3))
    assert decided is not None
    memo_row = next(
        line for line in _section(5).splitlines() if "/v1/analysis/analysis_memo.md" in line
    )
    assert f"`{decided.group(1)}`" in memo_row


def test_the_gate_rows_are_all_present() -> None:
    """N1〜N3・N5 は合格、N4 は人間が判定する（D19 §3、決定 54）。"""
    rows = {
        line.split("|")[1].strip(): line
        for line in _section(5).splitlines()
        if re.match(r"\| N[1-5] \|", line)
    }
    assert sorted(rows) == ["N1", "N2", "N3", "N4", "N5"]
    for key in ("N1", "N2", "N5"):
        assert rows[key].rstrip().endswith("| 合格 |"), rows[key]
    assert rows["N3"].rstrip().endswith("| 合格（再現は第 2.5 節の記録による） |"), rows["N3"]
    assert rows["N4"].rstrip().endswith("| 人間 |"), rows["N4"]


def test_section_5_carries_no_verdict_values() -> None:
    """第 5 節に判定の値（4 値・2 軸・fold の判定）の識別子と表示の語が無い（D14 §7.1 (a)）。"""
    section = _section(5)
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


def test_section_5_carries_no_price_like_decimals() -> None:
    """第 5 節に小数点以下 3 桁以上の小数（実価格や指標の値の形）が無い（D19 §6）。

    格子の値（R の 1.0・1.5 など）は小数点以下 1 桁なので当たらない。
    """
    assert re.search(r"(?<![0-9A-Za-z])[0-9]+\.[0-9]{3,}(?![0-9])", _section(5)) is None


def _table_rows(text: str, heading: str) -> list[tuple[str, str]]:
    """`heading` の後の最初の表の、見出し行と区切り行を除いた各行の 1 列目と 2 列目。"""
    body = text[text.index(heading) :]
    rows: list[tuple[str, str]] = []
    for line in body.splitlines()[1:]:
        if line.startswith("|"):
            cells = line.split("|")
            rows.append((cells[1].strip(), cells[2].strip()))
        elif rows:
            break
    return rows[2:]


def test_the_prediction_rows_match_the_preregistered_predictions() -> None:
    """第 5.2 節の予想と実際は、第 3.3 節の予想と同じ対象を同じ順で並べ、語は 3 つのどれか。"""
    predicted = [head for head, _ in _table_rows(_section(3), "### 3.3 予想")]
    compared = _table_rows(_section(5), "**予想（第 3.3 節）と実際の一致・不一致**")
    assert len(predicted) == 10
    assert [head for head, _ in compared] == predicted
    for head, word in compared:
        assert word in {"一致", "一部一致", "不一致"}, (head, word)


def test_the_human_notes_are_in_the_preregistration_and_the_loop_report() -> None:
    """N5: 事前の記録（第 1.6 節）と一巡の報告（第 5.7 節）に、注記 (1)〜(3) がある（D19 §3）。"""
    preregistration = _section(1)
    assert "**(1) 評価範囲は 1 巡目と同じ**" in preregistration
    assert "**(2) 研究仮説を選ぶ前に人間が見た成績の範囲**" in preregistration
    assert "**(3) 台帳の累計に 1 巡目の実行が入る**" in preregistration
    report = _section(5)
    assert "### 5.7 " in report
    assert "**(1) 版 1 の判定も、版 2 の判定も、独立した確認ではない**" in report
    assert "**(2) H6 は、関連する戦略の成績の要約を見た人が選んだ仮説である**" in report
    assert "**(3) 台帳の累計に 1 巡目の実行が入る**" in report
