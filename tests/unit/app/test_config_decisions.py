"""分類ファイル（形式版 2）の読込の単体テスト（D03 §3.1・§3.9・§4 の 9、§10 v1.7）。

確かめること:

- 分類の系列は、snapshot が**実際に持つ系列**から解決される。時間足の版を決め打たない。
- 「全系列」（`all`）は snapshot 内の具体的な系列の集合へ解決される（確定時の検査4）。
- snapshot に無い系列・空の系列集合・同じ系列の重複は拒否される。
- 分類対象の検査種別は明示集合の2種別だけで、種別と結果の組合せの誤りは拒否される。
- 形式版 1 のファイルは読まない。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from odyssey_fx.app.config import ConfigError, load_classification_decisions, resolve_series_id
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.domain.classification import ClassificationOutcome
from odyssey_fx.marketdata.domain.integrity import CheckKind
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId

REPO_ROOT = Path(__file__).resolve().parents[3]
CALENDAR_V1 = REPO_ROOT / "configs/calendars/fx_ny17_v1.yaml"

#: 版 1 と版 2 の同じ系列（時間足の版だけが違う）。
HOURLY_V1 = SeriesId(symbol=Symbol("USDJPY"), timeframe=TimeframeRef("1h", 1), basis=PriceBasis.BID)
HOURLY_V2 = SeriesId(symbol=Symbol("USDJPY"), timeframe=TimeframeRef("1h", 2), basis=PriceBasis.BID)
EURUSD = SeriesId(symbol=Symbol("EURUSD"), timeframe=TimeframeRef("1h", 1), basis=PriceBasis.BID)

#: 系列の文字列表記には版が含まれない（D03 §3.1）。
SERIES_TEXT = "USDJPY/1h/bid"


def _decisions_file(
    tmp_path: Path,
    *,
    kind: str = "MISSING_EXPECTED_BAR",
    outcome: str = "CLOSURE",
    series: str = f"[{SERIES_TEXT}]",
    head: str = "schema_version: 2\n",
) -> Path:
    path = tmp_path / "decisions.yaml"
    path.write_text(
        head + "decisions:\n"
        f"  - kind: {kind}\n"
        "    interval:\n"
        '      start: "2022-01-06T10:00:00Z"\n'
        '      end: "2022-01-06T11:00:00Z"\n'
        f"    series: {series}\n"
        f"    outcome: {outcome}\n"
        "    note: 試験用\n",
        encoding="utf-8",
    )
    return path


# --- 時間足の版（D03 §3.1）--------------------------------------------------


def test_the_series_version_comes_from_the_snapshot() -> None:
    """系列の表記は、snapshot が持つ系列へ解決される（版を決め打たない）。"""
    assert resolve_series_id(SERIES_TEXT, [HOURLY_V2], "label") == HOURLY_V2
    assert resolve_series_id(SERIES_TEXT, [HOURLY_V2], "label").timeframe.version == 2


def test_the_decision_takes_the_version_two_series(tmp_path: Path) -> None:
    """版 2 の定義を使った snapshot では、分類の系列も版 2 になる。"""
    loaded = load_classification_decisions(_decisions_file(tmp_path), [HOURLY_V2])
    (decision,) = loaded.decisions
    assert decision.series == (HOURLY_V2,)
    assert decision.kind is CheckKind.MISSING_EXPECTED_BAR
    assert decision.outcome is ClassificationOutcome.CLOSURE


def test_a_series_the_snapshot_does_not_have_is_rejected(tmp_path: Path) -> None:
    """snapshot に無い系列の分類は拒否する（実在しない系列の記録を作らせない）。"""
    with pytest.raises(ConfigError, match="この snapshot にない"):
        load_classification_decisions(_decisions_file(tmp_path), [EURUSD])


def test_a_malformed_series_is_reported_as_such(tmp_path: Path) -> None:
    """綴りの誤りは「未知の系列」ではなく書式の誤りとして報告する。"""
    with pytest.raises(ConfigError, match="系列として読めない|価格基準"):
        load_classification_decisions(_decisions_file(tmp_path, series="[USDJPY/1h]"), [HOURLY_V1])


# --- 対象系列の集合（D03 §4 の確定時の検査4）--------------------------------


def test_all_resolves_to_every_series_of_the_snapshot(tmp_path: Path) -> None:
    """「全系列」は snapshot 内の具体的な系列の集合として保存する。"""
    loaded = load_classification_decisions(
        _decisions_file(tmp_path, series="all"), [HOURLY_V1, EURUSD]
    )
    (decision,) = loaded.decisions
    assert decision.series == (EURUSD, HOURLY_V1)


def test_an_explicit_set_of_several_series_is_accepted(tmp_path: Path) -> None:
    loaded = load_classification_decisions(
        _decisions_file(tmp_path, series=f"[{SERIES_TEXT}, EURUSD/1h/bid]"), [HOURLY_V1, EURUSD]
    )
    assert loaded.decisions[0].series == (EURUSD, HOURLY_V1)


def test_an_empty_series_set_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="空"):
        load_classification_decisions(_decisions_file(tmp_path, series="[]"), [HOURLY_V1])


def test_a_series_listed_twice_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="2度"):
        load_classification_decisions(
            _decisions_file(tmp_path, series=f"[{SERIES_TEXT}, {SERIES_TEXT}]"), [HOURLY_V1]
        )


# --- 種別と結果の語彙（D03 §3.9）--------------------------------------------


@pytest.mark.parametrize(
    ("kind", "outcome"),
    [
        ("MISSING_EXPECTED_BAR", "CLOSURE"),
        ("MISSING_EXPECTED_BAR", "DATA_GAP"),
        ("UNEXPECTED_BAR", "CALENDAR_EXCEPTION"),
        ("UNEXPECTED_BAR", "OUT_OF_SESSION_DATA"),
    ],
)
def test_the_allowed_pairs_are_accepted(tmp_path: Path, kind: str, outcome: str) -> None:
    loaded = load_classification_decisions(
        _decisions_file(tmp_path, kind=kind, outcome=outcome), [HOURLY_V1]
    )
    assert loaded.decisions[0].outcome is ClassificationOutcome(outcome)


@pytest.mark.parametrize(
    ("kind", "outcome"),
    [
        ("MISSING_EXPECTED_BAR", "CALENDAR_EXCEPTION"),
        ("MISSING_EXPECTED_BAR", "OUT_OF_SESSION_DATA"),
        ("UNEXPECTED_BAR", "CLOSURE"),
        ("UNEXPECTED_BAR", "DATA_GAP"),
    ],
)
def test_a_mismatched_pair_is_rejected(tmp_path: Path, kind: str, outcome: str) -> None:
    """休場帯の足を休場・欠損へ無理に当てはめない（D03 §3.9）。"""
    with pytest.raises(ConfigError, match="not an outcome for"):
        load_classification_decisions(
            _decisions_file(tmp_path, kind=kind, outcome=outcome), [HOURLY_V1]
        )


def test_a_kind_outside_the_classifiable_set_is_rejected(tmp_path: Path) -> None:
    """銘柄間の境界ずれなどは分類を要しない（明示集合の外、D03 §3.9）。"""
    with pytest.raises(ConfigError, match="分類を要しない"):
        load_classification_decisions(
            _decisions_file(tmp_path, kind="CROSS_SYMBOL_MISALIGNMENT"), [HOURLY_V1]
        )


def test_an_unknown_kind_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="MISSING_EXPECTED_BAR"):
        load_classification_decisions(_decisions_file(tmp_path, kind="MAYBE"), [HOURLY_V1])


def test_an_unknown_outcome_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="outcome"):
        load_classification_decisions(_decisions_file(tmp_path, outcome="MAYBE"), [HOURLY_V1])


def test_the_interval_must_carry_an_offset(tmp_path: Path) -> None:
    """区間の時刻はオフセット必須（見た目から規約を推測しない）。"""
    path = tmp_path / "decisions.yaml"
    path.write_text(
        "schema_version: 2\n"
        "decisions:\n"
        "  - kind: MISSING_EXPECTED_BAR\n"
        "    interval:\n"
        '      start: "2022-01-06 10:00:00"\n'
        '      end: "2022-01-06 11:00:00"\n'
        f"    series: [{SERIES_TEXT}]\n"
        "    outcome: CLOSURE\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_classification_decisions(path, [HOURLY_V1])


# --- カレンダーの新版の指定（D03 §4 の 9）-----------------------------------


def test_the_calendar_is_optional(tmp_path: Path) -> None:
    """カレンダーを変えない分類では、カレンダーの指定は無い。"""
    loaded = load_classification_decisions(_decisions_file(tmp_path), [HOURLY_V1])
    assert loaded.calendar is None
    assert loaded.calendar_path is None
    assert loaded.decisions[0].calendar_ref is None


def test_the_calendar_is_read_and_referenced_by_calendar_changing_decisions(
    tmp_path: Path,
) -> None:
    """カレンダーを指す分類では、そのカレンダーを読み、休場の分類が版を参照する。"""
    path = _decisions_file(tmp_path, head=f"schema_version: 2\ncalendar: {CALENDAR_V1}\n")
    loaded = load_classification_decisions(path, [HOURLY_V1])
    assert loaded.calendar is not None
    assert loaded.calendar_path == CALENDAR_V1
    assert loaded.decisions[0].calendar_ref == "fx_ny17@v1"


def test_a_data_gap_does_not_reference_the_calendar(tmp_path: Path) -> None:
    """データ欠損はカレンダーを変えないので、カレンダーの版を参照しない。"""
    path = _decisions_file(
        tmp_path, outcome="DATA_GAP", head=f"schema_version: 2\ncalendar: {CALENDAR_V1}\n"
    )
    assert load_classification_decisions(path, [HOURLY_V1]).decisions[0].calendar_ref is None


# --- 形式版（D01 §10.1）----------------------------------------------------


def test_the_version_one_format_is_rejected(tmp_path: Path) -> None:
    """形式版 1 は読まない（項目の意味が違い、読み替えると取り違えが起きうる）。"""
    path = tmp_path / "decisions.yaml"
    path.write_text("schema_version: 1\ndecisions: []\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="schema_version"):
        load_classification_decisions(path, [HOURLY_V1])


def test_an_empty_decision_list_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "decisions.yaml"
    path.write_text("schema_version: 2\ndecisions: []\n", encoding="utf-8")
    assert load_classification_decisions(path, [HOURLY_V1]).decisions == ()
