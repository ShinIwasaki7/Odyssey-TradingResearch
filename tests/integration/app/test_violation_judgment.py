"""後から品質確認の条件違反と判定した閲覧の管理コマンド（`odyssey-fx gate judge-violation`）。

ADR-0035 第3項（判定の行・違反の範囲・消費の記録・commit と origin への push・やり直し）、第4項の
5・7（ロック・行の形）、ADR-0038 第8項の 3（ADR-0014 の消費遷移の直列化）。

**人工データと一時の git リポジトリだけ**を使う。封印の snapshot は、偽の MT5 の API（人工の価格）で
取得した原ファイルを受け入れ・分類して作る（`test_sealed_intake.py` の手順。モジュールで 1 回だけ
作り、テストごとに写す）。origin は一時ディレクトリの bare リポジトリで、実際の origin には触れない
（git の全体の設定も読まない）。2024 年以降の実際のデータ・旧 CSV は読まない。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from odyssey_fx.app.cli.main import main
from odyssey_fx.app.holdout_access_log import GitHoldoutAccessLog
from odyssey_fx.app.sealed_intake import existing_sealed_partitions, snapshot_sealed_partitions
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.application.ports import HoldoutAccessLog, HoldoutAccessLogStop
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.adapters.quality_access_log_store import QualityAccessLogFile
from odyssey_fx.marketdata.domain.access import AccessClass, HoldoutState
from odyssey_fx.marketdata.domain.quality_access import (
    PRE_PERMIT_MECHANISM,
    AccessPeriod,
    QualityAccessJudgment,
    QualityAccessLogContents,
    QualityAccessStart,
    QualityCheckKind,
    QualityOutputKind,
    QualityRecordKind,
    RawFileTarget,
    ToolRef,
)
from odyssey_fx.marketdata.domain.snapshot import LegacyAccessRecord, LegacyObservation
from tests.fixtures.synthetic import market
from tests.fixtures.synthetic import snapshots as synthetic
from tests.integration.app.test_sealed_intake import (
    SPECS,
    _accept,
    _classify,
    _fetch,
    _pending,
    _repo,
)

ROOT = Path(__file__).resolve().parents[3]
JUDGED_AT = "2026-11-20T03:00:00Z"
#: 人工の価格（画面出力・記録に現れないことを確かめる印）。
PRICES = tuple(spec[2] for spec in SPECS.values())


# --- 一時の git リポジトリ ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """git の全体・システムの設定を読まず、一時の名前で commit する（実際の origin に触れない）。"""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(key, raising=False)


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def _publish_repo(repo: Path, tmp_path: Path) -> Path:
    """`repo` を git の作業木にし、一時ディレクトリの bare の origin へ main を push する。"""
    assert tmp_path in repo.parents  # 一時ディレクトリの中だけで動かす
    shutil.copy(ROOT / ".gitignore", repo / ".gitignore")
    _git(repo, "init", "--quiet")
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "base")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(origin)], check=True)
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "--quiet", "origin", "main")
    return origin


def _origin_head(origin: Path) -> str:
    return _git(origin, "rev-parse", "refs/heads/main")


# --- 人工の封印の snapshot（モジュールで 1 回作る）-------------------------------------------


@pytest.fixture(scope="module")
def sealed_base(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """確定済みの封印の snapshot（USDJPY・前向きの封印の区分の 1 週・5 系列）を持つリポジトリ。"""
    work = tmp_path_factory.mktemp("sealed_base")
    repo = _repo(work)
    copy = _fetch(repo, work)
    assert _accept(repo, copy) == 0
    (pending_dir,) = _pending(repo)
    assert _classify(repo, pending_dir.name, work) == 0
    shutil.rmtree(repo / "data" / "snapshots" / "_pending", ignore_errors=True)
    yield repo


def _sealed_repo(sealed_base: Path, tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    shutil.copytree(sealed_base, repo)
    return repo, _publish_repo(repo, tmp_path)


def _rows(repo: Path) -> QualityAccessLogContents:
    contents = QualityAccessLogFile(repo).read()
    assert isinstance(contents, QualityAccessLogContents)
    return contents


def _access_ids(repo: Path) -> list[str]:
    """受入れ・分類の閲覧の識別子（記録の順）。"""
    return [row.access_id for row in _rows(repo).rows if isinstance(row, QualityAccessStart)]


def _final(repo: Path) -> Path:
    (final,) = [p for p in (repo / "data" / "snapshots").iterdir() if not p.name.startswith("_")]
    return final


def _judge(repo: Path, access_id: str, *extra: str) -> int:
    return main(
        [
            "gate",
            "judge-violation",
            "--access-id",
            access_id,
            "--conditions",
            "1",
            "3",
            "--by",
            "人間",
            "--judged-at",
            JUDGED_AT,
            "--repo-root",
            str(repo),
            *extra,
        ]
    )


def _no_price(text: str) -> None:
    for price in PRICES:
        assert price not in text
    assert "157.4" not in text


def _snapshot_bytes(repo: Path) -> dict[str, bytes]:
    return {
        path.relative_to(repo).as_posix(): path.read_bytes()
        for path in sorted(repo.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(repo).parts
    }


def _retroactive_start(repo: Path, access_id: str, symbol: str) -> None:
    """原ファイルだけを対象にした、後から記録した開始の行（人工。ハッシュ無し）。"""
    row = QualityAccessStart(
        row_id=f"{access_id}-start",
        access_id=access_id,
        accessed_at=UtcTime.parse("2026-11-15T00:00:00Z"),
        tool=ToolRef(name="synthetic", version="1"),
        classes=frozenset({AccessClass.HOLDOUT_SINCE_2024}),
        period=AccessPeriod(start=UtcTime.parse("2024-01-01T00:00:00Z"), end=None),
        output_kinds=frozenset({QualityOutputKind.COUNTS}),
        permit_id=PRE_PERMIT_MECHANISM,
        targets=(
            RawFileTarget(location=f"data/raw/{symbol}.csv", sha256=None, symbol=Symbol(symbol)),
        ),
        check_kind=QualityCheckKind.AUDIT_COUNT,
        record_kind=QualityRecordKind.RETROACTIVE,
    )
    appended = QualityAccessLogFile(repo).append(row)
    assert appended == row


# --- 乾式実行 --------------------------------------------------------------------------


def test_the_dry_run_writes_nothing_and_prints_the_row_and_the_counts(
    sealed_base: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, origin = _sealed_repo(sealed_base, tmp_path)
    accept_id = _access_ids(repo)[0]
    before, head = _snapshot_bytes(repo), _git(repo, "rev-parse", "HEAD")
    capsys.readouterr()
    assert _judge(repo, accept_id, "--dry-run") == 0
    printed = capsys.readouterr().out
    assert "乾式実行" in printed
    assert '"row_kind":"JUDGMENT"' in printed and '"violated_conditions":[1,3]' in printed
    assert "違反の範囲に重なる封印の partition: 5（snapshot 1）" in printed
    assert "消費の記録を追記するもの（SEALED）: 5、既に使用済み（CONSUMED）: 0" in printed
    _no_price(printed)
    assert _snapshot_bytes(repo) == before
    assert _git(repo, "rev-parse", "HEAD") == head == _origin_head(origin)


# --- 判定の行・違反の範囲・消費の記録・commit と push ------------------------------------------


def test_a_raw_file_judgment_consumes_every_sealed_partition_of_the_symbol_and_pushes(
    sealed_base: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """受入れ（原ファイルが対象）の閲覧の違反判定: 銘柄の封印の区分の全体（仮置き S3-7）。"""
    repo, origin = _sealed_repo(sealed_base, tmp_path)
    accept_id = _access_ids(repo)[0]
    capsys.readouterr()
    assert _judge(repo, accept_id) == 0
    printed = capsys.readouterr().out
    _no_price(printed)
    assert "判定の行: この回に追記した" in printed
    assert "消費の記録: この回に 5 / 5 件追記した" in printed
    # 判定の行の形（ADR-0035 第4項の 7）。
    judgment = _rows(repo).rows[-1]
    assert isinstance(judgment, QualityAccessJudgment)
    assert judgment.access_id == accept_id
    assert judgment.violated_conditions == frozenset({1, 3})
    assert judgment.judged_by == "人間" and str(judgment.judged_at) == JUDGED_AT
    raw = json.loads((repo / "research/quality_access_log.jsonl").read_bytes().splitlines()[-1])
    assert set(raw) == {
        "access_id",
        "judged_at",
        "judged_by",
        "row_id",
        "row_kind",
        "schema_version",
        "violated_conditions",
    }
    assert raw["row_kind"] == "JUDGMENT" and raw["schema_version"] == 1
    # 消費の記録（今の AccessLogEntry の形の CONSUMED。理由として閲覧と判定の行を指す）。
    final = _final(repo)
    lines = [json.loads(line) for line in (final / "access_log.jsonl").read_text().splitlines()]
    assert len(lines) == 5 and {line["kind"] for line in lines} == {"CONSUMED"}
    assert {line["purpose"] for line in lines} == {
        f"VIOLATION_JUDGMENT access_id={accept_id} judgment_row_id={judgment.row_id}"
    }
    assert {line["actor"] for line in lines} == {"人間"}
    states = existing_sealed_partitions(repo / "data" / "snapshots")
    assert len(states) == 5 and {item.state for item in states} == {HoldoutState.CONSUMED}
    # 変えた 2 つのファイルだけを 1 つの commit にし、origin の main へ push した。
    head = _git(repo, "rev-parse", "HEAD")
    assert head == _origin_head(origin)
    changed = _git(repo, "show", "--name-only", "--format=", head).splitlines()
    assert sorted(changed) == sorted(
        [
            "research/quality_access_log.jsonl",
            f"data/snapshots/{final.name}/access_log.jsonl",
        ]
    )
    assert _git(repo, "status", "--porcelain") == ""
    _no_price(_git(repo, "show", head))


def test_a_partition_judgment_consumes_the_same_series_and_overlapping_weeks(
    sealed_base: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """分類（partition が対象）の閲覧の違反判定: 同じ系列・重なる期間（ADR-0035 第3項）。"""
    repo, origin = _sealed_repo(sealed_base, tmp_path)
    finalize_id = _access_ids(repo)[1]
    assert _judge(repo, finalize_id) == 0
    states = existing_sealed_partitions(repo / "data" / "snapshots")
    assert {item.state for item in states} == {HoldoutState.CONSUMED}
    assert _git(repo, "rev-parse", "HEAD") == _origin_head(origin)
    _no_price(capsys.readouterr().out)


def test_a_judgment_of_another_symbol_consumes_nothing(
    sealed_base: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, _ = _sealed_repo(sealed_base, tmp_path)
    _retroactive_start(repo, "eurusd-audit", "EURUSD")
    _git(repo, "commit", "--quiet", "-am", "start row")
    _git(repo, "push", "--quiet", "origin", "main")
    capsys.readouterr()
    assert _judge(repo, "eurusd-audit") == 0
    printed = capsys.readouterr().out
    assert "違反の範囲に重なる封印の partition: 0（snapshot 0）" in printed
    assert not (_final(repo) / "access_log.jsonl").exists()
    changed = _git(repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert changed == ["research/quality_access_log.jsonl"]


def test_before_any_sealed_snapshot_only_the_judgment_row_is_committed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S5a の前（封印の partition が 0 件）: 判定の行だけを commit・push する。"""
    repo = _repo(tmp_path)
    _retroactive_start(repo, "early", "USDJPY")
    origin = _publish_repo(repo, tmp_path)
    capsys.readouterr()
    assert _judge(repo, "early") == 0
    printed = capsys.readouterr().out
    assert "違反の範囲に重なる封印の partition: 0（snapshot 0）" in printed
    assert "消費の記録: この回に 0 / 0 件追記した" in printed
    assert isinstance(_rows(repo).rows[-1], QualityAccessJudgment)
    assert _git(repo, "rev-parse", "HEAD") == _origin_head(origin)
    changed = _git(repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert changed == ["research/quality_access_log.jsonl"]


def test_a_legacy_holdout_partition_is_consumed_only_while_it_is_sealed(tmp_path: Path) -> None:
    """旧データの snapshot の `LEGACY_HOLDOUT` も封印の区分（ADR-0035 第0項の 3）。"""
    repo = _repo(tmp_path)
    _retroactive_start(repo, "legacy", "USDJPY")
    snapshots = repo / "data" / "snapshots"
    store = ParquetSnapshotStore(root=snapshots)
    hourly = market.series()
    record = synthetic.partition(hourly, AccessClass.LEGACY_HOLDOUT)
    unseen = synthetic.manifest(
        partitions=(record,),
        legacy_access=(
            LegacyAccessRecord(
                series_id=hourly,
                interval=Interval(start=record.interval.start, end=record.interval.end),
                observation=LegacyObservation.NOT_OBSERVED,
            ),
        ),
    )
    store.write_manifest(str(unseen.snapshot_id()), unseen)
    found = snapshot_sealed_partitions(snapshots, legacy=True)
    assert [(item.snapshot_id, item.partition.state) for item in found] == [
        (str(unseen.snapshot_id()), HoldoutState.SEALED)
    ]
    assert snapshot_sealed_partitions(snapshots) == []  # 受入れの例外 2 は旧データを数えない
    origin = _publish_repo(repo, tmp_path)
    assert _judge(repo, "legacy") == 0
    (after,) = snapshot_sealed_partitions(snapshots, legacy=True)
    assert after.partition.state is HoldoutState.CONSUMED
    assert _git(repo, "rev-parse", "HEAD") == _origin_head(origin)


# --- 止まる場合（何も書かない）---------------------------------------------------------------


def test_an_existing_lock_stops_before_anything_is_written(
    sealed_base: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, origin = _sealed_repo(sealed_base, tmp_path)
    accept_id = _access_ids(repo)[0]
    (repo / "research" / "quality_access_log.lock").write_text("held\n")
    before, head = _snapshot_bytes(repo), _git(repo, "rev-parse", "HEAD")
    capsys.readouterr()
    assert _judge(repo, accept_id) == 1
    printed = capsys.readouterr().out
    assert "失敗（lock）" in printed and "判定の行: 追記していない" in printed
    assert _snapshot_bytes(repo) == before
    assert _git(repo, "rev-parse", "HEAD") == head == _origin_head(origin)


def test_an_origin_ahead_of_the_working_tree_stops_before_anything_is_written(
    sealed_base: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """origin に知らない追記があれば止める（ADR-0014 の消費の手順 (1)）。"""
    repo, origin = _sealed_repo(sealed_base, tmp_path)
    accept_id = _access_ids(repo)[0]
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "--quiet", str(origin), str(other)], check=True)
    _git(other, "config", "user.name", "o")
    _git(other, "config", "user.email", "o@example.invalid")
    (other / "note.txt").write_text("x\n")
    _git(other, "add", "note.txt")
    _git(other, "commit", "--quiet", "-m", "other")
    _git(other, "push", "--quiet", "origin", "main")
    before = _snapshot_bytes(repo)
    capsys.readouterr()
    assert _judge(repo, accept_id) == 1
    assert "失敗（base）" in capsys.readouterr().out
    assert _snapshot_bytes(repo) == before


def test_an_unknown_access_or_an_unreadable_consumption_log_stops(
    sealed_base: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, _ = _sealed_repo(sealed_base, tmp_path)
    accept_id = _access_ids(repo)[0]
    before = _snapshot_bytes(repo)
    assert _judge(repo, "no-such-access") == 1
    assert "失敗（read）" in capsys.readouterr().out
    (_final(repo) / "access_log.jsonl").write_text("{broken\n")
    before = _snapshot_bytes(repo)
    assert _judge(repo, accept_id) == 1
    assert "失敗（state）" in capsys.readouterr().out
    assert _snapshot_bytes(repo) == before


def test_a_pending_sealed_snapshot_in_the_range_stops(
    sealed_base: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """暫定 snapshot の `SEALED` の partition が範囲に重なれば止める（仮置き）。"""
    repo, _ = _sealed_repo(sealed_base, tmp_path)
    accept_id = _access_ids(repo)[0]
    copy = next((repo / "docs" / "data" / "sealed").iterdir()) / "manifest.json"
    assert _accept(repo, copy, variant="candidate") == 0
    before = _snapshot_bytes(repo)
    capsys.readouterr()
    assert _judge(repo, accept_id) == 1
    assert "失敗（pending）" in capsys.readouterr().out
    assert _snapshot_bytes(repo) == before


# --- 途中で止まったとき（部分的な commit を残さない・やり直し）----------------------------------


def test_a_rejected_push_leaves_no_commit_and_a_retry_completes(
    sealed_base: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, origin = _sealed_repo(sealed_base, tmp_path)
    accept_id = _access_ids(repo)[0]
    head = _git(repo, "rev-parse", "HEAD")
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    capsys.readouterr()
    assert _judge(repo, accept_id) == 1
    printed = capsys.readouterr().out
    assert "失敗（push）" in printed and "commit: なし" in printed
    assert "消費の記録: この回に 5 / 5 件追記した" in printed
    assert _git(repo, "rev-parse", "HEAD") == head == _origin_head(origin)  # commit は残らない
    assert _git(repo, "diff", "--cached", "--name-only") == ""
    hook.unlink()
    assert _judge(repo, accept_id) == 0
    printed = capsys.readouterr().out
    assert "既にあったので追記しなかった（やり直し）" in printed
    assert "消費の記録: この回に 0 / 0 件追記した" in printed
    judgments = [row for row in _rows(repo).rows if isinstance(row, QualityAccessJudgment)]
    assert len(judgments) == 1
    lines = (_final(repo) / "access_log.jsonl").read_text().splitlines()
    assert len(lines) == 5
    assert _git(repo, "rev-parse", "HEAD") == _origin_head(origin) != head
    assert _git(repo, "status", "--porcelain") == ""


def test_a_failed_consumption_append_leaves_no_commit_and_a_retry_adds_only_the_rest(
    sealed_base: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, origin = _sealed_repo(sealed_base, tmp_path)
    accept_id = _access_ids(repo)[0]
    head = _git(repo, "rev-parse", "HEAD")
    original = GitHoldoutAccessLog.append_consumed
    calls = {"n": 0}

    def flaky(self: GitHoldoutAccessLog, *args: object, **kwargs: object) -> str:
        calls["n"] += 1
        if calls["n"] == 3:
            raise HoldoutAccessLogStop("append", "synthetic failure")
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(GitHoldoutAccessLog, "append_consumed", flaky)
    capsys.readouterr()
    assert _judge(repo, accept_id) == 1
    printed = capsys.readouterr().out
    assert "失敗（append）" in printed and "この回に 2 / 5 件追記した" in printed
    assert _git(repo, "rev-parse", "HEAD") == head == _origin_head(origin)
    monkeypatch.setattr(GitHoldoutAccessLog, "append_consumed", original)
    retry = [
        "gate",
        "judge-violation",
        "--access-id",
        accept_id,
        "--conditions",
        "6",
        "--by",
        "別の人",
        "--judged-at",
        JUDGED_AT,
        "--repo-root",
        str(repo),
    ]
    assert main(retry) == 0
    printed = capsys.readouterr().out
    assert "この回に 3 / 3 件追記した" in printed
    assert "入力が違う項目（既にある行を使った）: violated_conditions, judged_by" in printed
    lines = (_final(repo) / "access_log.jsonl").read_text().splitlines()
    assert len(lines) == 5 and len({json.loads(line)["partition"] for line in lines}) == 5
    assert {json.loads(line)["actor"] for line in lines} == {"人間"}  # 既にある判定の行の判定者
    assert _git(repo, "rev-parse", "HEAD") == _origin_head(origin) != head


def test_an_edited_record_file_is_never_committed(
    sealed_base: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """作業木の記録が「commit 済みの内容 + 追記」でなければ止める（追記専用）。"""
    repo, origin = _sealed_repo(sealed_base, tmp_path)
    accept_id = _access_ids(repo)[0]
    log = repo / "research" / "quality_access_log.jsonl"
    accept_rows, finalize_rows = (
        log.read_bytes().splitlines(keepends=True)[:2],
        log.read_bytes().splitlines(keepends=True)[2:],
    )
    log.write_bytes(
        b"".join(finalize_rows + accept_rows)
    )  # 2 つの閲覧の順を入れ替えた（構造は正しい）
    before = _snapshot_bytes(repo)
    capsys.readouterr()
    assert _judge(repo, accept_id) == 1
    assert "失敗（base）" in capsys.readouterr().out
    assert _snapshot_bytes(repo) == before
    head = _git(repo, "rev-parse", "HEAD")
    assert head == _origin_head(origin)


def test_the_git_log_satisfies_the_port() -> None:
    assert issubclass(GitHoldoutAccessLog, HoldoutAccessLog)


def test_a_consumption_log_that_git_ignores_stops_before_anything_is_written(
    sealed_base: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """origin に届かない（版管理されない）消費の記録には書かない（ADR-0014）。"""
    repo, origin = _sealed_repo(sealed_base, tmp_path)
    accept_id = _access_ids(repo)[0]
    # 消費の記録はまだ無い（最初の消費）。無い置き場にも無視の規則を当てる（Codex 2 巡目）。
    assert not (_final(repo) / "access_log.jsonl").exists()
    with (repo / ".gitignore").open("a", encoding="utf-8") as handle:
        handle.write("data/snapshots/*/access_log.jsonl\n")
    _git(repo, "commit", "--quiet", "-am", "ignore the consumption logs")
    _git(repo, "push", "--quiet", "origin", "main")
    before = _snapshot_bytes(repo)
    capsys.readouterr()
    assert _judge(repo, accept_id) == 1
    assert "失敗（base）" in capsys.readouterr().out
    assert _snapshot_bytes(repo) == before
    assert _git(repo, "rev-parse", "HEAD") == _origin_head(origin)


def test_a_committed_consumption_log_missing_from_the_working_tree_stops(
    sealed_base: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """消えた記録を空の履歴と読んで、新しいファイルで履歴を置き換えない（Codex 1 巡目）。"""
    repo, origin = _sealed_repo(sealed_base, tmp_path)
    accept_id = _access_ids(repo)[0]
    log = _final(repo) / "access_log.jsonl"
    partition = existing_sealed_partitions(repo / "data" / "snapshots")[0].partition_id
    line = {
        "actor": "t",
        "kind": "CONSUMED",
        "partition": str(partition),
        "purpose": "",
        "recorded_at": "2026-11-15T00:00:00Z",
    }
    log.write_text(json.dumps(line, sort_keys=True) + "\n")
    _git(repo, "add", str(log))
    _git(repo, "commit", "--quiet", "-m", "one consumption")
    _git(repo, "push", "--quiet", "origin", "main")
    log.unlink()
    before = _snapshot_bytes(repo)
    capsys.readouterr()
    assert _judge(repo, accept_id) == 1
    printed = capsys.readouterr().out
    assert "失敗（base）" in printed and "missing from the working tree" in printed
    assert _snapshot_bytes(repo) == before and not log.exists()
    assert _git(repo, "rev-parse", "HEAD") == _origin_head(origin)


def test_a_refused_commit_leaves_nothing_staged(
    sealed_base: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """commit が断られたら（hook など）commit は無く、この回に index へ置いたファイルも外す。"""
    repo, origin = _sealed_repo(sealed_base, tmp_path)
    accept_id = _access_ids(repo)[0]
    head = _git(repo, "rev-parse", "HEAD")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    capsys.readouterr()
    assert _judge(repo, accept_id) == 1
    assert "失敗（commit）" in capsys.readouterr().out
    assert _git(repo, "rev-parse", "HEAD") == head == _origin_head(origin)
    assert _git(repo, "diff", "--cached", "--name-only") == ""
    hook.unlink()
    assert _judge(repo, accept_id) == 0
    assert _git(repo, "rev-parse", "HEAD") == _origin_head(origin) != head


def test_another_judgment_with_sealed_partitions_left_stops_first(
    sealed_base: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """他の閲覧の判定の範囲に SEALED が残れば、その閲覧のやり直しを先にする（Codex 3 巡目）。"""
    repo, origin = _sealed_repo(sealed_base, tmp_path)
    accept_id = _access_ids(repo)[0]
    judged = QualityAccessJudgment(
        row_id="a-judgment",
        access_id=accept_id,
        violated_conditions=frozenset({1}),
        judged_at=UtcTime.parse(JUDGED_AT),
        judged_by="人間",
    )
    assert QualityAccessLogFile(repo).append(judged) == judged  # 消費の記録の前に止まった跡
    _retroactive_start(repo, "eurusd-audit", "EURUSD")
    before = _snapshot_bytes(repo)
    capsys.readouterr()
    assert _judge(repo, "eurusd-audit") == 1
    printed = capsys.readouterr().out
    assert "失敗（other-judgment）" in printed and accept_id in printed
    assert _snapshot_bytes(repo) == before
    assert _judge(repo, accept_id) == 0  # 先にやり直すと
    assert _judge(repo, "eurusd-audit") == 0  # 次の判定へ進める
    assert _git(repo, "rev-parse", "HEAD") == _origin_head(origin)


def test_an_unpublished_consumption_of_another_judgment_is_committed_with_the_log(
    sealed_base: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """前の判定の push が断られて残った消費の記録は、次の判定の commit に一緒に入る。"""
    repo, origin = _sealed_repo(sealed_base, tmp_path)
    accept_id = _access_ids(repo)[0]
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    assert _judge(repo, accept_id) == 1
    hook.unlink()
    _retroactive_start(repo, "eurusd-audit", "EURUSD")
    capsys.readouterr()
    assert _judge(repo, "eurusd-audit") == 0
    final = _final(repo)
    changed = _git(repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert f"data/snapshots/{final.name}/access_log.jsonl" in changed
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "rev-parse", "HEAD") == _origin_head(origin)
