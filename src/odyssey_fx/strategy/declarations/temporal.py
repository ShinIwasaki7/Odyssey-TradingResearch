"""部品全体の時刻に関する制約（D04 §9.2）。

- `WarmupSpec`: その部品が有効な出力を出せるようになるまでに必要な確定足数。段階2の完了
  条件「ウォームアップ中の注文ゼロ」（全体計画 §8.2）はこの宣言か、履歴読み取りが返す
  `WARMUP_INSUFFICIENT`（D03 §6.2）で判定する。
- `AlignmentRequirement`: 入力を跨ぐ時刻整合性。段階2の規則は「同じ観測区間であること」
  の1種類だけ。`InputSpec` 側には重複させない（上位設計書 §4.3.7）。

追加制約がない部品も本型を保持し、**空であることを明示する**（上位設計書 §4.3.7）。
「制約が無い」と「制約を書き忘れた」を区別するためである。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.strategy.declarations.read_spec import ParameterRef
from odyssey_fx.strategy.declarations.validation import (
    normalized_unique,
    require_identifier,
    require_instance,
    require_int,
    require_tuple_of,
)

__all__ = ["AlignmentRequirement", "AlignmentRule", "TemporalConstraints", "WarmupSpec"]


class AlignmentRule(Enum):
    """入力を跨ぐ時刻整合の規則（D04 §9.2）。段階2は1種類のみ。"""

    SAME_OBSERVATION_INTERVAL = "SAME_OBSERVATION_INTERVAL"


@dataclass(frozen=True, slots=True)
class WarmupSpec:
    """有効な出力を出すまでに必要な確定足数（D04 §9.2）。"""

    series: SeriesId
    bars: int | ParameterRef

    def __post_init__(self) -> None:
        require_instance(self.series, SeriesId, "WarmupSpec.series")
        if isinstance(self.bars, ParameterRef):
            return
        bars = require_int(self.bars, "WarmupSpec.bars")
        if bars < 1:
            raise KernelValueError(f"WarmupSpec.bars must be >= 1, got {bars}")


@dataclass(frozen=True, slots=True)
class AlignmentRequirement:
    """複数の入力に同じ観測区間を要求する（D04 §9.2）。"""

    input_names: tuple[str, ...]
    rule: AlignmentRule = AlignmentRule.SAME_OBSERVATION_INTERVAL

    def __post_init__(self) -> None:
        require_tuple_of(self.input_names, str, "AlignmentRequirement.input_names")
        if len(self.input_names) < 2:
            raise KernelValueError(
                "AlignmentRequirement.input_names must name at least two inputs"
                " (an alignment between fewer than two inputs constrains nothing)"
            )
        for name in self.input_names:
            require_identifier(name, "AlignmentRequirement.input_names item")
        object.__setattr__(
            self,
            "input_names",
            normalized_unique(self.input_names, key=str, label="AlignmentRequirement.input_names"),
        )
        require_instance(self.rule, AlignmentRule, "AlignmentRequirement.rule")


@dataclass(frozen=True, slots=True)
class TemporalConstraints:
    """部品全体の時刻制約（D04 §9.2）。空でも保持して明示する。"""

    warmup: WarmupSpec | None = None
    alignment: tuple[AlignmentRequirement, ...] = ()

    def __post_init__(self) -> None:
        if self.warmup is not None:
            require_instance(self.warmup, WarmupSpec, "TemporalConstraints.warmup")
        require_tuple_of(self.alignment, AlignmentRequirement, "TemporalConstraints.alignment")
        # 入力名を並べ替えたうえで、その結果の順に整列する（D04 §3 の表）。
        object.__setattr__(
            self,
            "alignment",
            normalized_unique(
                self.alignment,
                key=lambda item: ",".join(item.input_names),
                label="TemporalConstraints.alignment",
            ),
        )
