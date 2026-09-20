"""設定ファイルの値の厳密な解析の単体テスト（ADR-0012、D03 §3.2）。

期間（ISO 8601）・現地時刻・現地日付・数値の解析が、受ける形式だけを受け、それ以外を
拒否することを確かめる。

期間は外部ライブラリを使わず自前で解析する。受けるのは `P<n>D` / `PT<n>H` / `PT<n>M` /
`PT<n>S` とその組合せだけで、週・月・年は「長さが文脈で決まる」ため拒否する。時間足の
名目長として使う値なので、文脈で長さが変わる単位を受けると足の検証が成立しない。
"""

from __future__ import annotations

from datetime import date, time, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest

from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.app.config.scalars import (
    parse_duration,
    parse_local_date,
    parse_local_time,
    require_decimal,
)

# --- 期間 -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("PT15M", timedelta(minutes=15)),
        ("PT1H", timedelta(hours=1)),
        ("PT4H", timedelta(hours=4)),
        ("P1D", timedelta(days=1)),
        ("PT30S", timedelta(seconds=30)),
        ("PT1H30M", timedelta(hours=1, minutes=30)),
        ("P1DT2H", timedelta(days=1, hours=2)),
        ("P2DT3H4M5S", timedelta(days=2, hours=3, minutes=4, seconds=5)),
    ],
)
def test_the_accepted_durations_are_parsed(text: str, expected: timedelta) -> None:
    assert parse_duration(text, "label") == expected


@pytest.mark.parametrize(
    "text",
    [
        "P1W",  # 週。名目長として使うと文脈で長さが変わりうる
        "P1M",  # 月
        "P1Y",  # 年
        "PT1.5H",  # 小数
        "-PT1H",  # 符号付き
        "PT-1H",
        "P",  # 要素が無い
        "PT",
        "PT0S",  # 長さ 0
        "P0D",
        "1h",  # ISO 8601 ではない
        "15m",
        "",
        "pt1h",  # 小文字
        "PT1H ",  # 余分な空白
        "P1DT",  # `T` の後が空
    ],
)
def test_the_rejected_durations_are_refused(text: str) -> None:
    with pytest.raises(ConfigError):
        parse_duration(text, "label")


def test_a_duration_that_is_not_a_string_is_refused() -> None:
    """文字列以外は受けない。型注釈を欺いて渡しても実行時に止まることを確かめる。"""
    not_a_string: Any = 90
    with pytest.raises(ConfigError):
        parse_duration(cast(str, not_a_string), "label")


# --- 現地時刻・現地日付 -----------------------------------------------------


def test_a_local_time_is_parsed() -> None:
    assert parse_local_time("17:00:00", "label") == time(17, 0, 0)


@pytest.mark.parametrize(
    "text",
    [
        "17:00",  # 秒まで必須
        "17:00:00+00:00",  # オフセット付きは受けない（変換は domain の責務）
        "25:00:00",  # 成立しない時刻
        "17:60:00",
        "7:00:00",  # 桁を揃える
        "",
    ],
)
def test_the_rejected_local_times_are_refused(text: str) -> None:
    with pytest.raises(ConfigError):
        parse_local_time(text, "label")


def test_a_local_date_is_parsed() -> None:
    assert parse_local_date("2024-12-25", "label") == date(2024, 12, 25)


@pytest.mark.parametrize("text", ["2024-12-25T00:00:00Z", "2024/12/25", "2024-13-01", "2024-2-3"])
def test_the_rejected_local_dates_are_refused(text: str) -> None:
    with pytest.raises(ConfigError):
        parse_local_date(text, "label")


# --- 数値（ADR-0012）--------------------------------------------------------


def test_a_decimal_is_built_from_a_string() -> None:
    """文字列から作るので、二進浮動小数の誤差が入らない（ADR-0012）。"""
    assert require_decimal("0.001", "label") == Decimal("0.001")


def test_a_float_is_refused() -> None:
    """浮動小数そのものを渡す経路を塞ぐ（ADR-0012）。"""
    a_float: Any = 0.001
    with pytest.raises(ConfigError):
        require_decimal(cast(str, a_float), "label")


@pytest.mark.parametrize("text", ["NaN", "Infinity", "abc", "", "1,000", "0x10"])
def test_the_rejected_numbers_are_refused(text: str) -> None:
    with pytest.raises(ConfigError):
        require_decimal(text, "label")
