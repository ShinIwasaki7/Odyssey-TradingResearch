"""補充分の書き出し（D03 §14.7・§14.10・§14.11・§14.11.1・§14.12 の出来事10・11）。

取得を終えた計画について、保管場所の tick から検証 5 点を行い、合格なら補充分
`data/raw/market/refill/<refill_id>/` を書き出す。不合格なら何も書き出さず、検証の結果を
取得記録 `journal.jsonl` に 1 行追記する。

本モジュールは通信を持たず、ファイルは `RefillStore` 越しにだけ読み書きする（D01 §4 v2.9）。
原データの読込とカレンダーの組み立ては呼び出し側（`app.composition`）が渡す関数が行う。
規則（状態の判定・検算・識別子の計算・書き出す内容）は本モジュールにある。

**書き出しの規約**（D03 §14.11.1 の W1〜W6）: 計画のロックを取ってから状態を判定する（W1）。
状態の判定の前に、書きかけの補充分・`plan.json`・取得記録・最終結果が指す保管場所のもの・
この計画の補充分の manifest を検算する（W5）。補充分のディレクトリを排他的に作り（W2）、
補充した足のファイルと `validation.json` を置いてから、完成の印 `refill_manifest.json` を最後に
置く（W4）。書いた直後に補充分を検算し直す（W5）。

**受入れが補充分を読む前の検算**（D03 §14.11・§14.11.1 の W5）も本モジュールに置く
（`verify_refill_directory`・`require_refill_set`）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Any, Final, Protocol

from odyssey_fx.common import canonical
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.application.ports import RefillFileStat, RefillStore
from odyssey_fx.marketdata.application.refill_fetch import (
    PlanState,
    check_finals,
    derive_state,
    effective_finals,
    read_journal,
    verify_archive,
)
from odyssey_fx.marketdata.application.refill_plan import (
    RawBarIndex,
    finalized_refills,
    require_consistent_refills,
    require_plan_matches,
)
from odyssey_fx.marketdata.application.refill_validation import HourData, validate_refill
from odyssey_fx.marketdata.domain.access import AccessBoundaries
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.errors import (
    MarketDataValueError,
    RefillAlreadyFinalized,
    RefillChainIncomplete,
    RefillNotFetched,
    RefillPlanDuplicated,
    RefillPlanNotFound,
    RefillStoreInconsistent,
)
from odyssey_fx.marketdata.domain.refill import (
    HourKey,
    HourOutcome,
    ProviderSymbol,
    RefillPlan,
    ValidationRecord,
    sha256_hex,
)
from odyssey_fx.marketdata.domain.refill_manifest import (
    REFILL_AGGREGATION_RULE_VERSION,
    REFILL_MANIFEST_FILE,
    REFILL_VALIDATION_FILE,
    REFILL_VALIDATION_FORMAT,
    HourRecord,
    RefillFileRecord,
    RefillManifest,
    SeriesCount,
    bar_file_name,
    not_built_payload,
    refill_id_of,
    unreconciled_payload,
)
from odyssey_fx.marketdata.domain.refill_validation import RefillValidation
from odyssey_fx.marketdata.domain.series import SeriesId

__all__ = [
    "REFILL_SOURCE_ROOT",
    "FinalizeInputs",
    "FinalizeReport",
    "RefillOutput",
    "bars_csv",
    "build_refill_output",
    "decimal_text",
    "failure_details",
    "finalize_plan",
    "refill_ids_in_sources",
    "require_refill_set",
    "validation_payload",
    "verify_refill_directory",
]

#: 補充分の置き場（リポジトリからの相対。D03 §14.11）。snapshot の `sources` の `path` も
#: この下を指す。
REFILL_SOURCE_ROOT: Final = "data/raw/market/refill"

#: 補充した足のファイルの見出し（原データと同じ列。先頭列は無名。D03 §2・§14.11）。
_CSV_HEADER: Final = ",open,high,low,close,volume,source\n"

#: 補充した足の出来高（「出来高不明」を意味する。D03 §14.6）と出所の値（D03 §14.18 の 5）。
_UNKNOWN_VOLUME: Final = "0"
_REFILL_SOURCE_VALUE: Final = "dukascopy_refill"


def decimal_text(value: Decimal) -> str:
    """Decimal を指数表記なしの文字列にする（記録を人が読むため。浮動小数を経由しない）。"""
    return format(value, "f")


# --- 書き出す内容（規則）----------------------------------------------------------------


def bars_csv(bars: Sequence[Bar], quote: ProviderSymbol) -> bytes:
    """補充した足のファイルの中身（D03 §14.11）。

    列・時刻の書式は原データと同じ（先頭列は無名の足の開始時刻 `YYYY-MM-DD HH:MM:SS+00:00`）。
    価格は銘柄の桁の分だけの小数の文字列、出来高は `0`（出来高不明）、出所は
    `dukascopy_refill`。行は時刻順。
    """
    exponent = quote.price_exponent
    if exponent is None:  # pragma: no cover - ProviderSymbol の構築時に拒否している
        raise MarketDataValueError("price_scale must be a power of 10")
    lines = [_CSV_HEADER]
    for bar in sorted(bars, key=lambda item: item.bar_start.value):
        if bar.series.symbol != quote.symbol:
            raise MarketDataValueError(f"{bar.series} written with the scale of {quote.symbol}")
        moment = bar.bar_start.value
        prices = [
            format(price.value, f".{exponent}f")
            for price in (bar.open, bar.high, bar.low, bar.close)
        ]
        lines.append(
            f"{moment:%Y-%m-%d %H:%M:%S}+00:00,{','.join(prices)},"
            f"{_UNKNOWN_VOLUME},{_REFILL_SOURCE_VALUE}\n"
        )
    return "".join(lines).encode("utf-8")


def _series_entry(series: SeriesId) -> dict[str, Any]:
    return {"series": str(series), "timeframe_version": series.timeframe.version}


def failure_details(validation: RefillValidation) -> Mapping[str, Any]:
    """不合格の取得記録の行に残す構造的な記録（D03 §14.7）。

    照合で合わなかった足と差、未照合の塊ごとの `(系列, 塊の開始時刻, 対象足の数)`、作らな
    かった対象足と理由。対象は研究履歴区分に限る（計画の対象足と比べる足が研究履歴区分の
    ものだけなので。D03 §14.4 の 4）。
    """
    return {
        "mismatches": [
            {
                **_series_entry(record.series),
                "built": record.built,
                "differences": [[name, decimal_text(value)] for name, value in record.differences],
                "hour": str(record.hour),
                "start": str(record.start),
            }
            for record in validation.reconciled
            if not record.matched
        ],
        "not_built": [not_built_payload(item) for item in validation.not_built],
        "unreconciled": [unreconciled_payload(item) for item in validation.unreconciled],
    }


def validation_payload(
    validation: RefillValidation, *, refill_id: str, plan_id: str
) -> Mapping[str, Any]:
    """検証記録 `validation.json` の内容（D03 §14.7 の「記録するもの」・§14.11）。

    照合した足の数と一致した数、範囲外の tick の数、bid が ask より大きい tick の数、前後の
    足との差（価格と pip。閾値以下も含めてすべての塊）と「要確認」の印、1時間足と 15分足
    4 本の一致、未照合の塊。合格した補充分だけが持つ（不合格は取得記録の行に残す）。
    """
    if not validation.passed:
        raise MarketDataValueError("validation.json is written only for a passed validation")
    return {
        "bid_above_ask_tick_count": validation.bid_above_ask_count,
        "format": REFILL_VALIDATION_FORMAT,
        "hourly_consistency": [
            {"consistent": item.consistent, "hour": item.hour.payload()}
            for item in validation.hourly_consistency
        ],
        "matched_count": validation.matched_count,
        "neighbors": [
            {
                **_series_entry(item.series),
                "chunk_end": str(item.chunk.end),
                "chunk_start": str(item.chunk.start),
                "crosses_closure": item.crosses_closure,
                "difference": None if item.difference is None else decimal_text(item.difference),
                "difference_pips": None
                if item.difference_pips is None
                else decimal_text(item.difference_pips),
                "needs_review": item.needs_review,
                "neighbor_start": None if item.neighbor_start is None else str(item.neighbor_start),
                "side": item.side.value,
                "status": item.status.value,
                "target_count": item.target_count,
            }
            for item in validation.neighbors
        ],
        "out_of_range_tick_count": validation.out_of_range_tick_count,
        "passed": True,
        "plan_id": plan_id,
        "reconciled_count": validation.reconciled_count,
        "refill_id": refill_id,
        "unreconciled": [unreconciled_payload(item) for item in validation.unreconciled],
    }


@dataclass(frozen=True, slots=True)
class RefillOutput:
    """書き出す補充分（D03 §14.11）。`files` は `(名前, 中身)` の列で、manifest は含まない。"""

    manifest: RefillManifest
    files: tuple[tuple[str, bytes], ...]

    @property
    def refill_id(self) -> str:
        """補充の識別子。"""
        return self.manifest.refill_id

    @property
    def manifest_bytes(self) -> bytes:
        """`refill_manifest.json` の中身（正規化エンコード）。"""
        return canonical.encode(self.manifest.payload()) + b"\n"


def build_refill_output(
    *,
    plan: RefillPlan,
    validation: RefillValidation,
    hours: Mapping[HourKey, HourData],
    code_version: str,
    created_at: UtcTime,
) -> RefillOutput:
    """合格した検証から補充分の中身を組み立てる（D03 §14.10・§14.11）。"""
    if not validation.passed:
        raise MarketDataValueError("a refill is built only from a passed validation")
    plan_id = plan.plan_id()
    records = tuple(
        HourRecord.of(hours[key].final, hours[key].provenance)
        for key in sorted(plan.hour_keys, key=lambda key: key.sort_key())
    )
    refill_id = refill_id_of(
        plan_id, [item.core for item in records], REFILL_AGGREGATION_RULE_VERSION, code_version
    )
    settings = plan.provider.settings
    by_series: dict[SeriesId, list[Bar]] = {}
    for bar in validation.built_bars:
        by_series.setdefault(bar.series, []).append(bar)
    files: list[tuple[str, bytes]] = []
    file_records: list[RefillFileRecord] = []
    for series in sorted(by_series, key=str):
        name = bar_file_name(series)
        content = bars_csv(by_series[series], settings.symbol(series.symbol))
        files.append((name, content))
        file_records.append(
            RefillFileRecord(
                name=name,
                sha256=sha256_hex(content),
                series=series,
                rows=len(by_series[series]),
            )
        )
    validation_bytes = (
        canonical.encode(validation_payload(validation, refill_id=refill_id, plan_id=plan_id))
        + b"\n"
    )
    files.append((REFILL_VALIDATION_FILE, validation_bytes))
    file_records.append(
        RefillFileRecord(
            name=REFILL_VALIDATION_FILE,
            sha256=sha256_hex(validation_bytes),
            series=None,
            rows=None,
        )
    )
    target_series = sorted({bar.series for bar in plan.target_bars}, key=str)
    counts = tuple(
        SeriesCount(
            series=series,
            targets=sum(1 for bar in plan.target_bars if bar.series == series),
            built=len(by_series.get(series, ())),
            not_built=sum(1 for item in validation.not_built if item.series == series),
        )
        for series in target_series
    )
    manifest = RefillManifest(
        refill_id=refill_id,
        plan_id=plan_id,
        plan=plan,
        aggregation_rule_version=REFILL_AGGREGATION_RULE_VERSION,
        code_version=code_version,
        hours=records,
        files=tuple(sorted(file_records, key=lambda item: item.name)),
        series_counts=counts,
        not_built=validation.not_built,
        unreconciled=validation.unreconciled,
        created_at=created_at,
    )
    return RefillOutput(manifest=manifest, files=tuple(sorted(files, key=lambda item: item[0])))


# --- 補充分の検算（W5）-------------------------------------------------------------------


def verify_refill_directory(
    name: str,
    payload: Mapping[str, Any] | None,
    stats: Sequence[RefillFileStat],
) -> RefillManifest:
    """補充分を使う前に識別子とファイルをすべて検算する（D03 §14.11.1 の W5）。

    - 完成の印 `refill_manifest.json` があり、形が読める。
    - 計画の中身から計算し直した `plan_id` が記録と一致し、時間ファイルごとの区分と内容の
      ダイジェスト・集約規則の版・変換コード版から計算し直した `refill_id` が記録と
      ディレクトリ名の両方に一致する（manifest の構築時に確かめる）。
    - ディレクトリのファイルの集合が manifest の記録と一致し、各ファイルの sha256 と、足の
      ファイルの行数（見出しを除く）が一致する。

    合わなければ `RefillStoreInconsistent`（W6）。
    """
    if payload is None:
        raise RefillStoreInconsistent(
            f"{name}/ has no {REFILL_MANIFEST_FILE} (an incomplete refill). Nothing was written;"
            " delete it after checking (D03 §14.11.1 W4・W6)"
        )
    try:
        manifest = RefillManifest.from_payload(payload)
    except MarketDataValueError as exc:
        raise RefillStoreInconsistent(
            f"{name}/{REFILL_MANIFEST_FILE} cannot be verified: {exc} (D03 §14.11.1 W5・W6)"
        ) from exc
    if manifest.refill_id != name:
        raise RefillStoreInconsistent(
            f"{name}/{REFILL_MANIFEST_FILE} describes the refill {manifest.refill_id}, not the"
            " directory name (D03 §14.11.1 W5・W6)"
        )
    present = {stat.name: stat for stat in stats}
    recorded = {item.name: item for item in manifest.files}
    if set(present) != set(recorded):
        extra = sorted(set(present) - set(recorded))
        missing = sorted(set(recorded) - set(present))
        raise RefillStoreInconsistent(
            f"{name}/ holds files that differ from its manifest (not recorded: {extra};"
            f" recorded but missing: {missing}) (D03 §14.11.1 W5・W6)"
        )
    for file_name, item in recorded.items():
        stat = present[file_name]
        if stat.sha256 != item.sha256:
            raise RefillStoreInconsistent(
                f"{name}/{file_name}: sha256 {stat.sha256} differs from the manifest's"
                f" {item.sha256} (D03 §14.11.1 W5・W6)"
            )
        if item.rows is not None and stat.newlines - 1 != item.rows:
            raise RefillStoreInconsistent(
                f"{name}/{file_name}: {stat.newlines - 1} rows differ from the manifest's"
                f" {item.rows} (D03 §14.11.1 W5・W6)"
            )
    return manifest


def refill_ids_in_sources(paths: Sequence[str]) -> tuple[str, ...]:
    """snapshot の `sources` のパスのうち、補充分を指すものの `refill_id`（整列・重複なし）。"""
    root = PurePosixPath(REFILL_SOURCE_ROOT).parts
    found: set[str] = set()
    for path in paths:
        parts = PurePosixPath(path).parts
        if len(parts) > len(root) + 1 and parts[: len(root)] == root:
            found.add(parts[len(root)])
    return tuple(sorted(found))


def require_refill_set(
    manifests: Sequence[RefillManifest],
    input_sources: Mapping[str, Sequence[str]],
) -> None:
    """受入れに渡した補充分の集合を確かめる（D03 §14.11）。

    - 同じ `plan_id` の補充分を 2 つ以上渡していない（`RefillPlanDuplicated`）。
    - 各補充分の入力 snapshot の `sources` に現れる補充分（補充を重ねた前の補充分）がすべて
      渡されている（`RefillChainIncomplete`。欠けた補充分を列挙する。自動では引き継がない）。

    `input_sources` は入力 snapshot の識別子から、その manifest の `sources` のパスの列。
    """
    by_plan: dict[str, list[str]] = {}
    for manifest in manifests:
        by_plan.setdefault(manifest.plan_id, []).append(manifest.refill_id)
    duplicated = {plan: ids for plan, ids in by_plan.items() if len(ids) > 1}
    if duplicated:
        listed = "; ".join(
            f"plan {plan}: {', '.join(sorted(ids))}" for plan, ids in duplicated.items()
        )
        raise RefillPlanDuplicated(
            f"two or more refills of the same plan were given ({listed}); pass only one of them"
            " (D03 §14.11, §14.12). Nothing was written"
        )
    given = {manifest.refill_id for manifest in manifests}
    missing: dict[str, list[str]] = {}
    for manifest in manifests:
        sources = input_sources.get(manifest.snapshot_id)
        if sources is None:
            raise RefillChainIncomplete(
                f"the input snapshot {manifest.snapshot_id} of the refill {manifest.refill_id}"
                " cannot be read, so the refills it contains cannot be checked (D03 §14.11)."
                " Nothing was written"
            )
        for refill_id in refill_ids_in_sources(sources):
            if refill_id not in given:
                missing.setdefault(refill_id, []).append(manifest.refill_id)
    if missing:
        listed = "; ".join(
            f"{refill_id} (contained in the input of {', '.join(sorted(users))})"
            for refill_id, users in sorted(missing.items())
        )
        raise RefillChainIncomplete(
            f"the refills contained in the input snapshots are not all given: {listed}."
            " Pass them with --refill as well (D03 §14.11). Nothing was written"
        )


# --- 書き出し（D03 §14.12 の出来事10・11）------------------------------------------------


class FinalizeInputs(Protocol):
    """書き出しが計画から読むもの（原データとカレンダー）。`app.composition` が実装する。"""

    def raw_bars(
        self, plan: RefillPlan
    ) -> tuple[tuple[SeriesId, ...], Mapping[SeriesId, Sequence[Bar]]]:
        """計画の入力 snapshot の原系列と、対象足のある銘柄の原データの足を返す。

        原データは読む前に sha256 と行数を snapshot の `sources` と照合する（D03 §14.4）。
        """
        ...

    def calendar(self, plan: RefillPlan) -> TradingCalendar:
        """計画が記録したカレンダーの正規化内容からカレンダーを組み立てる。"""
        ...


@dataclass(frozen=True, slots=True)
class FinalizeReport:
    """書き出し 1 回の結果（画面の要約の材料。価格は持たない）。

    合格なら `refill_id` と書いた補充分の manifest を持つ。不合格なら `failures` に理由を
    持ち、取得記録に検証の結果の行を追記した（補充分は書いていない）。
    """

    plan_id: str
    state_before: PlanState
    validation: RefillValidation
    refill_id: str | None
    manifest: RefillManifest | None

    @property
    def passed(self) -> bool:
        """検証が合格して補充分を書き出したか。"""
        return self.refill_id is not None


def finalize_plan(
    plan_id: str,
    *,
    store: RefillStore,
    inputs: FinalizeInputs,
    boundaries: AccessBoundaries,
    code_version: str,
    clock: Callable[[], UtcTime],
) -> FinalizeReport:
    """検証して書き出す（D03 §14.12 の出来事10・11）。

    手順:

    1. 作業ディレクトリが無ければ、この計画の補充分があれば `RefillAlreadyFinalized`
       （取得記録が無いので書き出し直せない）、無ければ `RefillPlanNotFound`。
    2. 計画のロックを取る（取れなければ `RefillPlanLocked`。状態を判定しない）。
    3. 検算（W5・W6）: 書きかけの補充分、`plan.json`、取得記録、この計画の補充分、有効な最終
       結果が指す保管場所のもの。指すものの無い最終結果があれば `RefillNotFetched`（取り直しは
       `fetch` の役目）。
    4. 状態: 書き出し済みなら、集約規則の版と変換コード版がいまと同じ補充分が 1 つでもあれば
       `RefillAlreadyFinalized`。どれとも違えば保管場所の tick から書き出し直す（通信しない）。
       計画済み・取得中なら `RefillNotFetched`。取得終了・不合格なら検証する。
    5. 検証が不合格なら、検証の結果の行を取得記録に追記して終わる（何も書き出さない）。
    6. 合格なら補充分を書く: ディレクトリを排他的に作り（既にあれば `RefillAlreadyExists`）、
       足のファイルと `validation.json` を置き、`refill_manifest.json` を最後に置く。書いた直後
       に検算し直す。
    """
    # どの出来事でも、状態を判定する前に書きかけの補充分を検算する（D03 §14.12 の注記、W6）。
    require_consistent_refills(store)
    if not store.work_dir_exists(plan_id):
        finished = finalized_refills(store, plan_id, None)
        if finished:
            raise RefillAlreadyFinalized(
                f"the plan {plan_id} is already finalized as"
                f" {', '.join(name for name, _ in finished)} and its work directory is gone;"
                " it cannot be finalized again without its journal (D03 §14.12)"
            )
        raise RefillPlanNotFound(
            f"no refill plan {plan_id} under _work/; create it with"
            " `odyssey-fx data refill plan` (D03 §14.12)"
        )
    store.acquire_plan_lock(plan_id)
    try:
        return _finalize_locked(
            plan_id,
            store=store,
            inputs=inputs,
            boundaries=boundaries,
            code_version=code_version,
            clock=clock,
        )
    finally:
        store.release_plan_lock(plan_id)


def _finalize_locked(
    plan_id: str,
    *,
    store: RefillStore,
    inputs: FinalizeInputs,
    boundaries: AccessBoundaries,
    code_version: str,
    clock: Callable[[], UtcTime],
) -> FinalizeReport:
    require_consistent_refills(store)
    plan = require_plan_matches(plan_id, store.read_plan(plan_id))
    journal = read_journal(plan_id, store.read_journal(plan_id), plan)
    finished = finalized_refills(store, plan_id, plan)
    existing = [
        verify_refill_directory(
            name, store.read_refill_manifest(name), store.list_refill_files(name)
        )
        for name, _ in finished
    ]
    entries = list(journal.entries)
    missing = check_finals(plan_id, plan, store, entries, clock())
    if missing:
        listed = ", ".join(str(item.hour) for item in missing)
        raise RefillNotFetched(
            f"the archive files of {listed} are missing; restore them with"
            " `odyssey-fx data refill fetch` before finalizing. Nothing was written"
            " (D03 §14.11.1 W5)"
        )
    finals = effective_finals(entries)
    if existing:
        state = PlanState.FINALIZED
        expected = existing[0].hours
        for record in expected:
            final = finals.get(record.hour)
            if final is None:
                raise RefillNotFetched(
                    f"{record.hour} has no valid final result (its archive file was restored"
                    " only in part); run `odyssey-fx data refill fetch` first. Nothing was"
                    " written (D03 §14.12)"
                )
            if (final.outcome, final.tick_digest) != (
                record.outcome,
                record.tick_digest,
            ):
                raise RefillStoreInconsistent(
                    f"_work/{plan_id}/journal.jsonl: the final result of {record.hour} differs"
                    f" from the finalized refill {existing[0].refill_id}. Nothing was written"
                    " (D03 §14.11.1 W5・W6)"
                )
        same = [
            manifest.refill_id
            for manifest in existing
            if manifest.aggregation_rule_version == REFILL_AGGREGATION_RULE_VERSION
            and manifest.code_version == code_version
        ]
        if same:
            raise RefillAlreadyFinalized(
                f"the plan {plan_id} is already finalized as {', '.join(same)} with the same"
                " aggregation rule and code version; nothing was written (D03 §14.12)"
            )
    else:
        state = derive_state(plan, entries, finalized=False)
        if state in (PlanState.PLANNED, PlanState.FETCHING):
            pending = [str(key) for key in plan.hour_keys if key not in finals]
            raise RefillNotFetched(
                f"the plan {plan_id} still has hour files without a final result"
                f" ({len(pending)}: {', '.join(pending[:5])}); run `odyssey-fx data refill fetch`"
                " first. Nothing was written (D03 §14.12)"
            )

    hours = _hour_data(plan, store, finals)
    originals, raw_bars = inputs.raw_bars(plan)
    validation = validate_refill(
        plan=plan,
        hours=hours,
        originals=originals,
        raw=RawBarIndex.build(raw_bars, boundaries),
        calendar=inputs.calendar(plan),
    )
    if journal.broken_tail_offset is not None:
        store.truncate_journal(plan_id, journal.broken_tail_offset)
    if not validation.passed:
        rejected = ValidationRecord(
            passed=False,
            reasons=validation.failures,
            at=clock(),
            details=failure_details(validation),
        )
        store.append_journal(plan_id, rejected.payload())
        return FinalizeReport(
            plan_id=plan_id,
            state_before=state,
            validation=validation,
            refill_id=None,
            manifest=None,
        )

    output = build_refill_output(
        plan=plan,
        validation=validation,
        hours=hours,
        code_version=code_version,
        created_at=clock(),
    )
    store.create_refill_dir(output.refill_id)
    for name, content in output.files:
        store.write_refill_file(output.refill_id, name, content)
    store.write_refill_file(output.refill_id, REFILL_MANIFEST_FILE, output.manifest_bytes)
    written = verify_refill_directory(
        output.refill_id,
        store.read_refill_manifest(output.refill_id),
        store.list_refill_files(output.refill_id),
    )
    return FinalizeReport(
        plan_id=plan_id,
        state_before=state,
        validation=validation,
        refill_id=output.refill_id,
        manifest=written,
    )


def _hour_data(
    plan: RefillPlan, store: RefillStore, finals: Mapping[HourKey, Any]
) -> dict[HourKey, HourData]:
    """時間ファイルごとの検証の材料（保管場所から読んで検算した tick と取得の出所）。"""
    source_digest = plan.provider.settings.source_digest()
    data: dict[HourKey, HourData] = {}
    for key in plan.hour_keys:
        final = finals[key]
        if final.outcome is HourOutcome.NOT_FETCHED:
            data[key] = HourData(final=final, decoded=None, provenance=None, archive_path=None)
            continue
        read = store.read_archive(key, source_digest, final.tick_digest)
        if read is None:  # pragma: no cover - check_finals が先に確かめている
            raise RefillNotFetched(f"the archive file of {key} is missing")
        decoded = verify_archive(read, plan)
        data[key] = HourData(
            final=final, decoded=decoded, provenance=read.provenance, archive_path=read.path
        )
    return data
