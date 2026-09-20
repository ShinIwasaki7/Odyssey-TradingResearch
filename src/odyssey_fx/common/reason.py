"""理由コードと型付き詳細（D02 §8）。

`ReasonCode` は「なぜ拒否・取消・終了したか」の語彙、`Reason` はコードと型付き詳細の組。
状態と理由は別フィールドで持ち、どの状態にどの理由を許すかの検証は各 domain が行う
（上位設計書 §4.7.14）。

`MissingInputReason`（D02 §8.3）は受付拒否ではなく「評価を見送った理由」なので、
`ReasonCode` とは別の enum にする。
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from decimal import Decimal
from enum import Enum
from typing import ClassVar, Final, Protocol, runtime_checkable

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import PositionId
from odyssey_fx.common.money import Money
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, ProcessingPoint, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef

__all__ = [
    "CarryNotAllowedDetail",
    "DataErrorDetail",
    "ExpiryDetail",
    "MissingInputReason",
    "NoCandidateDetail",
    "PositionClosedDetail",
    "Reason",
    "ReasonCode",
    "ReasonDetail",
    "RiskRejectionDetail",
    "RunEndDetail",
]


class ReasonCode(Enum):
    """受付拒否・取消・終了の理由（D02 §8.1、上位設計書 §4.7.14 の初期語彙）。

    語彙の追加（執行理由など）は該当設計文書（D06）で行い、D02 §8.1 の表を更新する。
    """

    #: 受付前拒否（率・総量・数量・証拠金の違反）。
    RISK = "RISK"
    #: 期限内に適格 open がない受付前拒否。
    NO_CANDIDATE = "NO_CANDIDATE"
    #: 末尾の受付前拒否、残存注文の CANCELED、管理要求の未適用。
    RUN_END = "RUN_END"
    #: 完全性検査・実行失敗、失敗に伴う残存注文の CANCELED。
    DATA_ERROR = "DATA_ERROR"
    #: 期限による注文の EXPIRED。
    EXPIRED = "EXPIRED"
    #: 週末持ち越し禁止による受付前拒否。
    CARRY_NOT_ALLOWED = "CARRY_NOT_ALLOWED"
    #: 閉鎖済み建玉への要求の拒否、保護決済後の残存決済注文の取消。
    POSITION_CLOSED = "POSITION_CLOSED"


class MissingInputReason(Enum):
    """入力不足の診断コード（D02 §8.3、上位設計書 §4.3.10 の4分類）。

    受付拒否ではなく「評価を見送った理由」なので `ReasonCode` とは分ける。使用箇所は
    戦略ランタイムの評価記録（D04/D05）と市場データビュー（D03）。
    """

    #: 助走期間が足りず、まだ評価できない。
    WARMUP_INSUFFICIENT = "WARMUP_INSUFFICIENT"
    #: 入力が欠損しているか、値として不正。
    INPUT_MISSING_OR_INVALID = "INPUT_MISSING_OR_INVALID"
    #: 判断時点で最新の確定足が取得できない。
    LATEST_BAR_UNAVAILABLE = "LATEST_BAR_UNAVAILABLE"
    #: 入手できた値が許容する古さ（max age）を超えている。
    MAX_AGE_EXCEEDED = "MAX_AGE_EXCEEDED"


@runtime_checkable
class ReasonDetail(Protocol):
    """理由の型付き詳細（D02 §8.2）。

    `code` をクラス変数に持つ frozen dataclass であること。`common` が定義する詳細型は
    `common` の型だけで表現できるものに限り、各 domain は自分の型を使う詳細型を追加できる
    （D02 承認事項6）。
    """

    code: ClassVar[ReasonCode]


@dataclass(frozen=True, slots=True)
class DataErrorDetail:
    """完全性検査・実行失敗の詳細（D02 §8.2）。"""

    code: ClassVar[ReasonCode] = ReasonCode.DATA_ERROR

    symbol: Symbol
    timeframe: TimeframeRef
    field: str
    expected_interval: Interval | None
    observed_interval: Interval | None
    cause: str

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, Symbol):
            raise KernelValueError("DataErrorDetail.symbol must be a Symbol")
        if not isinstance(self.timeframe, TimeframeRef):
            raise KernelValueError("DataErrorDetail.timeframe must be a TimeframeRef")
        if not isinstance(self.field, str) or not self.field:
            raise KernelValueError("DataErrorDetail.field must be a non-empty str")
        for value, label in (
            (self.expected_interval, "expected_interval"),
            (self.observed_interval, "observed_interval"),
        ):
            if value is not None and not isinstance(value, Interval):
                raise KernelValueError(f"DataErrorDetail.{label} must be an Interval or None")
        if not isinstance(self.cause, str) or not self.cause:
            raise KernelValueError("DataErrorDetail.cause must be a non-empty str")


@dataclass(frozen=True, slots=True)
class ExpiryDetail:
    """期限切れの詳細（D02 §8.2）。"""

    code: ClassVar[ReasonCode] = ReasonCode.EXPIRED

    expires_at: UtcTime
    observed_at: ProcessingPoint

    def __post_init__(self) -> None:
        if not isinstance(self.expires_at, UtcTime):
            raise KernelValueError("ExpiryDetail.expires_at must be a UtcTime")
        if not isinstance(self.observed_at, ProcessingPoint):
            raise KernelValueError("ExpiryDetail.observed_at must be a ProcessingPoint")


@dataclass(frozen=True, slots=True)
class NoCandidateDetail:
    """期限内に適格 open がなかったことの詳細（D02 §8.2）。"""

    code: ClassVar[ReasonCode] = ReasonCode.NO_CANDIDATE

    expires_at: UtcTime
    earliest_candidate: UtcTime | None

    def __post_init__(self) -> None:
        if not isinstance(self.expires_at, UtcTime):
            raise KernelValueError("NoCandidateDetail.expires_at must be a UtcTime")
        if self.earliest_candidate is not None and not isinstance(self.earliest_candidate, UtcTime):
            raise KernelValueError("NoCandidateDetail.earliest_candidate must be a UtcTime or None")


@dataclass(frozen=True, slots=True)
class RunEndDetail:
    """run 末尾に達したことの詳細（D02 §8.2）。"""

    code: ClassVar[ReasonCode] = ReasonCode.RUN_END

    run_end: UtcTime

    def __post_init__(self) -> None:
        if not isinstance(self.run_end, UtcTime):
            raise KernelValueError("RunEndDetail.run_end must be a UtcTime")


@dataclass(frozen=True, slots=True)
class CarryNotAllowedDetail:
    """週末持ち越し禁止による拒否の詳細（D02 §8.2）。"""

    code: ClassVar[ReasonCode] = ReasonCode.CARRY_NOT_ALLOWED

    next_candidate: UtcTime
    session_close: UtcTime

    def __post_init__(self) -> None:
        if not isinstance(self.next_candidate, UtcTime):
            raise KernelValueError("CarryNotAllowedDetail.next_candidate must be a UtcTime")
        if not isinstance(self.session_close, UtcTime):
            raise KernelValueError("CarryNotAllowedDetail.session_close must be a UtcTime")


@dataclass(frozen=True, slots=True)
class PositionClosedDetail:
    """閉鎖済み建玉への要求の詳細（D02 §8.2）。"""

    code: ClassVar[ReasonCode] = ReasonCode.POSITION_CLOSED

    position_id: PositionId
    closed_at: ProcessingPoint

    def __post_init__(self) -> None:
        if not isinstance(self.position_id, PositionId):
            raise KernelValueError("PositionClosedDetail.position_id must be a PositionId")
        if not isinstance(self.closed_at, ProcessingPoint):
            raise KernelValueError("PositionClosedDetail.closed_at must be a ProcessingPoint")


@dataclass(frozen=True, slots=True)
class RiskRejectionDetail:
    """リスク審査による拒否の詳細（D02 §8.2）。

    `check`（どの検査に触れたか）の語彙は D06 が決める。`limit` と `observed` は金額
    （`Money`）か比率（`Decimal`）のいずれかで、両者の型は一致していなければならない。
    """

    code: ClassVar[ReasonCode] = ReasonCode.RISK

    check: str
    limit: Money | Decimal
    observed: Money | Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.check, str) or not self.check:
            raise KernelValueError("RiskRejectionDetail.check must be a non-empty str")
        for value, label in ((self.limit, "limit"), (self.observed, "observed")):
            if not isinstance(value, (Money, Decimal)):
                raise KernelValueError(
                    f"RiskRejectionDetail.{label} must be a Money or a Decimal,"
                    f" got {type(value).__name__}"
                )
            if isinstance(value, Decimal) and not value.is_finite():
                raise KernelValueError(f"RiskRejectionDetail.{label} must be finite, got {value}")
        if type(self.limit) is not type(self.observed):
            raise KernelValueError(
                "RiskRejectionDetail.limit and .observed must have the same type,"
                f" got {type(self.limit).__name__} and {type(self.observed).__name__}"
            )
        if isinstance(self.limit, Money) and isinstance(self.observed, Money):
            if self.limit.currency != self.observed.currency:
                raise KernelValueError(
                    "RiskRejectionDetail.limit and .observed must share a currency,"
                    f" got {self.limit.currency} and {self.observed.currency}"
                )


#: 値としてそのまま許す不変な型（D02 §1 規則2）。
#: `bool` は `int` の派生なので個別に挙げなくてよい。`Enum` と frozen dataclass は
#: `_require_immutable` が型ではなく性質で判定する。
#: `datetime` 系は標準ライブラリの不変型で、`UtcTime.value` などが直接保持する。
_IMMUTABLE_LEAF_TYPES: Final = (
    bool,
    int,
    float,
    str,
    bytes,
    Decimal,
    datetime,
    date,
    time,
    timedelta,
    tzinfo,
)

#: 明示的に拒否する可変コンテナ。`tuple` 以外のコレクションは受け付けない。
_MUTABLE_CONTAINERS: Final = (list, dict, set, bytearray, frozenset)


def _require_frozen_dataclass(value: object, path: str) -> None:
    """`value` が frozen dataclass のインスタンスであることを確かめる。"""
    value_type = type(value)
    if not is_dataclass(value_type):
        raise KernelValueError(f"{path} must be a frozen dataclass, got {value_type.__name__}")
    params = getattr(value_type, "__dataclass_params__", None)
    if params is None or not params.frozen:
        raise KernelValueError(
            f"{path} must be a frozen dataclass, but {value_type.__name__} is mutable"
        )


def _require_immutable(value: object, path: str) -> None:
    """`value` とその内部が再帰的に不変であることを確かめる（D02 §1 規則2・§8.2）。

    frozen dataclass であっても、`notes: list[str]` のような可変なフィールドを持てば中身は
    後から書き換えられる。根拠として保存した理由が実行後に変わらないよう、葉まで検査する。

    許す葉: `None`、`bool` / `int` / `float` / `str` / `bytes` / `Decimal`、`Enum` の要素、
    frozen dataclass（そのフィールドを再帰的に検査）。コンテナは `tuple` だけを許し、
    要素を再帰的に検査する。`list` / `dict` / `set` / `bytearray` は拒否する。
    """
    if value is None or isinstance(value, Enum) or isinstance(value, _IMMUTABLE_LEAF_TYPES):
        return
    if isinstance(value, _MUTABLE_CONTAINERS):
        raise KernelValueError(
            f"{path} must be immutable, but it holds a {type(value).__name__};"
            " use a tuple of immutable values instead"
        )
    if isinstance(value, tuple):
        for index, item in enumerate(value):
            _require_immutable(item, f"{path}[{index}]")
        return
    # 残るのは frozen dataclass だけ（`common` の値型はすべてこれに当たる）。
    _require_frozen_dataclass(value, path)
    for field in fields(value):  # type: ignore[arg-type]
        _require_immutable(getattr(value, field.name), f"{path}.{field.name}")


@dataclass(frozen=True, slots=True)
class Reason:
    """理由コードと、任意の型付き詳細の組（D02 §8.2）。

    詳細が付く場合は `detail.code == code` でなければならない。
    """

    code: ReasonCode
    detail: ReasonDetail | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, ReasonCode):
            raise KernelValueError(f"Reason.code must be a ReasonCode, got {self.code!r}")
        if self.detail is None:
            return

        detail_type = type(self.detail)
        # 詳細型は frozen dataclass であり、その中身も再帰的に不変でなければならない
        # （D02 §1 規則2・§8.2）。`code` を持つだけの任意のオブジェクトや、可変な
        # フィールドを持つ詳細を受けると、`Reason` が不変でも記録した理由が実行後に
        # 書き換わってしまう。
        _require_immutable(self.detail, "Reason.detail")

        detail_code = getattr(detail_type, "code", None)
        if not isinstance(detail_code, ReasonCode):
            raise KernelValueError(
                "Reason.detail must be a ReasonDetail carrying a ReasonCode class variable,"
                f" got {detail_type.__name__}"
            )
        if detail_code is not self.code:
            raise KernelValueError(
                f"Reason.detail carries {detail_code.value}, but the reason is {self.code.value}"
            )

    def __str__(self) -> str:
        return self.code.value
