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

from odyssey_fx.backtest.domain.fills import CostKind, FillRecord
from odyssey_fx.backtest.domain.policies import ConversionPath
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

__all__ = [
    "CompositeRow",
    "EvidenceKind",
    "EvidenceRecord",
    "ManagementApplication",
    "MarketObservationRef",
    "TraceTable",
    "canonical_text",
    "cost_columns",
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
    return str(value)


def _expand(annotation: Any, value: object, name: str) -> dict[str, object]:
    """1つのフィールドを列へ開く（値が `None` でも列の集合は変わらない）。"""
    _, inner = _is_optional(annotation)

    variants = _variants(inner)
    if variants:
        columns: dict[str, object] = {_join(name, "kind"): None}
        for variant in variants:
            for field_name, hint in _hints(variant).items():
                if field_name == "kind":
                    continue
                columns.update(_expand(hint, None, _join(name, field_name)))
        if value is not None:
            columns[_join(name, "kind")] = getattr(value, "kind", type(value).__name__)
            for field_name, hint in _hints(type(value)).items():
                if field_name == "kind":
                    continue
                columns.update(_expand(hint, getattr(value, field_name), _join(name, field_name)))
        return columns

    origin = typing.get_origin(inner)
    if origin in (tuple, list, Sequence):
        items: tuple[object, ...] = (
            () if value is None else tuple(value)  # type: ignore[arg-type]
        )
        return {name: [_list_item(item) for item in items]}

    if inner is Reason or (isinstance(value, Reason)):
        reason = value if isinstance(value, Reason) else None
        return {
            _join(name, "code"): None if reason is None else reason.code.value,
            _join(name, "detail"): (
                None if reason is None or reason.detail is None else canonical_text(reason.detail)
            ),
        }

    if inner is PhaseRank or isinstance(value, PhaseRank):
        return {name: None if value is None else str(value)}

    if isinstance(inner, type) and issubclass(inner, _SCALAR_TYPES):
        return {name: _scalar_column(value)}

    if isinstance(inner, type) and issubclass(inner, Enum):
        return {name: None if value is None else _scalar_column(value)}

    if isinstance(inner, type) and is_dataclass(inner) and not _is_scalar_record(inner):
        columns = {}
        for field_name, hint in _hints(inner).items():
            child = None if value is None else getattr(value, field_name)
            columns.update(_expand(hint, child, _join(name, field_name)))
        return columns

    return {name: _scalar_column(value)}


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
