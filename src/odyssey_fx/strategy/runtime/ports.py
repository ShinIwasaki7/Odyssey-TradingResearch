"""ランタイムの呼び出し境界（D05 §6.1）。

戦略ランタイムは自分から市場データや台帳を探しに行かない。**エンジンが渡した公開バッチと、
ポート越しに読む時点情報だけ**で評価する。所在と実装者は D01 §4 が確定しており、本モジュール
はその呼び出し形を定める。

| ポート | 誰が実装するか |
|---|---|
| `MarketDataView` | `marketdata` の as-of ビュー（D03 §6.2） |
| `RuntimeContextView` | `backtest.engine`（建玉・口座の時点情報。D06） |
| `OutputSink` | `backtest.trace`（判断履歴への転送） |
| `StrategyRuntime` | 本パッケージの評価器。`backtest.engine` が呼ぶ |

**公開イベントは `marketdata.domain` の型だけで渡す**。D03 §7.1 の公開イベントは
`marketdata.application` に属し、`strategy` はそこを参照できない（契約 F2）。そこでエンジンが
変換して渡す。公開は足の鍵の列、足の終了予定は「鍵＋その足の区間」の列である。

**足の区間を渡す理由**: 取引機会の対象区間は起動した足の区間であり、系列と足の開始時刻だけ
では夏時間の切替日や短縮セッションで実際の区間を復元できない（D03 §3.3）。区間を決めるのは
カレンダーを持つ `marketdata` 側で、戦略側で再計算すると規則が2か所になる。

**run 末尾の合図**（D05 §6.1 v1.1）: 末尾に残った取引機会を終端させる入口として、公開バッチに
「これが末尾である」ことを示す項目を置く。入口は `step` 1つのままで、「同じバッチ識別子で
2度呼ばない」という既存の不変条件だけで呼び出し規則が閉じる。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import AttemptId, EventId, OpportunityId, PositionId
from odyssey_fx.common.reason import MissingInputReason, Reason
from odyssey_fx.common.time import Interval, PhaseSet, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar, BarKey
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.strategy.declarations.evaluation import RuntimeEventKind
from odyssey_fx.strategy.declarations.read_spec import ReadWindow
from odyssey_fx.strategy.declarations.validation import (
    require_bool,
    require_instance,
    require_tuple_of,
)
from odyssey_fx.strategy.records.records import OutputRecord

if TYPE_CHECKING:
    from odyssey_fx.strategy.runtime.requests import RuntimeStepResult

__all__ = [
    "AdmissionNotice",
    "BarClosure",
    "MarketDataView",
    "MissingInputView",
    "OutputSink",
    "PublicationBatch",
    "RuntimeContextView",
    "RuntimeEventNotice",
    "StrategyRuntime",
]


@runtime_checkable
class MissingInputView(Protocol):
    """市場データが読めなかったことと、その診断理由（D03 §6.2）。

    実体は `marketdata.application` の `MissingInput` だが、`strategy` はそこを参照できない
    （契約 F2）。診断理由の語彙は `common` が持つので、**理由を読み出せること**だけを構造
    として要求する。
    """

    @property
    def reason(self) -> MissingInputReason: ...


@runtime_checkable
class MarketDataView(Protocol):
    """判断時刻までに公開された市場データだけを読むビュー（D03 §6.2 の5操作）。

    すべての操作が判断時刻を必須にし、その時刻より後に利用可能になった足は返さない。
    未来参照は構造的に起こらない。
    """

    def latest_available(self, series: SeriesId, at: UtcTime) -> Bar | MissingInputView: ...

    def history(
        self,
        series: SeriesId,
        window: ReadWindow,
        at: UtcTime,
        *,
        end_offset_bars: int = 0,
    ) -> tuple[Bar, ...] | MissingInputView: ...

    def bar(self, series: SeriesId, bar_start: UtcTime, at: UtcTime) -> Bar | MissingInputView: ...

    def expected_latest_key(self, series: SeriesId, at: UtcTime) -> BarKey | None: ...

    def freshness(self, series: SeriesId, bar: Bar) -> UtcTime: ...


@runtime_checkable
class RuntimeContextView(Protocol):
    """エンジンが評価時点に供給する建玉・口座（D05 §6.1）。

    `position_id` が `None` は「現在の建玉」を意味し、段階2の単一建玉でだけ使える。戻り値の
    項目は D06 が定める。
    """

    def position_context(self, at: UtcTime, position_id: PositionId | None) -> object | None: ...

    def account_context(self, at: UtcTime) -> object: ...


@runtime_checkable
class OutputSink(Protocol):
    """出力記録の受け口（D05 §6.1）。判断履歴への転送はエンジン側が行う。"""

    def emit(self, records: tuple[OutputRecord[object], ...]) -> None: ...


@dataclass(frozen=True, slots=True)
class BarClosure:
    """足が終了した通知（D05 §6.1）。鍵とその足の区間を持つ。"""

    bar_key: BarKey
    interval: Interval

    def __post_init__(self) -> None:
        require_instance(self.bar_key, BarKey, "BarClosure.bar_key")
        require_instance(self.interval, Interval, "BarClosure.interval")
        if self.bar_key.bar_start != self.interval.start:
            raise KernelValueError(
                "BarClosure.interval must start at the bar's start"
                f" ({self.interval.start} != {self.bar_key.bar_start})"
            )


@dataclass(frozen=True, slots=True)
class RuntimeEventNotice:
    """エンジンからの実行時イベント通知（D05 §3・§8）。

    段階2は建玉の生成通知だけ。**1件につき1つの評価要求**を作り、どの建玉についての評価か
    を評価記録と管理要求から一意に読めるようにする。
    """

    kind: RuntimeEventKind
    position_id: PositionId
    opportunity_id: OpportunityId

    def __post_init__(self) -> None:
        require_instance(self.kind, RuntimeEventKind, "RuntimeEventNotice.kind")
        require_instance(self.position_id, PositionId, "RuntimeEventNotice.position_id")
        require_instance(self.opportunity_id, OpportunityId, "RuntimeEventNotice.opportunity_id")


@dataclass(frozen=True, slots=True)
class AdmissionNotice:
    """発注試行の受付結果の通知（D05 §7.2 の遷移5〜7）。

    ランタイムは受付の可否を自分で決めない（リスク審査はエンジン側）。通知を次の `step` の
    入口で適用する。
    """

    opportunity_id: OpportunityId
    attempt_id: AttemptId
    accepted: bool
    reason: Reason | None = None

    def __post_init__(self) -> None:
        require_instance(self.opportunity_id, OpportunityId, "AdmissionNotice.opportunity_id")
        require_instance(self.attempt_id, AttemptId, "AdmissionNotice.attempt_id")
        require_bool(self.accepted, "AdmissionNotice.accepted")
        if self.reason is not None:
            require_instance(self.reason, Reason, "AdmissionNotice.reason")


@dataclass(frozen=True, slots=True)
class PublicationBatch:
    """1つの判断時点でエンジンが戦略へ渡すもの（D05 §6.1）。"""

    batch_id: EventId
    decision_time: UtcTime
    phases: PhaseSet
    available_bars: tuple[BarKey, ...] = ()
    scheduled_closes: tuple[BarClosure, ...] = ()
    runtime_events: tuple[RuntimeEventNotice, ...] = ()
    admissions: tuple[AdmissionNotice, ...] = ()
    is_run_end: bool = False

    def __post_init__(self) -> None:
        require_instance(self.batch_id, EventId, "PublicationBatch.batch_id")
        require_instance(self.decision_time, UtcTime, "PublicationBatch.decision_time")
        require_instance(self.phases, PhaseSet, "PublicationBatch.phases")
        require_tuple_of(self.available_bars, BarKey, "PublicationBatch.available_bars")
        require_tuple_of(self.scheduled_closes, BarClosure, "PublicationBatch.scheduled_closes")
        require_tuple_of(self.runtime_events, RuntimeEventNotice, "PublicationBatch.runtime_events")
        require_tuple_of(self.admissions, AdmissionNotice, "PublicationBatch.admissions")
        require_bool(self.is_run_end, "PublicationBatch.is_run_end")
        if self.is_run_end and (
            self.available_bars or self.scheduled_closes or self.runtime_events or self.admissions
        ):
            raise KernelValueError(
                "a run-end batch must not carry publications or notices; the end-of-run signal"
                " and ordinary publications belong to different batches (D05 §6.1)"
            )


@runtime_checkable
class StrategyRuntime(Protocol):
    """戦略ランタイムの唯一の入口（D05 §6.1）。"""

    def step(self, batch: PublicationBatch) -> RuntimeStepResult: ...
