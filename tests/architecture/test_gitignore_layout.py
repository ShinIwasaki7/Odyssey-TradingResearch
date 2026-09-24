"""`.gitignore` のデータ配置規則の挙動検査（D01 §10.2・§13、ADR-0013、ADR-0014）。

規則そのものを読むのではなく `git check-ignore` に判定させる。再包含（`!`）は
「親ディレクトリを除外すると中身を復活できない」という順序依存があり、規則の
字面を見るだけでは正しさが分からないためである。

とくに `access_log.jsonl` は holdout の消費遷移の正本であり、追跡されていない
追記は「未記録」として扱われる（ADR-0014 の消費遷移の直列化）。誤って除外すると
消費記録が origin に到達せず、fail-closed のはずの holdout 公開が壊れる。

判定に終了コードだけを使わない理由: `git check-ignore` の終了コードは
「無視されるか」ではなく「規則に一致したか」を表す。再包含された
`manifest.json` は `!data/snapshots/*/manifest.json` に一致するため、
無視されないにもかかわらず終了コード 0 を返す。したがって `-v` で
最終的に一致した規則を読み、それが否定規則（`!` 始まり）かどうかで判定する。

`--no-index` を付ける理由: 既定の `git check-ignore` は追跡済みのパスを
「無視対象ではない」として規則の照合自体を省き、終了コード 1（一致なし）を返す。
本 PR は manifest.json と access_log.jsonl を実際に追跡するので、実データが
コミットされた時点で規則が正しいまま検査が失敗してしまう。ここで確かめたいのは
索引の状態ではなく規則そのものの挙動なので、索引を見ない `--no-index` を使う。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: git 管理外であるべきパス。原データ・snapshot 実体・実行成果物。
IGNORED_PATHS = [
    "data/raw/market/x.csv",
    "data/snapshots/s1/part.parquet",
    "data/snapshots/s1/RESEARCH_HISTORY/x.parquet",
    "runs/r1/trace.parquet",
    # 暫定 snapshot（`_pending/` 配下）はすべて git 管理外（ADR-0013 2026-09-20 改訂）。
    "data/snapshots/_pending/p1/manifest.json",
    "data/snapshots/_pending/p1/integrity_report.json",
    "data/snapshots/_pending/p1/USDJPY_1h_bid/RESEARCH_HISTORY/bars.parquet",
]

#: snapshot 配下で追跡するファイル（ADR-0013 改訂、ADR-0014）。検査報告2種は
#: ADR-0013 の 2026-09-20 改訂（D03 v1.7）で追加した。
TRACKED_PATHS = [
    "data/snapshots/s1/manifest.json",
    "data/snapshots/s1/integrity_report.json",
    "data/snapshots/s1/integrity_report_provisional.json",
    "data/snapshots/s1/access_log.jsonl",
]

#: 追跡してよい snapshot 配下のファイル名。
TRACKED_NAMES = {Path(path).name for path in TRACKED_PATHS}

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (REPO_ROOT / ".git").exists(),
    reason="git が使えない環境では .gitignore の挙動を検査できない",
)


def _matched_pattern(path: str) -> str | None:
    """`path` に最終的に一致した .gitignore の規則。一致しなければ None。

    `git check-ignore -v` は `<source>:<line>:<pattern>\t<path>` を出力する。
    規則が `!` で始まる場合、そのパスは（一致はしたが）無視されない。

    `--no-index` は必須。これが無いと追跡済みのパスで一致なし（None）になる。
    """
    result = subprocess.run(
        ["git", "check-ignore", "-v", "--no-index", "--", path],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode == 1:
        return None
    assert result.returncode == 0, (
        f"git check-ignore failed for {path!r} (exit {result.returncode})\n"
        f"--- stderr ---\n{result.stderr}"
    )
    # `<source>:<line>:<pattern>\t<path>` の pattern 部分を取り出す。
    fields = result.stdout.split("\t", 1)[0].split(":")
    return fields[2]


def _is_ignored(path: str) -> bool:
    pattern = _matched_pattern(path)
    return pattern is not None and not pattern.startswith("!")


@pytest.mark.parametrize("path", IGNORED_PATHS)
def test_data_and_run_artifacts_are_ignored(path: str) -> None:
    """原データ・snapshot 実体・実行成果物は git 管理外（ADR-0013）。"""
    assert _is_ignored(path), f"{path} は git 管理外でなければならない"


@pytest.mark.parametrize("path", TRACKED_PATHS)
def test_snapshot_manifest_reports_and_access_log_are_not_ignored(path: str) -> None:
    """manifest.json・検査報告・access_log.jsonl は再包含される（ADR-0013 改訂、ADR-0014）。"""
    pattern = _matched_pattern(path)
    assert pattern is not None and pattern.startswith("!"), (
        f"{path} は追跡できなければならない（最終一致規則: {pattern!r}）。"
        "`data/snapshots/*/*` の後ろに `!` の再包含規則があるか確認すること"
    )


def test_no_ignored_data_artifact_is_actually_tracked() -> None:
    """除外対象が誤って追跡されていないことを実リポジトリの索引で確かめる。

    規則の判定（`--no-index`）は索引を見ないため、「規則では除外なのに過去に
    強制追加されて追跡されている」状態を検出できない。原データや snapshot 実体が
    追跡されると、容量だけでなく holdout の実体が共有先に流出しうるので別途見る。
    """
    result = subprocess.run(
        ["git", "ls-files", "--", "data", "runs"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked = [line for line in result.stdout.splitlines() if line]
    unexpected = [
        path
        for path in tracked
        if not (
            path.startswith("data/snapshots/")
            and not path.startswith("data/snapshots/_pending/")
            and Path(path).name in TRACKED_NAMES
        )
    ]
    assert not unexpected, (
        f"data/ と runs/ で追跡してよいのは確定 snapshot の {sorted(TRACKED_NAMES)} だけ。"
        f"実際に追跡されている想定外のパス: {unexpected}"
    )


def test_snapshot_directory_lists_exactly_the_tracked_files(tmp_path: Path) -> None:
    """再包含の実効を git 自身の列挙で確かめる（規則の順序依存を含めた総合検査）。

    `git check-ignore` は1パスずつの判定なので、snapshot ディレクトリ全体で
    「追跡されるのは4ファイルだけ」（暫定 snapshot の配下は1つも追跡しない）を別途確かめる。

    検査は本物の `.gitignore` を複製した一時リポジトリの中で行い、実リポジトリの
    作業ツリーには一切触れない。実リポジトリに探索用ファイルを置くと、既存の
    manifest や未コミットの消費記録を後始末で消してしまう恐れがあるためである
    （消費記録の喪失は ADR-0014 の holdout 状態の正本を壊す）。
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    shutil.copyfile(REPO_ROOT / ".gitignore", tmp_path / ".gitignore")

    probes = [
        *TRACKED_PATHS,
        "data/snapshots/s1/part.parquet",
        "data/snapshots/_pending/p1/manifest.json",
        "data/snapshots/_pending/p1/integrity_report.json",
    ]
    for rel in probes:
        probe = tmp_path / rel
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.touch()

    result = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", "data/snapshots"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    listed = sorted(line for line in result.stdout.splitlines() if line)
    assert listed == sorted(TRACKED_PATHS), (
        f"snapshot 配下で追跡されるのは {sorted(TRACKED_NAMES)} だけ。実際: {listed}"
    )
