"""コマンドライン入口（D03 §10）。

暫定の識別子を使う二段階フロー（D03 §3.7.1）に対応する3コマンドを提供する。CLI ライブラリ
は段階2まで標準の `argparse` を使う（D03 §10）。

- `odyssey-fx data accept`: 受入れの 1〜8 を実行し、`data/snapshots/_pending/<暫定 ID>/`
  に暫定 manifest・検査報告・partition を書く。
- `odyssey-fx data classify`: 人間の分類を記入し、最終の識別子を計算して
  `data/snapshots/<最終 ID>/` へ確定する。
- `odyssey-fx data approve`: 確定済み snapshot に承認と価格基準の宣言記録を記入する。

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
    ConfigError,
    load_calendar,
    load_closure_decisions,
    load_datasource,
    load_symbol_specs,
    load_timeframes,
)
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.marketdata.application.acceptance import (
    FinalizedSnapshot,
    PendingSnapshot,
    approve,
    finalize,
    reaccept_with_calendar,
)
from odyssey_fx.marketdata.application.ports import SnapshotStore
from odyssey_fx.marketdata.application.report_digest import integrity_report_digest_hex
from odyssey_fx.marketdata.application.snapshot_access import (
    PENDING_DIRECTORY,
    classification_mismatch,
    require_matching_partition_content,
)
from odyssey_fx.marketdata.domain.access import INITIAL_ACCESS_BOUNDARIES
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.marketdata.domain.snapshot import (
    Approval,
    BasisDeclaration,
    DeclarationRecord,
    SnapshotManifest,
)

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
        help="欠落区間の分類を記入して snapshot を確定する（D03 §4 の 9）",
    )
    classify.add_argument("--pending", required=True, help="暫定の識別子（provisional_id）")
    classify.add_argument("--decisions", type=Path, required=True, help="欠落区間の分類（YAML）")
    classify.add_argument(
        "--out", type=Path, required=True, help="snapshot の基点（data/snapshots/）"
    )
    # カレンダーを変える分類では、上位足の生成とカレンダー照合に時間足定義が要る。
    # 原データは読み直さない（暫定 snapshot の partition を再利用する）ので、列対応の
    # 宣言・銘柄仕様・リポジトリの位置は要らない（D03 §4 の 9）。
    classify.add_argument(
        "--timeframes",
        type=Path,
        default=None,
        help="時間足定義（カレンダーを変える分類で 5〜7 を再実行する場合に必要）",
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
    return parser


# --- accept -----------------------------------------------------------------


def _write_snapshot(
    store: SnapshotStore,
    directory: str,
    pending: PendingSnapshot,
    manifest: SnapshotManifest,
) -> None:
    """検査報告・partition の実体・manifest をこの順で書く（D03 §4 の 7〜8）。

    manifest を最後に書くのは、manifest があるのに実体や報告が無い状態を残さないため
    である。読み取りの関門は manifest と報告の両方を照合するので、manifest が先にできて
    いると「開けない snapshot」が残る。
    """
    store.write_integrity_report(directory, pending.report)
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
        out.line("人間の分類が要る警告の件数規模:")
        out.lines(warning_lines)

    out.line("")
    out.line(
        "暫定 snapshot は as-of ビュー・公開フィードから読めない。"
        " 警告を休場 / 欠損に分類して `odyssey-fx data classify` を実行すること"
        "（D03 §3.7.1 の 1・2）"
    )
    return _EXIT_OK


# --- classify ---------------------------------------------------------------


def _pending_from_store(
    store: SnapshotStore,
    snapshot_dir: str,
    manifest: SnapshotManifest,
    report: IntegrityReport,
) -> PendingSnapshot:
    """保存済みの暫定 snapshot を読み戻す（manifest・報告・partition ごとの足）。"""
    return PendingSnapshot(
        manifest=manifest,
        report=report,
        partition_bars={
            record.partition_id: tuple(store.read_partition(snapshot_dir, record.partition_id))
            for record in manifest.partitions
        },
    )


def _require_timeframes_argument(args: argparse.Namespace) -> None:
    """カレンダーを変える分類に必要な引数が揃っているか確かめる（D03 §4 の 9）。

    要るのは時間足定義だけである。上位足の生成とカレンダー照合に使う。原データは
    読み直さないので、列対応の宣言も銘柄仕様も要らない。
    """
    if args.timeframes is None:
        raise ConfigError(
            "分類がカレンダーの新版を指しているので、受入れの 5〜7 を再実行する必要がある。"
            " 時間足定義（--timeframes）を指定すること（D03 §4 の 9）"
        )


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

    # 分類の系列は、暫定 snapshot が実際に持つ系列から解決する。分類ファイルの系列表記
    # （`USDJPY/1h/bid`）には時間足の版が含まれないので、版を決め打つと版 2 以降の定義を
    # 使った snapshot で記録が食い違う（D03 §3.1）。
    decisions_file = load_closure_decisions(
        args.decisions, [record.series_id for record in manifest.series]
    )

    if decisions_file.calendar_path is not None:
        # カレンダーを変える分類は、受入れの 5〜7（カレンダー照合・上位足の生成・
        # partition 分け）を新しいカレンダーで再実行する（D03 §4 の 9）。報告も
        # partition も変わるので、暫定段階からやり直すのと同じ手順になる。
        _require_timeframes_argument(args)
        out.line(f"分類がカレンダーの新版を指している: {decisions_file.calendar_path}")
        out.line("受入れの 5〜7 を新しいカレンダーで再実行する（D03 §4 の 9）")
        out.line("原ファイルは読み直さず、暫定 snapshot の足をそのまま使う")

        # **原ファイルを読み直さない**。読み直すと、人間が分類の根拠にした報告には無かった
        # 内容（原ファイルの差し替え・価格の書き換え・設定の変更）が最終 snapshot に
        # 入りうる。価格だけの変更は構造検査もカレンダー照合も素通りするので、警告を1つも
        # 出さずに別のデータへ置き換わってしまう（D03 §4 の 9 は「5〜7 を再実行」と定める）。
        pending = reaccept_with_calendar(
            _pending_from_store(store, pending_directory, manifest, report),
            calendar=load_calendar(decisions_file.calendar_path),
            timeframe_defs=load_timeframes(args.timeframes),
            boundaries=INITIAL_ACCESS_BOUNDARIES,
            aggregation_targets=composition.AGGREGATION_TARGETS,
        )
        # 分類の正当性は**元の報告**（人間が見た報告）に対して判定する。新しいカレンダーが
        # 休場として説明した欠落は、再受入れ後の報告から正当に消えるためである
        # （D03 §4 の 9）。突き合わせの2段階は application 側が行う。
        original_report: IntegrityReport | None = report
        resolved = len(report.warnings) - len(pending.report.warnings)
        out.line(f"新しいカレンダーで説明が付いた警告: {resolved} 件")
    else:
        original_report = None
        pending = _pending_from_store(store, pending_directory, manifest, report)

    finalized: FinalizedSnapshot = finalize(
        pending, decisions_file.decisions, original_report=original_report
    )
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

    _write_snapshot(store, final_id, pending, finalized.manifest)

    # 実体を最終ディレクトリへ書き終えてから暫定ディレクトリを畳む。先に消すと、
    # 書き出しが途中で失敗したときに暫定も確定も残らない。
    shutil.rmtree(args.out / PENDING_DIRECTORY / args.pending, ignore_errors=True)

    out.line("")
    out.line(f"最終の識別子（snapshot_id）: {final_id}")
    out.line(f"確定先: {args.out / final_id}")
    out.line(f"記入した分類: {len(decisions_file.decisions)} 件")
    out.line("")
    out.line(
        "確定しただけでは読めない。`odyssey-fx data approve` で承認を記入するまで、"
        " as-of ビュー・公開フィードから読み取り対象にならない（D03 §3.7.1 の 3）"
    )
    return _EXIT_OK


# --- approve ----------------------------------------------------------------


def _require_recorded_content(
    store: SnapshotStore,
    snapshot_dir: str,
    manifest: SnapshotManifest,
    report: IntegrityReport,
) -> None:
    """実体と検査報告が manifest の記録どおりであることを確かめる（D03 §3.7.1）。

    承認の前に行う。承認は「この内容でよい」という人間の確認であり、確認した内容と保存
    されている内容が食い違ったまま承認を記入すると、**承認済みなのに読み取りの関門で
    拒否される snapshot** ができてしまう。

    確かめるのは2つ。

    1. 検査報告の内容から再計算したダイジェストが、manifest の `integrity_report_ref` と
       一致する（報告が差し替えられていない）。
    2. partition ごとの実体が manifest の記録どおりである（系列・足数・区間・内容
       ダイジェストの4点）。照合の規則は読み取りの関門と同じ関数を使う。
    """
    recomputed = integrity_report_digest_hex(report)
    if recomputed != manifest.integrity_report_ref.hex:
        raise ConfigError(
            f"{snapshot_dir} の検査報告は {recomputed} になるが、manifest は"
            f" {manifest.integrity_report_ref.hex} を記録している。報告が manifest と"
            " 食い違ったまま承認すると、承認済みでも読めない snapshot になる"
            "（D03 §3.7.1）"
        )

    for record in manifest.partitions:
        bars = store.read_partition(snapshot_dir, record.partition_id)
        require_matching_partition_content(manifest, record.partition_id, bars)


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
    _require_recorded_content(store, args.snapshot, manifest, report)

    approved_at = composition.now_utc()
    # 確定段階を経た snapshot だけが承認できる形にする。分類を飛ばした承認を防ぐため、
    # **未分類の警告が残っていないこと**をここでも確かめる。
    #
    # 見るのは未分類の警告だけで、対応する警告の無い分類（余分な分類）は見ない。読み取りの
    # 関門と同じ判断にするためである。カレンダーを変えた分類（D03 §4 の 9）では、休場と
    # して説明が付いた区間の警告が再受入れ後の報告から正当に消えるので、その分類を余分と
    # 見なすと、設計の主たる用途で作った snapshot を承認できなくなる。分類が正当かどうかの
    # 判定は確定（`acceptance.finalize`）が「人間が見た報告」に対して行っている。
    undecided, _ = classification_mismatch(report, manifest.closure_decisions)
    if undecided:
        raise ConfigError(
            f"{args.snapshot} には未分類の警告が {len(undecided)} 件残っている:"
            f" {list(undecided)}。分類を済ませてから承認すること（D03 §4 の 9）"
        )
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
    )


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
    }
    try:
        return handlers[args.command](args, out)
    except (ConfigError, KernelValueError, FileNotFoundError, ValueError) as exc:
        sys.stderr.write(f"失敗: {exc}\n")
        return _EXIT_FAILED


if __name__ == "__main__":  # pragma: no cover - スクリプト入口
    raise SystemExit(main())
