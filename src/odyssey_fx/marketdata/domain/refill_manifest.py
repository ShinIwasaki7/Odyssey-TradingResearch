"""補充分の識別子と補充の manifest（D03 §14.10・§14.11、D01 §4 v2.9）。

補充分のディレクトリ `data/raw/market/refill/<refill_id>/` に置く `refill_manifest.json` の型と、
補充の識別子 `refill_id` の求め方を定める。本モジュールの型はすべて不変で、実時計・乱数・
環境変数を読まない（D01 §2.2 規則2）。

**補充の識別子 `refill_id`**（D03 §14.10）は次の正規化内容のダイジェストである: `plan_id`、
時間ファイルごとの `(銘柄, 時刻, 最終結果の区分, 解凍後の tick の内容のダイジェスト（NOT_FETCHED
なら空）)` の列（`(銘柄, 時刻)` で整列）、集約規則の版（`refill_ticks_v1`）、変換コード版。
取得時刻・試行の回数・失敗の詳細・応答の中身の sha256 は入れない（D03 §14.10）。

**補充の manifest**（D03 §14.11）は価格を持たない。持つのは識別子・計画の中身（`plan.json` と
同じ正規化内容）・入力と設定の識別・時間ファイルごとの出所・補充した足のファイルごとの
`(ファイル名, 銘柄, 時間足)` と sha256 と行数・`validation.json` の sha256・系列ごとの本数・作らな
かった対象足と理由・未照合の塊・配信元の値の差の塊（v1.19）・作成時刻（識別子に入れない）。
PR #58 が定めた最小の項目（`plan_id` と時間ごとの `hour`・`outcome`・`tick_digest`）を含む
（`RefillManifestCore` が読む）。

**形式の版**（D03 §14.7・§14.11 の v1.19）: 配信元の値の差の塊を足した書き出しは
`refill_manifest_v2`・`refill_validation_v2`。v1.19 より前に書いた補充分（形式 v1。配信元の値の差の
記録を持たない）もそのまま読む（配信元の値の差は 0 件として読む。補充分は一度書いたら変えない
ため）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from odyssey_fx.common import canonical
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import decimal_from_str
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.refill import (
    REFILL_TIMEFRAME_IDS,
    ArchiveProvenance,
    FailureKind,
    FinalResult,
    HourKey,
    HourOutcome,
    ManifestHour,
    RefillPlan,
    require_hex_digest,
    series_from_record,
)
from odyssey_fx.marketdata.domain.refill_validation import (
    HourlyConsistency,
    NeighborCheck,
    NeighborSide,
    NeighborStatus,
    NotBuiltBar,
    NotBuiltReason,
    SourceDifferenceChunk,
    SourceDifferenceEvidence,
    UnreconciledChunk,
)
from odyssey_fx.marketdata.domain.series import SeriesId

__all__ = [
    "REFILL_AGGREGATION_RULE_VERSION",
    "REFILL_MANIFEST_FILE",
    "REFILL_MANIFEST_FORMAT",
    "REFILL_VALIDATION_FILE",
    "REFILL_VALIDATION_FORMAT",
    "HourRecord",
    "RefillFileRecord",
    "RefillManifest",
    "RefillValidationRecord",
    "SeriesCount",
    "bar_file_name",
    "not_built_from_payload",
    "not_built_payload",
    "refill_id_of",
    "source_difference_evidence_from_payload",
    "source_difference_evidence_payload",
    "source_difference_from_payload",
    "source_difference_payload",
    "unreconciled_from_payload",
    "unreconciled_payload",
]

#: 集約規則の版（D03 §14.10 の仮称 `refill_ticks_v1`）。
REFILL_AGGREGATION_RULE_VERSION: Final = "refill_ticks_v1"

#: 補充の manifest の形式の印と、補充分のディレクトリの中の名前（D03 §14.11）。
REFILL_MANIFEST_FORMAT: Final = "refill_manifest_v2"
REFILL_VALIDATION_FORMAT: Final = "refill_validation_v2"
#: v1.19 より前の形式（配信元の値の差の記録を持たない）。読むだけで書かない。
_REFILL_MANIFEST_FORMAT_V1: Final = "refill_manifest_v1"
_REFILL_VALIDATION_FORMAT_V1: Final = "refill_validation_v1"
REFILL_MANIFEST_FILE: Final = "refill_manifest.json"
REFILL_VALIDATION_FILE: Final = "validation.json"


def bar_file_name(series: SeriesId) -> str:
    """補充した足のファイルの名前 `<SYMBOL>_<15m|1h>_refill.csv`（D03 §14.11）。"""
    if series.timeframe.id not in REFILL_TIMEFRAME_IDS:
        raise MarketDataValueError(f"{series} is not a refill series (15m or 1h)")
    return f"{series.symbol}_{series.timeframe.id}_refill.csv"


def _refill_id_payload(
    plan_id: str,
    hours: Sequence[ManifestHour],
    aggregation_rule_version: str,
    code_version: str,
) -> Mapping[str, Any]:
    ordered = sorted(hours, key=lambda item: item.hour.sort_key())
    return {
        "aggregation_rule_version": aggregation_rule_version,
        "code_version": code_version,
        "hours": [
            {
                "outcome": item.outcome.value,
                "start": str(item.hour.start),
                "symbol": str(item.hour.symbol),
                "tick_digest": "" if item.tick_digest is None else item.tick_digest,
            }
            for item in ordered
        ],
        "plan_id": plan_id,
    }


def refill_id_of(
    plan_id: str,
    hours: Sequence[ManifestHour],
    aggregation_rule_version: str,
    code_version: str,
) -> str:
    """補充の識別子（16進 64 文字。D03 §14.10）。"""
    require_hex_digest(plan_id, "plan_id")
    if not isinstance(aggregation_rule_version, str) or not aggregation_rule_version:
        raise MarketDataValueError("aggregation_rule_version must be a non-empty str")
    if not isinstance(code_version, str) or not code_version:
        raise MarketDataValueError("code_version must be a non-empty str")
    payload = _refill_id_payload(plan_id, hours, aggregation_rule_version, code_version)
    return canonical.digest(payload).hex


# --- 読み取りの補助 -----------------------------------------------------------------


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MarketDataValueError(f"{label} must be a mapping, got {type(value).__name__}")
    return value


def _sequence(value: object, label: str) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        raise MarketDataValueError(f"{label} must be a list, got {type(value).__name__}")
    return value


def _keys(mapping: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    actual = frozenset(mapping)
    if actual != expected:
        raise MarketDataValueError(
            f"{label} keys {sorted(actual)} do not match the declared keys {sorted(expected)}"
        )


def _str(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise MarketDataValueError(f"{label} must be a non-empty str, got {value!r}")
    return value


def _int(value: object, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise MarketDataValueError(f"{label} must be an int >= {minimum}, got {value!r}")
    return value


def _optional_int(value: object, label: str, *, minimum: int) -> int | None:
    return None if value is None else _int(value, label, minimum=minimum)


def _optional_str(value: object, label: str) -> str | None:
    return None if value is None else _str(value, label)


def _time(value: object, label: str) -> UtcTime:
    if not isinstance(value, str):
        raise MarketDataValueError(f"{label} must be a UTC literal, got {value!r}")
    try:
        return UtcTime.parse(value)
    except KernelValueError as exc:
        raise MarketDataValueError(f"{label}: {exc}") from exc


def _optional_time(value: object, label: str) -> UtcTime | None:
    return None if value is None else _time(value, label)


def _bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise MarketDataValueError(f"{label} must be a bool, got {value!r}")
    return value


def _series_payload(series: SeriesId) -> dict[str, Any]:
    return {"series": str(series), "timeframe_version": series.timeframe.version}


# --- 時間ファイルごとの出所（D03 §14.7 の 5・§14.8）----------------------------------


@dataclass(frozen=True, slots=True)
class HourRecord:
    """補充の manifest の時間ファイル 1 本の記録（D03 §14.7 の 5・§14.8）。

    `hour`・`outcome`・`tick_digest` は `refill_id` の計算に使う（PR #58 の最小の項目）。残りは
    出所の記録で識別子に入れない。取得した時間（`FETCHED`・`FETCHED_EMPTY`）は、保管場所の
    ファイルに記録された実際の取得の出所（URL・取得時刻・HTTP の状態・応答の中身の sha256・
    試行の回数・tick の件数）と、読んだ保管場所のファイル・「保管場所から読んだ」印を持つ
    （D03 §14.5）。取得できなかった時間（`NOT_FETCHED`）は URL・理由（最後の失敗の種類と
    詳細）・試行の回数を持つ（D03 §14.8）。
    """

    hour: HourKey
    outcome: HourOutcome
    tick_digest: str | None
    tick_count: int | None
    from_archive: bool
    archive_file: str | None
    url: str
    fetched_at: UtcTime | None
    http_status: int | None
    response_sha256: str | None
    attempts: int
    failure: FailureKind | None
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.hour, HourKey):
            raise MarketDataValueError("HourRecord.hour must be an HourKey")
        if not isinstance(self.outcome, HourOutcome):
            raise MarketDataValueError("HourRecord.outcome must be an HourOutcome")
        _bool(self.from_archive, "HourRecord.from_archive")
        _str(self.url, "HourRecord.url")
        _int(self.attempts, "HourRecord.attempts", minimum=0)
        _str(self.detail, "HourRecord.detail", allow_empty=True)
        if self.failure is not None and not isinstance(self.failure, FailureKind):
            raise MarketDataValueError("HourRecord.failure must be a FailureKind or None")
        if self.http_status is not None:
            _int(self.http_status, "HourRecord.http_status", minimum=100)
        if self.outcome is HourOutcome.NOT_FETCHED:
            if self.failure is None or self.from_archive:
                raise MarketDataValueError("a NOT_FETCHED hour carries its failure (D03 §14.8)")
            if any(
                value is not None
                for value in (
                    self.tick_digest,
                    self.tick_count,
                    self.archive_file,
                    self.fetched_at,
                    self.response_sha256,
                )
            ):
                raise MarketDataValueError("a NOT_FETCHED hour has no tick, archive or response")
            return
        require_hex_digest(self.tick_digest, "HourRecord.tick_digest")
        count = _int(self.tick_count, "HourRecord.tick_count", minimum=0)
        if (count > 0) != (self.outcome is HourOutcome.FETCHED):
            raise MarketDataValueError("HourRecord.outcome does not match tick_count")
        _str(self.archive_file, "HourRecord.archive_file")
        if not isinstance(self.fetched_at, UtcTime):
            raise MarketDataValueError("a fetched hour records its fetch time")
        require_hex_digest(self.response_sha256, "HourRecord.response_sha256")
        _int(self.http_status, "HourRecord.http_status", minimum=100)
        _int(self.attempts, "HourRecord.attempts", minimum=1)
        if self.failure is not None:
            raise MarketDataValueError("a fetched hour carries no failure")

    @classmethod
    def of(cls, final: FinalResult, provenance: ArchiveProvenance | None) -> HourRecord:
        """取得記録の有効な最終結果と、保管場所のファイルに記録された取得の出所から作る。

        保管場所から読んだ時間も、出所はそのファイルに記録された同じ設定での実際の取得の
        ものを使う（写さない。D03 §14.5・§14.7 の 5）。
        """
        if final.outcome is HourOutcome.NOT_FETCHED:
            return cls(
                hour=final.hour,
                outcome=final.outcome,
                tick_digest=None,
                tick_count=None,
                from_archive=False,
                archive_file=None,
                url=_str(final.url, "FinalResult.url"),
                fetched_at=None,
                http_status=final.http_status,
                response_sha256=None,
                attempts=final.attempts,
                failure=final.failure,
                detail=final.detail,
            )
        if provenance is None:
            raise MarketDataValueError(f"{final.hour}: a fetched hour needs its archive provenance")
        if provenance.tick_digest != final.tick_digest:
            raise MarketDataValueError(
                f"{final.hour}: the archive provenance does not describe the final result"
            )
        return cls(
            hour=final.hour,
            outcome=final.outcome,
            tick_digest=final.tick_digest,
            tick_count=provenance.tick_count,
            from_archive=final.from_archive,
            archive_file=final.archive_file,
            url=provenance.url,
            fetched_at=provenance.fetched_at,
            http_status=provenance.http_status,
            response_sha256=provenance.response_sha256,
            attempts=provenance.attempts,
            failure=None,
            detail="",
        )

    @property
    def core(self) -> ManifestHour:
        """識別子の計算に使う部分（`(時間, 区分, tick の内容のダイジェスト)`）。"""
        return ManifestHour(hour=self.hour, outcome=self.outcome, tick_digest=self.tick_digest)

    def payload(self) -> Mapping[str, Any]:
        """manifest に書く形。"""
        return {
            "archive_file": self.archive_file,
            "attempts": self.attempts,
            "detail": self.detail,
            "failure": None if self.failure is None else self.failure.value,
            "fetched_at": None if self.fetched_at is None else str(self.fetched_at),
            "from_archive": self.from_archive,
            "hour": self.hour.payload(),
            "http_status": self.http_status,
            "outcome": self.outcome.value,
            "response_sha256": self.response_sha256,
            "tick_count": self.tick_count,
            "tick_digest": self.tick_digest,
            "url": self.url,
        }

    @classmethod
    def from_payload(cls, payload: object, label: str) -> HourRecord:
        """manifest から読む。"""
        mapping = _mapping(payload, label)
        _keys(
            mapping,
            frozenset(
                {
                    "archive_file",
                    "attempts",
                    "detail",
                    "failure",
                    "fetched_at",
                    "from_archive",
                    "hour",
                    "http_status",
                    "outcome",
                    "response_sha256",
                    "tick_count",
                    "tick_digest",
                    "url",
                }
            ),
            label,
        )
        try:
            outcome = HourOutcome(mapping["outcome"])
            failure = None if mapping["failure"] is None else FailureKind(mapping["failure"])
        except ValueError as exc:
            raise MarketDataValueError(f"{label}: {exc}") from exc
        tick_digest = mapping["tick_digest"]
        response = mapping["response_sha256"]
        return cls(
            hour=HourKey.from_payload(mapping["hour"], f"{label}.hour"),
            outcome=outcome,
            tick_digest=None if tick_digest is None else require_hex_digest(tick_digest, label),
            tick_count=_optional_int(mapping["tick_count"], f"{label}.tick_count", minimum=0),
            from_archive=_bool(mapping["from_archive"], f"{label}.from_archive"),
            archive_file=_optional_str(mapping["archive_file"], f"{label}.archive_file"),
            url=_str(mapping["url"], f"{label}.url"),
            fetched_at=_optional_time(mapping["fetched_at"], f"{label}.fetched_at"),
            http_status=_optional_int(mapping["http_status"], f"{label}.http_status", minimum=100),
            response_sha256=None if response is None else require_hex_digest(response, label),
            attempts=_int(mapping["attempts"], f"{label}.attempts", minimum=0),
            failure=failure,
            detail=_str(mapping["detail"], f"{label}.detail", allow_empty=True),
        )


# --- 補充分のファイル（D03 §14.11）----------------------------------------------------


@dataclass(frozen=True, slots=True)
class RefillFileRecord:
    """補充分のディレクトリにある `refill_manifest.json` 以外のファイル 1 つの記録。

    補充した足のファイルは系列（`(銘柄, 時間足)`。受入れが系列に対応させる記録。D03 §4 の
    v1.15 の追記）と行数（見出しを除く足の本数）を持つ。`validation.json` は系列も行数も
    持たない（`None`）。
    """

    name: str
    sha256: str
    series: SeriesId | None
    rows: int | None

    def __post_init__(self) -> None:
        _str(self.name, "RefillFileRecord.name")
        if "/" in self.name or self.name in (".", ".."):
            raise MarketDataValueError(f"RefillFileRecord.name must be a plain name: {self.name!r}")
        require_hex_digest(self.sha256, "RefillFileRecord.sha256")
        if self.name == REFILL_MANIFEST_FILE:
            raise MarketDataValueError("the manifest does not record itself")
        if self.name == REFILL_VALIDATION_FILE:
            if self.series is not None or self.rows is not None:
                raise MarketDataValueError("validation.json has no series and no rows")
            return
        if not isinstance(self.series, SeriesId):
            raise MarketDataValueError(f"{self.name}: a bar file records its series")
        if self.name != bar_file_name(self.series):
            raise MarketDataValueError(
                f"{self.name} is not the bar file name of {self.series}"
                f" ({bar_file_name(self.series)})"
            )
        _int(self.rows, f"{self.name}.rows", minimum=1)

    @property
    def is_bar_file(self) -> bool:
        """補充した足のファイルか。"""
        return self.series is not None

    def payload(self) -> Mapping[str, Any]:
        """manifest に書く形。"""
        base: dict[str, Any] = {"name": self.name, "rows": self.rows, "sha256": self.sha256}
        if self.series is None:
            base.update({"series": None, "timeframe_version": None})
        else:
            base.update(_series_payload(self.series))
        return base

    @classmethod
    def from_payload(cls, payload: object, label: str) -> RefillFileRecord:
        """manifest から読む。"""
        mapping = _mapping(payload, label)
        _keys(mapping, frozenset({"name", "rows", "series", "sha256", "timeframe_version"}), label)
        series = (
            None
            if mapping["series"] is None
            else series_from_record(mapping["series"], mapping["timeframe_version"], label)
        )
        if series is None and mapping["timeframe_version"] is not None:
            raise MarketDataValueError(f"{label}: a timeframe version without a series")
        return cls(
            name=_str(mapping["name"], f"{label}.name"),
            sha256=require_hex_digest(mapping["sha256"], f"{label}.sha256"),
            series=series,
            rows=_optional_int(mapping["rows"], f"{label}.rows", minimum=1),
        )


@dataclass(frozen=True, slots=True)
class SeriesCount:
    """系列ごとの対象足の本数・補充した本数・作らなかった本数（D03 §14.11・§14.15 の 2）。"""

    series: SeriesId
    targets: int
    built: int
    not_built: int

    def __post_init__(self) -> None:
        if not isinstance(self.series, SeriesId):
            raise MarketDataValueError("SeriesCount.series must be a SeriesId")
        _int(self.targets, "SeriesCount.targets", minimum=1)
        _int(self.built, "SeriesCount.built", minimum=0)
        _int(self.not_built, "SeriesCount.not_built", minimum=0)
        if self.built + self.not_built != self.targets:
            raise MarketDataValueError(
                f"{self.series}: built + not built must equal the target bars"
            )

    def payload(self) -> Mapping[str, Any]:
        """manifest に書く形。"""
        return {
            **_series_payload(self.series),
            "built": self.built,
            "not_built": self.not_built,
            "targets": self.targets,
        }

    @classmethod
    def from_payload(cls, payload: object, label: str) -> SeriesCount:
        """manifest から読む。"""
        mapping = _mapping(payload, label)
        _keys(
            mapping,
            frozenset({"built", "not_built", "series", "targets", "timeframe_version"}),
            label,
        )
        return cls(
            series=series_from_record(mapping["series"], mapping["timeframe_version"], label),
            targets=_int(mapping["targets"], f"{label}.targets", minimum=1),
            built=_int(mapping["built"], f"{label}.built", minimum=0),
            not_built=_int(mapping["not_built"], f"{label}.not_built", minimum=0),
        )


def not_built_payload(item: NotBuiltBar) -> Mapping[str, Any]:
    """作らなかった対象足 1 本の記録の形（manifest と取得記録の検証の行）。"""
    return {
        **_series_payload(item.series),
        "detail": item.detail,
        "reason": item.reason.value,
        "start": str(item.start),
    }


def not_built_from_payload(payload: object, label: str) -> NotBuiltBar:
    """作らなかった対象足 1 本の記録を読む。"""
    mapping = _mapping(payload, label)
    _keys(mapping, frozenset({"detail", "reason", "series", "start", "timeframe_version"}), label)
    try:
        reason = NotBuiltReason(mapping["reason"])
    except ValueError as exc:
        raise MarketDataValueError(f"{label}: {exc}") from exc
    return NotBuiltBar(
        series=series_from_record(mapping["series"], mapping["timeframe_version"], label),
        start=_time(mapping["start"], f"{label}.start"),
        reason=reason,
        detail=_str(mapping["detail"], f"{label}.detail", allow_empty=True),
    )


def unreconciled_payload(item: UnreconciledChunk) -> Mapping[str, Any]:
    """未照合の塊 1 つの記録の形 `(系列, 塊の開始時刻, 対象足の数)`。"""
    return {
        **_series_payload(item.series),
        "chunk_start": str(item.chunk_start),
        "target_count": item.target_count,
    }


def unreconciled_from_payload(payload: object, label: str) -> UnreconciledChunk:
    """未照合の塊 1 つの記録を読む。"""
    mapping = _mapping(payload, label)
    _keys(mapping, frozenset({"chunk_start", "series", "target_count", "timeframe_version"}), label)
    return UnreconciledChunk(
        series=series_from_record(mapping["series"], mapping["timeframe_version"], label),
        chunk_start=_time(mapping["chunk_start"], f"{label}.chunk_start"),
        target_count=_int(mapping["target_count"], f"{label}.target_count", minimum=1),
    )


def source_difference_payload(item: SourceDifferenceChunk) -> Mapping[str, Any]:
    """配信元の値の差の塊 1 つの記録の形 `(系列, 塊の開始時刻, 対象足の数)`（価格を持たない）。"""
    return {
        **_series_payload(item.series),
        "chunk_start": str(item.chunk_start),
        "target_count": item.target_count,
    }


def source_difference_from_payload(payload: object, label: str) -> SourceDifferenceChunk:
    """配信元の値の差の塊 1 つの記録を読む。"""
    mapping = _mapping(payload, label)
    _keys(mapping, frozenset({"chunk_start", "series", "target_count", "timeframe_version"}), label)
    return SourceDifferenceChunk(
        series=series_from_record(mapping["series"], mapping["timeframe_version"], label),
        chunk_start=_time(mapping["chunk_start"], f"{label}.chunk_start"),
        target_count=_int(mapping["target_count"], f"{label}.target_count", minimum=1),
    )


_SOURCE_DIFFERENCE_BAR_KEYS: Final = frozenset(
    {"built", "differences", "hour", "kind", "series", "start", "timeframe_version"}
)
_OHLC_NAMES: Final = frozenset({"open", "high", "low", "close"})


def _source_difference_bar_from_payload(
    payload: object, label: str
) -> tuple[SeriesId, UtcTime, tuple[tuple[str, Decimal], ...]]:
    """`validation.json` の配信元の値の差に当たる不一致の足 1 本の記録を検査して読む（D03 §14.7）。

    書き手は `refill_finalize._mismatch_entry`。区分は配信元の値の差だけで、tick から作った足
    （`built` が真）の丸めた後の差（四本値の名前と 0 でない差）を 1 つ以上持つ。
    """
    mapping = _mapping(payload, label)
    _keys(mapping, _SOURCE_DIFFERENCE_BAR_KEYS, label)
    series = series_from_record(mapping["series"], mapping["timeframe_version"], label)
    start = _time(mapping["start"], f"{label}.start")
    hour = _str(mapping["hour"], f"{label}.hour")
    expected_hour = (
        f"{series.symbol}@{UtcTime(start.value.replace(minute=0, second=0, microsecond=0))}"
    )
    if hour != expected_hour:
        raise MarketDataValueError(f"{label}.hour must be {expected_hour!r}, got {hour!r}")
    if mapping["kind"] != "SOURCE_DIFFERENCE":
        raise MarketDataValueError(f"{label}.kind must be 'SOURCE_DIFFERENCE'")
    if _bool(mapping["built"], f"{label}.built") is not True:
        raise MarketDataValueError(f"{label}.built must be true for a source difference")
    differences: list[tuple[str, Decimal]] = []
    for index, entry in enumerate(_sequence(mapping["differences"], f"{label}.differences")):
        at = f"{label}.differences[{index}]"
        pair = _sequence(entry, at)
        if len(pair) != 2 or pair[0] not in _OHLC_NAMES:
            raise MarketDataValueError(f"{at} must be [open|high|low|close, difference]")
        value = _optional_decimal(pair[1], f"{at}[1]")
        if value is None or value == 0:
            raise MarketDataValueError(f"{at}[1] must be a non-zero difference")
        differences.append((str(pair[0]), value))
    if not differences:
        raise MarketDataValueError(f"{label}.differences must not be empty")
    return series, start, tuple(differences)


_EVIDENCE_KEYS: Final = frozenset(
    {
        "chunk_start",
        "max_difference",
        "max_difference_pips",
        "mismatch_count",
        "series",
        "target_count",
        "timeframe_version",
    }
)


def source_difference_evidence_payload(item: SourceDifferenceEvidence) -> Mapping[str, Any]:
    """配信元の値の差の塊と理由（不一致の足の数・差の最大の価格と pip）の記録の形。

    検証記録 `validation.json` と取得記録の検証の結果の行に書く（D03 §14.7 の v1.19・§14.15 の 2）。
    """
    return {
        **source_difference_payload(item.chunk),
        "max_difference": format(item.max_difference, "f"),
        "max_difference_pips": format(item.max_difference_pips, "f"),
        "mismatch_count": item.mismatch_count,
    }


def source_difference_evidence_from_payload(
    payload: object, label: str
) -> SourceDifferenceEvidence:
    """配信元の値の差の塊と理由の記録を読む。"""
    mapping = _mapping(payload, label)
    _keys(mapping, _EVIDENCE_KEYS, label)
    chunk = source_difference_from_payload(
        {
            key: mapping[key]
            for key in ("chunk_start", "series", "target_count", "timeframe_version")
        },
        label,
    )
    largest = _optional_decimal(mapping["max_difference"], f"{label}.max_difference")
    largest_pips = _optional_decimal(mapping["max_difference_pips"], f"{label}.max_difference_pips")
    if largest is None or largest_pips is None:
        raise MarketDataValueError(f"{label}: the largest difference is required")
    return SourceDifferenceEvidence(
        chunk=chunk,
        mismatch_count=_int(mapping["mismatch_count"], f"{label}.mismatch_count", minimum=1),
        max_difference=largest,
        max_difference_pips=largest_pips,
    )


# --- 補充の manifest（D03 §14.11）-----------------------------------------------------

_MANIFEST_KEYS: Final = frozenset(
    {
        "aggregation_rule_version",
        "calendar",
        "code_version",
        "created_at",
        "files",
        "format",
        "hours",
        "not_built",
        "plan",
        "plan_id",
        "provider",
        "refill_id",
        "series_counts",
        "snapshot_id",
        "unreconciled",
    }
)
_MANIFEST_KEYS_V2: Final = _MANIFEST_KEYS | {"source_differences"}


@dataclass(frozen=True, slots=True)
class RefillManifest:
    """補充の manifest `refill_manifest.json`（D03 §14.11）。

    構築時に次を確かめる（合わなければ `MarketDataValueError`。読み手は補充分の食い違い
    `RefillStoreInconsistent` にする。D03 §14.11.1 の W5・W6）:

    - 計画の中身から計算し直した `plan_id` が記録と一致する。
    - 時間ファイルの記録が計画の時間ファイルの列とちょうど同じ（`(銘柄, 時刻)` の順）。
    - 時間ファイルの区分と tick の内容のダイジェスト・集約規則の版・変換コード版から計算し
      直した `refill_id` が記録と一致する。
    - 補充した足のファイルが 1 つ以上あり、`validation.json` がちょうど 1 つある。足の
      ファイルの系列はすべて計画の対象足の系列で、系列ごとの本数（`series_counts`）と行数が
      合う。作らなかった足と未照合の塊は計画の対象足の系列のもの。
    """

    refill_id: str
    plan_id: str
    plan: RefillPlan
    aggregation_rule_version: str
    code_version: str
    hours: tuple[HourRecord, ...]
    files: tuple[RefillFileRecord, ...]
    series_counts: tuple[SeriesCount, ...]
    not_built: tuple[NotBuiltBar, ...]
    unreconciled: tuple[UnreconciledChunk, ...]
    created_at: UtcTime
    #: 配信元の値の差の塊ごとの `(系列, 塊の開始時刻, 対象足の数)`（D03 §14.7 の v1.19・§14.11）。
    source_differences: tuple[SourceDifferenceChunk, ...] = ()

    def __post_init__(self) -> None:
        require_hex_digest(self.refill_id, "RefillManifest.refill_id")
        require_hex_digest(self.plan_id, "RefillManifest.plan_id")
        if not isinstance(self.plan, RefillPlan):
            raise MarketDataValueError("RefillManifest.plan must be a RefillPlan")
        _str(self.aggregation_rule_version, "RefillManifest.aggregation_rule_version")
        _str(self.code_version, "RefillManifest.code_version")
        if not isinstance(self.created_at, UtcTime):
            raise MarketDataValueError("RefillManifest.created_at must be a UtcTime")
        if self.plan.plan_id() != self.plan_id:
            raise MarketDataValueError(
                f"the plan recorded in the manifest is the plan {self.plan.plan_id()},"
                f" not {self.plan_id} (D03 §14.11.1 W5)"
            )
        if tuple(item.hour for item in self.hours) != tuple(
            sorted(self.plan.hour_keys, key=lambda key: key.sort_key())
        ):
            raise MarketDataValueError(
                "the manifest's hour files differ from the plan's hour files (D03 §14.11.1 W5)"
            )
        recomputed = refill_id_of(
            self.plan_id,
            [item.core for item in self.hours],
            self.aggregation_rule_version,
            self.code_version,
        )
        if recomputed != self.refill_id:
            raise MarketDataValueError(
                f"the manifest's hour results, rule version and code version give the refill id"
                f" {recomputed}, not {self.refill_id} (D03 §14.10, §14.11.1 W5)"
            )
        names = [item.name for item in self.files]
        if names != sorted(names) or len(set(names)) != len(names):
            raise MarketDataValueError("RefillManifest.files must be sorted by name, without twins")
        if sum(1 for item in self.files if item.name == REFILL_VALIDATION_FILE) != 1:
            raise MarketDataValueError("the manifest records exactly one validation.json")
        bar_files = [item for item in self.files if item.is_bar_file]
        if not bar_files:
            raise MarketDataValueError("an empty refill is not written (D03 §14.7)")
        target_series = {bar.series for bar in self.plan.target_bars}
        counts = {count.series: count for count in self.series_counts}
        if set(counts) != target_series or len(counts) != len(self.series_counts):
            raise MarketDataValueError(
                "RefillManifest.series_counts must list each target series exactly once"
            )
        for series, count in counts.items():
            targets = sum(1 for bar in self.plan.target_bars if bar.series == series)
            if count.targets != targets:
                raise MarketDataValueError(f"{series}: the target count differs from the plan")
            not_built = sum(1 for item in self.not_built if item.series == series)
            if count.not_built != not_built:
                raise MarketDataValueError(f"{series}: the not-built count differs from its list")
        for item in bar_files:
            assert item.series is not None  # is_bar_file
            listed = counts.get(item.series)
            if listed is None or listed.built != item.rows:
                raise MarketDataValueError(
                    f"{item.name}: its rows do not match the built bars of {item.series}"
                )
        written = {item.series for item in bar_files}
        for series, count in counts.items():
            if count.built and series not in written:
                raise MarketDataValueError(f"{series}: built bars without a bar file")
        # 作らなかった足と未照合の塊の開始は、計画の対象足を 1 回ずつだけ指す（D03 §14.11 の
        # 「作らなかった対象足」。系列だけでなく時刻まで照らす。報告が足ごとの理由に使うため）。
        target_keys = {(bar.series, bar.start) for bar in self.plan.target_bars}
        for label, keys in (
            ("not_built", [(entry.series, entry.start) for entry in self.not_built]),
            ("unreconciled", [(entry.series, entry.chunk_start) for entry in self.unreconciled]),
            (
                "source_differences",
                [(entry.series, entry.chunk_start) for entry in self.source_differences],
            ),
        ):
            seen: set[tuple[SeriesId, UtcTime]] = set()
            for series, start in keys:
                if (series, start) not in target_keys:
                    raise MarketDataValueError(
                        f"RefillManifest.{label}: {series} {start} is not a target bar of the plan"
                        " (D03 §14.11)"
                    )
                if (series, start) in seen:
                    raise MarketDataValueError(
                        f"RefillManifest.{label}: {series} {start} is listed twice (D03 §14.11)"
                    )
                seen.add((series, start))

    @property
    def bar_files(self) -> tuple[RefillFileRecord, ...]:
        """補充した足のファイルの記録（名前順）。"""
        return tuple(item for item in self.files if item.is_bar_file)

    @property
    def snapshot_id(self) -> str:
        """入力の snapshot の識別子（計画の中身から）。"""
        return self.plan.snapshot_id

    def payload(self) -> Mapping[str, Any]:
        """`refill_manifest.json` に書く形。価格は持たない。"""
        return {
            "aggregation_rule_version": self.aggregation_rule_version,
            "calendar": {"id": self.plan.calendar.id, "version": self.plan.calendar.version},
            "code_version": self.code_version,
            "created_at": str(self.created_at),
            "files": [item.payload() for item in self.files],
            "format": REFILL_MANIFEST_FORMAT,
            "hours": [item.payload() for item in self.hours],
            "not_built": [not_built_payload(item) for item in self.not_built],
            "plan": self.plan.identity_payload(),
            "plan_id": self.plan_id,
            "provider": {
                "id": self.plan.provider.settings.id,
                "version": self.plan.provider.settings.version,
            },
            "refill_id": self.refill_id,
            "series_counts": [item.payload() for item in self.series_counts],
            "snapshot_id": self.plan.snapshot_id,
            "source_differences": [
                source_difference_payload(item) for item in self.source_differences
            ],
            "unreconciled": [unreconciled_payload(item) for item in self.unreconciled],
        }

    @classmethod
    def from_payload(cls, payload: object) -> RefillManifest:
        """`refill_manifest.json` の内容から読む。形が違えば `MarketDataValueError`。

        形式 v1（v1.19 より前の書き出し。配信元の値の差の鍵を持たない）も読む。
        """
        label = REFILL_MANIFEST_FILE
        mapping = _mapping(payload, label)
        if mapping.get("format") == _REFILL_MANIFEST_FORMAT_V1:
            _keys(mapping, _MANIFEST_KEYS, label)
        else:
            _keys(mapping, _MANIFEST_KEYS_V2, label)
            if mapping["format"] != REFILL_MANIFEST_FORMAT:
                raise MarketDataValueError(
                    f"{label}.format must be {REFILL_MANIFEST_FORMAT!r} (or"
                    f" {_REFILL_MANIFEST_FORMAT_V1!r}), got {mapping['format']!r}"
                )
        plan = RefillPlan.from_payload(mapping["plan"])
        calendar = _mapping(mapping["calendar"], f"{label}.calendar")
        provider = _mapping(mapping["provider"], f"{label}.provider")
        _keys(calendar, frozenset({"id", "version"}), f"{label}.calendar")
        _keys(provider, frozenset({"id", "version"}), f"{label}.provider")
        if (calendar["id"], calendar["version"]) != (plan.calendar.id, plan.calendar.version):
            raise MarketDataValueError(f"{label}.calendar differs from the plan's calendar")
        if (provider["id"], provider["version"]) != (
            plan.provider.settings.id,
            plan.provider.settings.version,
        ):
            raise MarketDataValueError(f"{label}.provider differs from the plan's provider")
        if mapping["snapshot_id"] != plan.snapshot_id:
            raise MarketDataValueError(f"{label}.snapshot_id differs from the plan's snapshot")
        return cls(
            refill_id=require_hex_digest(mapping["refill_id"], f"{label}.refill_id"),
            plan_id=require_hex_digest(mapping["plan_id"], f"{label}.plan_id"),
            plan=plan,
            aggregation_rule_version=_str(
                mapping["aggregation_rule_version"], f"{label}.aggregation_rule_version"
            ),
            code_version=_str(mapping["code_version"], f"{label}.code_version"),
            hours=tuple(
                HourRecord.from_payload(item, f"{label}.hours[{index}]")
                for index, item in enumerate(_sequence(mapping["hours"], f"{label}.hours"))
            ),
            files=tuple(
                RefillFileRecord.from_payload(item, f"{label}.files[{index}]")
                for index, item in enumerate(_sequence(mapping["files"], f"{label}.files"))
            ),
            series_counts=tuple(
                SeriesCount.from_payload(item, f"{label}.series_counts[{index}]")
                for index, item in enumerate(
                    _sequence(mapping["series_counts"], f"{label}.series_counts")
                )
            ),
            not_built=tuple(
                not_built_from_payload(item, f"{label}.not_built[{index}]")
                for index, item in enumerate(_sequence(mapping["not_built"], f"{label}.not_built"))
            ),
            unreconciled=tuple(
                unreconciled_from_payload(item, f"{label}.unreconciled[{index}]")
                for index, item in enumerate(
                    _sequence(mapping["unreconciled"], f"{label}.unreconciled")
                )
            ),
            created_at=_time(mapping["created_at"], f"{label}.created_at"),
            source_differences=tuple(
                source_difference_from_payload(item, f"{label}.source_differences[{index}]")
                for index, item in enumerate(
                    _sequence(mapping.get("source_differences", []), f"{label}.source_differences")
                )
            ),
        )


# --- 検証記録（D03 §14.7「記録するもの」・§14.11。報告 §14.15 の 2 が読む）------------------

_VALIDATION_KEYS: Final = frozenset(
    {
        "bid_above_ask_tick_count",
        "format",
        "hourly_consistency",
        "matched_count",
        "neighbors",
        "out_of_range_tick_count",
        "passed",
        "plan_id",
        "reconciled_count",
        "refill_id",
        "unreconciled",
    }
)
_VALIDATION_KEYS_V2: Final = _VALIDATION_KEYS | {
    "rounding_matched_count",
    "source_difference_bars",
    "source_differences",
}
_NEIGHBOR_KEYS: Final = frozenset(
    {
        "chunk_end",
        "chunk_start",
        "crosses_closure",
        "difference",
        "difference_pips",
        "needs_review",
        "neighbor_start",
        "series",
        "side",
        "status",
        "target_count",
        "timeframe_version",
    }
)


def _optional_decimal(value: object, label: str) -> Decimal | None:
    if value is None:
        return None
    try:
        return decimal_from_str(_str(value, label))
    except KernelValueError as exc:
        raise MarketDataValueError(f"{label}: {exc}") from exc


def _neighbor_from_payload(payload: object, label: str) -> NeighborCheck:
    mapping = _mapping(payload, label)
    _keys(mapping, _NEIGHBOR_KEYS, label)
    try:
        chunk = Interval(
            start=_time(mapping["chunk_start"], f"{label}.chunk_start"),
            end=_time(mapping["chunk_end"], f"{label}.chunk_end"),
        )
        side = NeighborSide(mapping["side"])
        status = NeighborStatus(mapping["status"])
    except (KernelValueError, ValueError) as exc:
        raise MarketDataValueError(f"{label}: {exc}") from exc
    crosses = mapping["crosses_closure"]
    return NeighborCheck(
        series=series_from_record(mapping["series"], mapping["timeframe_version"], label),
        chunk=chunk,
        target_count=_int(mapping["target_count"], f"{label}.target_count", minimum=1),
        side=side,
        status=status,
        neighbor_start=_optional_time(mapping["neighbor_start"], f"{label}.neighbor_start"),
        crosses_closure=None if crosses is None else _bool(crosses, f"{label}.crosses_closure"),
        difference=_optional_decimal(mapping["difference"], f"{label}.difference"),
        difference_pips=_optional_decimal(mapping["difference_pips"], f"{label}.difference_pips"),
        needs_review=_bool(mapping["needs_review"], f"{label}.needs_review"),
    )


def _hourly_from_payload(payload: object, label: str) -> HourlyConsistency:
    mapping = _mapping(payload, label)
    _keys(mapping, frozenset({"consistent", "hour"}), label)
    return HourlyConsistency(
        hour=HourKey.from_payload(mapping["hour"], f"{label}.hour"),
        consistent=_bool(mapping["consistent"], f"{label}.consistent"),
    )


@dataclass(frozen=True, slots=True)
class RefillValidationRecord:
    """合格した補充分の検証記録 `validation.json` を読んだもの（D03 §14.7・§14.11）。

    書き手は `refill_finalize.validation_payload`。読むのは報告（D03 §14.15 の 2「検証 5 点の
    結果の要約」）。形（鍵の集合・型・語彙）が書き手の形と違えば `MarketDataValueError`
    （読み手は補充分の食い違い `RefillStoreInconsistent` にする。D03 §14.11.1 の W5・W6）。
    """

    refill_id: str
    plan_id: str
    reconciled_count: int
    matched_count: int
    out_of_range_tick_count: int
    bid_above_ask_tick_count: int
    neighbors: tuple[NeighborCheck, ...]
    hourly_consistency: tuple[HourlyConsistency, ...]
    unreconciled: tuple[UnreconciledChunk, ...]
    #: 丸めで初めて一致した照合用の足の数（v1.19。形式 v1 の記録には無いので `None`）。
    rounding_matched_count: int | None = None
    #: 配信元の値の差の塊と理由（v1.19。形式 v1 の記録は 0 件）。
    source_differences: tuple[SourceDifferenceEvidence, ...] = ()

    def __post_init__(self) -> None:
        require_hex_digest(self.refill_id, "RefillValidationRecord.refill_id")
        require_hex_digest(self.plan_id, "RefillValidationRecord.plan_id")
        _int(self.reconciled_count, "RefillValidationRecord.reconciled_count", minimum=0)
        _int(self.matched_count, "RefillValidationRecord.matched_count", minimum=0)
        if self.matched_count > self.reconciled_count:
            raise MarketDataValueError("RefillValidationRecord: more matched than reconciled")
        _int(self.out_of_range_tick_count, "RefillValidationRecord.out_of_range", minimum=0)
        _int(self.bid_above_ask_tick_count, "RefillValidationRecord.bid_above_ask", minimum=0)

    @classmethod
    def from_payload(cls, payload: object) -> RefillValidationRecord:
        """`validation.json` の内容から読む。形が違えば `MarketDataValueError`。"""
        label = REFILL_VALIDATION_FILE
        mapping = _mapping(payload, label)
        legacy = mapping.get("format") == _REFILL_VALIDATION_FORMAT_V1
        _keys(mapping, _VALIDATION_KEYS if legacy else _VALIDATION_KEYS_V2, label)
        if not legacy and mapping["format"] != REFILL_VALIDATION_FORMAT:
            raise MarketDataValueError(
                f"{label}.format must be {REFILL_VALIDATION_FORMAT!r} (or"
                f" {_REFILL_VALIDATION_FORMAT_V1!r}), got {mapping['format']!r}"
            )
        if not legacy:
            for index, item in enumerate(
                _sequence(mapping["source_difference_bars"], f"{label}.source_difference_bars")
            ):
                _source_difference_bar_from_payload(
                    item, f"{label}.source_difference_bars[{index}]"
                )
        if mapping["passed"] is not True:
            raise MarketDataValueError(f"{label}.passed must be true (only a passed refill has it)")

        def items(key: str) -> list[tuple[str, Any]]:
            return [
                (f"{label}.{key}[{index}]", item)
                for index, item in enumerate(_sequence(mapping[key], f"{label}.{key}"))
            ]

        return cls(
            refill_id=require_hex_digest(mapping["refill_id"], f"{label}.refill_id"),
            plan_id=require_hex_digest(mapping["plan_id"], f"{label}.plan_id"),
            reconciled_count=_int(
                mapping["reconciled_count"], f"{label}.reconciled_count", minimum=0
            ),
            matched_count=_int(mapping["matched_count"], f"{label}.matched_count", minimum=0),
            out_of_range_tick_count=_int(
                mapping["out_of_range_tick_count"], f"{label}.out_of_range_tick_count", minimum=0
            ),
            bid_above_ask_tick_count=_int(
                mapping["bid_above_ask_tick_count"], f"{label}.bid_above_ask_tick_count", minimum=0
            ),
            neighbors=tuple(_neighbor_from_payload(item, at) for at, item in items("neighbors")),
            hourly_consistency=tuple(
                _hourly_from_payload(item, at) for at, item in items("hourly_consistency")
            ),
            unreconciled=tuple(
                unreconciled_from_payload(item, at) for at, item in items("unreconciled")
            ),
            rounding_matched_count=None
            if legacy
            else _int(
                mapping["rounding_matched_count"], f"{label}.rounding_matched_count", minimum=0
            ),
            source_differences=()
            if legacy
            else tuple(
                source_difference_evidence_from_payload(item, at)
                for at, item in items("source_differences")
            ),
        )
