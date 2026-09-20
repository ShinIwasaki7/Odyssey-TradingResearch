"""3コマンドの通し試験（D03 §3.7.1・§10・§11）。

人工の原 CSV を一時ディレクトリに置き、受入れ（accept）→ 分類（classify）→ 承認
（approve）を実際のコマンドとして実行し、最後に `open_readable` で読めるところまでを
確かめる。

特に確かめること:

- 暫定段階の出力が `_pending/<暫定 ID>/` に置かれ、その識別子で読めない。
- 承認するまで読めない（D03 §3.7.1 の 3）。承認して初めて `open_readable` が通る。
- 暫定 snapshot は承認できない（D03 §3.7.1 の 1）。
- 未分類の警告が残る状態では確定できない（D03 §4 の 9）。
- 分類の内容が違えば最終の識別子が変わる（D03 §3.7.1）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from odyssey_fx.app.cli.main import main
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.errors import SnapshotNotApproved
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from tests.fixtures.synthetic import market

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIGS = REPO_ROOT / "configs"

#: 人工データの範囲。2週ぶん（週末をまたぐので、週の開閉と上位足の生成が働く）。
WINDOW = Interval(
    start=UtcTime.parse("2022-01-05T22:00:00Z"), end=UtcTime.parse("2022-01-14T22:00:00Z")
)

#: 間引く1時間足。存在すべき足の欠落（`MISSING_EXPECTED_BAR`）を1件作る。
DROPPED = UtcTime.parse("2022-01-06T10:00:00Z")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """原 CSV と設定を置いた作業場を作る。

    設定は実物を写し、銘柄仕様だけ USDJPY に絞る（試験の実行時間を短くするため）。
    実物を写すので、設定ファイルの書き方が変わればこの試験も追従する。
    """
    repo = tmp_path / "repo"
    raw = repo / "data/raw/market"
    raw.mkdir(parents=True)

    configs = repo / "configs"
    configs.mkdir()
    for name in ("calendars", "datasources"):
        _copy_tree(CONFIGS / name, configs / name)
    (configs / "symbols").mkdir()
    _copy(CONFIGS / "symbols/USDJPY.yaml", configs / "symbols/USDJPY.yaml")

    calendar = market.calendar()
    for timeframe_id, definition in (("1h", market.TF_1H), ("15m", market.TF_15M)):
        bars = _bars(definition, timeframe_id, calendar)
        (raw / f"USDJPY_{timeframe_id}_merged.csv").write_text(
            market.csv_text(bars), encoding="utf-8"
        )
    return repo


def _bars(
    definition: TimeframeDefinition, timeframe_id: str, calendar: TradingCalendar
) -> tuple[Bar, ...]:
    """1系列ぶんの足を作る。1時間足は1本だけ間引いて欠落を作る。"""
    series = market.series(market.USDJPY, timeframe_id)
    skip = (DROPPED,) if timeframe_id == "1h" else ()
    return market.make_bars(series, definition, calendar, WINDOW, skip_starts=skip)


def _copy(source: Path, target: Path) -> None:
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def _copy_tree(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.glob("*.yaml")):
        _copy(path, target / path.name)


def _accept(repo: Path) -> int:
    """受入れコマンドを実行する。"""
    configs = repo / "configs"
    return main(
        [
            "data",
            "accept",
            "--datasource",
            str(configs / "datasources/legacy_merged_csv_v1.yaml"),
            "--calendar",
            str(configs / "calendars/fx_ny17_v1.yaml"),
            "--timeframes",
            str(configs / "calendars/timeframes_v1.yaml"),
            "--symbols",
            str(configs / "symbols"),
            "--out",
            str(repo / "data/snapshots"),
            "--repo-root",
            str(repo),
        ]
    )


def _pending_id(repo: Path) -> str:
    """書かれた暫定 snapshot の識別子を1つ取り出す。"""
    pending = sorted((repo / "data/snapshots/_pending").iterdir())
    assert len(pending) == 1
    return pending[0].name


def _decisions_file(repo: Path, store: ParquetSnapshotStore, pending: str, kind: str) -> Path:
    """報告に出たすべての警告を、指定した分類で記入したファイルを書く。

    分類は人間の判断だが、この試験が見たいのは「分類が記入されれば確定できる」ことと
    「分類が違えば別 snapshot になる」ことなので、全件を同じ分類にする。

    **分類は系列と区間の組ごとに1件**書く。報告は同じ系列・同じ区間・同じ種別でも
    詳細（`detail`）が違えば別の記録になる（D03 §3.7.1 の整列鍵に詳細が入る）。上位足の
    欠落は、カレンダー照合が「足が無い」として1件、上位足の生成が「構成足が足りず生成
    できなかった」として1件、同じ区間に対して報告する。一方で警告と分類の突き合わせは
    区間全体で取るので、その区間に対する分類は1件でよい。区間ごとに2件書くと、余分な
    分類として拒否される。
    """
    report = store.read_integrity_report(f"_pending/{pending}")
    lines = ["schema_version: 1", "decisions:"]
    for series_text, start, end in sorted(
        {
            (str(result.series), str(result.interval.start), str(result.interval.end))
            for result in report.warnings
        }
    ):
        lines.extend(
            [
                f"  - series: {series_text}",
                "    interval:",
                f'      start: "{start}"',
                f'      end: "{end}"',
                f"    kind: {kind}",
                f"    note: {kind} として分類",
            ]
        )
    path = repo / f"decisions_{kind}.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _classify(repo: Path, pending: str, decisions: Path) -> int:
    return main(
        [
            "data",
            "classify",
            "--pending",
            pending,
            "--decisions",
            str(decisions),
            "--out",
            str(repo / "data/snapshots"),
        ]
    )


def _approve(repo: Path, snapshot: str) -> int:
    return main(
        [
            "data",
            "approve",
            "--snapshot",
            snapshot,
            "--by",
            "レビュー担当",
            "--comment",
            "受入れ確認済み",
            "--out",
            str(repo / "data/snapshots"),
        ]
    )


def _final_id(repo: Path) -> str:
    """確定した snapshot の識別子を取り出す（`_pending` は除く）。"""
    root = repo / "data/snapshots"
    names = sorted(path.name for path in root.iterdir() if path.name != "_pending")
    assert len(names) == 1
    return names[0]


# --- 受入れ（accept、D03 §4 の 1〜8）----------------------------------------


def test_accept_writes_the_provisional_snapshot(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """暫定 manifest・検査報告・partition が `_pending/<暫定 ID>/` に書かれる。"""
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    directory = workspace / "data/snapshots/_pending" / pending

    assert (directory / "manifest.json").is_file()
    assert (directory / "integrity_report.json").is_file()
    # 原の2系列と、生成した上位足2系列の partition がある（D03 §5）。
    partitions = sorted(path.name for path in directory.iterdir() if path.is_dir())
    assert partitions == [
        "USDJPY_15m_bid",
        "USDJPY_1d_ny17_bid",
        "USDJPY_1h_bid",
        "USDJPY_4h_ny17_bid",
    ]
    assert pending in capsys.readouterr().out


def test_accept_reports_the_findings_per_series(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """系列 × 種別の件数が表示される（D03 §10 の `accept`）。"""
    assert _accept(workspace) == 0
    printed = capsys.readouterr().out
    assert "系列ごとの足数" in printed
    assert "完全性検査" in printed
    assert "MISSING_EXPECTED_BAR" in printed


def test_accept_does_not_print_prices(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """要約に価格の統計を載せない（D03 §3.9）。

    封印期間・未分類の隔離期間の partition について、構造情報だけを出す決まりである。
    """
    assert _accept(workspace) == 0
    printed = capsys.readouterr().out
    for line in printed.splitlines():
        assert "open=" not in line
        assert "close=" not in line


def test_a_provisional_snapshot_is_not_readable(workspace: Path) -> None:
    """暫定 snapshot は承認の対象にならず、常に読めない（D03 §3.7.1 の 1・3）。"""
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    store = ParquetSnapshotStore(root=workspace / "data/snapshots")
    with pytest.raises(SnapshotNotApproved):
        store.open_readable(f"_pending/{pending}")


# --- 分類（classify、D03 §4 の 9）------------------------------------------


def test_classify_fails_while_warnings_are_unclassified(workspace: Path) -> None:
    """未分類の警告が残る場合は失敗する（D03 §10 の `classify`）。"""
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    empty = workspace / "empty.yaml"
    empty.write_text("schema_version: 1\ndecisions: []\n", encoding="utf-8")
    assert _classify(workspace, pending, empty) == 1


def test_classify_settles_the_snapshot(workspace: Path) -> None:
    """分類を記入すると最終ディレクトリへ移り、暫定ディレクトリは残らない。"""
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    store = ParquetSnapshotStore(root=workspace / "data/snapshots")
    decisions = _decisions_file(workspace, store, pending, "CLOSURE")

    assert _classify(workspace, pending, decisions) == 0
    final = _final_id(workspace)
    assert (workspace / "data/snapshots" / final / "manifest.json").is_file()
    assert not (workspace / "data/snapshots/_pending" / pending).exists()


def test_the_classification_changes_the_snapshot_id(workspace: Path) -> None:
    """分類が異なれば別 snapshot である（D03 §3.7.1）。"""
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    store = ParquetSnapshotStore(root=workspace / "data/snapshots")

    as_closure = _decisions_file(workspace, store, pending, "CLOSURE")
    assert _classify(workspace, pending, as_closure) == 0
    closure_id = _final_id(workspace)

    # 同じ原データを受け入れ直し、今度は欠損として分類する。
    assert _accept(workspace) == 0
    pending_again = _pending_id(workspace)
    assert pending_again == pending, "同じ入力なら暫定の識別子は変わらない"
    as_gap = _decisions_file(workspace, store, pending_again, "DATA_GAP")
    assert _classify(workspace, pending_again, as_gap) == 0

    root = workspace / "data/snapshots"
    finals = sorted(path.name for path in root.iterdir() if path.name != "_pending")
    assert len(finals) == 2, "分類が違えば別の snapshot になる"
    assert closure_id in finals
    # どちらも暫定の識別子とは異なる（分類が識別子の計算対象に入るため）。
    assert pending not in finals


# --- 承認（approve、D03 §3.7.1 の 2・3）------------------------------------


def test_a_settled_snapshot_is_not_readable_before_the_approval(workspace: Path) -> None:
    """確定しただけでは読めない（D03 §3.7.1 の 3）。"""
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    store = ParquetSnapshotStore(root=workspace / "data/snapshots")
    decisions = _decisions_file(workspace, store, pending, "CLOSURE")
    assert _classify(workspace, pending, decisions) == 0

    with pytest.raises(SnapshotNotApproved):
        store.open_readable(_final_id(workspace))


def test_the_whole_flow_ends_with_a_readable_snapshot(workspace: Path) -> None:
    """受入れ → 分類 → 承認 の順に進めば `open_readable` で読める（D03 §3.7.1）。"""
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    store = ParquetSnapshotStore(root=workspace / "data/snapshots")
    decisions = _decisions_file(workspace, store, pending, "CLOSURE")
    assert _classify(workspace, pending, decisions) == 0
    final = _final_id(workspace)
    assert _approve(workspace, final) == 0

    readable = store.open_readable(final)
    assert str(readable.snapshot_id) == final
    assert readable.manifest.approval is not None
    assert readable.manifest.approval.approved_by == "レビュー担当"
    # 価格基準の宣言の記録（誰がいつ宣言したか）も承認と同じ操作で入る（D03 §3.7）。
    assert readable.manifest.declaration_record is not None

    # 実体も読み戻せる。partition の内容が manifest の記録と一致していないと開けない。
    for record in readable.manifest.partitions:
        bars = store.read_partition(final, record.partition_id)
        assert len(bars) == record.bar_count


def test_the_approval_does_not_change_the_snapshot_id(workspace: Path) -> None:
    """承認は識別子の計算対象外である（D03 §3.7.1）。"""
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    store = ParquetSnapshotStore(root=workspace / "data/snapshots")
    decisions = _decisions_file(workspace, store, pending, "CLOSURE")
    assert _classify(workspace, pending, decisions) == 0
    final = _final_id(workspace)

    assert _approve(workspace, final) == 0
    assert str(store.read_manifest(final).snapshot_id()) == final


def test_a_provisional_snapshot_cannot_be_approved(workspace: Path) -> None:
    """暫定 snapshot は承認できない（D03 §3.7.1 の 1）。"""
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    assert _approve(workspace, f"_pending/{pending}") == 1


def test_a_snapshot_cannot_be_approved_twice(workspace: Path) -> None:
    """二重の承認は拒否する（誰の承認が有効かが分からなくなるため）。"""
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    store = ParquetSnapshotStore(root=workspace / "data/snapshots")
    decisions = _decisions_file(workspace, store, pending, "CLOSURE")
    assert _classify(workspace, pending, decisions) == 0
    final = _final_id(workspace)

    assert _approve(workspace, final) == 0
    assert _approve(workspace, final) == 1


def test_an_unknown_snapshot_fails_cleanly(workspace: Path) -> None:
    """存在しない識別子は、traceback ではなく1行の失敗として返る。"""
    assert _approve(workspace, "0" * 64) == 1
