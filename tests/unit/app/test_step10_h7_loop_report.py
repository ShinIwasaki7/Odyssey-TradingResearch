"""順 10 の最後の PR の記録の確認（D22 §2.1 の手順 6「一巡の報告」、§3 の関門 X2・X5）。

順 8 の一巡の報告の確認（`test_step8_h6_loop_report.py`。PR #159）と同じ形。

- 版管理された試行台帳に、元の実験（研究仮説 H7 の版 1）と改善後の実験（版 2）の開始の行と結末の行が
  各 1 行あり、版 2 の系譜が版 1 の実行番号 1 を指し、変更の種類が `STRATEGY_CODE` の 1 つで、
  理由の文のダイジェストが版 2 の実験設定の理由の文と一致する（関門 X2。D09 §10.10）。
- 台帳の鎖が連続し、累計（`ledger_tally`）の件数が README 第 5.7 節の (3) の件数と同じ。
- 注記 `docs/data/eurusd_step10_research_loop/README.md` の第 5 節（一巡の報告）に、関門 X1〜X5 の
  行（X4 は人間の判定。決定 199）、予想と実際の行（第 3.3 節の予想と同じ対象・同じ順。件数は
  決定 197 の原文と同じ）、SHA-256 の記載と決定 127・197 の原文との一致、人の手の注記 (1)〜(3)
  （関門 X5）がある。
- 第 5 節に判定の値（4 値・2 軸・fold の判定）の識別子と表示の語が無く、価格の形の小数も無い
  （D14 §7.1 (a)。決定 104・110。数値入りの比較表と分析メモは版管理の外）。

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
from odyssey_fx.evaluation.domain.adoption_review import ledger_tally
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
EXPERIMENT_NAME: Final = "h7_h1_dual_ema_cross_eurusd"
EXPERIMENT_V2: Final = REPO_ROOT / f"configs/experiments/{EXPERIMENT_NAME}_v2.yaml"
README: Final = REPO_ROOT / "docs/data/eurusd_step10_research_loop/README.md"
#: 版 1・版 2 の記録票の識別子（README §2.1・§4.1）。
V1_EXPERIMENT_ID: Final = "6a9a3c8a28076e881243ca76babc94470eaf8c92ae51c15eccb43a7a9cf9b78a"
V2_EXPERIMENT_ID: Final = "8845d8d9ece6e54c36cf513109f432eeed2be88025d5d7e110ebb6e836f20be9"
#: 順 5・順 8 の 8 行と版 1・版 2 の 4 行（README §4.3）。
LEDGER_LINES_THROUGH_V2: Final = 12
#: 第 5 節で判定の表示の語と同じ文字列を、予想と実際の照合の区分として使う箇所（決定 197 の原文の
#: (3)）。判定の値ではないので、判定の表示の語の検査の前に取り除く。
COMPARISON_PHRASES: Final = ("一致 4・不一致 7・判定できない 0",)


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


def test_section_5_has_the_eight_subsections_in_order() -> None:
    """第 5 節は順 8 の第 5 節と同じ 8 つの小節を同じ順に持つ。"""
    headings = re.findall(r"^### (5\.[0-9]) ", _section(5), flags=re.MULTILINE)
    assert headings == [f"5.{index}" for index in range(1, 9)]


@pytest.mark.parametrize("event", [TrialLedgerEvent.STARTED, TrialLedgerEvent.FINISHED])
def test_version_1_lines_are_in_the_ledger_without_lineage(event: TrialLedgerEvent) -> None:
    """X2: 版 1 の開始・結末の行が 1 行ずつあり、系譜を持たない（元の実験）。"""
    entry = _line_of(event, V1_EXPERIMENT_ID).entry
    assert (entry.experiment_name, entry.experiment_version) == (EXPERIMENT_NAME, 1)
    assert entry.lineage is None


@pytest.mark.parametrize("event", [TrialLedgerEvent.STARTED, TrialLedgerEvent.FINISHED])
def test_version_2_lines_carry_the_lineage_to_version_1(event: TrialLedgerEvent) -> None:
    """X2: 版 2 の開始・結末の行の系譜が版 1 の実行番号 1 を指し、変更の種類は 1 つ。"""
    entry = _line_of(event, V2_EXPERIMENT_ID).entry
    assert (entry.experiment_name, entry.experiment_version) == (EXPERIMENT_NAME, 2)
    lineage = entry.lineage
    assert lineage is not None and lineage.derived_from is not None
    assert lineage.derived_from.experiment_id.digest.hex == V1_EXPERIMENT_ID
    assert lineage.derived_from.execution == 1
    assert lineage.change_kinds == (ChangeKind.STRATEGY_CODE,)
    # 版 2 が「既に見た」区間は、版 1 の検証区間そのもの（D09 §10.10）。
    source = _line_of(TrialLedgerEvent.STARTED, V1_EXPERIMENT_ID).entry
    assert lineage.viewed_before == source.validation_intervals
    assert len(lineage.viewed_before) == 5


def test_the_note_digest_is_the_digest_of_the_version_2_note() -> None:
    """X2: 理由の文のダイジェストがあり、版 2 の実験設定の `lineage.note` の SHA-256 と一致する。"""
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


def test_the_ledger_chain_is_continuous_through_version_2() -> None:
    """X2: 版 2 までの 12 行の鎖が連続する（各行の `prev` が直前の行のダイジェスト）。

    後で行が足されても、先頭の 12 行だけを見るので左右されない。
    """
    lines = _lines()[:LEDGER_LINES_THROUGH_V2]
    assert len(lines) == LEDGER_LINES_THROUGH_V2
    assert lines[0].prev is None
    for before, after in zip(lines, lines[1:], strict=False):
        assert after.prev == before.digest
    assert lines[-1].entry.experiment_id.digest.hex == V2_EXPERIMENT_ID


def test_the_readme_records_the_sha256_of_the_ledger_through_version_2() -> None:
    """README §4.3 のファイルの SHA-256 と末尾の行のダイジェストは、台帳の版 2 までの 12 行のもの。

    後で行が足されても、先頭の 12 行だけを比べるので左右されない。
    """
    raw = (REPO_ROOT / TRIAL_LEDGER_PATH).read_bytes()
    first = b"".join(raw.splitlines(keepends=True)[:LEDGER_LINES_THROUGH_V2])
    section = _section(4)
    assert f"| 追記の後のファイルの SHA-256 | `{hashlib.sha256(first).hexdigest()}` |" in section
    assert _lines()[LEDGER_LINES_THROUGH_V2 - 1].digest.hex in section


def test_the_ledger_tally_matches_section_5_7() -> None:
    """X5 の (3): 台帳の数え方（`ledger_tally`）の件数が第 5.7 節の件数と同じ。

    版 1 の先行 4 実行（試行 16）、版 2 の先行 5 実行（試行 20）、重なる実行は全体で 6 実行
    （試行 24）。
    版 2 までの 12 行だけで数える（後で足された行に左右されない）。
    """
    lines = _lines()[:LEDGER_LINES_THROUGH_V2]
    v1 = ledger_tally(lines, _line_of(TrialLedgerEvent.STARTED, V1_EXPERIMENT_ID))
    v2 = ledger_tally(lines, _line_of(TrialLedgerEvent.STARTED, V2_EXPERIMENT_ID))
    assert (v1.prior_overlapping_executions, v1.prior_overlapping_trials) == (4, 16)
    assert (v2.prior_overlapping_executions, v2.prior_overlapping_trials) == (5, 20)
    assert (v2.overlapping_executions, v2.overlapping_trials) == (6, 24)
    report = _section(5)
    assert "計 4 実行（試行 16）" in report
    assert "版 1 を足した 5 実行（試行 20）" in report
    assert "台帳の全体で 6 実行（試行 24）" in report


def test_every_hash_in_section_5_4_is_a_full_sha256() -> None:
    """第 5.4 節の置き場の表は 3 行で、SHA-256 の列は 64 桁の小文字の 16 進。"""
    rows = [
        line
        for line in _section(5).splitlines()
        if line.startswith("| ") and line.count("|") == 4 and "`runs/" in line
    ]
    assert len(rows) == 3
    for row in rows:
        cell = row.rstrip(" |").rsplit("|", 1)[1].strip()
        assert re.fullmatch(r"`[0-9a-f]{64}`", cell), row


def _row_hash(fragment: str) -> str:
    row = next(line for line in _section(5).splitlines() if fragment in line and "`runs/" in line)
    return row.rstrip(" |").rsplit("|", 1)[1].strip().strip("`")


def test_the_version_1_memo_hash_matches_decision_127() -> None:
    """第 5.4 節の版 1 の分析メモの SHA-256 は、第 3.1 節の決定 127 の値と同じ。"""
    decided = re.search(r"メモの SHA-256: ([0-9a-f]{64})", _section(3))
    assert decided is not None
    assert _row_hash("/v1/analysis/analysis_memo.md") == decided.group(1)


def test_the_version_2_hashes_match_decision_197() -> None:
    """第 5.4 節の版 2 の分析メモと比較表の SHA-256 は、第 5.8 節の決定 197 の原文 (1)・(2)
    と同じ。"""
    section = _section(5)
    memo = re.search(r"> \(1\) 分析メモ（版 2）の SHA-256: ([0-9a-f]{64})", section)
    table = re.search(r"> \(2\) 比較表の SHA-256: ([0-9a-f]{64})", section)
    assert memo is not None and table is not None
    assert _row_hash("/v2/analysis/analysis_memo.md") == memo.group(1)
    assert _row_hash("/v2/analysis/comparison_table.md") == table.group(1)


def test_decision_197_is_quoted_verbatim() -> None:
    """第 5.8 節に決定 197 の原文 (3)・(4) がある（原文が正本）。"""
    section = _section(5)
    assert "> (3) 予想と実際: 一致 4・不一致 7・判定できない 0" in section
    assert "> (4) 人間の決定の原文: 「ここで終える（推奨）」" in section
    assert "「記録して受け入れる（仮定付き合格。推奨）」" in section


def test_the_gate_rows_are_present_and_x4_is_judged_by_the_human() -> None:
    """X1 は版 1 合格・版 2 仮定付き合格（決定 175）、X2・X3・X5 は合格、X4 は人間が判定して
    合格（決定 199）。終了は決定 200。"""
    rows = {
        line.split("|")[1].strip(): line
        for line in _section(5).splitlines()
        if re.match(r"\| X[1-5] \|", line)
    }
    assert sorted(rows) == ["X1", "X2", "X3", "X4", "X5"]
    assert (
        rows["X1"]
        .rstrip()
        .endswith(
            "| 版 1 は合格。版 2 は仮定付き合格（人間の決定 175。"
            "仮定: 未追跡のテストのファイル 1 本は run の入力に含まれない） |"
        )
    ), rows["X1"]
    for key in ("X2", "X3", "X5"):
        assert rows[key].rstrip().endswith("| 合格 |"), rows[key]
    assert rows["X4"].rstrip().endswith("| 合格（人間の判定「合格（結んでいる）」。決定 199） |"), (
        rows["X4"]
    )
    section = _section(5)
    assert "「合格（結んでいる）」" in section
    assert "「順 10 を終了とする（推奨）」" in section
    assert "「6 件このまま。決定を写して merge（推奨）」" in section
    assert "**2026-11-16 の決定 200 で順 10 は終了（本 PR の merge が終了の記録）。" in section


def test_section_5_carries_no_verdict_values() -> None:
    """第 5 節に判定の値（4 値・2 軸・fold の判定）の識別子と表示の語が無い（D14 §7.1 (a)）。

    決定 197 の原文 (3) の「判定できない 0」は予想と実際の照合の区分で、判定の値ではないので除く。
    """
    section = _section(5)
    for phrase in COMPARISON_PHRASES:
        assert phrase in section, phrase
        section = section.replace(phrase, "")
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
    """第 5 節に小数点以下 3 桁以上の小数（実価格や指標の値の形）が無い（D22 §6）。"""
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
    """第 5.2 節の予想と実際は、第 3.3 節の予想と同じ対象を同じ順で並べ、件数は決定 197 と同じ。"""
    predicted = [head for head, _ in _table_rows(_section(3), "### 3.3 予想")]
    compared = _table_rows(_section(5), "**予想（第 3.3 節）と実際の一致・不一致**")
    assert len(predicted) == 11
    assert [head for head, _ in compared] == predicted
    words = [word for _, word in compared]
    assert set(words) <= {"一致", "不一致", "判定できない"}, words
    assert (words.count("一致"), words.count("不一致"), words.count("判定できない")) == (4, 7, 0)


def test_the_human_notes_are_in_the_preregistration_and_the_loop_report() -> None:
    """X5: 事前の記録（第 1.6 節）と一巡の報告（第 5.7 節）に、注記 (1)〜(3) がある（D22 §3）。"""
    preregistration = _section(1)
    assert "**(1) 評価範囲は 1 巡目・2 巡目と同じ**" in preregistration
    assert "**(2) 研究仮説を選ぶ前に人間が見た成績の範囲**" in preregistration
    assert "**(3) 台帳の累計に順 5・順 8 の実行が入る**" in preregistration
    report = _section(5)
    assert "### 5.7 " in report
    assert "**(1) 版 1 の判定も、版 2 の判定も、独立した確認ではない**" in report
    assert (
        "**(2) H7 は、同じ通貨（EURUSD）・同じ評価範囲での H6 の結果と分析を"
        "見た人が選んだ仮説である**" in report
    )
    assert "**(3) 台帳の累計に順 5・順 8 の 4 実行が入る**" in report


def test_section_5_1_carries_the_ledger_identifiers() -> None:
    """第 5.1 節の実行の識別子と系譜の省略形は、台帳の版 1・版 2 の識別子と同じ。"""
    start = _text().index("### 5.1 ")
    section = _text()[start : _text().index("### 5.2 ", start)]
    assert f"版 1 `{V1_EXPERIMENT_ID}`（実行番号 1）" in section
    assert f"版 2 `{V2_EXPERIMENT_ID}`（実行番号 1）" in section
    assert f"`{V1_EXPERIMENT_ID[:8]}…{V1_EXPERIMENT_ID[-4:]}`" in section
    assert _line_of(TrialLedgerEvent.STARTED, V1_EXPERIMENT_ID).entry.execution == 1
    assert _line_of(TrialLedgerEvent.STARTED, V2_EXPERIMENT_ID).entry.execution == 1


def test_decision_127_in_section_5_8_is_the_same_as_in_section_3_1() -> None:
    """第 5.8 節に再掲した決定 127 の 3 行は、第 3.1 節の原文と一字一句同じ。"""

    def quoted(section: str) -> list[str]:
        return re.findall(r"^> [1-3]\. .+$", section, flags=re.MULTILINE)

    original = quoted(_section(3))
    assert len(original) == 3
    assert quoted(_section(5)) == original
