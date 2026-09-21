"""評価スケジュール（D04 §8）。

「いつ部品を評価するか」を、契約が許す範囲（`EvaluationSpec`）と、使用箇所が実際に選んだ
起動条件（`EvaluationSchedule`）の2段で宣言する。上流の更新だけで下流を自動評価しない
（上位設計書 §4.3.2）。同時刻の実行優先度を部品ごとの自由な整数で指定しない
（上位設計書 §4.3.5）。順序は依存グラフから決まる（D05 §5.4）。

**起動条件に名前を付ける理由**: 「どの起動条件でどの入力が必須か」を宣言するには、起動
条件を指す名前が要る（`EvaluationSpec.required_inputs` のキー）。名前は1つの
`EvaluationSchedule` の中で一意でなければならない。重複を許すと、1つのキーに2つの起動
条件が畳まれ、起動条件ごとに違う必須入力を宣言できなくなる。

**約定通知で評価を起こす区分**（`OnRuntimeEvent`）は、検証戦略 A の「約定価格から固定
リスクリワード比の利確水準を決める Exit 部品」を約定時点で評価するために要る。これが
ないと次の足まで初期の利確水準が付かない（D05 §8）。段階2の実行時イベントは
`POSITION_OPENED` の1値だけである。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.strategy.declarations.refs import require_kind
from odyssey_fx.strategy.declarations.validation import (
    freeze_mapping,
    normalized_unique,
    require_bool,
    require_identifier,
    require_instance,
    require_tuple_of,
)

__all__ = [
    "AllowedBarClose",
    "AllowedInputEvent",
    "AllowedRuntimeEvent",
    "AllowedTrigger",
    "EvaluationSchedule",
    "EvaluationSpec",
    "EvaluationTrigger",
    "OnBarClose",
    "OnInputEvent",
    "OnRuntimeEvent",
    "RuntimeEventKind",
]


class RuntimeEventKind(Enum):
    """エンジンが通知する実行時イベント（D04 §8）。

    段階2は建玉の生成通知1つだけ。決済通知・保護水準の更新通知は段階3（D05 v0.2）。
    """

    POSITION_OPENED = "POSITION_OPENED"


@dataclass(frozen=True, slots=True)
class AllowedBarClose:
    """足の確定による起動を許す（D04 §8）。

    `timeframes=None` は「任意の時間足を許す」。銘柄は制約せず、D04 §5 の銘柄伝播が
    検査する（同じ制約を2か所に置かないため）。
    """

    timeframes: tuple[TimeframeRef, ...] | None = None
    kind: str = "BAR_CLOSE"

    def __post_init__(self) -> None:
        require_kind(self.kind, "BAR_CLOSE", "AllowedBarClose.kind")
        if self.timeframes is None:
            return
        require_tuple_of(self.timeframes, TimeframeRef, "AllowedBarClose.timeframes")
        if not self.timeframes:
            raise KernelValueError(
                "AllowedBarClose.timeframes must be None (any timeframe) or a non-empty tuple"
            )
        object.__setattr__(
            self,
            "timeframes",
            normalized_unique(self.timeframes, key=str, label="AllowedBarClose.timeframes"),
        )

    def allows_timeframe(self, timeframe: TimeframeRef) -> bool:
        """その時間足の確定で起動してよいか。"""
        return self.timeframes is None or timeframe in self.timeframes


@dataclass(frozen=True, slots=True)
class AllowedInputEvent:
    """入力イベントの配送による起動を許す（D04 §8）。

    `input_names` は `DeliveredEvent` を読む入力名に限る。契約の `inputs` に存在するか
    どうかは契約の構築時に照合する（`ComponentContract`）。
    """

    input_names: tuple[str, ...]
    kind: str = "INPUT_EVENT"

    def __post_init__(self) -> None:
        require_kind(self.kind, "INPUT_EVENT", "AllowedInputEvent.kind")
        require_tuple_of(self.input_names, str, "AllowedInputEvent.input_names")
        if not self.input_names:
            raise KernelValueError("AllowedInputEvent.input_names must not be empty")
        for name in self.input_names:
            require_identifier(name, "AllowedInputEvent.input_names item")
        object.__setattr__(
            self,
            "input_names",
            normalized_unique(self.input_names, key=str, label="AllowedInputEvent.input_names"),
        )


@dataclass(frozen=True, slots=True)
class AllowedRuntimeEvent:
    """実行時イベントの通知による起動を許す（D04 §8）。"""

    events: tuple[RuntimeEventKind, ...]
    kind: str = "RUNTIME_EVENT"

    def __post_init__(self) -> None:
        require_kind(self.kind, "RUNTIME_EVENT", "AllowedRuntimeEvent.kind")
        require_tuple_of(self.events, RuntimeEventKind, "AllowedRuntimeEvent.events")
        if not self.events:
            raise KernelValueError("AllowedRuntimeEvent.events must not be empty")
        object.__setattr__(
            self,
            "events",
            normalized_unique(
                self.events, key=lambda item: item.value, label="AllowedRuntimeEvent.events"
            ),
        )


#: 区分タグ付き union（D04 §8）。
AllowedTrigger = AllowedBarClose | AllowedInputEvent | AllowedRuntimeEvent


@dataclass(frozen=True, slots=True)
class OnBarClose:
    """特定の系列の足が確定したときに起動する（D04 §8）。

    D03 §7.2 の `ScheduledBoundary`（カレンダー上その足が終了する予定時刻の通知）に
    結び付ける。データが遅延していても予定時点で検査を起動でき、欠損は読み取り条件の
    `on_missing` で扱う。
    """

    name: str
    series: SeriesId
    kind: str = "BAR_CLOSE"

    def __post_init__(self) -> None:
        require_kind(self.kind, "BAR_CLOSE", "OnBarClose.kind")
        require_identifier(self.name, "OnBarClose.name")
        require_instance(self.series, SeriesId, "OnBarClose.series")


@dataclass(frozen=True, slots=True)
class OnInputEvent:
    """特定の入力にイベントが配送されたときに起動する（D04 §8）。"""

    name: str
    input_name: str
    kind: str = "INPUT_EVENT"

    def __post_init__(self) -> None:
        require_kind(self.kind, "INPUT_EVENT", "OnInputEvent.kind")
        require_identifier(self.name, "OnInputEvent.name")
        require_identifier(self.input_name, "OnInputEvent.input_name")


@dataclass(frozen=True, slots=True)
class OnRuntimeEvent:
    """エンジンからの実行時イベント通知で起動する（D04 §8）。"""

    name: str
    event: RuntimeEventKind
    kind: str = "RUNTIME_EVENT"

    def __post_init__(self) -> None:
        require_kind(self.kind, "RUNTIME_EVENT", "OnRuntimeEvent.kind")
        require_identifier(self.name, "OnRuntimeEvent.name")
        require_instance(self.event, RuntimeEventKind, "OnRuntimeEvent.event")


#: 区分タグ付き union（D04 §8）。
EvaluationTrigger = OnBarClose | OnInputEvent | OnRuntimeEvent


def _allowed_sort_key(trigger: AllowedTrigger) -> tuple[str, str]:
    """`EvaluationSpec.allowed` の整列鍵（D04 §3 の「(区分タグ, 正規化エンコード)順」）。"""
    if isinstance(trigger, AllowedBarClose):
        if trigger.timeframes is None:
            payload = "*"
        else:
            payload = ",".join(str(item) for item in trigger.timeframes)
    elif isinstance(trigger, AllowedInputEvent):
        payload = ",".join(trigger.input_names)
    else:
        payload = ",".join(item.value for item in trigger.events)
    return (trigger.kind, payload)


@dataclass(frozen=True, slots=True)
class EvaluationSpec:
    """契約が許す評価の起こし方（D04 §8）。

    `fixed=True` は「契約が評価条件を固定する」ことを表し、そのとき `allowed` はちょうど
    1件で、制約まで一意に定まっていなければならない（時間足が `None` ではなく1件、など）。
    使用箇所は同じ内容の起動条件しか書けない。
    """

    allowed: tuple[AllowedTrigger, ...]
    fixed: bool = False
    required_inputs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, tuple) or not self.allowed:
            raise KernelValueError("EvaluationSpec.allowed must be a non-empty tuple")
        for index, item in enumerate(self.allowed):
            if not isinstance(item, (AllowedBarClose, AllowedInputEvent, AllowedRuntimeEvent)):
                raise KernelValueError(
                    f"EvaluationSpec.allowed[{index}] must be an AllowedTrigger, got {item!r}"
                )
        object.__setattr__(
            self,
            "allowed",
            normalized_unique(self.allowed, key=_allowed_sort_key, label="EvaluationSpec.allowed"),
        )
        require_bool(self.fixed, "EvaluationSpec.fixed")
        if self.fixed:
            self._require_unambiguous()

        if not isinstance(self.required_inputs, Mapping):
            raise KernelValueError("EvaluationSpec.required_inputs must be a Mapping")
        normalized: dict[str, tuple[str, ...]] = {}
        for trigger_name, input_names in self.required_inputs.items():
            require_identifier(trigger_name, "EvaluationSpec.required_inputs key")
            require_tuple_of(input_names, str, f"EvaluationSpec.required_inputs[{trigger_name!r}]")
            for name in input_names:
                require_identifier(name, "EvaluationSpec.required_inputs item")
            # 必須入力の集合であり評価順ではないので、入力名順に正規化する（D04 §3）。
            normalized[trigger_name] = normalized_unique(
                input_names,
                key=str,
                label=f"EvaluationSpec.required_inputs[{trigger_name!r}]",
            )
        object.__setattr__(self, "required_inputs", freeze_mapping(normalized))

    def _require_unambiguous(self) -> None:
        """`fixed=True` の契約は起動条件が一意に定まることを要求する（D04 §8）。"""
        if len(self.allowed) != 1:
            raise KernelValueError(
                "EvaluationSpec with fixed=True must declare exactly one allowed trigger,"
                f" got {len(self.allowed)}"
            )
        only = self.allowed[0]
        if isinstance(only, AllowedBarClose):
            if only.timeframes is None or len(only.timeframes) != 1:
                raise KernelValueError(
                    "EvaluationSpec with fixed=True must pin AllowedBarClose to exactly one"
                    " timeframe"
                )
        elif isinstance(only, AllowedInputEvent):
            if len(only.input_names) != 1:
                raise KernelValueError(
                    "EvaluationSpec with fixed=True must pin AllowedInputEvent to exactly one input"
                )
        elif len(only.events) != 1:
            raise KernelValueError(
                "EvaluationSpec with fixed=True must pin AllowedRuntimeEvent to exactly one event"
            )


@dataclass(frozen=True, slots=True)
class EvaluationSchedule:
    """使用箇所が実際に選んだ起動条件（D04 §8）。"""

    triggers: tuple[EvaluationTrigger, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.triggers, tuple) or not self.triggers:
            raise KernelValueError("EvaluationSchedule.triggers must be a non-empty tuple")
        for index, item in enumerate(self.triggers):
            if not isinstance(item, (OnBarClose, OnInputEvent, OnRuntimeEvent)):
                raise KernelValueError(
                    f"EvaluationSchedule.triggers[{index}] must be an EvaluationTrigger,"
                    f" got {item!r}"
                )
        # 起動条件名は一意（D04 §8）。重複は `normalized_unique` が鍵の重複として弾く。
        object.__setattr__(
            self,
            "triggers",
            normalized_unique(
                self.triggers, key=lambda item: item.name, label="EvaluationSchedule.triggers"
            ),
        )

    @property
    def names(self) -> tuple[str, ...]:
        """宣言された起動条件名（整列済み）。"""
        return tuple(trigger.name for trigger in self.triggers)
