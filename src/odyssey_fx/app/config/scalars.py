"""設定ファイルの値から厳密な Python の値を作る（D01 §10.1、ADR-0012・ADR-0018）。

設定ファイルに書かれた文字列を、期間（`timedelta`）・現地時刻（`time`）・現地日付
（`date`）・数値（`Decimal`）へ変換する。いずれも**文字列からの厳密な解析**であり、
見た目からの推測や、浮動小数を経由した変換は行わない。

- 期間（`PT15M`、`P1D`）は外部ライブラリを使わず自前で解析する。受ける形式は
  `P<n>D`、`PT<n>H`、`PT<n>M`、`PT<n>S` とその組合せに限り、それ以外（週 `W`・月 `M`・
  年 `Y`・小数・符号付き）は拒否する。年月週は長さが文脈で変わり、時間足の名目長として
  使えないため。
- 数値は文字列から `Decimal` を作る（`common.money.decimal_from_str`）。YAML の浮動小数
  として読むと二進浮動小数の誤差が入る（ADR-0012）。設定ファイル側も価格・数量は文字列
  で書くことになっており、浮動小数で書かれた値はここで拒否される。
"""

from __future__ import annotations

import re
from datetime import date, time, timedelta
from decimal import Decimal
from typing import Final

from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import decimal_from_str

__all__ = [
    "parse_duration",
    "parse_local_date",
    "parse_local_time",
    "require_decimal",
]

#: 受ける期間の形式（D03 §9 の `PT15M` / `PT1H` / `PT4H` / `P1D`）。
#: `P` の直後の日部分と `T` の後の時分秒部分をそれぞれ任意とし、少なくとも一方は必要。
#: `T` を書いたなら時分秒のいずれかが要る（`P1DT` のような空の時刻部分は ISO 8601 として
#: 成立しない）。週・月・年・小数・符号は含めない（長さが文脈で変わる、または厳密さを
#: 欠くため）。要素が1つも無い `P` / `PT` は `parse_duration` が別に弾く。
_DURATION_PATTERN: Final = re.compile(
    r"^P(?:(?P<days>\d+)D)?"
    r"(?:T(?=\d)(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)

#: 現地時刻（`17:00:00`）。秒まで必須にして、書き方の揺れを許さない。
_LOCAL_TIME_PATTERN: Final = re.compile(r"^(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})$")

#: 現地日付（`2024-12-25`）。
_LOCAL_DATE_PATTERN: Final = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})$")


def parse_duration(text: str, label: str) -> timedelta:
    """ISO 8601 の期間を `timedelta` にする（D03 §3.2 の `nominal_length`）。

    受けるのは `P<n>D`、`PT<n>H`、`PT<n>M`、`PT<n>S` とその組合せだけ。週（`W`）・月・年は
    長さが文脈で決まるため拒否し、小数・符号付きも拒否する。`P0D` のような長さ 0 も、
    名目長として意味を持たないので拒否する。

    外部ライブラリは使わない（D01 §5 の許可表に期間解析ライブラリは無い）。
    """
    if not isinstance(text, str):
        raise ConfigError(f"{label} は期間の文字列でなければならない（{text!r} が与えられた）")
    match = _DURATION_PATTERN.fullmatch(text)
    parts = (
        {}
        if match is None
        else {name: int(value) for name, value in match.groupdict().items() if value is not None}
    )
    # `P` と `PT` は正規表現には合うが期間の要素を持たないので、ここで一緒に弾く。
    if match is None or not parts:
        raise ConfigError(
            f"{label}: {text!r} は受け付けない期間表記である。"
            " `P<n>D` / `PT<n>H` / `PT<n>M` / `PT<n>S` とその組合せだけを受ける"
            "（週・月・年・小数・符号は、長さが文脈で変わるか厳密さを欠くため不可）"
        )

    duration = timedelta(
        days=parts.get("days", 0),
        hours=parts.get("hours", 0),
        minutes=parts.get("minutes", 0),
        seconds=parts.get("seconds", 0),
    )
    if duration <= timedelta(0):
        raise ConfigError(f"{label}: 期間は正でなければならない（{text!r} が与えられた）")
    return duration


def parse_local_time(text: str, label: str) -> time:
    """`HH:MM:SS` 形式の現地時刻を読む（タイムゾーンを持たない）。

    現地時刻はカレンダー・時間足定義が `zoneinfo` の規則で UTC へ変換するので、ここでは
    オフセットを受け付けない。オフセット付きの値を書けば拒否される。
    """
    if not isinstance(text, str):
        raise ConfigError(f"{label} は `HH:MM:SS` の文字列でなければならない（{text!r}）")
    match = _LOCAL_TIME_PATTERN.fullmatch(text)
    if match is None:
        raise ConfigError(
            f"{label}: {text!r} は現地時刻として読めない。`HH:MM:SS`（秒まで、"
            " タイムゾーンなし）で書くこと"
        )
    hour, minute, second = (int(match.group(name)) for name in ("hour", "minute", "second"))
    try:
        return time(hour=hour, minute=minute, second=second)
    except ValueError as exc:
        raise ConfigError(f"{label}: {text!r} は現地時刻として成立しない: {exc}") from exc


def parse_local_date(text: str, label: str) -> date:
    """`YYYY-MM-DD` 形式の現地日付を読む。"""
    if not isinstance(text, str):
        raise ConfigError(f"{label} は `YYYY-MM-DD` の文字列でなければならない（{text!r}）")
    match = _LOCAL_DATE_PATTERN.fullmatch(text)
    if match is None:
        raise ConfigError(f"{label}: {text!r} は日付として読めない。`YYYY-MM-DD` で書くこと")
    year, month, day = (int(match.group(name)) for name in ("year", "month", "day"))
    try:
        return date(year=year, month=month, day=day)
    except ValueError as exc:
        raise ConfigError(f"{label}: {text!r} は日付として成立しない: {exc}") from exc


def require_decimal(text: str, label: str) -> Decimal:
    """文字列から `Decimal` を作る（float を経由しない、ADR-0012）。

    設定ファイルの価格・数量は**文字列で書く**決まりである（`configs/symbols/*.yaml` の
    注記）。Pydantic モデルが `str` として受けるので、浮動小数で書かれた値はモデルの検証で
    型不一致として拒否され、ここには到達しない。
    """
    if not isinstance(text, str):
        raise ConfigError(
            f"{label} は数値を表す文字列でなければならない（{text!r} が与えられた）。"
            " 浮動小数として書くと二進浮動小数の誤差が入る（ADR-0012）"
        )
    try:
        return decimal_from_str(text)
    except KernelValueError as exc:
        raise ConfigError(f"{label}: {text!r} は数値として読めない: {exc}") from exc
