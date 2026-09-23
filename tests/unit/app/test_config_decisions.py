"""欠落区間の分類の読込の単体テスト（D03 §3.1・§4 の 9、§10 の `classify`）。

確かめること:

- 分類の系列は、snapshot が**実際に持つ系列**から解決される。時間足の版を決め打たない。
- snapshot に無い系列の分類は拒否される。
- 同じ系列・同じ区間の分類が2度現れる設定は拒否される。
- 分類の種別（休場 / データ欠損）と区間の書式が検証される。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from odyssey_fx.app.config import ConfigError, load_closure_decisions, resolve_series_id
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.snapshot import ClosureDecisionKind

#: 版 1 と版 2 の同じ系列（時間足の版だけが違う）。
HOURLY_V1 = SeriesId(symbol=Symbol("USDJPY"), timeframe=TimeframeRef("1h", 1), basis=PriceBasis.BID)
HOURLY_V2 = SeriesId(symbol=Symbol("USDJPY"), timeframe=TimeframeRef("1h", 2), basis=PriceBasis.BID)

#: 系列の文字列表記には版が含まれない（D03 §3.1）。
SERIES_TEXT = "USDJPY/1h/bid"


def _decisions_file(tmp_path: Path, *, kind: str = "CLOSURE", series: str = SERIES_TEXT) -> Path:
    path = tmp_path / "decisions.yaml"
    path.write_text(
        "schema_version: 1\n"
        "decisions:\n"
        f"  - series: {series}\n"
        "    interval:\n"
        '      start: "2022-01-06T10:00:00Z"\n'
        '      end: "2022-01-06T11:00:00Z"\n'
        f"    kind: {kind}\n"
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
    """版 2 の定義を使った snapshot では、分類の系列も版 2 になる。

    版を 1 と決め打つと、分類の系列が報告・系列記録と食い違ったまま manifest に記録され、
    識別子の計算に入ってしまう。
    """
    loaded = load_closure_decisions(_decisions_file(tmp_path), [HOURLY_V2])
    (decision,) = loaded.decisions
    assert decision.series_id == HOURLY_V2
    assert decision.series_id.timeframe.version == 2
    assert decision.kind is ClosureDecisionKind.CLOSURE


def test_the_decision_takes_the_version_one_series(tmp_path: Path) -> None:
    """版 1 の snapshot ではこれまでどおり版 1（振る舞いを変えていない）。"""
    loaded = load_closure_decisions(_decisions_file(tmp_path), [HOURLY_V1])
    (decision,) = loaded.decisions
    assert decision.series_id == HOURLY_V1


def test_a_series_the_snapshot_does_not_have_is_rejected(tmp_path: Path) -> None:
    """snapshot に無い系列の分類は拒否する。

    分類は識別子の計算対象なので、実在しない系列の記録を含む snapshot を作らせない。
    """
    other = SeriesId(symbol=Symbol("EURUSD"), timeframe=TimeframeRef("1h", 1), basis=PriceBasis.BID)
    with pytest.raises(ConfigError, match="この snapshot にない"):
        load_closure_decisions(_decisions_file(tmp_path), [other])


def test_a_malformed_series_is_reported_as_such(tmp_path: Path) -> None:
    """綴りの誤りは「未知の系列」ではなく書式の誤りとして報告する。"""
    with pytest.raises(ConfigError, match="系列として読めない|価格基準"):
        load_closure_decisions(_decisions_file(tmp_path, series="USDJPY/1h"), [HOURLY_V1])


# --- 重複と語彙 -------------------------------------------------------------


def test_a_duplicate_decision_is_rejected(tmp_path: Path) -> None:
    """同じ系列・同じ区間の分類が2度現れる設定は拒否する（D03 §3.7.1）。"""
    path = tmp_path / "decisions.yaml"
    entry = (
        f"  - series: {SERIES_TEXT}\n"
        "    interval:\n"
        '      start: "2022-01-06T10:00:00Z"\n'
        '      end: "2022-01-06T11:00:00Z"\n'
        "    kind: CLOSURE\n"
    )
    path.write_text("schema_version: 1\ndecisions:\n" + entry + entry, encoding="utf-8")
    with pytest.raises(ConfigError, match="2度"):
        load_closure_decisions(path, [HOURLY_V1])


def test_an_unknown_decision_kind_is_rejected(tmp_path: Path) -> None:
    """分類は「休場」か「データ欠損」のどちらかに限る（D03 §3.4）。"""
    with pytest.raises(ConfigError, match="CLOSURE|DATA_GAP"):
        load_closure_decisions(_decisions_file(tmp_path, kind="MAYBE"), [HOURLY_V1])


def test_a_data_gap_decision_is_accepted(tmp_path: Path) -> None:
    """データ欠損としての分類も受ける。"""
    loaded = load_closure_decisions(_decisions_file(tmp_path, kind="DATA_GAP"), [HOURLY_V1])
    assert loaded.decisions[0].kind is ClosureDecisionKind.DATA_GAP


def test_the_interval_must_carry_an_offset(tmp_path: Path) -> None:
    """区間の時刻はオフセット必須（見た目から規約を推測しない）。"""
    path = tmp_path / "decisions.yaml"
    path.write_text(
        "schema_version: 1\n"
        "decisions:\n"
        f"  - series: {SERIES_TEXT}\n"
        "    interval:\n"
        '      start: "2022-01-06 10:00:00"\n'
        '      end: "2022-01-06 11:00:00"\n'
        "    kind: CLOSURE\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_closure_decisions(path, [HOURLY_V1])


# --- カレンダーの新版の指定（D03 §4 の 9）-----------------------------------


def test_the_calendar_path_is_optional(tmp_path: Path) -> None:
    """カレンダーを変えない分類では、カレンダーの指定は無い。"""
    assert load_closure_decisions(_decisions_file(tmp_path), [HOURLY_V1]).calendar_path is None


def test_the_calendar_path_is_read_when_declared(tmp_path: Path) -> None:
    """カレンダーを変える分類では、新しい版のファイルを指す。"""
    path = tmp_path / "decisions.yaml"
    path.write_text(
        "schema_version: 1\ncalendar: configs/calendars/fx_ny17_v2.yaml\ndecisions: []\n",
        encoding="utf-8",
    )
    loaded = load_closure_decisions(path, [HOURLY_V1])
    assert loaded.calendar_path == Path("configs/calendars/fx_ny17_v2.yaml")
    assert loaded.decisions == ()


def test_an_unknown_schema_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "decisions.yaml"
    path.write_text("schema_version: 2\ndecisions: []\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="schema_version"):
        load_closure_decisions(path, [HOURLY_V1])
