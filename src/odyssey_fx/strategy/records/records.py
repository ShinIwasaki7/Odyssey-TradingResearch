"""実行時の出力記録（上位設計書 §4.3.15 の3層構成のうち上2層）。

- `OutputRecord[T]`: 出力の同一性・評価との関連・判断時刻・利用可能時刻・因果順序と、
  内容 `T`。**共通メタデータはランタイムが付ける**（D05 §6.6）。部品はこの型を作らない。
- `Observation[T]`: 市場の観測値に、対象・対象区間・鮮度基準を付ける。イベントまで一律に
  観測で包まない（上位設計書 §4.3.15）。

フィールド名と責務の正本は上位設計書 §4.3.15 であり、本モジュールはそれを型にしただけで
ある。`producer` は「どの使用箇所のどの出力か」を指すので、宣言側の `OutputRef` をその
まま使う（同じ組を表す型を2つ作らない）。

`Observation` は段階2では作られない。観測で包んで配送する唯一のデータ型
（`market_permission@v1`）を段階2の部品が持たないためである（D05 §4.2 の表）。
"""

from __future__ import annotations

from dataclasses import dataclass

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import EvaluationId, OutputId
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import BarKey
from odyssey_fx.strategy.declarations.refs import OutputRef
from odyssey_fx.strategy.declarations.validation import require_instance, require_int

__all__ = ["Observation", "OutputRecord"]


@dataclass(frozen=True, slots=True)
class Observation[T]:
    """市場の観測値と、その対象・対象区間・鮮度基準（上位設計書 §4.3.15）。

    `subject` は観測した足である（同節の例「Observation の対象は当該日足」）。計算に使った
    履歴全体の区間と `observation_interval` を混同しない。
    """

    value: T
    subject: BarKey | None
    observation_interval: Interval | None
    freshness_time: UtcTime

    def __post_init__(self) -> None:
        # 対象区間を持たない評価（実行時イベントで起動した評価）の出力は観測した足が定まらない
        # （D05 §6.12 の「subject が None の出力」、`RetainedOutput.subject: BarKey | None`）。
        # そのときは足と区間の両方を空にする。片方だけ空の観測は作らない。
        if (self.subject is None) != (self.observation_interval is None):
            raise KernelValueError(
                "Observation.subject and observation_interval are either both set or both empty"
            )
        if self.subject is not None:
            require_instance(self.subject, BarKey, "Observation.subject")
        if self.observation_interval is not None:
            require_instance(
                self.observation_interval, Interval, "Observation.observation_interval"
            )
        require_instance(self.freshness_time, UtcTime, "Observation.freshness_time")


@dataclass(frozen=True, slots=True)
class OutputRecord[T]:
    """1件の出力と、その共通メタデータ（上位設計書 §4.3.15、D05 §6.6）。

    `sequence` は `step` 内の通し番号で、評価順と、1評価内では契約の出力名のキー順で決まる。
    同じ宣言・同じ入力・同じ公開順から同じ番号列が出ることが、「同一入力の再実行で判断履歴
    が一致する」（全体計画 §8.2）の土台になる。

    `available_at` は段階2では `decision_time` と同じである（計算遅延を入れない）。将来
    遅延を入れるときにフィールドを足さずに済むよう、別の項目として持つ。
    """

    output_id: OutputId
    evaluation_id: EvaluationId
    producer: OutputRef
    decision_time: UtcTime
    available_at: UtcTime
    sequence: int
    payload: T

    def __post_init__(self) -> None:
        require_instance(self.output_id, OutputId, "OutputRecord.output_id")
        require_instance(self.evaluation_id, EvaluationId, "OutputRecord.evaluation_id")
        require_instance(self.producer, OutputRef, "OutputRecord.producer")
        require_instance(self.decision_time, UtcTime, "OutputRecord.decision_time")
        require_instance(self.available_at, UtcTime, "OutputRecord.available_at")
        sequence = require_int(self.sequence, "OutputRecord.sequence")
        if sequence < 0:
            raise KernelValueError(f"OutputRecord.sequence must be >= 0, got {sequence}")
        if self.available_at < self.decision_time:
            raise KernelValueError(
                "OutputRecord.available_at must not be earlier than decision_time"
                f" ({self.available_at} < {self.decision_time})"
            )
