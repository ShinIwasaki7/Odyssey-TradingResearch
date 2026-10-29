"""封印の取り込み（2024 年以降の OANDA MT5 の 1 分足）の受入れ・分類・承認の結線（順 6 の S4a）。

制約の改定文（ADR-0037 第1項の 2 の 2）が許す「取得した原ファイルを、関所の品質確認専用の許可の
下で受け入れ、封印の区分の partition として取り込むこと」を組み立てる。業務の規則は持たず、
`marketdata`（受入れ・初期状態）と関所（`evaluation.application.holdout_gate`）を
品質確認専用の入口（`app.quality_check.run_quality_check`）でつなぐだけ（D01 §7、ADR-0036 第9項）。

- **受入れ**（`accept_sealed`）: 8 通貨のどれも同じ規則（D03 §16.3.2。順 6 の S4b）。読む前に
  取得記録の銘柄仕様を銘柄の設定と照らし（検査 3。`require_symbol_spec_matches`）、承認済みの許可の
  定義 `oanda_mt5_sealed_accept`（原ファイルが対象）
  の下で、取得記録の原ファイル（置き場とハッシュ）だけを読み（`read_symbol_files` が読む前に
  `covers_raw_file` で確かめる）、取引週の partition を作り、初期状態（ADR-0038 第3項）と閲覧の
  識別子・「品質確認の許可を要したか」（ADR-0036 第7項）を暫定 manifest に記録する。暫定 snapshot は
  道具の中で書く（終了の行に失敗しても調査のために残る。PR #80 の仮置き 18）。
- **分類・承認**（`finalize_request`・`require_acceptance_completed`）: 封印の区分の partition を
  読む分類・確定・承認は、承認済みの許可の定義 `oanda_mt5_sealed_finalize`（partition が対象）の
  下で行う（ADR-0037 第1項の 3 の 3「受入れ（分類・確定・承認を含む）」）。どちらも、受入れの
  閲覧の識別子に「完了」の終了の行があることを先に確かめる（ADR-0036 第7項。PR #80 の仮置き
  18・19）。

出力は件数・時刻・区間・ハッシュ・合否だけ（ADR-0037 第1項の 4。D03 §16.4）。価格を表示しない。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final

from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.app.config.mt5_datasource import Mt5DataSourceConfig
from odyssey_fx.app.config.sealed_window import (
    SEALED_WINDOW_PATH,
    SealedWindowConfig,
    load_sealed_window,
)
from odyssey_fx.app.config.symbols import load_symbol_spec
from odyssey_fx.app.mt5 import (
    DEFAULT_MT5_DATASOURCE,
    DEFAULT_TIMEFRAMES_V2,
    SEALED_COUNTING_RULES,
    Mt5Rules,
    load_datasource,
    load_mt5_rules,
)
from odyssey_fx.app.quality_check import QualityCheckRefused, run_quality_check
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import decimal_from_str
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.quality_permit import (
    SEALED_RAW_ROOT,
    QualityCheckRequest,
    QualityPermitRefusal,
    QualityReadRefused,
)
from odyssey_fx.marketdata.adapters.mt5_csv_source import (
    file_sha256,
    read_fetch_manifest,
    read_symbol_files,
)
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.adapters.quality_access_log_store import QualityAccessLogFile
from odyssey_fx.marketdata.application.acceptance import PendingSnapshot
from odyssey_fx.marketdata.application.mt5_acceptance import (
    InitialStateOf,
    Mt5AcceptanceFailed,
    Mt5FetchEntry,
    Mt5ReadFile,
    build_mt5_pending,
)
from odyssey_fx.marketdata.domain.access import (
    OANDA_SEALED_CLASSES,
    HoldoutState,
    next_trading_week_start,
    trading_week_start,
)
from odyssey_fx.marketdata.domain.errors import (
    HoldoutAccessViolation,
    HoldoutStateUnknown,
    MarketDataValueError,
)
from odyssey_fx.marketdata.domain.quality_access import (
    SEALED_INTAKE_OUTPUT_KINDS,
    AccessPeriod,
    PartitionTarget,
    QualityAccessLogFailure,
    QualityCheckKind,
    QualityReadPermit,
    RawFileTarget,
    ToolRef,
    completed_access_ids,
    violation_scopes,
)
from odyssey_fx.marketdata.domain.sealed_state import (
    ExistingSealedPartition,
    ViolationScope,
    initial_sealed_state,
)
from odyssey_fx.marketdata.domain.snapshot import (
    PartitionId,
    QualityReadRecord,
    SealedInitialState,
    SnapshotManifest,
)
from odyssey_fx.marketdata.domain.time_rule import TimeRuleVariant

__all__ = [
    "SEALED_ACCEPT_TOOL",
    "SEALED_CONFIG_FILES",
    "SEALED_FETCH_KIND",
    "SEALED_FINALIZE_TOOL",
    "SYMBOL_SPECS_DIR",
    "SealedAcceptance",
    "SealedDataError",
    "SealedIntakeRefused",
    "accept_sealed",
    "without_values",
    "existing_sealed_partitions",
    "finalize_request",
    "initial_state_provider",
    "is_sealed_snapshot",
    "require_acceptance_completed",
    "require_symbol_spec_matches",
    "sealed_config_digest",
    "sealed_raw_location",
]

#: 封印の取り込みの受入れの道具（許可の定義 `configs/quality_checks/oanda_mt5_sealed_accept.yaml`。
#: 対象は原ファイル。1 つの道具に承認済みの版は 1 つ。PR #110 の仮置き S3-9）。
SEALED_ACCEPT_TOOL: Final = ToolRef(name="oanda_mt5_sealed_accept", version="1")
#: 封印の取り込みの分類・確定・承認の道具（許可の定義 `oanda_mt5_sealed_finalize.yaml`。対象は
#: partition。ADR-0037 第1項の 3 の 3 の「受入れ（分類・確定・承認を含む）」）。
SEALED_FINALIZE_TOOL: Final = ToolRef(name="oanda_mt5_sealed_finalize", version="1")

#: 取得の道具が書く取得記録の種類（`tools/survey/mt5_api_probe.py` の `SEALED_KIND`）。
SEALED_FETCH_KIND: Final = "oanda_mt5_sealed_fetch"

#: 許可の定義の「設定のダイジェスト」の対象（固定したファイル。D03 §16.3.1 の検査 1）。数え方の
#: 規則が指す表・承認済みの一覧・カレンダー・休場の定義は `sealed_config_digest` が規則から足す。
SEALED_CONFIG_FILES: Final = (
    SEALED_COUNTING_RULES,
    DEFAULT_MT5_DATASOURCE,
    DEFAULT_TIMEFRAMES_V2,
    SEALED_WINDOW_PATH,
)


#: 銘柄の設定の置き場（D02 §5.2。D03 §16.3.1 の検査 3 が取得記録と照合する）。研究と共有する
#: 設定なので、本経路のために書き換えない（研究の実験の記録が本文を持つため）。
SYMBOL_SPECS_DIR: Final = Path("configs/symbols")

#: 価格基準の宣言（列対応の宣言の `basis_declaration`）と、取得記録の `chart_mode`（MT5 の
#: `SYMBOL_CHART_MODE_*`。0 が bid、1 が last）の対応（D12-26。D03 §16.3.1 の検査 3 の
#: 「0 = bid」）。
_CHART_MODE_OF_BASIS: Final = {"bid": 0}


class SealedIntakeRefused(ConfigError):
    """封印の取り込みを始めない・分類・承認を拒む（何も読まない・書かない）。"""


class SealedDataError(Mt5AcceptanceFailed):
    """封印のデータを読む・変換する処理の途中の例外（元の文は出さない。値を含みうるため）。"""


#: 文が件数・時刻・種別・置き場・ハッシュだけで、値を含まない例外（本基盤が封印の経路で送出する
#: もの）。これ以外の例外は、封印のデータを読んだ後では文を出さない（D03 §16.4）。
_VALUE_FREE_ERRORS: Final = (
    ConfigError,
    Mt5AcceptanceFailed,
    HoldoutAccessViolation,
    HoldoutStateUnknown,
)


def without_values[T](step: str, run: Callable[[], T]) -> T:
    """封印のデータを読む処理を動かし、例外の文から値を除く（ADR-0037 第1項の 4、D03 §16.4）。

    価格を含む行の解析・型の構築の失敗（例: 価格が正でない）は、その値を例外の文に含めることが
    ある。本基盤が値を含めないと決めた例外（`_VALUE_FREE_ERRORS`）はそのまま通し、それ以外は
    例外の種類と処理の名前だけを持つ `SealedDataError` に置き換える（元の例外は連ねない）。
    """
    try:
        return run()
    except _VALUE_FREE_ERRORS:
        raise
    except Exception as exc:  # noqa: BLE001 - 文を出さない置き換えのため、すべて受ける
        raise SealedDataError(
            f"{type(exc).__name__} while {step}; the message is withheld because it may carry"
            " values of the sealed data (counts, times and kinds only. D03 §16.4)"
        ) from None


def _rules_files(rules: Mt5Rules) -> list[Path]:
    counting = rules.counting
    return [
        Path(counting.time_rule_table_file),
        Path(counting.approved_list_file),
        Path(counting.calendar().file),
        Path(counting.calendar_base.file),
        Path(counting.holiday_exceptions_file),
    ]


def sealed_config_digest(repo_root: Path, rules: Mt5Rules | None = None) -> str:
    """許可の定義の設定のダイジェスト。

    固定したファイルの置き場と SHA-256 の正規化 JSON の SHA-256。

    1 バイトでも変われば値が変わり、許可の定義（人間が merge で承認したもの）と一致しなくなる
    （ADR-0036 第4項）。
    """
    root = Path(repo_root)
    if rules is None:
        rules = load_mt5_rules(root, SEALED_COUNTING_RULES)
    files = sorted({*SEALED_CONFIG_FILES, *_rules_files(rules)}, key=lambda p: p.as_posix())
    payload = {path.as_posix(): file_sha256(root / path) for path in files}
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sealed_raw_location(fetch_date: str) -> str:
    """封印の原ファイルの取得日の置き場（リポジトリの根からの POSIX 相対パス）。"""
    if not fetch_date or "/" in fetch_date or fetch_date in (".", "..") or "\\" in fetch_date:
        raise SealedIntakeRefused(f"取得日 {fetch_date!r} は置き場の名前として使えない")
    return "/".join((*SEALED_RAW_ROOT, fetch_date))


def is_sealed_snapshot(manifest: SnapshotManifest) -> bool:
    """封印の区分の partition を持つ snapshot か（分類・承認に品質確認の許可が要る）。"""
    return any(record.access_class in OANDA_SEALED_CLASSES for record in manifest.partitions)


# --- 初期状態（ADR-0038 第3項）--------------------------------------------------------------


def _violations(repo_root: Path) -> tuple[ViolationScope, ...]:
    contents = QualityAccessLogFile(repo_root).read()
    if isinstance(contents, QualityAccessLogFailure):
        raise HoldoutStateUnknown(
            f"the quality access log cannot be read ({contents.kind.value}): {contents.detail};"
            " the acceptance does not start (ADR-0038 第3項の 2)"
        )
    return violation_scopes(contents.rows)


def _consumed_in_log(lines: Sequence[object], partition_id: PartitionId) -> bool | None:
    """消費の記録に partition の消費の行があるか（行の形が読めなければ `None`）。"""
    found = False
    for line in lines:
        if not isinstance(line, dict) or not {"kind", "partition"} <= set(line):
            return None
        if line["kind"] not in ("GRANTED", "CONSUMED", "RESEARCH_OPT_IN"):
            return None
        if line["partition"] == str(partition_id) and line["kind"] == "CONSUMED":
            found = True
    return found


def existing_sealed_partitions(snapshots_root: Path) -> list[ExistingSealedPartition]:
    """既にある OANDA MT5 の経路の snapshot の封印の partition とその状態（ADR-0038 第3項の 3）。

    確定済みの snapshot（`data/snapshots/<snapshot_id>/`）を全部読む。暫定・退避（`_pending/`）は
    消費されないので数えない。manifest が読めない snapshot があれば、封印の区分を持つかどうかも
    分からないので `HoldoutStateUnknown`（受入れを始めない）。消費の記録は作業木のものを読む
    （origin の既定ブランチとの照合は最終検証の手順。D15 の R2・R4。S4a の仮置き）。
    """
    root = Path(snapshots_root)
    if not root.exists():
        return []
    store = ParquetSnapshotStore(root=root)
    out: list[ExistingSealedPartition] = []
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        if directory.name.startswith("_"):
            continue
        try:
            manifest = store.read_manifest(directory.name)
        except Exception as exc:  # noqa: BLE001 - どの失敗も「状態を導けない」に倒す
            raise HoldoutStateUnknown(
                f"the snapshot {directory.name} cannot be read ({type(exc).__name__}); the states"
                " of existing sealed partitions cannot be derived (ADR-0038 第3項の 3)"
            ) from exc
        if manifest.conversion.mt5 is None or not manifest.sealed_initial_states:
            continue  # 旧データの snapshot の LEGACY_HOLDOUT は数えない（ADR-0038 第3項の 3）
        try:
            lines: Sequence[object] = list(store.read_access_log(directory.name))
        except Exception:  # noqa: BLE001
            lines = [None]
        for item in manifest.sealed_initial_states:
            consumed = _consumed_in_log(lines, item.partition_id)
            state: HoldoutState | None
            if consumed is None:
                state = None
            elif item.state is HoldoutState.CONSUMED or consumed:
                state = HoldoutState.CONSUMED
            else:
                state = HoldoutState.SEALED
            out.append(ExistingSealedPartition(partition_id=item.partition_id, state=state))
    return out


def initial_state_provider(
    violations: Sequence[ViolationScope], existing: Sequence[ExistingSealedPartition]
) -> InitialStateOf:
    """受入れが partition ごとに呼ぶ初期状態の決め方（manifest の形にする）。"""

    def decide(partition_id: PartitionId) -> SealedInitialState:
        decided = initial_sealed_state(partition_id, violations, existing)
        return SealedInitialState(
            partition_id=partition_id,
            state=decided.state,
            reasons=tuple(sorted(reason.value for reason in decided.reasons)),
        )

    return decide


# --- 受入れ（D03 §16.3.1、ADR-0036 第7項）----------------------------------------------------


@dataclass(frozen=True, slots=True)
class SealedAcceptance:
    """封印の取り込みの受入れの結果（暫定 snapshot と、それを書いた置き場）。"""

    pending: PendingSnapshot
    directory: str
    raw_location: str
    #: 検査 3（銘柄の検査）で照合した銘柄の設定の置き場（合格したものだけがここに来る）。
    symbol_spec: str


def _fetch_record(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SealedIntakeRefused(f"取得記録 {path} が読めない: {exc}") from exc
    if not isinstance(payload, dict):
        raise SealedIntakeRefused(f"取得記録 {path} の形が違う")
    return payload


def _utc_of(api_epoch: int, symbol: Symbol, rules: Mt5Rules, *, end: bool) -> datetime:
    """API の時刻を標準の組で UTC に直す（表は API の時刻の暦日で引く。終わりは 1 分前の暦日）。"""
    api = datetime.fromtimestamp(api_epoch, UTC)
    key = api - timedelta(minutes=1) if end else api
    row = rules.table.row_for(str(symbol), key.date())
    if row is None:
        raise SealedIntakeRefused(
            f"時刻の規則の表 {rules.table.table_id} が覆わない期間の要求（{api.isoformat()}）。"
            "受入れの前に止める（D03 §16.2.4）"
        )
    return api - timedelta(hours=row.offset_hours(TimeRuleVariant.STANDARD))


def _require_inside_window(
    entries: Sequence[Mt5FetchEntry],
    window: SealedWindowConfig,
    rules: Mt5Rules,
    stage: object,
) -> None:
    """要求の区間が許可の窓と取得の段に合うか（D03 §16.3.1 の検査 2 のうち、読む前に分かる部分）。

    標準の組の変換で UTC に直し、始まりが `allowed_start` 以上、一括取得は終わりが取得の開始時点
    以下、定期取得は始まりが取得の開始時点以上であることを確かめる（ADR-0037 第2項の 3・
    第4項の 3）。
    全行の足の始まりが要求の区間の中にあることは、許可の下で読んだ後に確かめる
    （`_require_rows_inside_requests`）。
    """
    if window.acquisition_start is None:
        raise SealedIntakeRefused(
            "窓の設定の取得の開始時点が空。2024-01-01 以降の足を含む受入れを始めない"
            "（ADR-0038 第1項）"
        )
    if stage not in ("bulk", "periodic"):
        raise SealedIntakeRefused(f"取得記録の段 {stage!r} は bulk でも periodic でもない")
    acquisition = window.acquisition_start.value
    for entry in entries:
        utc_from = _utc_of(entry.requested_from_api, entry.symbol, rules, end=False)
        utc_to = _utc_of(entry.requested_to_api, entry.symbol, rules, end=True)
        if utc_from < window.allowed_start.value:
            raise SealedIntakeRefused(
                f"{entry.rid}: 要求の始まり {utc_from.isoformat()} が許可の窓の始まり"
                f" {window.allowed_start} より前（ADR-0037 第4項の 3）"
            )
        if stage == "bulk" and utc_to > acquisition:
            raise SealedIntakeRefused(
                f"{entry.rid}: 一括取得の要求が取得の開始時点 {window.acquisition_start} を越える"
                "（ADR-0037 第2項の 3）"
            )
        if stage == "periodic" and utc_from < acquisition:
            raise SealedIntakeRefused(
                f"{entry.rid}: 定期取得の要求が取得の開始時点 {window.acquisition_start} より前から"
                "始まる（ADR-0037 第2項の 3）"
            )


def _spec_value(value: object) -> Decimal | None:
    """取得記録の `point`（JSON の数）を `Decimal` にする。

    浮動小数は `repr`（最短の 10 進表記）を経て、二進の誤差を持ち込まない（ADR-0012。構築は
    `money.decimal_from_str`。`NaN`・`Infinity` は読めない値として `None`）。
    """
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return decimal_from_str(repr(value) if isinstance(value, float) else str(value))
    except KernelValueError:
        return None


def require_symbol_spec_matches(
    record: Mapping[str, object], symbol: Symbol, repo_root: Path, datasource: Mt5DataSourceConfig
) -> Path:
    """検査 3（D03 §16.3.1）: 取得記録の銘柄の `point`・`digits`・`chart_mode` が設定と同じか。

    - `point` は銘柄の設定（`configs/symbols/<銘柄>.yaml`）の価格の刻み（`price_tick`）と同じ値。
    - `digits` は価格の刻みの小数の桁数（`price_tick` を正規化した指数の符号を反転した値。
      `"0.001"` なら 3、`"0.00001"` なら 5）。銘柄の設定は桁数を持たないので刻みから導く
      （S4b の仮置き）。
    - `chart_mode` は列対応の宣言の価格基準（bid）に当たる値（0）。

    取得記録（価格を含まない）と設定だけを見るので、原ファイルを読む前に確かめる。食い違えば
    何も読まず・書かずに止める。文は項目の名前だけ（値は設定と取得記録の銘柄仕様で、価格では
    ないが、出力を合否に限るため出さない）。照合に使った銘柄の設定の置き場を返す。
    """
    path = SYMBOL_SPECS_DIR / f"{symbol}.yaml"
    full = Path(repo_root) / path
    if not full.is_file():
        raise SealedIntakeRefused(
            f"{symbol} の銘柄の設定 {path.as_posix()} が無い。検査 3（銘柄の検査）ができないので"
            "受け入れない（D03 §16.3.1）"
        )
    spec = load_symbol_spec(full)
    symbols = record.get("symbols")
    meta = symbols.get(str(symbol)) if isinstance(symbols, Mapping) else None
    if not isinstance(meta, Mapping):
        raise SealedIntakeRefused(
            f"取得記録に {symbol} の銘柄の記録（symbols）が無い。検査 3（銘柄の検査）ができないので"
            "受け入れない（D03 §16.3.1）"
        )
    tick = spec.price_tick.normalize()
    exponent = tick.as_tuple().exponent
    digits = -exponent if isinstance(exponent, int) and exponent < 0 else 0
    chart_mode = _CHART_MODE_OF_BASIS.get(datasource.basis_declaration.value.value)
    mismatched = []
    point = _spec_value(meta.get("point"))
    if point is None or point != spec.price_tick:
        mismatched.append("point")
    recorded_digits = meta.get("digits")
    if isinstance(recorded_digits, bool) or recorded_digits != digits:
        mismatched.append("digits")
    recorded_mode = meta.get("chart_mode")
    if chart_mode is None or isinstance(recorded_mode, bool) or recorded_mode != chart_mode:
        mismatched.append("chart_mode")
    if mismatched:
        raise SealedIntakeRefused(
            f"検査 3（銘柄の検査）の不合格: 取得記録の {symbol} の {', '.join(mismatched)} が"
            f"銘柄の設定 {path.as_posix()}（価格の刻み）・列対応の宣言 {datasource.ref}（価格基準）"
            "と違う。何も読まずに止める（D03 §16.3.1）"
        )
    return path


def _require_known_states(
    existing: Sequence[ExistingSealedPartition],
    symbol: Symbol,
    entries: Sequence[Mt5FetchEntry],
    rules: Mt5Rules,
) -> None:
    """この受入れの partition と重なりうる既にある partition の状態が、すべて導けるか。

    重なりうる範囲は、要求の区間（標準の組の UTC）を含む取引週の全体（候補の組の 1 時間のずれも
    含む）。同じ銘柄のその範囲に、状態を導けない partition があれば受入れを始めない（ADR-0038
    第3項の 3。原ファイルを読む前に止める）。
    """
    starts = [_utc_of(entry.requested_from_api, symbol, rules, end=False) for entry in entries]
    ends = [_utc_of(entry.requested_to_api, symbol, rules, end=True) for entry in entries]
    lo = trading_week_start(UtcTime(min(starts)))
    hi = next_trading_week_start(trading_week_start(UtcTime(max(ends))))
    span = Interval(start=lo, end=hi)
    unknown = sorted(
        str(item.partition_id)
        for item in existing
        if item.state is None
        and item.partition_id.series.symbol == symbol
        and item.partition_id.period is not None
        and item.partition_id.period.overlaps(span)
    )
    if unknown:
        raise HoldoutStateUnknown(
            f"the state of the existing partitions {unknown[:3]} (and {max(len(unknown) - 3, 0)}"
            " more) that may overlap this intake cannot be derived; the acceptance does not start"
            " and no raw file is read (ADR-0038 第3項の 3)"
        )


def _require_rows_inside_requests(
    entries: Sequence[Mt5FetchEntry], files: Mapping[str, Mt5ReadFile]
) -> None:
    """全行の足の始まり（API の時刻）が要求の区間の中か（検査 2。外れれば失敗。件数だけ）。"""
    outside = 0
    for entry in entries:
        read = files.get(entry.rid)
        if read is None:
            continue
        outside += sum(
            1
            for row in read.rows
            if not entry.requested_from_api <= row.api_time < entry.requested_to_api
        )
    if outside:
        raise Mt5AcceptanceFailed(
            f"{outside} row(s) start outside their requested intervals; acceptance fails and"
            " nothing is written (D03 §16.3.1 の検査 2. counts only)"
        )


def accept_sealed(
    *,
    repo_root: Path,
    fetch_date: str,
    symbol: Symbol,
    variant: TimeRuleVariant,
    manifest_copy: Path,
    snapshots_root: Path,
    created_at: UtcTime,
    code_version: str,
    write: Callable[[PendingSnapshot], str],
    now: Callable[[], UtcTime] | None = None,
    new_id: Callable[[], str] | None = None,
) -> SealedAcceptance:
    """封印の取り込みの受入れ（1 通貨 × 1 組。取得の回 × 通貨 × 組で 1 つの snapshot。S2b-5 (a)）。

    `write` は暫定 snapshot を `_pending/<provisional_id>` に書き、その置き場を返す（道具の中で
    呼ぶ。PR #80 の仮置き 18）。許可が出ない・記録に失敗したときは何も読まずに
    `QualityCheckRefused` を送出する。
    """
    root = Path(repo_root)
    rules = load_mt5_rules(root, SEALED_COUNTING_RULES)
    datasource: Mt5DataSourceConfig = load_datasource(root)
    window = load_sealed_window(root / SEALED_WINDOW_PATH)
    if symbol not in window.symbols:
        raise SealedIntakeRefused(f"{symbol} は窓の設定の銘柄に無い（ADR-0037 第1項の 2 の 1）")
    location = sealed_raw_location(fetch_date)
    raw_dir = root / location
    record_path = raw_dir / datasource.fetch_manifest
    record = _fetch_record(record_path)
    if record.get("kind") != SEALED_FETCH_KIND:
        raise SealedIntakeRefused(f"{record_path} は封印の取得の記録ではない（kind が違う）")
    window_sha = file_sha256(root / SEALED_WINDOW_PATH)
    if record.get("window_config_sha256") != window_sha:
        raise SealedIntakeRefused(
            f"{record_path} を作った窓の設定（{record.get('window_config_sha256')}）が、今の窓の"
            f"設定（{window_sha}）と違う（D03 §16.3.1 の検査 1）"
        )
    record_sha = file_sha256(record_path)
    if file_sha256(Path(manifest_copy)) != record_sha:
        raise SealedIntakeRefused(
            f"取得記録 {record_path} と版管理の写し {manifest_copy} が違う（D03 §16.3.1 の検査 1）"
        )
    own = [entry for entry in read_fetch_manifest(record_path) if entry.symbol == symbol]
    if not own:
        raise SealedIntakeRefused(f"{record_path} に {symbol} の要求が無い")
    planned = record.get("planned")
    if not isinstance(planned, dict) or not isinstance(planned.get(str(symbol)), list):
        raise SealedIntakeRefused(
            f"{record_path} に計画の要求の一覧（planned）が無い。取得済みかを確かめられないので"
            "受け入れない（D03 §16.5.8）"
        )
    wanted = {str(rid) for rid in planned[str(symbol)]}
    recorded = {entry.rid for entry in own}
    extra = sorted(recorded - wanted)
    if extra:
        raise SealedIntakeRefused(f"{record_path} に計画に無い要求がある（{', '.join(extra)}）")
    missing = sorted(
        [entry.rid for entry in own if not entry.acquired]
        + [f"{rid}（未要求）" for rid in sorted(wanted - recorded)]
    )
    if missing:
        # 0 本・失敗の要求が残った通貨のその回は受け入れない（D03 §16.5.8、PR #109 の仮置き
        # S2b-13。2024 年以降のどの月・週にも取引日はあるので、0 本は取り寄せの問題とみなす）。
        raise SealedIntakeRefused(
            f"{symbol} の要求 {len(missing)} 件が取得済みでない（{', '.join(missing)}）。この回の"
            f" {symbol} は受け入れない。取り直しの後に受け入れる（D03 §16.5.8）"
        )
    _require_inside_window(own, window, rules, record.get("stage"))
    spec_path = require_symbol_spec_matches(record, symbol, root, datasource)

    # 初期状態の材料は読み取りの前にそろえる（読めなければ始めない。ADR-0038 第3項の 2・3）。
    # 重なりうる既にある partition の状態が導けないときも、原ファイルを読む前に止める。
    existing = existing_sealed_partitions(snapshots_root)
    _require_known_states(existing, symbol, own, rules)
    provider = initial_state_provider(_violations(root), existing)
    request = QualityCheckRequest(
        tool=SEALED_ACCEPT_TOOL,
        config_digest=sealed_config_digest(root, rules),
        check_kind=QualityCheckKind.ACCEPTANCE,
        classes=OANDA_SEALED_CLASSES,
        period=AccessPeriod(start=window.allowed_start, end=None),
        targets=tuple(
            RawFileTarget(
                location=f"{location}/{entry.file}", sha256=entry.sha256_gz, symbol=symbol
            )
            for entry in own
        ),
        output_kinds=SEALED_INTAKE_OUTPUT_KINDS,
    )
    minute_def = rules.timeframe_defs[datasource.minute_timeframe]

    def build(permit: QualityReadPermit) -> PendingSnapshot:
        files = read_symbol_files(
            raw_dir, own, datasource.columns, read_permit=permit, location_prefix=location
        )
        _require_rows_inside_requests(own, files)
        built = build_mt5_pending(
            created_at=created_at,
            symbol=symbol,
            entries=own,
            files=files,
            source_paths={entry.rid: f"{location}/{entry.file}" for entry in own},
            fetch_manifest_sha256=record_sha,
            column_mapping=datasource.ref,
            table=rules.table,
            variant=variant,
            calendar=rules.calendar,
            minute_def=minute_def,
            timeframe_defs=rules.timeframe_defs,
            targets=rules.targets,
            boundaries=window.boundaries,
            basis_declaration=datasource.basis_declaration,
            code_version=code_version,
            initial_state_of=provider,
        )
        outside = sorted(
            str(record.partition_id)
            for record in built.manifest.partitions
            if record.access_class not in OANDA_SEALED_CLASSES
        )
        if outside:
            raise Mt5AcceptanceFailed(
                f"{len(outside)} partition(s) fall before the window (bars starting before"
                f" {window.allowed_start}); the sealed intake never holds them. Nothing was written"
                " (D03 §16.1, PR #109 の仮置き S2b-7)"
            )
        pending = PendingSnapshot(
            manifest=built.manifest.with_quality_read(
                QualityReadRecord(permit_required=True, access_id=permit.access_id)
            ),
            report=built.report,
            partition_bars=built.partition_bars,
        )
        return pending

    def tool(permit: QualityReadPermit) -> SealedAcceptance:
        pending = without_values(
            "reading and converting the sealed raw files", lambda: build(permit)
        )
        directory = write(pending)
        return SealedAcceptance(
            pending=pending,
            directory=directory,
            raw_location=location,
            symbol_spec=spec_path.as_posix(),
        )

    if now is None or new_id is None:
        return run_quality_check(repository_root=root, request=request, tool=tool)
    return run_quality_check(
        repository_root=root, request=request, tool=tool, now=now, new_id=new_id
    )


# --- 分類・確定・承認（ADR-0036 第7項、ADR-0037 第1項の 3 の 3）------------------------------


def require_acceptance_completed(repo_root: Path, manifest: SnapshotManifest, label: str) -> None:
    """受入れが品質確認の許可を要したなら、その閲覧に「完了」の終了の行があることを確かめる。

    MT5 経路の snapshot は「品質確認の許可を要したか」の項目を必ず持つ（無ければ S4a より前に
    作られたものとして拒む。ADR-0036 第7項。PR #80 の仮置き 19）。旧データの経路の snapshot
    （`data accept`）は対象にしない（S4a の仮置き）。
    """
    if manifest.conversion.mt5 is None:
        return
    record = manifest.quality_read
    if record is None:
        if is_sealed_snapshot(manifest):
            raise SealedIntakeRefused(
                f"{label} は封印の区分の partition を持つのに「品質確認の許可を要したか」の"
                "項目が無い。"
                "分類・確定・承認へ進めない（ADR-0036 第7項）"
            )
        if label.startswith("_pending"):
            raise SealedIntakeRefused(
                f"{label} の暫定 manifest に「品質確認の許可を要したか」の項目が無い（S4a より前に"
                "作られた）。受入れをやり直す（ADR-0036 第7項。PR #80 の仮置き 19）"
            )
        return
    if not record.permit_required:
        if is_sealed_snapshot(manifest):
            raise SealedIntakeRefused(
                f"{label} は封印の区分の partition を持つのに、許可を要しない受入れと記録されている"
                "（ADR-0036 第7項）"
            )
        return
    contents = QualityAccessLogFile(Path(repo_root)).read()
    if isinstance(contents, QualityAccessLogFailure):
        raise SealedIntakeRefused(
            f"品質確認の閲覧の記録が読めない（{contents.kind.value}）。{label} の分類・確定・承認へ"
            "進めない（ADR-0036 第7項）"
        )
    if record.access_id not in completed_access_ids(contents.rows):
        raise SealedIntakeRefused(
            f"{label} の受入れの閲覧 {record.access_id} に「完了」の終了の行が無い。分類・確定・"
            "承認へ進めない。受入れをやり直す（前の暫定 snapshot は _pending/_failed/ へ退避する。"
            "ADR-0036 第7項。PR #80 の仮置き 18・19）"
        )


def finalize_request(repo_root: Path, manifest: SnapshotManifest) -> QualityCheckRequest:
    """封印の区分の partition を読む分類・確定・承認の品質確認の要求。"""
    root = Path(repo_root)
    window = load_sealed_window(root / SEALED_WINDOW_PATH)
    sealed = [
        record.partition_id
        for record in manifest.partitions
        if record.access_class in OANDA_SEALED_CLASSES
    ]
    if not sealed:
        raise SealedIntakeRefused(
            "封印の区分の partition が無い snapshot に品質確認の許可は要らない"
        )
    targets = []
    for partition_id in sealed:
        period = partition_id.period
        if period is None:  # pragma: no cover - 封印の区分の識別は期間を持つ
            raise MarketDataValueError(f"{partition_id} has no trading week")
        targets.append(PartitionTarget(partition_id=partition_id, interval=period))
    return QualityCheckRequest(
        tool=SEALED_FINALIZE_TOOL,
        config_digest=sealed_config_digest(root),
        check_kind=QualityCheckKind.ACCEPTANCE,
        classes=frozenset(partition_id.access_class for partition_id in sealed),
        period=AccessPeriod(start=window.allowed_start, end=None),
        targets=tuple(targets),
        output_kinds=SEALED_INTAKE_OUTPUT_KINDS,
    )


def refusal_detail(error: QualityCheckRefused) -> str:
    """拒否の理由（種類と詳細。価格は含まない）。"""
    refusal: QualityReadRefused = error.refusal
    kind: QualityPermitRefusal = refusal.kind
    return f"{kind.value}: {refusal.detail}"
