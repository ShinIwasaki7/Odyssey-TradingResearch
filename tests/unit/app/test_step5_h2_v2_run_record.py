"""順 5 の最後の PR（PR-3）の記録の確認（D14 §2.7 の手順 6、§7.2 の確認項目 E2）。

- 版管理された試行台帳に、改善後の実験（研究仮説 H2 の版 2）の開始の行と結末の行があり、その系譜が
  元の実験（版 1）の実行番号 1 を指し、変更の種類が `PARAMETERS` の 1 つで、理由の文のダイジェストが
  版 2 の実験設定の理由の文と一致する（D14 §7.2 の E2、D13 §7.1、D09 §10.10）。
- 注記 `docs/data/usdjpy_step5_research_loop/README.md` に第 4 節（版 2 の実行記録）と第 5 節
  （一巡の報告）があり、終了条件の確認項目 E1〜E5 の行と、台帳の先頭 4 行の SHA-256 が実物と
  一致する記録がある（D14 §7.1 (a)）。
- 第 4 節・第 5 節に判定の値（4 値・2 軸・fold の判定）の識別子と表示の語が無い（D14 §7.1 (a)、
  §10。数値入りの比較表と分析メモは版管理の外）。

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
EXPERIMENT_V2: Final = REPO_ROOT / "configs/experiments/h2_daily_uptrend_h1_ema_cross_v2.yaml"
README: Final = REPO_ROOT / "docs/data/usdjpy_step5_research_loop/README.md"
#: 版 1・版 2 の記録票の識別子（README §2.1・§4.1）。
V1_EXPERIMENT_ID: Final = "9c95335880803f535800f919d4a8d84b4e5291a808941e6c0506a0d6b92efc25"
V2_EXPERIMENT_ID: Final = "4d6e2b04dadc51f9464e05aef1af95a60ba02e14f47ff2870eaec016576da27b"
#: 版 1・版 2 の台帳の 4 行（README §4.2）。
LEDGER_LINES_THROUGH_V2: Final = 4


def _lines() -> tuple[TrialLedgerLine, ...]:
    contents = read_trial_ledger_file(REPO_ROOT / TRIAL_LEDGER_PATH)
    assert isinstance(contents, TrialLedgerContents), contents
    return tuple(contents.lines)


def _section_4_and_5() -> str:
    text = README.read_text(encoding="utf-8")
    start = text.index("\n## 4. ")
    assert "\n## 5. " in text[start:]
    return text[start:]


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
def test_version_2_lines_carry_the_lineage_to_version_1(event: TrialLedgerEvent) -> None:
    """E2: 版 2 の開始・結末の行の系譜が版 1 の実行番号 1 を指し、変更の種類は 1 つ。"""
    entry = _line_of(event, V2_EXPERIMENT_ID).entry
    assert (entry.experiment_name, entry.experiment_version) == (
        "h2_daily_uptrend_h1_ema_cross",
        2,
    )
    lineage = entry.lineage
    assert lineage is not None and lineage.derived_from is not None
    assert lineage.derived_from.experiment_id.digest.hex == V1_EXPERIMENT_ID
    assert lineage.derived_from.execution == 1
    assert lineage.change_kinds == (ChangeKind.PARAMETERS,)
    # 版 2 が「既に見た」区間は、版 1 の検証区間そのもの（D09 §10.10）。
    source = _line_of(TrialLedgerEvent.STARTED, V1_EXPERIMENT_ID).entry
    assert lineage.viewed_before == source.validation_intervals


def test_the_note_digest_is_the_digest_of_the_version_2_note() -> None:
    """E2: 理由の文のダイジェストがあり、版 2 の実験設定の `lineage.note` の SHA-256 と一致する。"""
    loaded = load_experiment_v2(
        EXPERIMENT_V2,
        repo_root=REPO_ROOT,
        registry=INITIAL_CATALOG,
        metric_set_versions=METRIC_SET_VERSIONS,
    )
    assert loaded.search is not None and loaded.search.lineage is not None
    note = loaded.search.lineage.note
    lineage = _line_of(TrialLedgerEvent.STARTED, V2_EXPERIMENT_ID).entry.lineage
    assert lineage is not None and lineage.note_digest is not None
    assert lineage.note_digest.hex == hashlib.sha256(note.encode("utf-8")).hexdigest()


def test_the_readme_records_the_sha256_of_the_first_four_ledger_lines() -> None:
    """README §4.2 のファイルの SHA-256 は台帳の版 2 までの 4 行のもの（追記に左右されない）。

    末尾の行のダイジェストも書いてある。
    """
    raw = (REPO_ROOT / TRIAL_LEDGER_PATH).read_bytes()
    first = b"".join(raw.splitlines(keepends=True)[:LEDGER_LINES_THROUGH_V2])
    digest = hashlib.sha256(first).hexdigest()
    section = _section_4_and_5()
    assert f"| ファイルの SHA-256 | `{digest}` |" in section
    tail = _lines()[LEDGER_LINES_THROUGH_V2 - 1]
    assert tail.digest.hex in section


def test_every_hash_in_sections_4_and_5_is_a_full_sha256() -> None:
    """第 4・5 節の表の SHA-256 の列は 64 桁の小文字の 16 進。"""
    rows = [
        line
        for line in _section_4_and_5().splitlines()
        if line.startswith("| ") and line.count("|") == 4 and "`runs/" in line
    ]
    assert len(rows) >= 9
    for row in rows:
        cell = row.rstrip(" |").rsplit("|", 1)[1].strip()
        assert re.fullmatch(r"`[0-9a-f]{64}`", cell), row


def test_the_exit_criteria_rows_are_all_present() -> None:
    """E1〜E3・E5 は合格、E4 は人間が判定する（D14 §7.2、D14-8 (a)）。"""
    rows = {
        line.split("|")[1].strip(): line
        for line in _section_4_and_5().splitlines()
        if re.match(r"\| E[1-5] \|", line)
    }
    assert sorted(rows) == ["E1", "E2", "E3", "E4", "E5"]
    for key in ("E1", "E2", "E3", "E5"):
        assert rows[key].rstrip().endswith("| 合格 |"), rows[key]
    assert "人間が判定する" in rows["E4"]


def test_sections_4_and_5_carry_no_verdict_values() -> None:
    """第 4・5 節に判定の値（4 値・2 軸・fold の判定）の識別子と表示の語が無い（D14 §7.1 (a)）。"""
    section = _section_4_and_5()
    identifiers = {
        member.value
        for enum in (SearchVerdict, ProfitabilityAxis, EvidenceAxis, FoldVerdict)
        for member in enum
    }
    for identifier in identifiers:
        assert re.search(rf"\b{identifier}\b", section) is None, identifier
    labels = {
        *_VERDICT_NAMES.values(),
        *_FOLD_VERDICT_NAMES.values(),
        *_PROFITABILITY_NAMES.values(),
        *_EVIDENCE_NAMES.values(),
    }
    for label in labels:
        assert label not in section, label
