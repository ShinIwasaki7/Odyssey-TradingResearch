"""待機（`WAIT_FOR_INPUT`）の記録と規則、欠損方針の強さ順（D05 §6.3・§6.8・§6.9）。

入力が足りない評価を、足りない入力が届くまで保留する仕組みの**記録の型**と、ランタイムの
手順が使う**判定の規則**を置く。手順そのもの（いつ判定し、いつ再開するか）は評価器
（`evaluator`）が `step` の中で行う。規則を純粋関数として切り出すのは、紙上トレース T02
§19 が引き渡した3つの規則を、1つずつ名前の付いた関数として検査できるようにするためである。

## 紙上トレース T02 §19 の引き渡し #3（待機と欠損の3規則）

- (a) 1回の評価で欠損方針が食い違うときは強い方針が勝つ（`Error` > `SkipEvaluation` >
  `WaitForInput` > `UsePrevious`）: `strongest_policy`（Q27 決定、D05 §6.3）
- (b) 出力参照だけが欠けた待機の本数期限は、その使用箇所の起動条件の系列で数える:
  `deadline_series`（Q28 決定、D05 §6.8）
- (c) 期限 → 追い越し → 失効の順に判定し、先に成立したものだけで決着させる:
  `judge_lifecycle`（Q29 決定、D05 §6.8 の手順2）

## 待機できる欠損理由（D05 §6.8、Q15 決定）

待っても解消しない理由（データ開始前 `WARMUP_INSUFFICIENT`、鮮度の上限超過
`MAX_AGE_EXCEEDED`）では待機に入らず、見送り（`SkipEvaluation`）と同じに扱う。これを
強さ順へ当てはめると「待っても解消しない入力が1つでもあれば待たない」になる（D05 §6.3 の
Q27 決定の本文）。

## 固定するのは「問い」であり値ではない（上位設計書 §4.3.14）

`WaitingRequest` は評価要求そのものと、市場データの入力ごとに固定した足（オフセットを適用
する前の基準足）を持つ。再開したときはその足で読み直し、再開した判断時刻の最新足へずらさ
ない。出力参照の入力と現在状態の入力は固定しない（D05 §6.8、T02 §14 #16）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import RequestId
from odyssey_fx.common.reason import MissingInputReason, Reason
from odyssey_fx.common.time import ProcessingPoint, UtcTime
from odyssey_fx.marketdata.domain.bar import BarKey
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.strategy.declarations.entry_policy import BarsDeadline, DurationDeadline
from odyssey_fx.strategy.declarations.missing import (
    Error,
    MissingInputPolicy,
    OnSuperseded,
    SkipEvaluation,
    UsePrevious,
    WaitDeadlineAction,
    WaitForInput,
)
from odyssey_fx.strategy.declarations.refs import require_kind
from odyssey_fx.strategy.declarations.validation import (
    freeze_mapping,
    require_identifier,
    require_instance,
    require_int,
    require_tuple_of,
)
from odyssey_fx.strategy.records.payloads import Opportunity

if TYPE_CHECKING:
    from odyssey_fx.strategy.runtime.requests import (
        EvaluationRequest,
        EventDelivery,
        MissingInputDiagnosis,
    )

__all__ = [
    "WAITABLE_REASONS",
    "LifecycleVerdict",
    "PolicyStrength",
    "WaitDeadline",
    "WaitEvent",
    "WaitEventKind",
    "WaitUntilBars",
    "WaitUntilTime",
    "WaitingRequest",
    "deadline_reached",
    "deadline_series",
    "effective_strength",
    "judge_lifecycle",
    "resolve_deadline",
    "strongest_policy",
    "tick_deadline",
]


# --- 期限（D05 §6.8「期限を絶対の形へ解決する」） ---------------------------


@dataclass(frozen=True, slots=True)
class WaitUntilTime:
    """経過時間で書いた期限を、待機開始の判断時刻に足して絶対時刻にしたもの（D05 §6.8）。"""

    at: UtcTime
    kind: str = "WAIT_UNTIL_TIME"

    def __post_init__(self) -> None:
        require_kind(self.kind, "WAIT_UNTIL_TIME", "WaitUntilTime.kind")
        require_instance(self.at, UtcTime, "WaitUntilTime.at")


@dataclass(frozen=True, slots=True)
class WaitUntilBars:
    """本数で書いた期限の残り（D05 §6.8）。

    数えるのは系列 `series` の**足の終了予定**（公開ではない）である。データが遅れて届かない
    ときにも数えるものが要るので、公開（`Publication`）ではなく予定（`ScheduledBoundary`）を
    使う（D03 §7.2 と同じ考え方）。残りが 0 になった判断時点で期限に到達する。
    """

    series: SeriesId
    remaining: int
    kind: str = "WAIT_UNTIL_BARS"

    def __post_init__(self) -> None:
        require_kind(self.kind, "WAIT_UNTIL_BARS", "WaitUntilBars.kind")
        require_instance(self.series, SeriesId, "WaitUntilBars.series")
        remaining = require_int(self.remaining, "WaitUntilBars.remaining")
        if remaining < 0:
            raise KernelValueError(f"WaitUntilBars.remaining must be >= 0, got {remaining}")


#: 区分タグ付き union（D05 §3・§6.8）。
WaitDeadline = WaitUntilTime | WaitUntilBars


def resolve_deadline(
    deadline: BarsDeadline | DurationDeadline, *, started: UtcTime, series: SeriesId | None
) -> WaitDeadline:
    """宣言の期限を絶対の形へ解決する（D05 §6.8）。

    経過時間の期限は待機開始の判断時刻に足した時刻、本数の期限は数える系列と残り本数に
    する。数える系列の決め方は `deadline_series` が持つ。
    """
    if isinstance(deadline, DurationDeadline):
        return WaitUntilTime(at=started + deadline.duration)
    bars = deadline.bars
    if series is None:
        raise KernelValueError("a bar deadline needs the series it counts on (D05 §6.8)")
    if not isinstance(bars, int) or isinstance(bars, bool):  # pragma: no cover - 構築時に解決
        raise KernelValueError(
            f"a wait deadline must count a resolved number of bars, got {bars!r} (D05 §6.8)"
        )
    return WaitUntilBars(series=series, remaining=bars)


def tick_deadline(deadline: WaitDeadline, closed_series: frozenset[SeriesId]) -> WaitDeadline:
    """その判断時点で終了予定を迎えた系列の足1本ぶん、本数の期限を進める（D05 §6.8）。"""
    if isinstance(deadline, WaitUntilBars) and deadline.series in closed_series:
        return WaitUntilBars(series=deadline.series, remaining=max(deadline.remaining - 1, 0))
    return deadline


def deadline_reached(deadline: WaitDeadline, decision_time: UtcTime) -> bool:
    """期限に到達したか（D05 §6.8）。"""
    if isinstance(deadline, WaitUntilBars):
        return deadline.remaining <= 0
    return decision_time >= deadline.at


def deadline_series(
    missing_market_series: Sequence[SeriesId], trigger_series: Sequence[SeriesId]
) -> SeriesId:
    """本数の期限を数える系列（D05 §6.8。規則(b) は Q28 決定）。

    - 足りない入力に市場データ参照があれば、**最初に足りなくなった入力の系列**（入力の宣言
      順で最初のもの）で数える。
    - 足りない入力が**出力参照だけ**なら、出力参照には系列が無いので、その使用箇所の
      **起動条件（足の確定）の系列**で数える（Q28 決定）。その系列がただ1つであることは
      コンパイラの検査 h が保証する（Q30 決定、D05 §5.6）。
    """
    if missing_market_series:
        return missing_market_series[0]
    distinct = tuple(dict.fromkeys(trigger_series))
    if len(distinct) != 1:
        raise KernelValueError(
            "a wait on output references alone counts its bar deadline on the series of the"
            f" bar-close trigger, and exactly one is required; got {distinct} (D05 §6.8,"
            " compiler check h)"
        )
    return distinct[0]


# --- 欠損方針の強さ順（D05 §6.3。規則(a) は Q27 決定） ----------------------


class PolicyStrength(Enum):
    """欠損方針の強さ。値が大きいほど強い（D05 §6.3、Q27 決定）。"""

    USE_PREVIOUS = 1
    WAIT_FOR_INPUT = 2
    SKIP_EVALUATION = 3
    ERROR = 4


#: 待機に入れる欠損理由（D05 §6.8 の表）。期待足の未到着と、窓内の期待足の欠けだけである。
WAITABLE_REASONS: frozenset[MissingInputReason] = frozenset(
    {MissingInputReason.LATEST_BAR_UNAVAILABLE, MissingInputReason.INPUT_MISSING_OR_INVALID}
)


def effective_strength(
    policy: MissingInputPolicy, reasons: Sequence[MissingInputReason]
) -> PolicyStrength:
    """1つの入力について、欠損方針の実効の強さを返す（D05 §6.3・§6.8）。

    待機を宣言していても、待っても解消しない理由（`WARMUP_INSUFFICIENT` /
    `MAX_AGE_EXCEEDED`）で欠けていれば待機に入らず、見送りと同じに扱う（D05 §6.8 の表）。
    """
    if isinstance(policy, Error):
        return PolicyStrength.ERROR
    if isinstance(policy, SkipEvaluation):
        return PolicyStrength.SKIP_EVALUATION
    if isinstance(policy, WaitForInput):
        if all(reason in WAITABLE_REASONS for reason in reasons):
            return PolicyStrength.WAIT_FOR_INPUT
        return PolicyStrength.SKIP_EVALUATION
    if isinstance(policy, UsePrevious):
        return PolicyStrength.USE_PREVIOUS
    raise KernelValueError(f"unknown missing-input policy: {policy!r}")  # pragma: no cover


def strongest_policy(strengths: Sequence[PolicyStrength]) -> PolicyStrength:
    """欠けた入力すべての方針のうち、いちばん強いものを返す（D05 §6.3、Q27 決定）。

    `Error` > `SkipEvaluation` > `WaitForInput` > `UsePrevious`。その1つを評価全体に適用
    する。`breakout_trigger` v2 は `level` が待機、`price` が見送りなので、両方が欠けると
    **待たずに見送る**。待っても解消しない入力を抱えたまま期限まで待たないためである。
    """
    if not strengths:
        raise KernelValueError("strongest_policy needs at least one missing input")
    return max(strengths, key=lambda item: item.value)


# --- 待機の記録（D05 §3・§6.8） ------------------------------------------------


class WaitEventKind(Enum):
    """待機の出来事の種類（D05 §3・§6.8）。"""

    WAIT_STARTED = "WAIT_STARTED"
    INPUT_ARRIVED = "INPUT_ARRIVED"
    RESUMED = "RESUMED"
    DEADLINE_REACHED = "DEADLINE_REACHED"
    SUPERSEDED = "SUPERSEDED"
    #: run 末尾で閉じた（D05 §6.1）。期限には到達していないので `DEADLINE_REACHED` を使わない。
    RUN_END_CLOSED = "RUN_END_CLOSED"


@dataclass(frozen=True, slots=True)
class WaitEvent:
    """待機の出来事1件（D05 §3・§6.8、判断履歴の表16 の行）。

    主キーは `(request_id, at)` である（D06 §9.2）。同じ処理点で2つ以上の入力が届いたら、
    1件の `INPUT_ARRIVED` にその全部を並べる（T02 §14 #13）。処理点の通し番号は出力記録・
    遷移記録と同じ1本を共有する（D05 §6.6、T02 §14 #10）。
    """

    request_id: RequestId
    kind: WaitEventKind
    at: ProcessingPoint
    reason: Reason | None = None
    arrived: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_instance(self.request_id, RequestId, "WaitEvent.request_id")
        require_instance(self.kind, WaitEventKind, "WaitEvent.kind")
        require_instance(self.at, ProcessingPoint, "WaitEvent.at")
        if self.reason is not None:
            require_instance(self.reason, Reason, "WaitEvent.reason")
        require_tuple_of(self.arrived, str, "WaitEvent.arrived")
        if (self.kind is WaitEventKind.INPUT_ARRIVED) != bool(self.arrived):
            raise KernelValueError(
                "WaitEvent.arrived names the inputs that arrived, so it is non-empty exactly"
                " for INPUT_ARRIVED (D05 §6.8)"
            )


@dataclass(frozen=True, slots=True)
class WaitingRequest:
    """待機中の評価要求（D05 §3・§6.8、上位設計書 §4.3.14 の「待機記録」）。

    **判断履歴には出ない**（ランタイムの状態の中にだけある。T02 §7.1）。記録に残るのは、
    待機に入った評価記録（`Waiting`）と待機の出来事（`WaitEvent`）である。

    - `pinned_bars`: 市場データ参照の入力ごとに固定した足。最新1件の読み方も履歴窓も
      **期待される最新足の鍵**であり、履歴窓では当該足を除く指定を適用する**前の**基準足を
      固定する（D05 §6.8）。出力参照・現在状態の入力は載せない。
    - `pinned_events`: 既に配送された入力イベント。再開時には再配送されないので持つ。
    - `missing`: まだ足りない入力の診断。届いた入力はここから消える。
    """

    request: EvaluationRequest
    missing: tuple[MissingInputDiagnosis, ...]
    started_at: ProcessingPoint
    deadline_at: WaitDeadline
    on_deadline: WaitDeadlineAction
    on_superseded: OnSuperseded
    pinned_bars: Mapping[str, BarKey] = field(default_factory=dict)
    pinned_events: Mapping[str, tuple[EventDelivery, ...]] = field(default_factory=dict)
    opportunity: Opportunity | None = None

    def __post_init__(self) -> None:
        # 評価要求の型は `requests` が定義し、`requests` は本モジュールの期限の型を import
        # する。循環を避けるため、検査に使う型だけをここで読む。
        from odyssey_fx.strategy.runtime.requests import (
            EvaluationRequest,
            EventDelivery,
            MissingInputDiagnosis,
        )

        require_instance(self.request, EvaluationRequest, "WaitingRequest.request")
        require_tuple_of(self.missing, MissingInputDiagnosis, "WaitingRequest.missing")
        require_instance(self.started_at, ProcessingPoint, "WaitingRequest.started_at")
        if not isinstance(self.deadline_at, (WaitUntilTime, WaitUntilBars)):
            raise KernelValueError(
                f"WaitingRequest.deadline_at must be a WaitDeadline, got {self.deadline_at!r}"
            )
        require_instance(self.on_deadline, WaitDeadlineAction, "WaitingRequest.on_deadline")
        require_instance(self.on_superseded, OnSuperseded, "WaitingRequest.on_superseded")
        for name, key in self.pinned_bars.items():
            require_identifier(name, "WaitingRequest.pinned_bars key")
            require_instance(key, BarKey, f"WaitingRequest.pinned_bars[{name!r}]")
        for name, events in self.pinned_events.items():
            require_identifier(name, "WaitingRequest.pinned_events key")
            require_tuple_of(events, EventDelivery, f"WaitingRequest.pinned_events[{name!r}]")
        if self.opportunity is not None:
            require_instance(self.opportunity, Opportunity, "WaitingRequest.opportunity")
        object.__setattr__(self, "pinned_bars", freeze_mapping(dict(self.pinned_bars)))
        object.__setattr__(self, "pinned_events", freeze_mapping(dict(self.pinned_events)))


# --- ライフサイクル検査の判定順（D05 §6.8 の手順2。規則(c) は Q29 決定） -----


class LifecycleVerdict(Enum):
    """ライフサイクル検査で待機要求に下す判定（D05 §6.8 の手順2）。"""

    #: 期限に到達した。`on_deadline` に従って見送りか失敗で決着する。
    DEADLINE = "DEADLINE"
    #: 対象の足が追い越された（`on_superseded=EXPIRE_REQUEST` のときだけ決着する）。
    SUPERSEDED = "SUPERSEDED"
    #: 受信済みの取引機会が失効した。
    INVALIDATED = "INVALIDATED"
    #: どれも成立しない。到着の検査へ進む。
    NONE = "NONE"


def judge_lifecycle(
    *, deadline_reached: bool, superseded: bool, invalidated: bool
) -> LifecycleVerdict:
    """期限 → 追い越し → 失効の順に判定する（D05 §6.8 の手順2、Q29 決定）。

    同じ判断時点で2つ以上が成立したら、**先に成立したものだけで決着させ、後ろの判定は
    行わない**。期限と追い越しが同時なら期限が勝つ。期限は宣言が決めた上限で、追い越しは
    市場データの到着順が決める事象なので、宣言が決めたほうを優先する（確認期限が確認の
    評価に勝つ規則 D05 §7.7 と向きがそろう）。

    `superseded` には、`on_superseded=KEEP_WAITING` の要求では常に偽を渡す（追い越しでは
    決着しない。D05 §6.10）。
    """
    if deadline_reached:
        return LifecycleVerdict.DEADLINE
    if superseded:
        return LifecycleVerdict.SUPERSEDED
    if invalidated:
        return LifecycleVerdict.INVALIDATED
    return LifecycleVerdict.NONE
