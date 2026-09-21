"""設定ファイルに書く期間値の文字列表現（D04 §13.1）。

宣言に現れる期間（`DurationWindow.duration`、`DurationDeadline.duration`、読み取り条件の
`max_age`）は、設定ファイルでは **`<正の整数><単位>` の文字列1形式だけ**で書く。単位は
`s`（秒）/ `m`（分）/ `h`（時）/ `d`（日）の4つで、1つの値に単位は1つだけである
（`1h30m` のような複合は書けず `90m` と書く）。例: `max_age: "2h"`、`duration: "30m"`。

受理をこの1形式に絞るのは、素の数値（単位が読み手の解釈になる）・ISO 8601 の期間
（`PT2H`）・複数単位の連結を許すと、**同じ意味の設定ファイルが別の文字列になり**、内容
ハッシュと実験の同一性が分かれるためである。単位の綴りは D02 §6 の時間足の文字列表記
（`15m` / `1h` / `1d`）と同じ読み方で、設定ファイルの中に2つの流儀が並ばないようにする。

本モジュールは文字列と `timedelta` の相互変換だけを持ち、I/O と YAML 解析は行わない
（YAML の読込は `app.config` の責務。D01 §5・契約 F5c）。`declarations` に置くのは、
同じ書式規則を `app.config` と設定ファイルの書き出しで二重実装しないためである。
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Final

from odyssey_fx.common.errors import KernelValueError

__all__ = ["DURATION_PATTERN", "DURATION_UNITS", "format_duration", "parse_duration"]

#: 受理する唯一の書式（D04 §13.1）。照合は `fullmatch`。
DURATION_PATTERN: Final = re.compile(r"^(?P<amount>[1-9][0-9]*)(?P<unit>[smhd])$")

#: 単位1つあたりの秒数（D04 §13.1）。
DURATION_UNITS: Final[dict[str, int]] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}

#: `format_duration` が大きい単位から順に試す並び。
_UNITS_LARGEST_FIRST: Final[tuple[str, ...]] = ("d", "h", "m", "s")


def parse_duration(text: str) -> timedelta:
    """`"2h"` 形式の文字列を `timedelta` にする（D04 §13.1）。

    ゼロと負の値は受け付けない。期間は「どれだけ遡るか・どれだけ待つか」を表すため、
    0 秒の窓や負の鮮度上限は宣言として意味を持たない。
    """
    if not isinstance(text, str):
        raise KernelValueError(f"duration must be a str, got {text!r}")
    match = DURATION_PATTERN.fullmatch(text)
    if match is None:
        raise KernelValueError(
            "duration must be written as <positive integer><unit> with a single unit"
            f" from s/m/h/d (for example '2h'), got {text!r}"
        )
    amount = int(match.group("amount"))
    return timedelta(seconds=amount * DURATION_UNITS[match.group("unit")])


def format_duration(value: timedelta) -> str:
    """`timedelta` を `"2h"` 形式の文字列にする（D04 §13.1 の逆変換）。

    単位は1つしか使えないので、**割り切れる最大の単位**を選ぶ（7200 秒は `"2h"`、
    5400 秒は `"90m"`）。どの単位でも割り切れない値（マイクロ秒を含む値など）は、
    設定ファイルに書けない期間なので拒否する。
    """
    if not isinstance(value, timedelta):
        raise KernelValueError(f"duration must be a timedelta, got {value!r}")
    total = value // timedelta(microseconds=1)
    if total <= 0:
        raise KernelValueError(f"duration must be positive, got {value!r}")
    if total % 1_000_000 != 0:
        raise KernelValueError(
            "duration must be a whole number of seconds to be written to a config file,"
            f" got {value!r}"
        )
    seconds = total // 1_000_000
    for unit in _UNITS_LARGEST_FIRST:
        size = DURATION_UNITS[unit]
        if seconds % size == 0:
            return f"{seconds // size}{unit}"
    raise KernelValueError(  # pragma: no cover - `s` が必ず割り切るため到達しない
        f"duration cannot be written with a single unit: {value!r}"
    )
