"""上流の出力を履歴窓で読むための保持と打ち切り（D05 §6.12、Q18・Q20・Q21 決定）。

他の部品の出力を「過去 N 本」で読む仕組みである。**何本保持するかはコンパイル結果の
保持本数の計画（`OutputRetentionPlan`）だけが決める**。本モジュールはその計画に載った
出力参照について、保持・置き換え・打ち切り・読み出しの規則を持つ。

- **何を積むか**: 繰り返し参照する値（`VALUE`）の出力で、観測した足（`subject`）が定まる
  ものだけ。1件は「観測した足1本」で、`(出力参照, 観測した足)` が主キー（Q20 決定）。
- **並び**: 観測した足の開始時刻の昇順（到着順ではない）。
- **同じ足の2件目**: 積まずに置き換える。件数も並びの位置も変わらない（Q20 決定）。
- **いつ捨てるか**: 新しい1件を積んだその場で、上限を超えたぶんを古い側から落とす。
- **どう読むか**: 対象区間の終了時刻以下の観測のうち最も新しいものを末尾とし、当該足を除く
  指定 `k` を適用し、残りの末尾 `n` 件を取る（Q21 決定）。待機から再開したときも同じ規則で
  あり、基準は要求の対象区間なので、待った評価と待たなかった評価の答えが一致する。

**最新1件の保持（`latest_outputs`）とは別物である**。最新1件は「最後に出た出力」（到着順）、
ここの末尾は「観測した足が最も新しい行」（足の順）であり、待機から再開した古い足の出力が
新しい足の出力より後に出たときだけ食い違う。食い違いを消さないのは、最新1件の読み方の意味
（段階2 で確定）を変えないためである（D05 §6.12）。

判断履歴には新しい記録を残さない。読んだ出力は出力記録として既に残っており、規則が決定論
なので、評価記録の判断時刻と保持本数の計画から読んだ窓を復元できる（D05 §6.12）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import BarKey
from odyssey_fx.strategy.declarations.refs import OutputRef
from odyssey_fx.strategy.declarations.validation import require_instance
from odyssey_fx.strategy.records.records import OutputRecord

__all__ = ["RetainedOutput", "retain", "window_ending_at"]


@dataclass(frozen=True, slots=True)
class RetainedOutput:
    """保持した出力1件（D05 §3・§6.12）。

    `subject` と `observation_interval` は出力の `Observation` から写したものである。読み手が
    窓の各要素の観測区間を見て、観測区間の一致（D05 §6.7）を確かめられるようにする。
    """

    record: OutputRecord[object]
    subject: BarKey | None
    observation_interval: Interval | None

    def __post_init__(self) -> None:
        require_instance(self.record, OutputRecord, "RetainedOutput.record")
        if self.subject is not None:
            require_instance(self.subject, BarKey, "RetainedOutput.subject")
        if self.observation_interval is not None:
            require_instance(
                self.observation_interval, Interval, "RetainedOutput.observation_interval"
            )


def retain(
    history: Mapping[OutputRef, tuple[RetainedOutput, ...]],
    limits: Mapping[OutputRef, int],
    item: RetainedOutput,
) -> dict[OutputRef, tuple[RetainedOutput, ...]]:
    """出力1件を保持する（D05 §6.12 の「何を保持するか」「いつ捨てるか」）。

    - 保持本数の計画に載らない出力参照は積まない（履歴で読む相手が1つも無い）。
    - 観測した足が定まらない出力（`subject` が `None`）は主キーを作れないので積まない。
    - 同じ足の行があれば置き換える。位置は変えない。
    - 無ければ観測した足の順の位置へ差し込み、上限を超えたぶんを古い側から落とす。
    """
    producer = item.record.producer
    updated = dict(history)
    limit = limits.get(producer)
    if limit is None or item.subject is None:
        return updated
    if limit < 1:  # pragma: no cover - `OutputRetentionPlan` が構築時に拒否する
        raise KernelValueError(f"retention for {producer} must be >= 1, got {limit}")
    rows = list(updated.get(producer, ()))
    for index, row in enumerate(rows):
        if row.subject == item.subject:
            rows[index] = item
            updated[producer] = tuple(rows)
            return updated
    rows.append(item)
    rows.sort(key=lambda row: _start(row).value)
    if len(rows) > limit:
        rows = rows[len(rows) - limit :]
    updated[producer] = tuple(rows)
    return updated


def window_ending_at(
    rows: tuple[RetainedOutput, ...],
    target_end: UtcTime,
    count: int,
    exclude_latest: int,
) -> tuple[RetainedOutput, ...] | None:
    """対象区間の終了時刻を基準に窓を切る（D05 §6.12 の「どう読むか」、Q21 決定）。

    1. 観測区間の終了時刻が `target_end` 以下の行のうち、最も新しいものを末尾とする。
    2. 末尾から新しい側の `exclude_latest` 件を除く。
    3. 残りの末尾 `count` 件を古い順に返す。

    `count + exclude_latest` 件に満たなければ `None`（部分的な窓を渡さない。呼び出し側は
    `INPUT_MISSING_OR_INVALID` の欠損として宣言した欠損方針に従う）。
    """
    if count < 1 or exclude_latest < 0:  # pragma: no cover - コンパイル時に解決済み
        raise KernelValueError(
            f"an output window needs count >= 1 and exclude_latest >= 0, got {count}, "
            f"{exclude_latest}"
        )
    eligible = [
        row
        for row in rows
        if row.observation_interval is not None and row.observation_interval.end <= target_end
    ]
    if exclude_latest:
        eligible = eligible[:-exclude_latest] if len(eligible) > exclude_latest else []
    if len(eligible) < count:
        return None
    return tuple(eligible[-count:])


def _start(row: RetainedOutput) -> UtcTime:
    if row.subject is None:  # pragma: no cover - `retain` が積まない
        raise KernelValueError("a retained output must have the bar it observed")
    return row.subject.bar_start
