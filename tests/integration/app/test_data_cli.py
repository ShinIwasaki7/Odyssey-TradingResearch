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

import json
from pathlib import Path

import polars as pl
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


#: 間引く15分足。1時間足の欠落と**同じ1時間**を覆う4本。こうしておくと、その1時間を
#: 休場として宣言する新しいカレンダーが、全系列の欠落をまとめて説明できる（D03 §4 の 9）。
DROPPED_QUARTERS = tuple(
    UtcTime.parse(f"2022-01-06T10:{minute:02d}:00Z") for minute in (0, 15, 30, 45)
)


def _bars(
    definition: TimeframeDefinition, timeframe_id: str, calendar: TradingCalendar
) -> tuple[Bar, ...]:
    """1系列ぶんの足を作る。同じ1時間を1時間足・15分足の両方から間引いて欠落を作る。"""
    series = market.series(market.USDJPY, timeframe_id)
    skip = (DROPPED,) if timeframe_id == "1h" else DROPPED_QUARTERS
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
    decisions = _decisions_file(workspace, store, pending, "DATA_GAP")

    assert _classify(workspace, pending, decisions) == 0
    final = _final_id(workspace)
    assert (workspace / "data/snapshots" / final / "manifest.json").is_file()
    assert not (workspace / "data/snapshots/_pending" / pending).exists()


def test_the_classification_changes_the_snapshot_id(workspace: Path) -> None:
    """分類が異なれば別 snapshot である（D03 §3.7.1）。

    同じ欠落を「データ欠損」と分類した場合と「休場」と分類した場合で、最終の識別子が
    変わることを確かめる。休場としての分類にはカレンダーの新版が要る（D03 §3.4・§4 の 9）
    ので、そちらはカレンダーを変える経路で確定する。
    """
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    store = ParquetSnapshotStore(root=workspace / "data/snapshots")

    as_gap = _decisions_file(workspace, store, pending, "DATA_GAP")
    assert _classify(workspace, pending, as_gap) == 0
    gap_id = _final_id(workspace)

    # 同じ原データを受け入れ直し、今度は休場として分類する（カレンダーの版を上げる）。
    assert _accept(workspace) == 0
    pending_again = _pending_id(workspace)
    assert pending_again == pending, "同じ入力なら暫定の識別子は変わらない"
    as_closure = _decisions_with_calendar(workspace, store, pending_again, _calendar_v2(workspace))
    assert _classify_with_calendar(workspace, pending_again, as_closure) == 0

    root = workspace / "data/snapshots"
    finals = sorted(path.name for path in root.iterdir() if path.name != "_pending")
    assert len(finals) == 2, "分類が違えば別の snapshot になる"
    assert gap_id in finals
    # どちらも暫定の識別子とは異なる（分類が識別子の計算対象に入るため）。
    assert pending not in finals


# --- 承認（approve、D03 §3.7.1 の 2・3）------------------------------------


def test_a_settled_snapshot_is_not_readable_before_the_approval(workspace: Path) -> None:
    """確定しただけでは読めない（D03 §3.7.1 の 3）。"""
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    store = ParquetSnapshotStore(root=workspace / "data/snapshots")
    decisions = _decisions_file(workspace, store, pending, "DATA_GAP")
    assert _classify(workspace, pending, decisions) == 0

    with pytest.raises(SnapshotNotApproved):
        store.open_readable(_final_id(workspace))


def test_the_whole_flow_ends_with_a_readable_snapshot(workspace: Path) -> None:
    """受入れ → 分類 → 承認 の順に進めば `open_readable` で読める（D03 §3.7.1）。"""
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    store = ParquetSnapshotStore(root=workspace / "data/snapshots")
    decisions = _decisions_file(workspace, store, pending, "DATA_GAP")
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
    decisions = _decisions_file(workspace, store, pending, "DATA_GAP")
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
    decisions = _decisions_file(workspace, store, pending, "DATA_GAP")
    assert _classify(workspace, pending, decisions) == 0
    final = _final_id(workspace)

    assert _approve(workspace, final) == 0
    assert _approve(workspace, final) == 1


def test_an_unknown_snapshot_fails_cleanly(workspace: Path) -> None:
    """存在しない識別子は、traceback ではなく1行の失敗として返る。"""
    assert _approve(workspace, "0" * 64) == 1


# --- 承認時の内容照合（D03 §3.7.1）------------------------------------------
#
# 承認は「この内容でよい」という人間の確認である。確認した内容と保存されている内容が
# 食い違ったまま承認を記入すると、**承認済みなのに読み取りの関門で拒否される snapshot**
# ができてしまう。承認の時点で照合することで、その状態を作れないようにする。


def _settled(workspace: Path) -> tuple[ParquetSnapshotStore, str]:
    """受入れから確定までを済ませ、承認だけが残った状態を作る。"""
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    store = ParquetSnapshotStore(root=workspace / "data/snapshots")
    decisions = _decisions_file(workspace, store, pending, "DATA_GAP")
    assert _classify(workspace, pending, decisions) == 0
    return store, _final_id(workspace)


def test_a_snapshot_whose_report_was_altered_cannot_be_approved(workspace: Path) -> None:
    """検査報告を書き換えた snapshot は承認できない。

    報告を差し替えると manifest の記録（`integrity_report_ref`）と食い違う。承認を通して
    しまうと、読み取りの関門で初めて拒否される「承認済みなのに読めない snapshot」に
    なる。
    """
    store, final = _settled(workspace)
    report_path = workspace / "data/snapshots" / final / "integrity_report.json"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["results"] = payload["results"][:-1]  # 1件削って内容を変える
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert _approve(workspace, final) == 1
    # 承認が記入されていないので、manifest は承認前のままである。
    assert store.read_manifest(final).approval is None


def test_a_snapshot_whose_partition_was_altered_cannot_be_approved(workspace: Path) -> None:
    """partition の実体を書き換えた snapshot は承認できない。

    足数・区間・内容ダイジェストのいずれかが manifest の記録と食い違えば、読み取りの
    関門は拒否する。承認の時点で同じ照合をして、食い違ったまま承認できないようにする。
    """
    store, final = _settled(workspace)
    manifest = store.read_manifest(final)
    target = next(
        record.partition_id
        for record in manifest.partitions
        if record.bar_count > 1 and record.partition_id.series.timeframe.id == "1h"
    )
    bars = list(store.read_partition(final, target))
    store.write_partition(final, target, bars[:-1])  # 1本減らす

    assert _approve(workspace, final) == 1
    assert store.read_manifest(final).approval is None


def test_an_unaltered_snapshot_is_still_approvable(workspace: Path) -> None:
    """照合を足したことで、正しい snapshot まで承認できなくなっていないこと。

    上の2件が「何をしても失敗する」ために通っているのではないことを確かめる。
    """
    store, final = _settled(workspace)
    assert _approve(workspace, final) == 0
    assert store.read_manifest(final).approval is not None


# --- カレンダーを変える分類（D03 §4 の 9）-----------------------------------
#
# 設計が定める主たる用途である。検査が「存在すべき足が無い」と報告した区間を、人間が
# 「休場だった」と判断したら、カレンダーへ追加して版を上げ、受入れの 5〜7 を再実行する。
# そのとき、休場として説明が付いた欠落は**新しい報告から消える**。消えた警告に対応する
# 分類を「余分」として拒否すると、この用途そのものが成立しない。


def _calendar_v2(workspace: Path) -> Path:
    """間引いた1時間を休場として宣言した、版 2 のカレンダーを書く。

    ニューヨーク現地 05:00〜06:00 は、この期間（冬時間）の 10:00〜11:00Z にあたる。
    """
    source = workspace / "configs/calendars/fx_ny17_v1.yaml"
    text = source.read_text(encoding="utf-8").replace("\nversion: 1\n", "\nversion: 2\n")
    text = text.replace(
        "closures: []",
        'closures:\n  - local_date: "2022-01-06"\n'
        '    start: "05:00:00"\n'
        '    end: "06:00:00"\n'
        "    note: 分類で休場と判断した区間",
    )
    target = workspace / "configs/calendars/fx_ny17_v2.yaml"
    target.write_text(text, encoding="utf-8")
    return target


def _decisions_with_calendar(
    workspace: Path, store: ParquetSnapshotStore, pending: str, calendar: Path
) -> Path:
    """元の報告の全警告を「休場」と分類し、新しいカレンダーを指すファイルを書く。"""
    report = store.read_integrity_report(f"_pending/{pending}")
    lines = ["schema_version: 1", f"calendar: {calendar}", "decisions:"]
    for series_text, start, end in sorted(
        {
            (str(result.series), str(result.interval.start), str(result.interval.end))
            for result in report.warnings
        }
    ):
        lines += [
            f"  - series: {series_text}",
            "    interval:",
            f'      start: "{start}"',
            f'      end: "{end}"',
            "    kind: CLOSURE",
            "    note: 新しいカレンダーで休場と宣言",
        ]
    path = workspace / "decisions_calendar_v2.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _classify_with_calendar(workspace: Path, pending: str, decisions: Path) -> int:
    """カレンダーを変える分類。必要なのは時間足定義だけ（原データは読み直さない）。"""
    return main(
        [
            "data",
            "classify",
            "--pending",
            pending,
            "--decisions",
            str(decisions),
            "--out",
            str(workspace / "data/snapshots"),
            "--timeframes",
            str(workspace / "configs/calendars/timeframes_v1.yaml"),
        ]
    )


def test_a_closure_declared_in_a_new_calendar_version_settles_and_reads(
    workspace: Path,
) -> None:
    """休場としての分類 → 新カレンダー版 → 確定 → 承認 → 読める、が通る。

    D03 §4 の 9 の主たる用途である。新しいカレンダーが説明した欠落の警告は再受入れ後の
    報告から消えるが、その分類は元の報告に対して正当なので確定できなければならない。
    """
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    store = ParquetSnapshotStore(root=workspace / "data/snapshots")

    original = store.read_integrity_report(f"_pending/{pending}")
    assert original.warnings, "この試験は警告のある状態を前提にしている"
    # 分類は「系列 × 区間」ごとに1件（同じ区間に対する重複した報告は1件にまとめる）。
    expected_decisions = len({(str(r.series), str(r.interval)) for r in original.warnings})

    decisions = _decisions_with_calendar(workspace, store, pending, _calendar_v2(workspace))
    assert _classify_with_calendar(workspace, pending, decisions) == 0

    final = _final_id(workspace)
    manifest = store.read_manifest(final)
    # 新しいカレンダーの版が記録される（識別子の計算対象、D03 §3.7.1）。
    assert manifest.conversion.calendar_version == 2
    # 休場として説明が付いたので、確定後の報告に警告は残らない。
    assert not store.read_integrity_report(final).warnings
    # 分類そのものは manifest に残る（人間の判断の記録）。
    assert len(manifest.closure_decisions) == expected_decisions

    # 承認でき、読み取りの関門も通る（余分な分類で拒否されない）。
    assert _approve(workspace, final) == 0
    readable = store.open_readable(final)
    assert str(readable.snapshot_id) == final


def test_a_gap_the_new_calendar_does_not_explain_still_blocks_settling(
    workspace: Path,
) -> None:
    """新しいカレンダーでも説明できない欠落が残れば、確定させない。

    2段階の突き合わせの後半（再受入れ後の報告に残る警告は分類に含まれていること）が
    効いていることの確認。ここでは分類を1件も書かずに新カレンダーだけを指す。
    """
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    calendar = _calendar_v2(workspace)
    empty = workspace / "empty_with_calendar.yaml"
    empty.write_text(f"schema_version: 1\ncalendar: {calendar}\ndecisions: []\n", encoding="utf-8")
    # 元の報告に未分類の警告が残るので、段階1で止まる。
    assert _classify_with_calendar(workspace, pending, empty) == 1


# --- 確定済み snapshot の上書き防止（D03 §3.7.1）----------------------------


def test_classify_does_not_overwrite_a_settled_snapshot(workspace: Path) -> None:
    """同じ入力・同じ分類で確定し直しても、確定済みディレクトリを上書きしない。

    同じ原ファイル・設定・分類なら同じ最終識別子になるので、確定をもう一度走らせると
    同じディレクトリを指す。そこには承認（`approval`）と価格基準の宣言記録が入っている
    かもしれず、書き直すと人間の確認の記録が消える。
    """
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    store = ParquetSnapshotStore(root=workspace / "data/snapshots")
    decisions = _decisions_file(workspace, store, pending, "DATA_GAP")
    assert _classify(workspace, pending, decisions) == 0
    final = _final_id(workspace)
    assert _approve(workspace, final) == 0

    # もう一度受入れて、同じ分類で確定しようとする。
    assert _accept(workspace) == 0
    pending_again = _pending_id(workspace)
    assert _classify(workspace, pending_again, decisions) == 1

    # 承認と宣言の記録は残っている。
    manifest = store.read_manifest(final)
    assert manifest.approval is not None
    assert manifest.approval.approved_by == "レビュー担当"
    assert manifest.declaration_record is not None
    # 暫定ディレクトリはそのまま残る（やり直せるように）。
    assert (workspace / "data/snapshots/_pending" / pending_again).is_dir()


def test_the_calendar_change_path_does_not_reread_the_raw_files(workspace: Path) -> None:
    """受入れ後に原 CSV を書き換えても、確定した snapshot は暫定の足を保つ。

    D03 §4 の 9 が定めるのは「5〜7 の再実行」である。原ファイルを読み直すと、人間が分類の
    根拠にした報告には無かった内容が最終 snapshot に入りうる。**とくに価格だけの変更は
    構造検査もカレンダー照合も素通りする**ので、警告を1件も出さずに別のデータへ置き換わって
    しまう。ここではまさにその状況——受入れ後に価格だけを書き換える——を作り、partition の
    内容ダイジェストが暫定のときと変わらないことを確かめる。
    """
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    store = ParquetSnapshotStore(root=workspace / "data/snapshots")
    provisional = store.read_manifest(f"_pending/{pending}")
    before = {str(r.partition_id): r.digest.hex for r in provisional.partitions}

    # 原 CSV の価格を書き換える（行数も時刻も変えないので、検査は何も報告しない）。
    raw = workspace / "data/raw/market/USDJPY_1h_merged.csv"
    lines = raw.read_text(encoding="utf-8").splitlines()
    rewritten = [lines[0]]
    for line in lines[1:]:
        fields = line.split(",")
        # 始値・高値・安値・終値をまとめて動かす（OHLC の不変条件は保つ）。
        fields[1:5] = [f"{float(value) + 10:.3f}" for value in fields[1:5]]
        rewritten.append(",".join(fields))
    raw.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    decisions = _decisions_with_calendar(workspace, store, pending, _calendar_v2(workspace))
    assert _classify_with_calendar(workspace, pending, decisions) == 0

    final = _final_id(workspace)
    manifest = store.read_manifest(final)
    after = {str(r.partition_id): r.digest.hex for r in manifest.partitions}

    # 原系列（15m・1h）の partition は、暫定のときと同じ内容のままでなければならない。
    for name, digest in before.items():
        if "_1h_bid/" in name or "_15m_bid/" in name:
            assert after[name] == digest, f"{name} の内容が書き換えた原ファイルに置き換わった"

    # 原ファイルの記録（sha256・行数）も暫定のものを引き継ぐ（読み直していないため）。
    assert manifest.sources == provisional.sources

    # 変換の記録は、カレンダーの識別と版だけが新しくなる。
    assert manifest.conversion.calendar_version == 2
    assert manifest.conversion.code_version == provisional.conversion.code_version
    assert manifest.conversion.time_convention == provisional.conversion.time_convention


def test_the_calendar_change_path_needs_only_the_timeframes(workspace: Path) -> None:
    """時間足定義を渡さなければ、何をすべきかを述べて失敗する。"""
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    store = ParquetSnapshotStore(root=workspace / "data/snapshots")
    decisions = _decisions_with_calendar(workspace, store, pending, _calendar_v2(workspace))

    assert (
        main(
            [
                "data",
                "classify",
                "--pending",
                pending,
                "--decisions",
                str(decisions),
                "--out",
                str(workspace / "data/snapshots"),
            ]
        )
        == 1
    )


# --- 暫定 snapshot の内容照合（D03 §3.7.1）----------------------------------
#
# 分類は暫定 partition を読み戻して最終 snapshot を組み立てる。照合せずに読み戻すと、
# 書き換えられた足がそのまま正当な partition として記録される。しかも `sources` は元の
# 原ファイルの sha256 を保持したままなので、**出所の記録と実データが一致しない snapshot**
# ができ、それを承認できてしまう。分類の冒頭で、カレンダー変更の有無に関わらず照合する。


def _tamper_with_a_provisional_partition(workspace: Path, pending: str) -> str:
    """暫定 partition の Parquet の価格だけを書き換える（足数も時刻も変えない）。"""
    directory = workspace / "data/snapshots/_pending" / pending
    target = next(directory.glob("USDJPY_1h_bid/*/bars.parquet"))
    frame = pl.read_parquet(target)
    frame = frame.with_columns(
        [
            (pl.col(column).cast(pl.Float64) + 10).cast(pl.String)
            for column in ("open", "high", "low", "close")
        ]
    )
    frame.write_parquet(target)
    return target.name


def _tamper_with_the_provisional_report(workspace: Path, pending: str) -> None:
    """暫定の検査報告から結果を1件落とす（manifest の記録と食い違わせる）。"""
    path = workspace / "data/snapshots/_pending" / pending / "integrity_report.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["results"] = payload["results"][:-1]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _no_final_snapshot(workspace: Path) -> bool:
    """最終 snapshot が1件も作られていないか。"""
    root = workspace / "data/snapshots"
    return [path.name for path in root.iterdir() if path.name != "_pending"] == []


def test_a_tampered_provisional_partition_blocks_the_calendar_change_path(
    workspace: Path,
) -> None:
    """(a) 暫定 partition を書き換えたら、カレンダー変更付きの分類を拒否する。"""
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    store = ParquetSnapshotStore(root=workspace / "data/snapshots")
    decisions = _decisions_with_calendar(workspace, store, pending, _calendar_v2(workspace))

    _tamper_with_a_provisional_partition(workspace, pending)

    assert _classify_with_calendar(workspace, pending, decisions) == 1
    assert _no_final_snapshot(workspace), "拒否したのに最終 snapshot が作られている"


def test_a_tampered_provisional_report_blocks_the_calendar_change_path(
    workspace: Path,
) -> None:
    """(b) 暫定の検査報告を差し替えたら、カレンダー変更付きの分類を拒否する。"""
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    store = ParquetSnapshotStore(root=workspace / "data/snapshots")
    decisions = _decisions_with_calendar(workspace, store, pending, _calendar_v2(workspace))

    _tamper_with_the_provisional_report(workspace, pending)

    assert _classify_with_calendar(workspace, pending, decisions) == 1
    assert _no_final_snapshot(workspace)


def test_a_tampered_provisional_partition_blocks_the_plain_classify(workspace: Path) -> None:
    """(c) カレンダーを変えない分類でも、書き換えられた partition を拒否する。"""
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    store = ParquetSnapshotStore(root=workspace / "data/snapshots")
    decisions = _decisions_file(workspace, store, pending, "DATA_GAP")

    _tamper_with_a_provisional_partition(workspace, pending)

    assert _classify(workspace, pending, decisions) == 1
    assert _no_final_snapshot(workspace)


def test_a_tampered_provisional_report_blocks_the_plain_classify(workspace: Path) -> None:
    """(c) カレンダーを変えない分類でも、差し替えられた報告を拒否する。"""
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    store = ParquetSnapshotStore(root=workspace / "data/snapshots")
    decisions = _decisions_file(workspace, store, pending, "DATA_GAP")

    _tamper_with_the_provisional_report(workspace, pending)

    assert _classify(workspace, pending, decisions) == 1
    assert _no_final_snapshot(workspace)


def test_an_untouched_provisional_snapshot_still_settles(workspace: Path) -> None:
    """照合を足したことで、正しい暫定 snapshot まで確定できなくなっていないこと。

    上の4件が「何をしても失敗する」ために通っているのではないことを確かめる。
    """
    assert _accept(workspace) == 0
    pending = _pending_id(workspace)
    store = ParquetSnapshotStore(root=workspace / "data/snapshots")
    decisions = _decisions_file(workspace, store, pending, "DATA_GAP")

    assert _classify(workspace, pending, decisions) == 0
    assert not _no_final_snapshot(workspace)
