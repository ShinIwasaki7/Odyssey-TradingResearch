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

`--no-index` は付けない。作業ツリーに実ファイルが無くても判定でき、かつ
「追跡済みファイルは無視されない」という実運用どおりの意味で検査するため。
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
]

#: snapshot 配下で追跡するファイル（ADR-0013 改訂、ADR-0014）。
TRACKED_PATHS = [
    "data/snapshots/s1/manifest.json",
    "data/snapshots/s1/access_log.jsonl",
]

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (REPO_ROOT / ".git").exists(),
    reason="git が使えない環境では .gitignore の挙動を検査できない",
)


def _matched_pattern(path: str) -> str | None:
    """`path` に最終的に一致した .gitignore の規則。一致しなければ None。

    `git check-ignore -v` は `<source>:<line>:<pattern>\t<path>` を出力する。
    規則が `!` で始まる場合、そのパスは（一致はしたが）無視されない。
    """
    result = subprocess.run(
        ["git", "check-ignore", "-v", "--", path],
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
def test_snapshot_manifest_and_access_log_are_not_ignored(path: str) -> None:
    """manifest.json と access_log.jsonl は再包含される（ADR-0013 改訂、ADR-0014）。"""
    pattern = _matched_pattern(path)
    assert pattern is not None and pattern.startswith("!"), (
        f"{path} は追跡できなければならない（最終一致規則: {pattern!r}）。"
        "`data/snapshots/*/*` の後ろに `!` の再包含規則があるか確認すること"
    )


def test_snapshot_directory_lists_exactly_the_tracked_files() -> None:
    """再包含の実効を git 自身の列挙で確かめる（規則の順序依存を含めた総合検査）。

    `git check-ignore` は1パスずつの判定なので、snapshot ディレクトリ全体で
    「追跡されるのは2ファイルだけ」を別途確かめる。
    """
    snapshot_dir = REPO_ROOT / "data" / "snapshots" / "s1"
    created_root = not (REPO_ROOT / "data").exists()
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    probes = [*TRACKED_PATHS, "data/snapshots/s1/part.parquet"]
    try:
        for rel in probes:
            (REPO_ROOT / rel).touch()
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "--", "data/snapshots"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        listed = sorted(line for line in result.stdout.splitlines() if line)
        assert listed == sorted(TRACKED_PATHS), (
            f"snapshot 配下で追跡されるのは manifest.json と access_log.jsonl だけ。実際: {listed}"
        )
    finally:
        for rel in probes:
            (REPO_ROOT / rel).unlink(missing_ok=True)
        if created_root:
            shutil.rmtree(REPO_ROOT / "data", ignore_errors=True)
