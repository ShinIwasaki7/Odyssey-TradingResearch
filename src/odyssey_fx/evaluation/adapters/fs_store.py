"""判断履歴と結果の保存（D06 §9.1、ADR-0027）。

`TraceSink` と `ResultWriter`（`backtest.application.ports`）を実装する。ポート定義は
import せず、構造的に満たす（D01 §2.2 規則7）。保存先は `runs/<run_id>/` で、表形式データは
Parquet、manifest は JSON である。

**平坦化の規則は `backtest.trace.recorder` が持つ**（D06 §9.1 が決めたのは backtest 側の
責務）。ここは受け取った行を `flatten_row` で列の辞書にしてから書くだけで、規則を持たない。
DataFrame はこのモジュールの外へ出さない（D01 §2.2 規則1）。

十進数はすべて**文字列列**として保存する。二進浮動小数へ落とすと再現性が壊れる
（ADR-0012）。
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from odyssey_fx.backtest.domain.account import AccountSpec
from odyssey_fx.backtest.domain.fills import CostKind
from odyssey_fx.backtest.domain.policies import (
    HierarchyCheckResult,
    ResolutionHierarchy,
    RunConfig,
)
from odyssey_fx.backtest.trace.manifest import (
    DataCapabilityReport,
    RunManifest,
    config_digest_of,
)
from odyssey_fx.backtest.trace.recorder import (
    TraceTable,
    canonical_text,
    column_kinds,
    column_names,
    flatten_row,
    table_column_kinds,
    table_columns,
)
from odyssey_fx.backtest.trace.result import BacktestResult, FinalSummaries, RunStatus
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import AccountId, RunId, SnapshotId
from odyssey_fx.common.money import CurrencyCode, Money, decimal_from_str
from odyssey_fx.common.reason import Reason, ReasonCode
from odyssey_fx.common.refs import (
    CodeDigest,
    CompiledStrategyRef,
    ConfigDigest,
    ContentDigest,
    EnvDigest,
    LockDigest,
    PolicyRef,
    SnapshotRef,
)
from odyssey_fx.common.symbol import Symbol, SymbolSpecRef
from odyssey_fx.common.time import Interval, PhaseRank, PhaseSet, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.evaluation.application.evaluate_run import EvaluationReport
from odyssey_fx.evaluation.application.manifest import (
    EvaluationManifest,
    EvaluationTable,
    RunEvaluationId,
)
from odyssey_fx.evaluation.application.ports import (
    ColumnValueKind,
    ManifestReadFailure,
    TableReadResult,
    TraceColumnSpec,
)
from odyssey_fx.evaluation.domain.metrics import (
    CategoryCount,
    FillDiagnostic,
    MetricRecord,
    TradeRecord,
)
from odyssey_fx.evaluation.domain.status import ConsistencyCheckResult
from odyssey_fx.marketdata.domain.bar import BarKey
from odyssey_fx.marketdata.domain.integrity import (
    CheckKind,
    CheckResult,
    IntegrityReport,
    Severity,
)
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId

__all__ = [
    "FileSystemResultRepository",
    "FileSystemResultWriter",
    "FileSystemTraceSink",
    "evaluation_directory",
    "manifest_from_payload",
    "reserve_run_directory",
    "result_from_payload",
    "run_directory",
]


def run_directory(root: Path, run_id: object) -> Path:
    """`runs/<run_id>/`（ADR-0027）。"""
    return Path(root) / "runs" / str(run_id)


def _columns(
    rows: Sequence[Mapping[str, object]], declared: Sequence[str]
) -> dict[str, list[object]]:
    """行の並びを保ったまま、列ごとの値へ組み替える。

    行ごとに列が欠けることは無い（平坦化の規則が「その行の変種に無い列は `None`」と定めて
    いる）。**行が1件も無い表でも列は落とさない**。取引が1件も無かった正常な run と、必須の
    列を欠いた壊れた表とを読む側が区別できなくなるためである。
    """
    names: list[str] = list(declared)
    for row in rows:
        for name in row:
            if name not in names:
                names.append(name)
    return {name: [row.get(name) for row in rows] for name in names}


def reserve_run_directory(root: Path, run_id: object, *, replace: bool = False) -> Path:
    """成果物の置き場所を確保する（ADR-0006）。

    同じ完全入力の再実行は同じ `RunId` になるので、`runs/<run_id>/` が既にあることは
    ふつうに起こる。**既存の成果物を無条件に上書きしない**（既定は失敗）。置換は明示的な
    指示があるときだけ行い、置換したときも**旧 manifest を記録に残す**。

    途中まで書いたところで失敗すると新旧の表が混ざるので、書き始める前にここで判断する。
    """
    directory = run_directory(root, run_id)
    existing = sorted(directory.glob("*")) if directory.exists() else []
    if existing and not replace:
        raise KernelValueError(
            f"{directory} already holds artifacts for this run; re-running the same complete"
            " input produces the same RunId, and overwriting would destroy the earlier"
            " reproducibility artifact. Pass replace=True to replace it (ADR-0006)"
        )
    if existing:
        previous = directory / "manifest.json"
        if previous.exists():
            # 置換しても旧成果物の manifest は記録に残す（ADR-0006）。
            (directory / "manifest.replaced.json").write_text(
                previous.read_text(encoding="utf-8"), encoding="utf-8"
            )
        for path in existing:
            if path.is_file() and path.name != "manifest.replaced.json":
                path.unlink()
            elif path.is_dir() and path.name == _EVALUATION_DIRECTORY:
                # **評価の成果物も一緒に畳む**。判断履歴だけを書き直すと、同じ実行の
                # 識別子の下に「前の判断履歴から作った指標」と「新しい判断履歴」が並ぶ。
                # 置換を頼むのは成果物が壊れているときなので、古い指標が信用できる値として
                # 残るのがいちばん危うい（ADR-0006）。
                shutil.rmtree(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@dataclass(slots=True)
class FileSystemTraceSink:
    """19表を `runs/<run_id>/<TABLE>.parquet` へ書く（D06 §9.1・§9.2）。

    最初の書き出しの前に置き場所を確保し、既存の成果物があれば失敗する（ADR-0006）。
    """

    root: Path
    run_id: object
    replace: bool = False
    #: 置き場所を確保したかどうか。run のはじめに1度だけ検査するための覚えで、
    #: 書き出し口の同一性には関わらない。
    _reserved: bool = field(default=False, repr=False, compare=False)

    def write(self, table: TraceTable, rows: tuple[object, ...]) -> None:
        """1つの表を書き出す。書き出し専用で、検索元にはならない。

        **全行が `run_id` を持つ**（上位設計書 §4.7.15）。連番 ID は run 内でのみ一意なので、
        永続参照は `(run_id, ID)` の組になる。行そのものが `run_id` を持たない表でも、
        ここで必ず列として足す。
        """
        if not self._reserved:
            reserve_run_directory(self.root, self.run_id, replace=self.replace)
            self._reserved = True
        directory = run_directory(self.root, self.run_id)
        run_id = str(self.run_id)
        flattened = [{"run_id": run_id, **flatten_row(row)} for row in rows]
        columns = _columns(flattened, table_columns(table))
        kinds = table_column_kinds(table)
        frame = pl.DataFrame(
            columns,
            schema={name: _dtype_of(kinds.get(name, "string")) for name in columns},
            strict=False,
        )
        frame.write_parquet(directory / f"{table.value}.parquet")


#: 列の物理的な型（`backtest.trace.recorder` が宣言した名前から引く）。
_DTYPES: Mapping[str, pl.DataType] = {
    "string": pl.String(),
    "int": pl.Int64(),
    "bool": pl.Boolean(),
    "list": pl.List(pl.String()),
}


def _dtype_of(kind: str) -> pl.DataType:
    """宣言された型に対応する物理的な型。

    行の有無で型が変わらないよう、**推論せず宣言に従う**。十進数は文字列で保存する決まり
    なので（ADR-0012）、実際に文字列以外になるのは整数・真偽・`list` の列だけである。
    """
    return _DTYPES.get(kind, pl.String())


@dataclass(frozen=True, slots=True)
class FileSystemResultWriter:
    """run manifest と結果 DTO を JSON で書く（ADR-0027）。"""

    root: Path

    def write(self, result: BacktestResult, manifest: RunManifest) -> None:
        """`manifest.json` と `result.json` を書き出す。

        置き場所の確保（既存成果物の検査）は判断履歴の書き出し口が run のはじめに行う
        （ADR-0006）。ここで作り直すと、同じ run の途中でもう一度検査することになる。
        """
        directory = run_directory(self.root, manifest.run_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "manifest.json").write_text(
            json.dumps(_manifest_payload(manifest), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (directory / "result.json").write_text(
            json.dumps(_result_payload(result), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _manifest_payload(manifest: RunManifest) -> dict[str, Any]:
    """run manifest の7群を JSON へ落とす（D06 §9.3）。

    能力検査の結果は**全体のまま**入れる（要約に畳まない）。入れ子のレコードは D02 §9.3 の
    正規化エンコード文字列にし、外部ツールが同じ文字列からダイジェストを再計算できるように
    する。
    """
    return {
        "run_id": str(manifest.run_id),
        "config_digest": str(manifest.config_digest.digest),
        "code_digest": manifest.code_digest.digest.hex,
        "lock_digest": manifest.lock_digest.digest.hex,
        "env_digest": manifest.env_digest.digest.hex,
        "git_commit": manifest.git_commit,
        "git_dirty": manifest.git_dirty,
        "config": canonical_text(manifest.config),
        "phases": [{"rank": phase.rank, "name": phase.name} for phase in manifest.phases.ordered()],
        "id_allocator_snapshot": dict(manifest.id_allocator_snapshot),
        "symbol_spec_ref": canonical_text(manifest.symbol_spec_ref),
        "calendar_ref": manifest.calendar_ref,
        "timeframe_def_refs": [str(ref) for ref in manifest.timeframe_def_refs],
        "resolution_hierarchy": [str(level) for level in manifest.resolution_hierarchy.levels],
        "unresolved_intrabar_count": manifest.unresolved_intrabar_count,
        "unresolved_intrabar_ratio": str(manifest.unresolved_intrabar_ratio),
        "swap_modeled": manifest.swap_modeled,
        "capability_report": _capability_payload(manifest.capability_report),
        "status": manifest.status,
        "reason": None if manifest.reason is None else manifest.reason.code.value,
        "warnings": list(manifest.warnings),
    }


def _capability_payload(report: DataCapabilityReport) -> dict[str, Any]:
    """データ能力検査の結果を**全体のまま** JSON へ落とす（D06 §9.3 の能力検査の群）。

    要約に畳まず、合格・不合格のどちらでも個別の結果を残す。run manifest と結果 DTO の
    両方が同じ内容を持つのは、`FAILED_CAPABILITY` の run では判断履歴の表が空になりうる
    ため、結果からも直接読めるようにするという D06 §9.4 の要求による。
    """
    return {
        "compiled_match": report.compiled_match,
        "runnable": report.runnable,
        "reason": None if report.reason is None else report.reason.code.value,
        "integrity": [canonical_text(result) for result in report.integrity.results],
        "hierarchy_checks": [canonical_text(check) for check in report.hierarchy_checks],
        "diagnostics": list(report.diagnostics),
    }


def _result_payload(result: BacktestResult) -> dict[str, Any]:
    """結果 DTO を JSON へ落とす（D06 §9.4）。"""
    summaries = result.summaries
    return {
        "run_id": str(result.run_id),
        "status": result.status.value,
        "manifest_ref": result.manifest_ref,
        "trace_tables": {table.value: path for table, path in result.trace_tables.items()},
        "capability_report": _capability_payload(result.capability_report),
        "swap_modeled": result.swap_modeled,
        "unresolved_intrabar_count": result.unresolved_intrabar_count,
        "trade_count": result.trade_count,
        "opportunity_count": result.opportunity_count,
        "summaries": None
        if summaries is None
        else {
            "realized": canonical_text(summaries.realized),
            "equity_with_mtm": canonical_text(summaries.equity_with_mtm),
            "hypothetical_closed": canonical_text(summaries.hypothetical_closed),
            "cost_breakdown": {
                kind.value: canonical_text(amount)
                for kind, amount in summaries.cost_breakdown.items()
            },
        },
    }


# --- run 成果物の読み戻し（D07 §4.1・§4.3）-----------------------------------


#: `runs/<run_id>/` の下で評価の成果物を置くディレクトリ（D07 §8.2、Q4 決定）。
_EVALUATION_DIRECTORY = "eval"


def evaluation_directory(root: Path, run_id: RunId, run_evaluation_id: RunEvaluationId) -> Path:
    """`runs/<run_id>/eval/<run_evaluation_id>/`（D07 §8.2、Q4 決定）。

    識別子が違う成果物を同じ場所へ書かない。評価コードを変えて評価し直した結果は
    `RunEvaluationId` が別の値になるので、前の成果物を上書きしない。
    """
    return run_directory(root, run_id) / _EVALUATION_DIRECTORY / str(run_evaluation_id)


def _digest_of(hex_value: object, label: str) -> ContentDigest:
    if not isinstance(hex_value, str):
        raise KernelValueError(f"{label} must be a hexadecimal string, got {hex_value!r}")
    return ContentDigest.sha256(hex_value)


def _loads(text: object, label: str) -> Any:
    """保存された正規化エンコード文字列を構造へ戻す（D02 §9.3）。

    正規化エンコードは JSON 互換のテキストなので（同節）、そのまま読み戻せる。数値は
    十進の文字列として入っているので、`Decimal` へは文字列から作る（ADR-0012）。
    """
    if not isinstance(text, str):
        raise KernelValueError(f"{label} must be a canonical-encoded string, got {text!r}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise KernelValueError(f"{label} is not canonical-encoded JSON: {exc}") from exc


def _interval_of(payload: Mapping[str, Any]) -> Interval:
    return Interval(start=UtcTime.parse(payload["start"]), end=UtcTime.parse(payload["end"]))


def _series_of(text: str, timeframes: Mapping[str, TimeframeRef]) -> SeriesId:
    """`USDJPY/15m/bid` から系列を組み立てる（D03 §3.1）。

    系列の文字列は時間足の**版を持たない**（同節: 「版は manifest の `conversion` と
    `series` が別に保持する」）。run manifest が記録している時間足定義の版参照から引く。
    引けない系列は、manifest が使った定義を特定できないということなので拒否する。
    """
    parts = text.split("/")
    if len(parts) != 3:
        raise KernelValueError(f"invalid series literal in the run manifest: {text!r}")
    symbol, timeframe_id, basis = parts
    timeframe = timeframes.get(timeframe_id)
    if timeframe is None:
        raise KernelValueError(
            f"the run manifest records the series {text!r} but declares no timeframe definition"
            f" for {timeframe_id!r}; the version of the definition cannot be recovered"
            " (D03 §3.1)"
        )
    return SeriesId(symbol=Symbol(symbol), timeframe=timeframe, basis=PriceBasis(basis))


def _policy_ref_of(payload: Mapping[str, Any]) -> PolicyRef:
    return PolicyRef(
        policy_kind=payload["policy_kind"],
        policy_id=payload["policy_id"],
        version=int(payload["version"]),
        digest=_digest_of(payload["digest"]["hex"], "PolicyRef.digest"),
    )


def _money_of(payload: Mapping[str, Any]) -> Money:
    return Money(decimal_from_str(payload["amount"]), CurrencyCode(payload["currency"]))


def _reason_of(code: object) -> Reason | None:
    """理由コードだけを読み戻す（D02 §8.2）。

    **型付き詳細（`detail`）は復元しない**。run manifest は理由コードだけを記録しており
    （D06 §9.3 の保存形式）、詳細は判断履歴の `*_detail` 列に正規化エンコード文字列として
    残る。無い値を作って埋めないため、ここでは詳細なしの `Reason` を返す。
    """
    if code is None:
        return None
    if not isinstance(code, str):
        raise KernelValueError(f"a reason code must be a string, got {code!r}")
    return Reason(ReasonCode(code))


def _check_result_of(text: str, timeframes: Mapping[str, TimeframeRef]) -> CheckResult:
    payload = _loads(text, "IntegrityReport entry")
    return CheckResult(
        kind=CheckKind(payload["kind"]),
        severity=Severity(payload["severity"]),
        series=_series_of(payload["series"], timeframes),
        interval=_interval_of(payload["interval"]),
        detail=tuple((key, value) for key, value in payload.get("detail", ())),
    )


def _bar_key_of(
    payload: Mapping[str, Any] | None, timeframes: Mapping[str, TimeframeRef]
) -> BarKey | None:
    if payload is None:
        return None
    return BarKey(
        series=_series_of(payload["series"], timeframes),
        bar_start=UtcTime.parse(payload["bar_start"]),
    )


def _hierarchy_check_of(text: str, timeframes: Mapping[str, TimeframeRef]) -> HierarchyCheckResult:
    payload = _loads(text, "HierarchyCheckResult entry")

    def series(key: str) -> SeriesId | None:
        value = payload.get(key)
        return None if value is None else _series_of(value, timeframes)

    def interval(key: str) -> Interval | None:
        value = payload.get(key)
        return None if value is None else _interval_of(value)

    def moment(key: str) -> UtcTime | None:
        value = payload.get(key)
        return None if value is None else UtcTime.parse(value)

    def basis(key: str) -> PriceBasis | None:
        value = payload.get(key)
        return None if value is None else PriceBasis(value)

    parent = series("parent_series")
    if parent is None:  # pragma: no cover - 構築時に必須の項目
        raise KernelValueError("HierarchyCheckResult.parent_series is missing from the manifest")
    return HierarchyCheckResult(
        check=payload["check"],
        passed=bool(payload["passed"]),
        parent_series=parent,
        child_series=series("child_series"),
        parent_bar=_bar_key_of(payload.get("parent_bar"), timeframes),
        child_bar=_bar_key_of(payload.get("child_bar"), timeframes),
        expected_interval=interval("expected_interval"),
        child_intervals=tuple(_interval_of(item) for item in payload.get("child_intervals", ())),
        coverage_gaps=tuple(_interval_of(item) for item in payload.get("coverage_gaps", ())),
        coverage_overlaps=tuple(
            _interval_of(item) for item in payload.get("coverage_overlaps", ())
        ),
        expected_boundary=moment("expected_boundary"),
        observed_boundary=moment("observed_boundary"),
        expected_basis=basis("expected_basis"),
        observed_basis=basis("observed_basis"),
        expected_available_at=moment("expected_available_at"),
        observed_available_at=moment("observed_available_at"),
    )


def _capability_report_of(
    payload: Mapping[str, Any], timeframes: Mapping[str, TimeframeRef]
) -> DataCapabilityReport:
    return DataCapabilityReport(
        compiled_match=bool(payload["compiled_match"]),
        integrity=IntegrityReport(
            results=tuple(
                _check_result_of(item, timeframes) for item in payload.get("integrity", ())
            )
        ),
        hierarchy_checks=tuple(
            _hierarchy_check_of(item, timeframes) for item in payload.get("hierarchy_checks", ())
        ),
        runnable=bool(payload["runnable"]),
        reason=_reason_of(payload.get("reason")),
        diagnostics=tuple(payload.get("diagnostics", ())),
    )


def _run_config_of(payload: Mapping[str, Any], timeframes: Mapping[str, TimeframeRef]) -> RunConfig:
    account = payload["account"]
    return RunConfig(
        run_interval=_interval_of(payload["run_interval"]),
        snapshot_ref=SnapshotRef(
            snapshot_id=SnapshotId(_digest_of(payload["snapshot_ref"]["snapshot_id"], "SnapshotId"))
        ),
        compiled_ref=CompiledStrategyRef(
            digest=_digest_of(payload["compiled_ref"]["digest"]["hex"], "CompiledStrategyRef")
        ),
        account=AccountSpec(
            account_id=AccountId(account["account_id"]),
            currency=CurrencyCode(account["currency"]),
            initial_balance=_money_of(account["initial_balance"]),
        ),
        risk_policy_ref=_policy_ref_of(payload["risk_policy_ref"]),
        execution_policy_ref=_policy_ref_of(payload["execution_policy_ref"]),
        cost_model_ref=_policy_ref_of(payload["cost_model_ref"]),
        conversion_policy_ref=_policy_ref_of(payload["conversion_policy_ref"]),
        delay_scenario_ref=_policy_ref_of(payload["delay_scenario_ref"]),
        execution_series=_series_of(payload["execution_series"], timeframes),
        seed=int(payload["seed"]),
    )


def _timeframes_of(payload: Mapping[str, Any]) -> dict[str, TimeframeRef]:
    """manifest が記録した時間足定義の版参照を `id` で引けるようにする（D03 §3.1）。"""
    return {
        ref.id: ref for ref in (TimeframeRef.parse(item) for item in payload["timeframe_def_refs"])
    }


def manifest_from_payload(payload: Mapping[str, Any]) -> RunManifest:
    """保存した run manifest を読み戻す（D06 §9.3、D07 §4.1 の入力2）。

    `FileSystemResultWriter.write` が書いた JSON と対になる。入れ子のレコードは正規化
    エンコード文字列で保存されており（同節）、その表現は JSON 互換なのでそのまま読める。
    """
    timeframes = _timeframes_of(payload)
    config = _run_config_of(_loads(payload["config"], "RunManifest.config"), timeframes)
    symbol_spec = _loads(payload["symbol_spec_ref"], "RunManifest.symbol_spec_ref")
    symbol_spec_ref = SymbolSpecRef(
        symbol=Symbol(symbol_spec["symbol"]),
        version=int(symbol_spec["version"]),
        digest=_digest_of(symbol_spec["digest"]["hex"], "SymbolSpecRef.digest"),
    )
    timeframe_def_refs = tuple(TimeframeRef.parse(item) for item in payload["timeframe_def_refs"])
    stored_digest = ConfigDigest(_digest_of(payload["config_digest"], "ConfigDigest"))
    recomputed = config_digest_of(
        config,
        symbol_spec_ref=symbol_spec_ref,
        calendar_ref=payload["calendar_ref"],
        timeframe_def_refs=timeframe_def_refs,
    )
    if recomputed != stored_digest:
        # **設定の中身と、その指紋を別々に信じない**（D06 §9.3、ADR-0006）。`RunManifest` は
        # 実行の識別子が4つのダイジェストから来ていることを確かめるが、設定の中身がその
        # ダイジェストと合っているかは見ない。中身だけを書き換えた manifest を通すと、
        # たとえば run 区間を変えるだけで指標（保有時間の割合など）が変わるのに、整合検査
        # 8件はすべて合格し、実行と評価の識別子も同じままになる。
        raise KernelValueError(
            f"the run manifest records the config digest {stored_digest.digest.hex} but its"
            f" config hashes to {recomputed.digest.hex}; the recorded settings and their"
            " fingerprint disagree (D06 §9.3)"
        )
    return RunManifest(
        run_id=RunId(_digest_of(payload["run_id"], "RunId")),
        config=config,
        config_digest=stored_digest,
        code_digest=CodeDigest(_digest_of(payload["code_digest"], "CodeDigest")),
        lock_digest=LockDigest(_digest_of(payload["lock_digest"], "LockDigest")),
        env_digest=EnvDigest(_digest_of(payload["env_digest"], "EnvDigest")),
        phases=PhaseSet(
            tuple(
                PhaseRank(rank=int(item["rank"]), name=item["name"]) for item in payload["phases"]
            )
        ),
        id_allocator_snapshot={
            key: int(value) for key, value in payload["id_allocator_snapshot"].items()
        },
        capability_report=_capability_report_of(payload["capability_report"], timeframes),
        resolution_hierarchy=ResolutionHierarchy(
            levels=tuple(_series_of(item, timeframes) for item in payload["resolution_hierarchy"])
        ),
        unresolved_intrabar_count=int(payload["unresolved_intrabar_count"]),
        unresolved_intrabar_ratio=decimal_from_str(payload["unresolved_intrabar_ratio"]),
        swap_modeled=bool(payload["swap_modeled"]),
        status=payload["status"],
        symbol_spec_ref=symbol_spec_ref,
        calendar_ref=payload["calendar_ref"],
        timeframe_def_refs=timeframe_def_refs,
        git_commit=payload["git_commit"],
        git_dirty=bool(payload["git_dirty"]),
        reason=_reason_of(payload.get("reason")),
        warnings=tuple(payload.get("warnings", ())),
    )


def result_from_payload(
    payload: Mapping[str, Any], timeframes: Mapping[str, TimeframeRef]
) -> BacktestResult:
    """保存した結果 DTO を読み戻す（D06 §9.4、D07 §4.1 の入力1）。"""
    summaries = payload["summaries"]
    return BacktestResult(
        run_id=RunId(_digest_of(payload["run_id"], "RunId")),
        status=RunStatus(payload["status"]),
        trace_tables={TraceTable(name): path for name, path in payload["trace_tables"].items()},
        capability_report=_capability_report_of(payload["capability_report"], timeframes),
        manifest_ref=payload["manifest_ref"],
        swap_modeled=bool(payload["swap_modeled"]),
        unresolved_intrabar_count=int(payload["unresolved_intrabar_count"]),
        trade_count=int(payload["trade_count"]),
        opportunity_count=int(payload["opportunity_count"]),
        summaries=None
        if summaries is None
        else FinalSummaries(
            realized=_money_of(_loads(summaries["realized"], "FinalSummaries.realized")),
            equity_with_mtm=_money_of(
                _loads(summaries["equity_with_mtm"], "FinalSummaries.equity_with_mtm")
            ),
            hypothetical_closed=_money_of(
                _loads(summaries["hypothetical_closed"], "FinalSummaries.hypothetical_closed")
            ),
            cost_breakdown={
                CostKind(name): _money_of(_loads(amount, "FinalSummaries.cost_breakdown"))
                for name, amount in summaries["cost_breakdown"].items()
            },
        ),
    )


#: 評価結果の5表と、その行の型（D07 §8.1）。行が1件も無くても列は落とさない。
_EVALUATION_ROW_TYPES: Mapping[EvaluationTable, type] = {
    EvaluationTable.METRICS: MetricRecord,
    EvaluationTable.CATEGORY_COUNTS: CategoryCount,
    EvaluationTable.TRADES: TradeRecord,
    EvaluationTable.FILL_DIAGNOSTICS: FillDiagnostic,
    EvaluationTable.CONSISTENCY_CHECKS: ConsistencyCheckResult,
}


def _list_column(value: object) -> str | None:
    """`list` 列の値を1つの文字列にする（D07 §4.3 の `LIST_STRING`）。

    判断履歴の可変長の列は正規化エンコード文字列の `list` として保存されている
    （D06 §9.1）。読み出しの口は文字列（または `None`）の行を返す約束なので、要素の列を
    JSON の配列として1つの文字列に畳む。要素そのものは触らない。
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence):
        return json.dumps(list(value), ensure_ascii=False, separators=(",", ":"))
    raise KernelValueError(
        f"a LIST_STRING column must hold a sequence of canonical-encoded strings, got {value!r}"
    )


@dataclass(frozen=True, slots=True)
class FileSystemResultRepository:
    """run の成果物の読み書き（D01 §4、D07 §4.3・§8.2）。

    `ResultRepository`（`evaluation.application.ports`）を構造的に満たす。Parquet を開くのは
    このモジュールだけで、`domain` と `application` には表形式ライブラリを入れない
    （D01 §5・ADR-0025）。

    `read_result` は `ResultRepository` の操作ではない。D07 §4.1 は結果 DTO を
    **引数**として受け取ると定めており、評価を別のコマンドとして起動する `app` が
    保存済みの結果を読むための入り口である。ポートを広げないため、具体クラスにだけ置く。
    """

    root: Path

    def read_manifest(self, run_id: RunId) -> RunManifest | ManifestReadFailure:
        """`runs/<run_id>/manifest.json` を読む（D06 §9.3）。

        **読めないとき（ファイルが無い・JSON として壊れている・項目が欠けている・設定と
        その指紋が食い違う）は例外にせず `ManifestReadFailure` を返す**（D07 v2.0 §3、
        §10.1.1 の R1-D07-4）。評価はそれを整合検査 C11 の不合格として残す。
        """
        path = run_directory(self.root, run_id) / "manifest.json"
        if not path.is_file():
            return ManifestReadFailure(
                run_id=run_id,
                detail=canonical_text(f"{path.name} does not exist; this run has no manifest"),
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise KernelValueError("the run manifest must be a JSON object")
            return manifest_from_payload(payload)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            # `KernelValueError` と JSON の読み取りの失敗は `ValueError` の派生。項目の欠落は
            # `KeyError`、形の違う値は `TypeError` / `AttributeError` として来る。
            return ManifestReadFailure(
                run_id=run_id, detail=canonical_text(f"{type(exc).__name__}: {exc}")
            )

    def read_result(self, run_id: RunId) -> BacktestResult:
        """`runs/<run_id>/result.json` を読む（D06 §9.4）。"""
        directory = run_directory(self.root, run_id)
        result_path = directory / "result.json"
        manifest_path = directory / "manifest.json"
        if not result_path.is_file():
            raise KernelValueError(f"{result_path} does not exist; this run has no result to read")
        if not manifest_path.is_file():
            raise KernelValueError(
                f"{manifest_path} does not exist; the timeframe definitions recorded there are"
                " needed to read the series of the result (D03 §3.1)"
            )
        timeframes = _timeframes_of(json.loads(manifest_path.read_text(encoding="utf-8")))
        result = result_from_payload(
            json.loads(result_path.read_text(encoding="utf-8")), timeframes
        )
        if result.run_id != run_id:
            # **読んだ場所と中身の実行の識別子が食い違ったまま進めない**。評価はこのあと
            # 結果 DTO の識別子で manifest と判断履歴を引くので、食い違ったまま通すと、
            # 利用者が指した run とは**別の run** の指標を、しかも別の場所へ書いてしまう。
            # 整合検査 C2 はその別の run の中では辻褄が合うので気付けない。
            raise KernelValueError(
                f"{result_path} holds the result of run {result.run_id} but it was read as"
                f" {run_id}; evaluating it would produce metrics for a different run"
                " (D07 §4.1・§10.2 の C2)"
            )
        return result

    def read_table(
        self, run_id: RunId, table: TraceTable, columns: tuple[TraceColumnSpec, ...]
    ) -> TableReadResult:
        """判断履歴の表から、要求した列だけを読む（D07 §4.3）。

        **列が無いことと行が0件であることを戻り値で区別する**。表が無ければ
        `table_present=False`、要求した列のうち表に無いものは `missing_columns` に入れ、
        `rows` は空にする。読み出しの時点では例外にしない（評価は「なぜ評価できなかったか」を
        残すのが仕事であり、ここで落ちると理由が残らない）。
        """
        names = tuple(spec.column for spec in columns)
        kinds = tuple(spec.value_kind for spec in columns)
        path = run_directory(self.root, run_id) / f"{table.value}.parquet"
        if not path.is_file():
            return TableReadResult(table=table, table_present=False)
        frame = pl.read_parquet(path)
        missing = tuple(name for name in names if name not in frame.columns)
        if missing:
            return TableReadResult(table=table, table_present=True, missing_columns=missing)
        selected = frame.select(list(names))
        rows: list[tuple[str | None, ...]] = []
        for record in selected.iter_rows():
            rows.append(
                tuple(
                    _list_column(value)
                    if kind is ColumnValueKind.LIST_STRING
                    else (None if value is None else str(value))
                    for value, kind in zip(record, kinds, strict=True)
                )
            )
        return TableReadResult(table=table, table_present=True, rows=tuple(rows))

    def write_evaluation(
        self, report: EvaluationReport, rows: Mapping[EvaluationTable, tuple[object, ...]]
    ) -> None:
        """評価結果の5表と評価 manifest を書く（D07 §8.1・§8.2）。

        保存先は `runs/<run_id>/eval/<run_evaluation_id>/`（Q4 決定）。**どの状態でも5表
        すべてを書く**。表の有無で状態を表すと、書き出しが途中で落ちた成果物と区別できない。
        """
        manifest = report.manifest
        if dict(rows) != report.rows:
            # 結果のダイジェストは `EvaluationReport.rows` から作られている（D07 §9.2）。
            # 別の行を書くと、ダイジェストと保存された表が食い違う成果物ができる。
            raise KernelValueError(
                "write_evaluation must save the same rows the result digest was built from"
                " (D07 §9.2); the report and the rows given disagree"
            )
        directory = evaluation_directory(self.root, manifest.run_id, manifest.run_evaluation_id)
        directory.mkdir(parents=True, exist_ok=True)
        for table in EvaluationTable:
            row_type = _EVALUATION_ROW_TYPES[table]
            declared = column_names(row_type)
            flattened = [flatten_row(row) for row in rows.get(table, ())]
            values = _columns(flattened, declared)
            kinds = column_kinds(row_type)
            frame = pl.DataFrame(
                values,
                schema={name: _dtype_of(kinds.get(name, "string")) for name in values},
                strict=False,
            )
            frame.write_parquet(directory / f"{table.value}.parquet")
        (directory / "evaluation.json").write_text(
            json.dumps(_evaluation_payload(manifest), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def read_evaluation_manifest(
        self, run_id: RunId, run_evaluation_id: RunEvaluationId
    ) -> dict[str, Any]:
        """保存した評価 manifest を読む（受入れ確認と再現性の比較に使う）。"""
        path = evaluation_directory(self.root, run_id, run_evaluation_id) / "evaluation.json"
        if not path.is_file():
            raise KernelValueError(f"{path} does not exist")
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return payload


def _evaluation_payload(manifest: EvaluationManifest) -> dict[str, Any]:
    """評価 manifest の6群を JSON へ落とす（D07 §8.3）。

    **実行時刻を入れない**。壁時計の時刻を入れると同じ判断履歴から同じ成果物が出なくなる。
    run manifest から写す5項目は、run manifest が読めなかったとき `null` になる
    （D07 §10.1.1 の R1-D07-4）。
    """
    reason = manifest.run_failure_reason
    calendar_id, calendar_version = manifest.calendar_ref
    return {
        "run_evaluation_id": str(manifest.run_evaluation_id),
        "run_id": str(manifest.run_id),
        "run_manifest_ref": None
        if manifest.run_manifest_ref is None
        else manifest.run_manifest_ref.hex,
        "metric_set_version": manifest.metric_set_version,
        "calendar_ref": {"id": calendar_id, "version": calendar_version},
        "evaluation_code_digest": manifest.evaluation_code_digest.digest.hex,
        "run_code_digest": None
        if manifest.run_code_digest is None
        else manifest.run_code_digest.digest.hex,
        "input_tables": [table.value for table in manifest.input_tables],
        "account_currency": None
        if manifest.account_currency is None
        else str(manifest.account_currency),
        "run_status": None if manifest.run_status is None else manifest.run_status.value,
        "run_failure_reason": None if reason is None else reason.code.value,
        "swap_modeled": manifest.swap_modeled,
        "status": manifest.status.value,
        "fatal_failure_count": manifest.fatal_failure_count,
        "warning_failure_count": manifest.warning_failure_count,
        "unreadable_check_count": manifest.unreadable_check_count,
        "result_digest": manifest.result_digest.hex,
    }
