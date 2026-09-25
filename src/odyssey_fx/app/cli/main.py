"""コマンドライン入口（D03 §10、D06 §4.2、D07 §4・§8）。

CLI ライブラリは段階2まで標準の `argparse` を使う（ADR-0028）。市場データの受入れ3
コマンドに、単一 run の実行と評価の2コマンドを足した5コマンドを提供する。

- `odyssey-fx data accept`: 受入れの 1〜8 を実行し、`data/snapshots/_pending/<暫定 ID>/`
  に暫定 manifest・検査報告・partition を書く。
- `odyssey-fx data classify`: 人間の分類を記入し、最終の識別子を計算して
  `data/snapshots/<最終 ID>/` へ確定する。
- `odyssey-fx data approve`: 確定済み snapshot に承認と価格基準の宣言記録を記入する。
- `odyssey-fx run`: 実験設定から1回の run を実行し、`runs/<run_id>/` に判断履歴19表・
  run manifest・結果を書く。
- `odyssey-fx evaluate`: 保存済みの run を評価し、`runs/<run_id>/eval/<評価 ID>/` に
  指標・集計・取引・診断・整合検査の5表と評価 manifest を書く。

**実行と評価を別のコマンドに分ける**（D07 §4.1）。評価は run を実行し直さず、保存された
判断履歴と manifest だけを読む。同じ run を別の指標集合の版で評価し直しても、`runs/` の
下の判断履歴は変わらない。

**業務ロジックは持たない**（D01 §7）。引数の解釈・設定の読込の呼び出し・ポート越しの
書き出し・画面への表示だけを行い、何を検査し何を識別子に含めるかは
`marketdata.application` が決める。

**承認前の snapshot はどのコマンドからも読み取り対象にならない**（D03 §3.7.1 の 3）。
暫定 snapshot（`_pending/`）は承認の対象にならず、`approve` は受け付けない。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from odyssey_fx.app import composition
from odyssey_fx.app.cli import summary
from odyssey_fx.app.config import (
    ClassificationDecisionFile,
    ConfigError,
    load_calendar,
    load_classification_decisions,
    load_datasource,
    load_symbol_specs,
    load_timeframes,
)
from odyssey_fx.app.config.experiment import ExperimentConfig, load_experiment
from odyssey_fx.backtest.trace.result import RunStatus
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import RunId
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.symbol import Symbol, SymbolSpec
from odyssey_fx.evaluation.application.manifest import METRIC_SET_VERSION
from odyssey_fx.evaluation.domain.status import EvaluationStatus
from odyssey_fx.marketdata.application.acceptance import (
    FinalizedSnapshot,
    PendingSnapshot,
    approve,
    finalize,
    out_of_session_exclusions,
    reaccept_with_calendar,
    requires_rerun,
)
from odyssey_fx.marketdata.application.classification import require_recorded_classification
from odyssey_fx.marketdata.application.ports import SnapshotStore
from odyssey_fx.marketdata.application.snapshot_access import (
    PENDING_DIRECTORY,
    require_recorded_content,
)
from odyssey_fx.marketdata.domain.access import INITIAL_ACCESS_BOUNDARIES
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.marketdata.domain.snapshot import (
    Approval,
    BasisDeclaration,
    DeclarationRecord,
    PartitionId,
    SnapshotManifest,
)
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG

__all__ = ["build_parser", "main"]

#: 終了コード。0 は成功、1 は設定・データの誤り（人間が直すもの）。
_EXIT_OK = 0
_EXIT_FAILED = 1


def build_parser() -> argparse.ArgumentParser:
    """3コマンドの引数定義（D03 §10）。"""
    parser = argparse.ArgumentParser(
        prog="odyssey-fx",
        description="FX 戦略研究基盤のコマンドライン入口（D03 §10）",
    )
    top = parser.add_subparsers(dest="group", required=True)
    data = top.add_parser("data", help="市場データの受入れ・分類・承認").add_subparsers(
        dest="command", required=True
    )

    accept = data.add_parser(
        "accept",
        help="原ファイルを受け入れて暫定 snapshot を作る（D03 §4 の 1〜8）",
    )
    accept.add_argument("--datasource", type=Path, required=True, help="列対応の宣言（YAML）")
    accept.add_argument("--calendar", type=Path, required=True, help="取引カレンダー（YAML）")
    accept.add_argument("--timeframes", type=Path, required=True, help="時間足定義（YAML）")
    accept.add_argument(
        "--symbols", type=Path, required=True, help="銘柄仕様のディレクトリ（configs/symbols/）"
    )
    accept.add_argument(
        "--out", type=Path, required=True, help="snapshot の基点（data/snapshots/）"
    )
    accept.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="原データの基点を解決するリポジトリの位置（既定は現在のディレクトリ）",
    )

    classify = data.add_parser(
        "classify",
        help="分類対象の警告の分類を記入して snapshot を確定する（D03 §4 の 9）",
    )
    classify.add_argument("--pending", required=True, help="暫定の識別子（provisional_id）")
    classify.add_argument(
        "--decisions", type=Path, required=True, help="分類ファイル（YAML、形式版 2）"
    )
    # 再実行（カレンダーの新版・セッション外データ異常の除外）では、上位足の生成と
    # カレンダー照合に時間足定義が要る（D03 §10 v1.7 は必須の引数として定める）。原データは
    # 読み直さない（暫定 snapshot の partition を再利用する）ので、列対応の宣言・銘柄仕様・
    # リポジトリの位置は要らない（D03 §4 の 9）。
    classify.add_argument("--timeframes", type=Path, required=True, help="時間足定義（YAML）")
    classify.add_argument(
        "--out", type=Path, required=True, help="snapshot の基点（data/snapshots/）"
    )

    approve_command = data.add_parser(
        "approve",
        help="確定済み snapshot に承認を記入する（D03 §3.7.1 の 2）",
    )
    approve_command.add_argument("--snapshot", required=True, help="最終の識別子（snapshot_id）")
    approve_command.add_argument("--by", required=True, help="承認者の名前")
    approve_command.add_argument("--comment", default="", help="承認時のコメント")
    approve_command.add_argument(
        "--out", type=Path, required=True, help="snapshot の基点（data/snapshots/）"
    )

    run_command = top.add_parser(
        "run",
        help="実験設定から1回の run を実行する（D06 §4.2）",
    )
    run_command.set_defaults(command="run")
    run_command.add_argument("--experiment", type=Path, required=True, help="実験設定（YAML）")
    run_command.add_argument("--calendar", type=Path, required=True, help="取引カレンダー（YAML）")
    run_command.add_argument("--timeframes", type=Path, required=True, help="時間足定義（YAML）")
    run_command.add_argument(
        "--symbols", type=Path, required=True, help="銘柄仕様のディレクトリ（configs/symbols/）"
    )
    run_command.add_argument(
        "--snapshots", type=Path, required=True, help="snapshot の基点（data/snapshots/）"
    )
    run_command.add_argument(
        "--out",
        type=Path,
        default=Path("."),
        help="成果物の基点（この下に runs/<run_id>/ を作る。既定は現在のディレクトリ）",
    )
    run_command.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="`uv.lock` と作業ツリーの状態を読むリポジトリの位置（既定は現在のディレクトリ）",
    )
    run_command.add_argument(
        "--replace",
        action="store_true",
        help="同じ実行の識別子の成果物が既にある場合に置き換える（既定は失敗、ADR-0006）",
    )

    evaluate_command = top.add_parser(
        "evaluate",
        help="保存済みの run を評価する（D07 §4・§8）",
    )
    evaluate_command.set_defaults(command="evaluate")
    evaluate_command.add_argument("--run", required=True, help="実行の識別子（run_id、16進64文字）")
    evaluate_command.add_argument(
        "--calendar",
        type=Path,
        required=True,
        help="取引カレンダー（YAML）。run が使ったカレンダーと同じ識別と版であること（D07 §4.1）",
    )
    evaluate_command.add_argument(
        "--out",
        type=Path,
        default=Path("."),
        help="成果物の基点（runs/<run_id>/ を探す位置。既定は現在のディレクトリ）",
    )
    evaluate_command.add_argument(
        "--metric-set-version",
        type=int,
        default=METRIC_SET_VERSION,
        help=f"指標集合の版（既定は {METRIC_SET_VERSION}）",
    )
    return parser


# --- accept -----------------------------------------------------------------


def _write_snapshot(
    store: SnapshotStore,
    directory: str,
    pending: PendingSnapshot,
    manifest: SnapshotManifest,
    *,
    provisional_report: IntegrityReport | None = None,
) -> None:
    """検査報告・partition の実体・manifest をこの順で書く（D03 §4 の 7〜8）。

    manifest を最後に書くのは、manifest があるのに実体や報告が無い状態を残さないため
    である。読み取りの関門は manifest と報告の両方を照合するので、manifest が先にできて
    いると「開けない snapshot」が残る。

    `provisional_report` は確定段階で 5〜7 を再実行した場合の暫定報告（人間が分類の根拠に
    した報告）。最終報告と異なるときだけ `integrity_report_provisional.json` として残す
    （D03 §3.7 v1.7。同じなら manifest の2つのダイジェストが一致し、ファイルは要らない）。
    """
    store.write_integrity_report(directory, pending.report)
    if provisional_report is not None and manifest.provisional_report_ref != (
        manifest.integrity_report_ref
    ):
        store.write_provisional_report(directory, provisional_report)
    for partition_id, bars in sorted(pending.partition_bars.items(), key=lambda pair: str(pair[0])):
        store.write_partition(directory, partition_id, bars)
    store.write_manifest(directory, manifest)


def _run_accept(args: argparse.Namespace, out: _Writer) -> int:
    """受入れの 1〜8 を実行する（D03 §4、§10 の `accept`）。"""
    datasource = load_datasource(args.datasource)
    calendar = load_calendar(args.calendar)
    timeframe_defs = load_timeframes(args.timeframes)
    symbol_specs = load_symbol_specs(args.symbols)

    service = composition.acceptance_service(
        repo_root=args.repo_root,
        snapshots_root=args.out,
        datasource=datasource,
        calendar=calendar,
        timeframe_defs=timeframe_defs,
    )

    # 受入れの対象は「銘柄仕様 × 宣言された時間足」の組。銘柄の順は仕様の読込が
    # ファイル名の昇順に固定しているので、実行ごとに同じ順になる。識別子は順に依存
    # しないが、失敗の出方を揃えるために順そのものも決めておく。
    targets = [
        (symbol, timeframe_id)
        for symbol in sorted(symbol_specs, key=str)
        for timeframe_id in datasource.timeframes
    ]
    out.line(f"受入れ対象: {len(targets)} ファイル（{len(symbol_specs)} 銘柄）")

    created_at = composition.now_utc()
    pending = service.accept(targets, created_at=created_at)
    provisional = str(pending.provisional_id)
    directory = f"{PENDING_DIRECTORY}/{provisional}"

    _write_snapshot(service.store, directory, pending, pending.manifest)

    out.line("")
    out.line(f"暫定の識別子（provisional_id）: {provisional}")
    out.line(f"出力先: {args.out / PENDING_DIRECTORY / provisional}")
    out.line(f"受入れ実行時刻（識別子には含めない）: {created_at}")
    out.line("")
    out.lines(summary.series_lines(pending.manifest))
    out.line("")
    out.lines(summary.partition_lines(pending.manifest))
    out.line("")
    out.lines(summary.findings_lines(pending.report))

    warning_lines = summary.warning_summary_lines(pending.report)
    if warning_lines:
        out.line("")
        out.line("人間の分類が要る警告（分類対象の2種別）の件数規模:")
        out.lines(warning_lines)

    out.line("")
    out.line(
        "暫定 snapshot は as-of ビュー・公開フィードから読めない。"
        " 分類対象の警告（存在すべき足の欠落・休場帯の足）を分類して"
        " `odyssey-fx data classify` を実行すること（D03 §3.7.1 の 1・2、§3.9）"
    )
    return _EXIT_OK


# --- classify ---------------------------------------------------------------


def _read_partitions(
    store: SnapshotStore, snapshot_dir: str, manifest: SnapshotManifest
) -> dict[PartitionId, tuple[Bar, ...]]:
    """manifest に記録された partition の足をすべて読む。"""
    return {
        record.partition_id: tuple(store.read_partition(snapshot_dir, record.partition_id))
        for record in manifest.partitions
    }


def _rerun_calendar(
    decisions_file: ClassificationDecisionFile, manifest: SnapshotManifest
) -> TradingCalendar:
    """確定段階の再実行に使うカレンダーを決める（D03 §4 の 9・除外規則）。

    分類ファイルがカレンダーを指していればそれを使う。指していないのに再実行が要る
    （セッション外データ異常の分類がある）場合は失敗させる。カレンダーを変えない再実行でも、
    暫定 snapshot と同じ版のカレンダーを分類ファイルの `calendar` に書いてもらう
    （D03 v1.8 §10、2026-09-24 の人間の決定）。
    """
    if decisions_file.calendar is None:
        raise ConfigError(
            "セッション外データ異常（OUT_OF_SESSION_DATA）の分類があるので、受入れの 5〜7 を"
            " 再実行して足を除外する必要がある（D03 §4 の除外規則）。分類ファイルの"
            " `calendar` に再実行で使うカレンダーを書くこと。カレンダーを変えない場合は"
            f" 暫定 snapshot と同じ {manifest.conversion.calendar_id} 版"
            f" {manifest.conversion.calendar_version} のファイルを指す"
        )
    # カレンダーの識別と版の検査（休場・営業例外には同じカレンダーの版の引き上げが要る）は
    # 確定（`acceptance.finalize`）が分類の内容と合わせて行う。
    return decisions_file.calendar


def _run_classify(args: argparse.Namespace, out: _Writer) -> int:
    """分類を記入して snapshot を確定する（D03 §4 の 9、§10 の `classify`）。"""
    store = composition.snapshot_store(args.out)
    pending_directory = f"{PENDING_DIRECTORY}/{args.pending}"

    manifest = store.read_manifest(pending_directory)
    report = store.read_integrity_report(pending_directory)
    if str(manifest.snapshot_id()) != args.pending:
        raise ConfigError(
            f"{pending_directory} の manifest は暫定の識別子 {manifest.snapshot_id()} を"
            f" 表しており、指定された {args.pending} と一致しない（D03 §3.7.1）"
        )

    # **暫定 snapshot の内容が記録どおりかを、何かを再利用する前に確かめる**（D03 §4
    # 「再実行の入力」）。分類は暫定 partition を読み戻して最終 snapshot を組み立てるので、
    # 照合しないと、書き換えられた足がそのまま正当な partition として記録される。
    provisional = PendingSnapshot(
        manifest=manifest,
        report=report,
        partition_bars=_read_partitions(store, pending_directory, manifest),
    )
    require_recorded_content(manifest, report, provisional.partition_bars)

    # 分類の系列（`all` を含む）は、暫定 snapshot が実際に持つ系列から解決する。
    decisions_file = load_classification_decisions(
        args.decisions, [record.series_id for record in manifest.series]
    )
    decisions = decisions_file.decisions

    if requires_rerun(decisions, calendar_changed=decisions_file.calendar is not None):
        # カレンダーの新版を伴う分類、またはセッション外データ異常の分類があれば、受入れの
        # 5〜7（カレンダー照合・上位足の生成・partition 分け）を再実行する（D03 §4 の 9）。
        calendar = _rerun_calendar(decisions_file, manifest)
        excluded = out_of_session_exclusions(provisional, decisions)
        out.line(
            f"受入れの 5〜7 を再実行する（カレンダー {calendar.id} 版 {calendar.version}、"
            f"除外する休場帯の足 {len(excluded)} 本）（D03 §4 の 9）"
        )
        out.line("原ファイルは読み直さず、暫定 snapshot の足をそのまま使う")
        # **原ファイルを読み直さない**。読み直すと、人間が分類の根拠にした報告には無かった
        # 内容（原ファイルの差し替え・価格の書き換え・設定の変更）が最終 snapshot に
        # 入りうる。
        final = reaccept_with_calendar(
            provisional,
            calendar=calendar,
            timeframe_defs=load_timeframes(args.timeframes),
            boundaries=INITIAL_ACCESS_BOUNDARIES,
            aggregation_targets=composition.AGGREGATION_TARGETS,
            excluded=excluded,
        )
        # 突き合わせは暫定報告と再実行後の報告の和集合に対して行う。再実行が新たな警告を
        # 生んだ場合は、未分類として列挙されて失敗する（D03 §4 の反復的な分類）。
        finalized = finalize(final, decisions, provisional=provisional)
    else:
        final = provisional
        finalized = finalize(final, decisions)
    final_id = str(finalized.snapshot_id)

    # 既に確定済みの snapshot は**上書きしない**。同じ原ファイル・設定・分類なら同じ最終
    # 識別子になるので、確定をもう一度走らせると同じディレクトリを指す。そこには既に
    # 承認（`approval`）と価格基準の宣言記録（`declaration_record`）が入っているかもしれず、
    # 書き直すとそれらが消える。承認は人間の確認の記録なので、黙って失わせない。
    if (args.out / final_id / "manifest.json").is_file():
        raise ConfigError(
            f"{args.out / final_id} は既に確定済みである。承認と価格基準の宣言記録を"
            " 保持するため上書きしない。確定し直す場合は、先に確定済みの snapshot を"
            f" 移動または削除すること（暫定 snapshot は {pending_directory} に残している）"
        )

    _write_snapshot(
        store,
        final_id,
        final,
        finalized.manifest,
        provisional_report=None if final is provisional else provisional.report,
    )

    # 実体を最終ディレクトリへ書き終えてから暫定ディレクトリを畳む。先に消すと、
    # 書き出しが途中で失敗したときに暫定も確定も残らない。
    shutil.rmtree(args.out / PENDING_DIRECTORY / args.pending, ignore_errors=True)

    resolved = finalized.manifest.resolved_classifications
    out.line("")
    out.line(f"最終の識別子（snapshot_id）: {final_id}")
    out.line(f"確定先: {args.out / final_id}")
    out.line(f"記入した分類: {len(decisions)} 件（警告ごとに解決すると {len(resolved)} 件）")
    out.lines(summary.resolved_lines(finalized.manifest))
    out.line("")
    out.line(
        "確定しただけでは読めない。`odyssey-fx data approve` で承認を記入するまで、"
        " as-of ビュー・公開フィードから読み取り対象にならない（D03 §3.7.1 の 3）"
    )
    return _EXIT_OK


# --- approve ----------------------------------------------------------------


def _run_approve(args: argparse.Namespace, out: _Writer) -> int:
    """承認と価格基準の宣言記録を記入する（D03 §3.7.1 の 2、§10 の `approve`）。"""
    if PENDING_DIRECTORY in Path(args.snapshot).parts:
        raise ConfigError(
            f"{args.snapshot!r} は暫定 snapshot である。暫定 snapshot は承認の対象に"
            " ならず、常に読めない（D03 §3.7.1 の 1・3）"
        )

    store = composition.snapshot_store(args.out)
    manifest = store.read_manifest(args.snapshot)
    report = store.read_integrity_report(args.snapshot)
    if str(manifest.snapshot_id()) != args.snapshot:
        raise ConfigError(
            f"{args.snapshot} の manifest は識別子 {manifest.snapshot_id()} を表しており、"
            " ディレクトリ名と一致しない。確定段階を経た snapshot だけを承認できる"
            "（D03 §3.7.1 の 2）"
        )
    if manifest.is_approved:
        raise ConfigError(
            f"{args.snapshot} は既に承認されている"
            f"（承認者 {manifest.approval.approved_by if manifest.approval else ''}）"
        )

    # 承認は「この内容でよい」という人間の確認なので、**承認の時点で**内容が manifest の
    # 記録どおりであることを確かめる。読み取りの関門でも同じ検査をするが、そこで初めて
    # 気づく形だと「承認済みなのに読めない snapshot」ができてしまう（D03 §3.7.1）。
    # 確定段階で再実行した snapshot は暫定報告も照合する（D03 §3.7 v1.7）。
    provisional_report = store.read_provisional_report_for(args.snapshot, manifest)
    require_recorded_content(
        manifest,
        report,
        _read_partitions(store, args.snapshot, manifest),
        provisional_report=provisional_report,
    )

    # 確定段階を経た snapshot だけが承認できる形にする。分類を飛ばした承認を防ぐため、
    # 読み取りの関門と同じ規則で、暫定報告と最終報告の和集合の分類対象の警告に分類が過不足
    # なく対応し、解決済みの分類がそれと一致することを確かめる（D03 §4 の 9 v1.7）。
    require_recorded_classification(
        manifest,
        report,
        report if provisional_report is None else provisional_report,
        error=ConfigError,
    )

    approved_at = composition.now_utc()
    finalized = FinalizedSnapshot(manifest=manifest)
    approved = approve(
        finalized,
        Approval(approved_by=args.by, approved_at=approved_at, comment=args.comment),
    )
    # 価格基準の宣言の記録（誰がいつ宣言したか）は識別子の計算対象外である（D03 §3.7.1）。
    # 承認と同じ操作で記入するので、承認者と承認時刻をそのまま宣言の記録に使う。
    approved = _with_declaration_record(
        approved, DeclarationRecord(declared_by=args.by, declared_at=approved_at)
    )
    if str(approved.snapshot_id()) != args.snapshot:  # pragma: no cover - 対象外のはず
        raise ConfigError(
            "承認と宣言の記録が識別子を変えてしまった。どちらも識別子の計算対象では"
            " ないはずである（D03 §3.7.1）"
        )
    store.write_manifest(args.snapshot, approved)

    out.line(f"承認しました: {args.snapshot}")
    out.line(f"承認者: {args.by}")
    out.line(f"承認日時（識別子には含めない）: {approved_at}")
    if args.comment:
        out.line(f"コメント: {args.comment}")
    out.line(f"価格基準の宣言: {approved.basis_declaration.value.value}（検証済みではない）")
    return _EXIT_OK


def _with_declaration_record(
    manifest: SnapshotManifest, record: DeclarationRecord
) -> SnapshotManifest:
    """宣言の記録を記入した manifest を返す（D03 §3.7）。

    `SnapshotManifest` は識別子の計算対象になる列だけに専用の差し替え関数を持つので、
    計算対象外のこの記録はここで組み直す。識別子は変わらない。
    """
    return SnapshotManifest(
        created_at=manifest.created_at,
        basis_declaration=BasisDeclaration(
            value=manifest.basis_declaration.value,
            verified=manifest.basis_declaration.verified,
        ),
        sources=manifest.sources,
        conversion=manifest.conversion,
        series=manifest.series,
        partitions=manifest.partitions,
        integrity_report_ref=manifest.integrity_report_ref,
        closure_decisions=manifest.closure_decisions,
        legacy_access=manifest.legacy_access,
        declaration_record=record,
        approval=manifest.approval,
        provisional_report_ref=manifest.provisional_report_ref,
        resolved_classifications=manifest.resolved_classifications,
    )


# --- run -------------------------------------------------------------------


def _load_run_configs(
    args: argparse.Namespace,
) -> tuple[
    TradingCalendar,
    dict[str, TimeframeDefinition],
    dict[Symbol, SymbolSpec],
    ExperimentConfig,
]:
    """run と評価に共通の設定を読む（D01 §10.1）。

    時間足定義を先に読むのは、系列の文字列（`USDJPY/1h/bid`）が時間足の版を持たず、版を
    定義の読込結果から解決するためである（D03 §3.1）。
    """
    calendar = load_calendar(args.calendar)
    timeframe_defs = dict(load_timeframes(args.timeframes))
    symbol_specs = dict(load_symbol_specs(args.symbols))
    experiment = load_experiment(args.experiment, timeframe_defs, INITIAL_CATALOG)
    return calendar, timeframe_defs, symbol_specs, experiment


def _run_run(args: argparse.Namespace, out: _Writer) -> int:
    """実験設定から1回の run を実行する（D06 §4.2、§10 の `run`）。"""
    calendar, timeframe_defs, symbol_specs, experiment = _load_run_configs(args)

    out.line(f"実験: {experiment.experiment_id} v{experiment.version}")
    out.line(f"snapshot: {experiment.snapshot_ref.snapshot_id}")
    out.line(f"run 区間: {experiment.run_interval}")
    out.line(f"執行系列: {experiment.execution_series}")

    outcome = composition.execute_run(
        experiment=experiment,
        calendar=calendar,
        timeframe_defs=timeframe_defs,
        symbol_specs=symbol_specs,
        snapshots_root=args.snapshots,
        artifacts_root=args.out,
        repo_root=args.repo_root,
        replace=args.replace,
    )
    result = outcome.result

    out.line("")
    out.line(f"実行の識別子（run_id）: {outcome.run_id}")
    out.line(f"出力先: {outcome.directory}")
    out.line(f"結末: {result.status.value}")
    out.line(f"完了した取引: {result.trade_count} 件")
    out.line(f"生成した取引機会: {result.opportunity_count} 件")
    out.line(f"解決できなかった足内競合: {result.unresolved_intrabar_count} 件")
    out.line(f"swap / rollover の計上: {'あり' if result.swap_modeled else 'なし'}（ADR-0029）")
    summaries = result.summaries
    if summaries is not None:
        out.line("")
        out.line(f"確定損益: {summaries.realized}")
        out.line(f"含み込み資産: {summaries.equity_with_mtm}（参考値）")
        out.line(f"仮決済損益: {summaries.hypothetical_closed}（参考値）")
    if result.status is not RunStatus.COMPLETED:
        out.line("")
        out.line(
            "この run は正常完走していない。評価は指標を算出せず、状態と診断だけを出す（D07 §10.1）"
        )
    out.line("")
    out.line(f"評価するには `odyssey-fx evaluate --run {outcome.run_id}` を実行すること")
    return _EXIT_OK


# --- evaluate ---------------------------------------------------------------


def _run_evaluate(args: argparse.Namespace, out: _Writer) -> int:
    """保存済みの run を評価する（D07 §4・§8、§10 の `evaluate`）。"""
    try:
        run_id = RunId(ContentDigest.sha256(args.run))
    except KernelValueError as exc:
        raise ConfigError(
            f"`--run` は実行の識別子（16進64文字）を書くこと（{args.run!r}）: {exc}"
        ) from exc

    if args.metric_set_version != METRIC_SET_VERSION:
        # **式のある版だけを受ける**。版の番号だけを変えても評価は現在の版の式で走るので、
        # 「別の指標集合で作った」と名乗る成果物ができてしまう（D07 §9.2 は版を評価の
        # 識別子の材料にしている）。実装が持つ指標集合は最新の1版だけである（D07 §5.5）。
        raise ConfigError(
            f"指標集合の版 {args.metric_set_version} の式は無い。"
            f" この実装が持つのは版 {METRIC_SET_VERSION} だけである（D07 §5.5）"
        )
    calendar = load_calendar(args.calendar)
    outcome = composition.evaluate_saved_run(
        run_id=run_id,
        artifacts_root=args.out,
        calendar=calendar,
        metric_set_version=args.metric_set_version,
    )
    report = outcome.report
    manifest = report.manifest

    out.line(f"評価の識別子（run_evaluation_id）: {manifest.run_evaluation_id}")
    out.line(f"出力先: {outcome.directory}")
    out.line(f"評価の状態: {report.status.value}")
    out.line(
        "run の結末: "
        + (
            "（run manifest を読めない）"
            if manifest.run_status is None
            else manifest.run_status.value
        )
    )
    out.line(f"指標集合の版: {manifest.metric_set_version}")
    out.line(f"取引カレンダー: {manifest.calendar_ref[0]} 版 {manifest.calendar_ref[1]}")
    out.line(f"結果のダイジェスト（result_digest）: {manifest.result_digest.hex}")
    out.line(
        "swap / rollover の計上: "
        f"{'あり' if manifest.swap_modeled else 'なし'}（ADR-0029。manifest の必須項目）"
    )
    if (
        manifest.run_code_digest is not None
        and manifest.evaluation_code_digest != manifest.run_code_digest
    ):
        out.line("")
        out.line(
            "注意: 評価したコードと run を実行したコードが違う"
            f"（評価 {manifest.evaluation_code_digest.digest.hex[:12]} /"
            f" 実行 {manifest.run_code_digest.digest.hex[:12]}）"
        )
    out.line("")
    out.lines(summary.consistency_lines(report.checks))
    if report.metrics:
        out.line("")
        out.lines(summary.metric_lines(report.metrics))
    if report.trades:
        out.line("")
        out.lines(summary.trade_lines(report.trades))
    if report.status is not EvaluationStatus.COMPLETED:
        out.line("")
        out.line(
            "指標は算出していない。正常完走していない run（拒否）と、致命の整合検査が"
            " 不合格だった評価（失敗）では、算出できた数値も出さない（D07 §10.1）"
        )
    return _EXIT_OK


# --- 入口 -------------------------------------------------------------------


class _Writer:
    """画面への出力をまとめる小さな入れ物。

    テストが出力を捕まえられるよう、書き出し先を差し替えられる形にしておく。
    """

    __slots__ = ("_stream",)

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def line(self, text: str) -> None:
        """1行書く。"""
        self._stream.write(text + "\n")

    def lines(self, texts: Sequence[str]) -> None:
        """複数行書く。"""
        for text in texts:
            self.line(text)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI の入口（`pyproject.toml` の `[project.scripts]` が指す関数）。

    設定・データの誤り（設定の読込・検証の失敗、市場データの構造エラー、共通カーネルの
    値エラー、ファイルの不在）は行き先の分かる1行として表示し、終了コード 1 で終える。
    想定していない失敗はそのまま送出し、traceback を残す。
    """
    args = build_parser().parse_args(argv)
    out = _Writer(sys.stdout)
    handlers: dict[str, Callable[[argparse.Namespace, _Writer], int]] = {
        "accept": _run_accept,
        "classify": _run_classify,
        "approve": _run_approve,
        "run": _run_run,
        "evaluate": _run_evaluate,
    }
    try:
        return handlers[args.command](args, out)
    except (ConfigError, KernelValueError, FileNotFoundError, ValueError) as exc:
        sys.stderr.write(f"失敗: {exc}\n")
        return _EXIT_FAILED


if __name__ == "__main__":  # pragma: no cover - スクリプト入口
    raise SystemExit(main())
