"""4種の設定ファイルの読込の単体テスト（D03 §9、D01 §10.1）。

確かめること:

- `configs/` にある**実物の設定ファイル**が読め、期待する frozen dataclass になる。
  人工の設定だけで試験すると、実物の書き方（現地時刻の表記、数値を文字列で書く決まり）
  がずれても気づけない。
- 未宣言キー・型不一致・未知の形式版が拒否される（D01 §10.1）。
- 浮動小数として書かれた価格が拒否される（ADR-0012）。
- Pydantic のモデルが読込の結果として外へ出てこない（D01 §10.1）。
"""

from __future__ import annotations

from datetime import time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from odyssey_fx.app.config import (
    ConfigError,
    load_calendar,
    load_datasource,
    load_symbol_spec,
    load_symbol_specs,
    load_timeframes,
    parse_file_name,
)
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.marketdata.domain.calendar import TradingCalendar, WeeklyMoment
from odyssey_fx.marketdata.domain.series import PriceBasis
from odyssey_fx.marketdata.domain.timeframe_def import (
    FixedUtcAlignment,
    SessionAlignment,
    TimeframeDefinition,
)
from tests.fixtures.synthetic import market

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIGS = REPO_ROOT / "configs"

CALENDAR_FILE = CONFIGS / "calendars/fx_ny17_v1.yaml"
TIMEFRAMES_FILE = CONFIGS / "calendars/timeframes_v1.yaml"
DATASOURCE_FILE = CONFIGS / "datasources/legacy_merged_csv_v1.yaml"
SYMBOLS_DIR = CONFIGS / "symbols"


# --- 実物のカレンダー -------------------------------------------------------


def test_the_real_calendar_file_loads() -> None:
    """実物のカレンダーが読め、週の開閉が宣言どおりになる（D03 §3.4）。"""
    calendar = load_calendar(CALENDAR_FILE)

    assert isinstance(calendar, TradingCalendar)
    assert calendar.id == "fx_ny17"
    assert calendar.version == 1
    assert calendar.tz == ZoneInfo("America/New_York")
    assert calendar.weekly_open == WeeklyMoment(weekday=6, at=time(17, 0, 0))
    assert calendar.weekly_close == WeeklyMoment(weekday=4, at=time(17, 0, 0))
    # 休場は宣言制で、分類が確定するまで空である（D03 §3.4）。
    assert calendar.closures == ()


def test_the_real_calendar_matches_the_one_the_tests_use() -> None:
    """設定ファイルから読んだカレンダーが、テストの人工データ生成器と同じ値になる。

    両者がずれていると、テストが通っても実データの受入れが別の規則で動く。
    """
    assert load_calendar(CALENDAR_FILE) == market.calendar()


def _calendar_with(tmp_path: Path, openings: str) -> Path:
    """実物のカレンダーの `openings` だけを差し替えた版 2 を書く。"""
    text = CALENDAR_FILE.read_text(encoding="utf-8").replace("\nversion: 1\n", "\nversion: 2\n")
    assert "openings: []" in text
    path = tmp_path / "fx_ny17_v2.yaml"
    path.write_text(text.replace("openings: []", openings), encoding="utf-8")
    return path


def test_the_real_calendar_declares_no_opening_yet() -> None:
    """営業例外も宣言制で、分類が確定するまで空である（D03 §3.4 v1.7）。"""
    assert load_calendar(CALENDAR_FILE).openings == ()


def test_an_opening_is_read(tmp_path: Path) -> None:
    """営業例外（`openings`）を読める（D03 §3.4 v1.7）。"""
    path = _calendar_with(
        tmp_path,
        'openings:\n  - local_date: "2022-01-09"\n    start: "16:00:00"\n'
        '    end: "17:00:00"\n    note: 早期開場\n',
    )
    (opening,) = load_calendar(path).openings
    assert opening.start == time(16, 0)
    assert opening.note == "早期開場"


def test_a_detached_opening_is_a_configuration_error(tmp_path: Path) -> None:
    """通常の週の開場区間から離れた営業例外は設定の誤りとして拒否する（2026-09-24）。"""
    path = _calendar_with(
        tmp_path,
        'openings:\n  - local_date: "2022-01-08"\n    start: "10:00:00"\n    end: "11:00:00"\n',
    )
    with pytest.raises(ConfigError, match="カレンダーとして成立しない"):
        load_calendar(path)


# --- 実物の時間足定義 -------------------------------------------------------


def test_the_real_timeframes_file_loads() -> None:
    """時間足定義 4 件が読める（D03 §3.2・§9）。"""
    definitions = load_timeframes(TIMEFRAMES_FILE)
    assert sorted(definitions) == ["15m", "1d_ny17", "1h", "4h_ny17"]
    for definition in definitions.values():
        assert isinstance(definition, TimeframeDefinition)


def test_the_fixed_utc_timeframes_align_to_their_nominal_length() -> None:
    """固定 UTC 整列の足の長さは常に名目長に等しい（D03 §3.2）。"""
    definitions = load_timeframes(TIMEFRAMES_FILE)

    hourly = definitions["1h"]
    assert hourly.nominal_length == timedelta(hours=1)
    assert isinstance(hourly.alignment, FixedUtcAlignment)
    assert hourly.alignment.step == timedelta(hours=1)

    quarter = definitions["15m"]
    assert quarter.nominal_length == timedelta(minutes=15)
    assert isinstance(quarter.alignment, FixedUtcAlignment)
    assert quarter.alignment.step == timedelta(minutes=15)


def test_the_session_timeframes_carry_local_anchors_only() -> None:
    """現地起点だけを持ち、固定 UTC 時刻は持たない（D03 §3.2）。"""
    definitions = load_timeframes(TIMEFRAMES_FILE)

    four_hour = definitions["4h_ny17"]
    assert isinstance(four_hour.alignment, SessionAlignment)
    assert four_hour.alignment.tz == ZoneInfo("America/New_York")
    assert four_hour.alignment.anchors_local == (
        time(1, 0),
        time(5, 0),
        time(9, 0),
        time(13, 0),
        time(17, 0),
        time(21, 0),
    )

    daily = definitions["1d_ny17"]
    assert isinstance(daily.alignment, SessionAlignment)
    assert daily.alignment.anchors_local == (time(17, 0),)
    assert daily.nominal_length == timedelta(days=1)


def test_the_real_timeframes_match_the_ones_the_tests_use() -> None:
    assert load_timeframes(TIMEFRAMES_FILE) == market.TIMEFRAME_DEFS


# --- 実物のデータソース -----------------------------------------------------


def test_the_real_datasource_file_loads() -> None:
    """列対応・時刻規約・宣言した価格基準が読める（D03 §4 の 2・§9）。"""
    datasource = load_datasource(DATASOURCE_FILE)

    assert datasource.id == "legacy_merged_csv"
    assert datasource.root == "data/raw/market"
    assert datasource.timeframes == ("15m", "1h")
    assert datasource.mapping.time_column == "timestamp"
    assert datasource.mapping.time_convention == "explicit_offset_utc"
    assert datasource.allowed_sources == frozenset({"histdata", "dukascopy"})


def test_the_declared_basis_is_never_marked_verified() -> None:
    """価格基準は人間の宣言であり、検証済みの事実ではない（D03 §2・§3.7）。"""
    declaration = load_datasource(DATASOURCE_FILE).basis_declaration
    assert declaration.value is PriceBasis.BID
    assert declaration.verified is False


def test_a_basis_declared_as_verified_is_rejected(tmp_path: Path) -> None:
    """検証していない宣言を検証済みとして記録する経路を作らない。"""
    text = DATASOURCE_FILE.read_text(encoding="utf-8").replace(
        "  verified: false", "  verified: true"
    )
    path = tmp_path / "datasource.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="verified"):
        load_datasource(path)


def test_the_file_name_pattern_round_trips() -> None:
    """ファイル名の組み立てと解析が一致する（D03 §4 の 1）。"""
    datasource = load_datasource(DATASOURCE_FILE)
    name = datasource.file_name(Symbol("USDJPY"), "1h")
    assert name == "USDJPY_1h_merged.csv"
    assert parse_file_name(datasource.filename_pattern, name) == (Symbol("USDJPY"), "1h")


@pytest.mark.parametrize(
    "name", ["USDJPY_1h.csv", "usdjpy_1h_merged.csv", "USDJPY_merged.csv", "notes.txt"]
)
def test_a_file_name_outside_the_pattern_is_not_matched(name: str) -> None:
    """規則に合わない名前は対象にしない。系列が宣言から決まらなくなるため。"""
    datasource = load_datasource(DATASOURCE_FILE)
    assert parse_file_name(datasource.filename_pattern, name) is None


# --- 実物の銘柄仕様 ---------------------------------------------------------


def test_the_real_symbol_specs_load() -> None:
    """初版の 10 ペアが読める（D03 §9）。"""
    specs = load_symbol_specs(SYMBOLS_DIR)
    assert len(specs) == 10
    assert Symbol("USDJPY") in specs


def test_the_symbol_spec_values_are_decimals_built_from_strings() -> None:
    """価格刻みなどが文字列から作った Decimal になる（ADR-0012）。"""
    spec = load_symbol_spec(SYMBOLS_DIR / "USDJPY.yaml")
    assert spec.symbol == Symbol("USDJPY")
    assert spec.price_tick == Decimal("0.001")
    assert spec.pip_size == Decimal("0.01")
    assert spec.quantity_step == Decimal("1000")
    assert spec.min_quantity == Decimal("1000")
    assert spec.lot_size == Decimal("100000")
    # 文字列から作っているので、浮動小数を経由した値とは一致しない桁も正確に表せる。
    assert str(spec.price_tick) == "0.001"


def test_a_price_written_as_a_float_is_rejected(tmp_path: Path) -> None:
    """浮動小数で書かれた価格は型不一致として拒否される（ADR-0012、D01 §10.1）。"""
    text = (SYMBOLS_DIR / "USDJPY.yaml").read_text(encoding="utf-8")
    path = tmp_path / "USDJPY.yaml"
    path.write_text(text.replace('price_tick: "0.001"', "price_tick: 0.001"), encoding="utf-8")
    with pytest.raises(ConfigError, match="price_tick"):
        load_symbol_spec(path)


def test_a_symbol_spec_whose_file_name_disagrees_is_rejected(tmp_path: Path) -> None:
    """ファイル名と `symbol` の食い違いを拒否する（ファイル名で引ける前提のため）。"""
    text = (SYMBOLS_DIR / "USDJPY.yaml").read_text(encoding="utf-8")
    path = tmp_path / "EURUSD.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="食い違う"):
        load_symbol_spec(path)


def test_an_empty_symbols_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="1件も無い"):
        load_symbol_specs(tmp_path)


# --- 未宣言キー・型不一致・未知の形式版（D01 §10.1）-------------------------


def test_an_unknown_key_is_rejected(tmp_path: Path) -> None:
    """綴りを誤ったキーが黙って無視される事故を防ぐ。"""
    text = CALENDAR_FILE.read_text(encoding="utf-8") + "\nunknown_key: 1\n"
    path = tmp_path / "calendar.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown_key"):
        load_calendar(path)


def test_a_type_mismatch_is_rejected(tmp_path: Path) -> None:
    text = CALENDAR_FILE.read_text(encoding="utf-8").replace("version: 1", 'version: "1"', 1)
    path = tmp_path / "calendar.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_calendar(path)


def test_an_unknown_schema_version_is_rejected(tmp_path: Path) -> None:
    text = CALENDAR_FILE.read_text(encoding="utf-8").replace(
        "schema_version: 1", "schema_version: 2"
    )
    path = tmp_path / "calendar.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="schema_version"):
        load_calendar(path)


def test_an_unknown_timezone_is_rejected(tmp_path: Path) -> None:
    text = CALENDAR_FILE.read_text(encoding="utf-8").replace(
        "tz: America/New_York", "tz: Mars/Olympus_Mons"
    )
    path = tmp_path / "calendar.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="時間帯"):
        load_calendar(path)


def test_a_weekday_outside_the_range_is_rejected(tmp_path: Path) -> None:
    text = CALENDAR_FILE.read_text(encoding="utf-8").replace("  weekday: 6 # 日曜", "  weekday: 7")
    path = tmp_path / "calendar.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_calendar(path)


def test_a_duplicate_timeframe_id_is_rejected(tmp_path: Path) -> None:
    """同じ `id` が2度現れる設定は、どちらを採るかが宣言から読み取れない。"""
    text = (
        "schema_version: 1\n"
        "timeframes:\n"
        "  - id: 1h\n"
        "    version: 1\n"
        "    nominal_length: PT1H\n"
        "    alignment:\n"
        "      kind: fixed_utc\n"
        "  - id: 1h\n"
        "    version: 2\n"
        "    nominal_length: PT1H\n"
        "    alignment:\n"
        "      kind: fixed_utc\n"
    )
    path = tmp_path / "timeframes.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="2度"):
        load_timeframes(path)


def test_a_timeframe_with_an_unknown_alignment_kind_is_rejected(tmp_path: Path) -> None:
    text = (
        "schema_version: 1\n"
        "timeframes:\n"
        "  - id: 1h\n"
        "    version: 1\n"
        "    nominal_length: PT1H\n"
        "    alignment:\n"
        "      kind: lunar\n"
    )
    path = tmp_path / "timeframes.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_timeframes(path)


def test_a_session_timeframe_without_anchors_is_rejected(tmp_path: Path) -> None:
    text = (
        "schema_version: 1\n"
        "timeframes:\n"
        "  - id: 1d_ny17\n"
        "    version: 1\n"
        "    nominal_length: P1D\n"
        "    alignment:\n"
        "      kind: session\n"
        "      tz: America/New_York\n"
        "      anchors_local: []\n"
    )
    path = tmp_path / "timeframes.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_timeframes(path)


# --- Pydantic を外へ出さない（D01 §10.1）------------------------------------


def test_the_loaders_return_domain_types_not_pydantic_models() -> None:
    """読込の結果に Pydantic の型が混じらない（D01 §10.1）。

    混じると、設定の検証ライブラリが `app.config` の外へ漏れ、domain が外部ライブラリの
    型に依存してしまう。
    """
    loaded: list[object] = [
        load_calendar(CALENDAR_FILE),
        *load_timeframes(TIMEFRAMES_FILE).values(),
        load_datasource(DATASOURCE_FILE),
        *load_symbol_specs(SYMBOLS_DIR).values(),
    ]
    for value in loaded:
        module = type(value).__module__
        assert not module.startswith("pydantic"), f"{type(value)} は Pydantic の型である"
