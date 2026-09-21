"""判断履歴の表と平坦化（D06 §9.1・§9.2）。

段階2で書き出す表は15件で、行は**平坦化**して保存する。列は本書・D05 の型のフィールドに
1対1で対応させ、入れ子は次の規則で開く（D06 §9.1、Q14 決定）。

1. 行そのものの型のフィールドには接頭辞を付けず、入れ子は `<フィールド名>_` を接頭辞として
   再帰的に開く。
2. 区分タグ付き union は `kind` がそのまま列になり、列は**全変種のフィールドの和集合**、
   その行の変種に無い列は `None` にする。
3. 複合表は「正本の型」欄の先頭の型を主として接頭辞なしで開き、従の型は
   `<型名のスネークケース>_` を接頭辞にする。

値の書き方は、`ProcessingPoint` が `*_time` / `*_phase` / `*_sequence`、`Reason` が
`*_code` / `*_detail`、`Money` が `*_amount` / `*_currency`、`Decimal` と `Price` と
`Quantity` は文字列、`UtcTime` は D02 §3.1 の文字列、ID 型は `__str__` である。**Decimal を
浮動小数で保存すると再現性が壊れる**ため文字列にする（ADR-0012）。

十進数の文字列には **D02 §9.3 の正規化エンコード**（`encode_decimal`）を使う。同じ値からは
常に同じ文字列になり、再実行の判断履歴を文字列のまま比べられるためである（D06 §4.4 の
「許容誤差は完全一致」）。`Decimal("1000000")` は `1e6`、`Decimal("149.500")` は
`149500e-3` になる。読み戻しは `Decimal(文字列)` で厳密に往復する。

可変長の入れ子（レコードの `tuple`）は、要素ごとに D02 §9.3 の正規化エンコード文字列にし、
その文字列の `list` 列として保存する。正規化エンコードを使うのは、同じ内容から常に同じ
文字列が出て再現性の比較ができるためである。
"""

from __future__ import annotations

import types
import typing
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Final, Protocol, runtime_checkable

from odyssey_fx.backtest.domain.events import OrderEvent
from odyssey_fx.backtest.domain.fills import CostKind, FillRecord
from odyssey_fx.backtest.domain.orders import AcceptedOrder, OrderRequest
from odyssey_fx.backtest.domain.policies import ConversionPath
from odyssey_fx.backtest.domain.positions import (
    Position,
    PositionRiskAllocation,
    RiskMeasurement,
)
from odyssey_fx.backtest.domain.reservations import ReservationState, RiskReservation
from odyssey_fx.common.canonical import encode, encode_decimal
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import (
    AttemptId,
    EvaluationId,
    EvidenceId,
    OutputId,
    PositionId,
)
from odyssey_fx.common.money import Money, Price, PriceOffset, Quantity
from odyssey_fx.common.reason import Reason
from odyssey_fx.common.refs import PolicyRef, SnapshotRef
from odyssey_fx.common.time import Interval, PhaseRank, ProcessingPoint
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.strategy.declarations.refs import MarketDataField
from odyssey_fx.strategy.records.records import OutputRecord

__all__ = [
    "CompositeRow",
    "EvidenceKind",
    "EvidenceRecord",
    "ManagementApplication",
    "MarketObservationRef",
    "TraceTable",
    "canonical_text",
    "cost_columns",
    "column_names",
    "table_column_kinds",
    "table_columns",
    "flatten",
    "flatten_composite",
    "flatten_row",
    "flatten_union",
]


class TraceTable(Enum):
    """段階2で書き出す15表（D06 §9.2）。"""

    OUTPUTS = "OUTPUTS"
    EVALUATIONS = "EVALUATIONS"
    OPPORTUNITY_TRANSITIONS = "OPPORTUNITY_TRANSITIONS"
    ORDER_REQUESTS = "ORDER_REQUESTS"
    ATTEMPT_DECISIONS = "ATTEMPT_DECISIONS"
    RISK_ASSESSMENTS = "RISK_ASSESSMENTS"
    ORDERS = "ORDERS"
    ORDER_EVENTS = "ORDER_EVENTS"
    FILLS = "FILLS"
    RESERVATIONS = "RESERVATIONS"
    POSITIONS = "POSITIONS"
    MANAGEMENT_APPLICATIONS = "MANAGEMENT_APPLICATIONS"
    INTRABAR_RESOLUTIONS = "INTRABAR_RESOLUTIONS"
    LEDGER_SNAPSHOTS = "LEDGER_SNAPSHOTS"
    EVIDENCE = "EVIDENCE"


class EvidenceKind(Enum):
    """根拠記録の種別（D06 §9.2）。"""

    ORDER_REQUEST = "ORDER_REQUEST"
    ADMISSION = "ADMISSION"
    FILL = "FILL"
    PROTECTION_UPDATE = "PROTECTION_UPDATE"


@dataclass(frozen=True, slots=True)
class MarketObservationRef:
    """根拠になった市場データの観測（D06 §9.2）。"""

    snapshot_ref: SnapshotRef
    series: SeriesId
    interval: Interval
    field: MarketDataField

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_ref, SnapshotRef):
            raise KernelValueError("MarketObservationRef.snapshot_ref must be a SnapshotRef")
        if not isinstance(self.series, SeriesId):
            raise KernelValueError("MarketObservationRef.series must be a SeriesId")
        if not isinstance(self.interval, Interval):
            raise KernelValueError("MarketObservationRef.interval must be an Interval")
        if not isinstance(self.field, MarketDataField):
            raise KernelValueError("MarketObservationRef.field must be a MarketDataField")


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """型付きの根拠記録（D06 §9.2 の表15）。説明文だけのログにはしない。

    `EvidenceRef` は識別子1件しか持たないため、根拠の中身をここへ型付きで残し、表1・2・4・
    12・14 へは自然キーで辿る。換算の経路（各脚の系列・率・観測時点・逆数化の有無）を
    `conversion_paths` に入れるのは、`ConversionRate` が合成後の1つの率しか持たず、
    D06 §8.5.1 の規則4・5 が要求する情報が他のどの表にも残らないためである。
    """

    evidence_id: EvidenceId
    at: ProcessingPoint
    kind: EvidenceKind
    output_ids: tuple[OutputId, ...] = ()
    evaluation_ids: tuple[EvaluationId, ...] = ()
    attempt_id: AttemptId | None = None
    position_id: PositionId | None = None
    market_refs: tuple[MarketObservationRef, ...] = ()
    conversion_paths: tuple[ConversionPath, ...] = ()
    ledger_snapshot_at: ProcessingPoint | None = None
    policy_refs: tuple[PolicyRef, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, EvidenceId):
            raise KernelValueError("EvidenceRecord.evidence_id must be an EvidenceId")
        if not isinstance(self.at, ProcessingPoint):
            raise KernelValueError("EvidenceRecord.at must be a ProcessingPoint")
        if not isinstance(self.kind, EvidenceKind):
            raise KernelValueError("EvidenceRecord.kind must be an EvidenceKind")


@dataclass(frozen=True, slots=True)
class ManagementApplication:
    """管理要求を建玉へ適用した結果（D06 §8.3・§9.2 の表12 の「適用結果」）。

    D06 §3 の型表には「適用結果」としか書かれていないため、その項目を表12 の列として
    保存できる形にしたものである。適用しなかった要求も**記録は残す**（`applied=False` と
    理由）。建玉は利確を持たないまま損切りだけで継続する。
    """

    at: ProcessingPoint
    applied: bool
    reason: Reason | None = None
    protection_version: int | None = None
    rounded_take_profit: Price | None = None
    realized_reward_risk: Decimal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.at, ProcessingPoint):
            raise KernelValueError("ManagementApplication.at must be a ProcessingPoint")
        if not isinstance(self.applied, bool):
            raise KernelValueError("ManagementApplication.applied must be a bool")
        if self.applied and self.reason is not None:
            raise KernelValueError(
                "a reason is recorded only when the request was not applied (D06 §8.3)"
            )
        if not self.applied and self.reason is None:
            raise KernelValueError("a request that was not applied must record why (D06 §8.3)")


# --- 平坦化 -----------------------------------------------------------------


@runtime_checkable
class _CanonicalScalar(Protocol):
    """`canonical_str()` を持つ値（ID 型・`UtcTime`・`SeriesId` など）。"""

    def canonical_str(self) -> str: ...


#: 単一の列へ落とす型（`canonical_str()` を持つ型はこの表を引かずに文字列化する）。
_SCALAR_TYPES: Final = (Decimal, str, bool, int)


def _join(prefix: str, child: str) -> str:
    return child if not prefix else f"{prefix}_{child}"


def _is_optional(annotation: Any) -> tuple[bool, Any]:
    """`X | None` を剥がす。戻り値は `(省略可能か, 中身の型)`。"""
    origin = typing.get_origin(annotation)
    if origin is types.UnionType or origin is typing.Union:
        args = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        optional = len(args) != len(typing.get_args(annotation))
        rebuilt: Any = args[0]
        for arg in args[1:]:
            rebuilt = rebuilt | arg
        return optional, rebuilt
    return False, annotation


def _variants(annotation: Any) -> tuple[type, ...]:
    """区分タグ付き union の変種（union でなければ空）。"""
    origin = typing.get_origin(annotation)
    if origin is types.UnionType or origin is typing.Union:
        args = tuple(arg for arg in typing.get_args(annotation) if arg is not type(None))
        if len(args) > 1 and all(is_dataclass(arg) for arg in args):
            return args
    return ()


def _hints(cls: type) -> Mapping[str, Any]:
    return typing.get_type_hints(cls)


def canonical_text(value: object) -> str:
    """1つのレコードを D02 §9.3 の正規化エンコード文字列にする（D06 §9.1）。

    `timedelta` は D02 §9.3 の対象型に無いため、**秒数の十進文字列**として符号化してから
    渡す。段階2で `timedelta` を持つのは換算経路のずれ（`ConversionPath.skew`）だけで、
    値の取りうる範囲も符号化の規則も1か所で閉じている。
    """
    return encode(_canonical_payload(value)).decode("utf-8")


def _canonical_payload(value: object) -> Any:
    """正規化エンコードへ渡せる形（`timedelta` だけを文字列へ落とす）。"""
    if isinstance(value, timedelta):
        return f"{value.total_seconds():.6f}"
    if isinstance(value, (tuple, list)):
        return [_canonical_payload(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _canonical_payload(item) for key, item in value.items()}
    if (
        is_dataclass(value)
        and not isinstance(value, type)
        and not isinstance(value, _CanonicalScalar)
    ):
        return {
            field.name: _canonical_payload(getattr(value, field.name)) for field in fields(value)
        }
    return value


def _scalar_column(value: object) -> object:
    """単一の列に入れる値へ落とす。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Price):
        return encode_decimal(value.value)
    if isinstance(value, PriceOffset):
        return encode_decimal(value.value)
    if isinstance(value, Quantity):
        return encode_decimal(value.units)
    if isinstance(value, Decimal):
        return encode_decimal(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, timedelta):
        return f"{value.total_seconds():.6f}"
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, _CanonicalScalar):
        return value.canonical_str()
    if is_dataclass(value) and not isinstance(value, type):
        # 型が注釈から決まらない値（出力記録の中身など）。正規化エンコードにしておけば、
        # 同じ内容から常に同じ文字列になり、再実行の判断履歴を文字列のまま比べられる。
        return canonical_text(value)
    return str(value)


def _expand(
    annotation: Any, value: object, name: str, kinds: dict[str, str] | None = None
) -> dict[str, object]:
    """1つのフィールドを列へ開く（値が `None` でも列の集合は変わらない）。

    `kinds` を渡すと、各列の**物理的な型**（`string` / `int` / `bool` / `list`）も一緒に
    記録する。行が1件も無い表でも、行のある表と同じ型で書けるようにするためである。
    """
    _, inner = _is_optional(annotation)

    variants = _variants(inner)
    if variants:
        columns: dict[str, object] = {_join(name, "kind"): None}
        _mark(kinds, _join(name, "kind"), "string")
        for variant in variants:
            for field_name, hint in _hints(variant).items():
                if field_name == "kind":
                    continue
                columns.update(_expand(hint, None, _join(name, field_name), kinds))
        if value is not None:
            columns[_join(name, "kind")] = getattr(value, "kind", type(value).__name__)
            for field_name, hint in _hints(type(value)).items():
                if field_name == "kind":
                    continue
                columns.update(
                    _expand(hint, getattr(value, field_name), _join(name, field_name), kinds)
                )
        return columns

    origin = typing.get_origin(inner)
    if origin in (tuple, list, Sequence):
        items: tuple[object, ...] = (
            () if value is None else tuple(value)  # type: ignore[arg-type]
        )
        _mark(kinds, name, "list")
        return {name: [_list_item(item) for item in items]}

    if inner is Reason or (isinstance(value, Reason)):
        reason = value if isinstance(value, Reason) else None
        _mark(kinds, _join(name, "code"), "string")
        _mark(kinds, _join(name, "detail"), "string")
        return {
            _join(name, "code"): None if reason is None else reason.code.value,
            _join(name, "detail"): (
                None if reason is None or reason.detail is None else canonical_text(reason.detail)
            ),
        }

    if inner is PhaseRank or isinstance(value, PhaseRank):
        _mark(kinds, name, "string")
        return {name: None if value is None else str(value)}

    if isinstance(inner, type) and issubclass(inner, _SCALAR_TYPES):
        _mark(kinds, name, _scalar_kind(inner))
        return {name: _scalar_column(value)}

    if isinstance(inner, type) and issubclass(inner, Enum):
        _mark(kinds, name, "string")
        return {name: None if value is None else _scalar_column(value)}

    if isinstance(inner, type) and is_dataclass(inner) and not _is_scalar_record(inner):
        columns = {}
        for field_name, hint in _hints(inner).items():
            child = None if value is None else getattr(value, field_name)
            columns.update(_expand(hint, child, _join(name, field_name), kinds))
        return columns

    _mark(kinds, name, "string")
    return {name: _scalar_column(value)}


def _mark(kinds: dict[str, str] | None, name: str, kind: str) -> None:
    """列の物理的な型を控える（既に控えてあれば上書きしない）。"""
    if kinds is not None:
        kinds.setdefault(name, kind)


def _scalar_kind(inner: type) -> str:
    """単一の列の物理的な型。`Decimal` は文字列で保存する（ADR-0012）。"""
    if issubclass(inner, bool):
        return "bool"
    if issubclass(inner, int):
        return "int"
    return "string"


def _is_scalar_record(cls: type) -> bool:
    """単一の列にするレコード。

    `canonical_str()` を持つ型（ID 型・`UtcTime`・`SeriesId` など）と、`Decimal` 1つを
    包んだ価格・数量の型がこれに当たる。D06 §9.1 は「`Decimal` と `Price` と `Quantity` は
    文字列」と定めており、`Price.value` のような内部のフィールド名を列名に出さない。
    """
    return hasattr(cls, "canonical_str") or issubclass(cls, (Price, PriceOffset, Quantity))


def _list_item(item: object) -> object:
    """可変長の入れ子の1要素（レコードは正規化エンコード文字列、ID は `__str__`）。"""
    if isinstance(item, _CanonicalScalar):
        return item.canonical_str()
    if is_dataclass(item) and not isinstance(item, type):
        return canonical_text(item)
    scalar = _scalar_column(item)
    return scalar if isinstance(scalar, str) else str(scalar)


def flatten(row: object, prefix: str = "") -> dict[str, object]:
    """1行を列の辞書へ開く（D06 §9.1 の規則1）。"""
    if not is_dataclass(row) or isinstance(row, type):
        raise KernelValueError(f"flatten requires a dataclass row, got {row!r}")
    columns: dict[str, object] = {}
    for field_name, hint in _hints(type(row)).items():
        columns.update(_expand(hint, getattr(row, field_name), _join(prefix, field_name)))
    return columns


def flatten_union(row: object, variants: Sequence[type]) -> dict[str, object]:
    """行そのものが区分タグ付き union である表を開く（D06 §9.1 の規則2）。

    接頭辞が付かないので、区分の列は `kind`、変種のフィールドは接頭辞なしで並ぶ。列は
    全変種のフィールドの和集合とし、その行の変種に無い列は `None` にする（表5）。
    """
    columns: dict[str, object] = {"kind": None}
    for variant in variants:
        for field_name, hint in _hints(variant).items():
            if field_name == "kind":
                continue
            columns.update(_expand(hint, None, field_name))
    columns["kind"] = getattr(row, "kind", type(row).__name__)
    for field_name, hint in _hints(type(row)).items():
        if field_name == "kind":
            continue
        columns.update(_expand(hint, getattr(row, field_name), field_name))
    return columns


def flatten_composite(
    primary: object, secondaries: Sequence[tuple[str, type, object | None]]
) -> dict[str, object]:
    """複合表の1行を開く（D06 §9.1 の規則3）。

    `secondaries` は `(接頭辞, 型, 値)` の並びで、値が `None` でも型から列を作る（その行の
    列が欠けると表の形が行ごとに変わってしまう）。
    """
    columns = flatten(primary)
    for prefix, secondary_type, value in secondaries:
        for field_name, hint in _hints(secondary_type).items():
            child = None if value is None else getattr(value, field_name)
            columns.update(_expand(hint, child, _join(prefix, field_name)))
    return columns


@dataclass(frozen=True, slots=True)
class CompositeRow:
    """複合表・union の行を運ぶ入れ物（D06 §9.1 の規則2・3）。

    D06 §3 の型表には無い**書き出しのための運び手**である。表10・11・12 は1行が複数の型
    からなり、表5 は行そのものが区分タグ付き union である。`trace` は `admission` /
    `execution` / `portfolio` を import できないため（D01 §3.3）、行の型をここで知ることが
    できない。そこで行を作る側（`engine`）が、従の型と変種を値と一緒に渡す。
    """

    primary: object
    parts: tuple[tuple[str, type, object | None], ...] = ()
    variants: tuple[type, ...] = ()


def column_names(
    row_type: type,
    *,
    secondaries: Sequence[tuple[str, type]] = (),
    variants: Sequence[type] = (),
) -> tuple[str, ...]:
    """その表の列名（行が1件も無くても表の形を保つために使う）。

    値を持たない `None` の行を開いたときの列と同じ並びになる。平坦化の規則が「その行の
    変種に無い列は `None`」と定めているので、行ごとに列が増減することはない。
    """
    if variants:
        columns: dict[str, object] = {"kind": None}
        for variant in variants:
            for field_name, hint in _hints(variant).items():
                if field_name == "kind":
                    continue
                columns.update(_expand(hint, None, field_name))
        return tuple(columns)
    columns = {}
    for field_name, hint in _hints(row_type).items():
        columns.update(_expand(hint, None, field_name))
    for prefix, secondary_type in secondaries:
        for field_name, hint in _hints(secondary_type).items():
            columns.update(_expand(hint, None, _join(prefix, field_name)))
    return tuple(columns)


def flatten_row(row: object) -> dict[str, object]:
    """表の1行を列の辞書へ開く（書き出し側の唯一の入口）。

    `CompositeRow` なら複合表・union の規則を、素のレコードなら規則1 を適用する。約定の行
    には区分別の費用列を足す（D06 §9.2、Q12 決定）。
    """
    if isinstance(row, CompositeRow):
        if row.variants:
            return flatten_union(row.primary, row.variants)
        return flatten_composite(row.primary, row.parts)
    columns = flatten(row)
    if isinstance(row, FillRecord):
        columns.update(cost_columns(row))
    return columns


#: 記録層から型を参照できる11表の「正本の型」（D06 §9.2 の表）。複合表は従の型を接頭辞と
#: 一緒に並べる（規則3）。
_TABLE_TYPES: Final[Mapping[TraceTable, tuple[type, tuple[tuple[str, type], ...]]]] = {
    TraceTable.OUTPUTS: (OutputRecord, ()),
    TraceTable.ORDER_REQUESTS: (OrderRequest, ()),
    TraceTable.ORDERS: (AcceptedOrder, ()),
    TraceTable.ORDER_EVENTS: (OrderEvent, ()),
    TraceTable.FILLS: (FillRecord, ()),
    TraceTable.RESERVATIONS: (
        RiskReservation,
        (("reservation_state", ReservationState),),
    ),
    TraceTable.POSITIONS: (
        Position,
        (
            ("position_risk_allocation", PositionRiskAllocation),
            ("risk_measurement", RiskMeasurement),
        ),
    ),
    TraceTable.EVIDENCE: (EvidenceRecord, ()),
}

#: 型をここから参照できない7表の列。理由は2つある。
#:
#: 1. `admission` / `execution` / `portfolio` は記録層と同じ中間層で、相互に import できない
#:    （D01 §3.3 の契約 L2c）: 表5・6・13・14
#: 2. `strategy.runtime` は記録層からは見えるが、この表を読む書き出し実装
#:    （`evaluation.adapters`）が `strategy.runtime` を参照できない（契約 F8）: 表2・3・12
#:
#: `tests/unit/backtest/test_fs_store.py` が、実際の行の列と一致することを機械検査する。
_BORROWED_COLUMNS: Final[Mapping[TraceTable, tuple[str, ...]]] = {
    TraceTable.EVALUATIONS: (
        "request_id",
        "evaluation_id",
        "instance_id",
        "trigger_names",
        "decision_time",
        "outcome_kind",
        "outcome_output_ids",
        "outcome_diagnoses",
        "outcome_reason_code",
        "outcome_reason_detail",
        "target_interval_start",
        "target_interval_end",
        "opportunity_id",
        "position_id",
    ),
    TraceTable.OPPORTUNITY_TRANSITIONS: (
        "opportunity_id",
        "from_state",
        "to_state",
        "at_time",
        "at_phase",
        "at_sequence",
        "phase",
        "reason_code",
        "reason_detail",
        "counterpart",
        "attempt_id",
    ),
    TraceTable.MANAGEMENT_APPLICATIONS: (
        "position_id",
        "action_kind",
        "action_price",
        "decision_time",
        "source_output_id",
        "application_at_time",
        "application_at_phase",
        "application_at_sequence",
        "application_applied",
        "application_reason_code",
        "application_reason_detail",
        "application_protection_version",
        "application_rounded_take_profit",
        "application_realized_reward_risk",
    ),
    TraceTable.ATTEMPT_DECISIONS: (
        "kind",
        "attempt_id",
        "order_id",
        "assessment_ref_assessment_id",
        "reason_code",
        "reason_detail",
    ),
    TraceTable.RISK_ASSESSMENTS: (
        "assessment_id",
        "attempt_id",
        "policy_ref_policy_kind",
        "policy_ref_policy_id",
        "policy_ref_version",
        "policy_ref_digest_algorithm",
        "policy_ref_digest_hex",
        "reached_step",
        "budget_balance_amount",
        "budget_balance_currency",
        "budget_trial_budget_amount",
        "budget_trial_budget_currency",
        "budget_account_remaining_amount",
        "budget_account_remaining_currency",
        "budget_admission_budget_amount",
        "budget_admission_budget_currency",
        "budget_consumed_amount",
        "budget_consumed_currency",
        "reference_quote_price",
        "reference_quote_basis",
        "reference_quote_observed_at",
        "reference_quote_derived_from_spread",
        "reference_quote_source_bar_series",
        "reference_quote_source_bar_bar_start",
        "stop_before_rounding",
        "checks",
        "stop_after_rounding",
        "adverse_fill_limit",
        "quantity_step",
        "quantity",
        "conversion_from_currency",
        "conversion_to_currency",
        "conversion_rate",
        "conversion_observed_at",
        "conversion_evidence_evidence_id",
        "cost_budget_amount",
        "cost_budget_currency",
        "reservation_amount_amount",
        "reservation_amount_currency",
    ),
    TraceTable.INTRABAR_RESOLUTIONS: (
        "position_id",
        "parent_bar_key_series",
        "parent_bar_key_bar_start",
        "method",
        "series_used",
        "verdict",
        "fill_id",
        "resolved_child_bar_key_series",
        "resolved_child_bar_key_bar_start",
    ),
    TraceTable.LEDGER_SNAPSHOTS: (
        "at_time",
        "at_phase",
        "at_sequence",
        "balance_amount",
        "balance_currency",
        "equity_amount",
        "equity_currency",
        "consumed_amount",
        "consumed_currency",
        "open_position_ids",
    ),
}


def table_columns(table: TraceTable) -> tuple[str, ...]:
    """表の列（行が1件も無くても同じ形で保存するために使う、D06 §9.2）。

    行の無い表を列の無いファイルとして書くと、取引が1件も無かった正常な run と、必須の列を
    欠いた壊れた表とを読む側が区別できない。すべての行が持つ `run_id`（上位設計書 §4.7.15）
    を先頭に置く。
    """
    if table in _BORROWED_COLUMNS:
        return _with_run_id(_BORROWED_COLUMNS[table])
    row_type, secondaries = _TABLE_TYPES[table]
    names = column_names(row_type, secondaries=secondaries)
    if table is TraceTable.FILLS:
        names = (*names, *(name for kind in CostKind for name in _cost_column_names(kind)))
    return _with_run_id(names)


def _with_run_id(names: Sequence[str]) -> tuple[str, ...]:
    """`run_id` を先頭に置く（行そのものが持つ表では重ねない）。"""
    return ("run_id", *(name for name in names if name != "run_id"))


#: 書き写した7表のうち、文字列にならない列。ここに無い列は文字列である。
#: `tests/unit/backtest/test_fs_store.py` が、実際の行の型と一致することを機械検査する。
_BORROWED_KINDS: Final[Mapping[TraceTable, Mapping[str, str]]] = {
    TraceTable.EVALUATIONS: {
        "trigger_names": "list",
        "outcome_output_ids": "list",
        "outcome_diagnoses": "list",
    },
    TraceTable.OPPORTUNITY_TRANSITIONS: {"at_sequence": "int"},
    TraceTable.RISK_ASSESSMENTS: {
        "policy_ref_version": "int",
        "reached_step": "int",
        "reference_quote_derived_from_spread": "bool",
        "checks": "list",
    },
    TraceTable.MANAGEMENT_APPLICATIONS: {
        "application_at_sequence": "int",
        "application_applied": "bool",
        "application_protection_version": "int",
    },
    TraceTable.INTRABAR_RESOLUTIONS: {"series_used": "list"},
    TraceTable.LEDGER_SNAPSHOTS: {"at_sequence": "int", "open_position_ids": "list"},
}


def table_column_kinds(table: TraceTable) -> Mapping[str, str]:
    """表の各列の物理的な型（`string` / `int` / `bool` / `list`）。

    行が1件も無い表を書くときに使う。すべて文字列にしてしまうと、行のある表では
    `list` や `int` だった列が空の表では文字列になり、両方を読み込むときに型が食い違う。
    """
    names = table_columns(table)
    if table in _BORROWED_COLUMNS:
        declared = _BORROWED_KINDS.get(table, {})
        return {name: declared.get(name, "string") for name in names}
    row_type, secondaries = _TABLE_TYPES[table]
    kinds: dict[str, str] = {}
    for field_name, hint in _hints(row_type).items():
        _expand(hint, None, field_name, kinds)
    for prefix, secondary_type in secondaries:
        for field_name, hint in _hints(secondary_type).items():
            _expand(hint, None, _join(prefix, field_name), kinds)
    return {name: kinds.get(name, "string") for name in names}


def _cost_column_names(kind: CostKind) -> tuple[str, str]:
    base = f"cost_{kind.value.lower()}"
    return (f"{base}_amount", f"{base}_currency")


def cost_columns(fill: FillRecord) -> dict[str, object]:
    """約定1件の費用を区分別の金額列にする（D06 §9.2、Q12 決定）。

    符号化された `costs` 列からは区分別の金額を復元できない（D02 §9.3 は復号を定義して
    いない）ため、口座通貨での計上額を区分ごとに2列ずつ足す。該当する区分の費用が無い約定
    では**どちらの列も `None`** にし、金額 0 の費用が記録された場合と区別する。
    """
    columns: dict[str, object] = {}
    for kind in CostKind:
        entry = fill.cost_of(kind)
        base = f"cost_{kind.value.lower()}"
        amount: Money | None = None if entry is None else entry.account
        columns[f"{base}_amount"] = None if amount is None else encode_decimal(amount.amount)
        columns[f"{base}_currency"] = None if amount is None else str(amount.currency)
    return columns
