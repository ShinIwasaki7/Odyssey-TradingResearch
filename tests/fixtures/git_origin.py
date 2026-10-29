"""テストの中の一時の git リポジトリと bare の origin（D09 §9.8: 閲覧記録の origin はテストで作る
ローカルの bare リポジトリ。ネットワークに依存しない。D01 §9）。

実際の origin には触れない。git の全体・システムの設定は読まない（`git_env` を子プロセスに渡す。
テスト本体の git の子プロセスには `isolate_git` で同じ環境変数を当てる）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

__all__ = ["git", "git_env", "isolate_git", "origin_head", "publish_repo"]

_ROOT = Path(__file__).resolve().parents[2]


def git_env(home: Path) -> dict[str, str]:
    """git の全体・システムの設定を読まない環境変数（`home` は一時ディレクトリ）。"""
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
    }
    env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return env


def isolate_git(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    """このテストの git の子プロセスが全体・システムの設定を読まないようにする。"""
    home.mkdir(parents=True, exist_ok=True)
    for key, value in git_env(home).items():
        if key.startswith(("GIT_", "HOME", "XDG_")):
            monkeypatch.setenv(key, value)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(key, raising=False)


def git(repo: Path, *args: str, home: Path | None = None) -> str:
    env = None if home is None else git_env(home)
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=env
    )
    return done.stdout.strip()


def publish_repo(repo: Path, origin: Path, *, home: Path | None = None) -> Path:
    """`repo` を git の作業木にし、bare の `origin` を作って main を push する。

    `repo` が既に git の作業木（写したもの）なら、その `.git` を捨てて作り直す。
    """
    if (repo / ".git").exists():
        shutil.rmtree(repo / ".git")
    if not (repo / ".gitignore").exists():
        shutil.copy(_ROOT / ".gitignore", repo / ".gitignore")
    if home is not None:
        home.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "--quiet", home=home)
    git(repo, "symbolic-ref", "HEAD", "refs/heads/main", home=home)
    git(repo, "config", "user.name", "test", home=home)
    git(repo, "config", "user.email", "test@example.invalid", home=home)
    git(repo, "config", "commit.gpgsign", "false", home=home)
    git(repo, "add", "-A", home=home)
    git(repo, "commit", "--quiet", "--allow-empty", "-m", "base", home=home)
    if origin.exists():
        shutil.rmtree(origin)
    subprocess.run(
        ["git", "init", "--quiet", "--bare", str(origin)],
        check=True,
        env=None if home is None else git_env(home),
    )
    git(origin, "symbolic-ref", "HEAD", "refs/heads/main", home=home)
    git(repo, "remote", "add", "origin", str(origin), home=home)
    git(repo, "push", "--quiet", "origin", "main", home=home)
    return origin


def origin_head(origin: Path, *, home: Path | None = None) -> str:
    return git(origin, "rev-parse", "refs/heads/main", home=home)
