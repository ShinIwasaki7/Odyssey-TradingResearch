"""リポジトリのある時点のファイルの読み方と、カードの道具が使う置き場（D25 §2.3・§3.3）。

カードの道具は、作業木（実行器の実行用の作業木・人間の作業木）からも、git のコミット（CI の
検査 4 の作り直し。PR 6 の `tools.ops.runner_ci.model.Snapshot`）からも同じ関数で読む。読み方は
`read`（無ければ `None`）と `list`（前置きの下のパスを辞書順）の 2 つだけで、PR 6 の `Snapshot` と
同じ形である（構造で一致するので、どちらの実装も渡せる）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Protocol

__all__ = [
    "CARD_PLACES",
    "EXPERIMENTS_DIR",
    "HYPOTHESES_DIR",
    "LOOPS_DIR",
    "POLICY_DIR",
    "STRATEGIES_DIR",
    "SYMBOLS_DIR",
    "TEMPLATES_DIR",
    "DictView",
    "DirView",
    "OverlayView",
    "RepoView",
    "ToolFailure",
]

#: カードの置き場（D23 §5.2、D25 §3.3）。待ち・実行中・終わり。
HYPOTHESES_DIR: Final = "research/hypotheses"
CARD_PLACES: Final = ("queue", "taken", "done")
#: 一巡の記録の置き場（事前登録の注記・変更の提案・実行記録・一巡の報告。D25 §2.3）。
LOOPS_DIR: Final = "research/loops"
#: 生成した事前登録の置き場（既存と同じ。D25 の仮置き 8）。
STRATEGIES_DIR: Final = "configs/strategies"
EXPERIMENTS_DIR: Final = "configs/experiments"
#: 通貨ごとの型板（D25 の仮置き 9）。
TEMPLATES_DIR: Final = "configs/runner/experiment_templates"
#: 研究ポリシーと版の登録簿（D07 §20.2、D09 §10.9）。
POLICY_DIR: Final = "configs/policies/research"
SYMBOLS_DIR: Final = "configs/symbols"


class ToolFailure(RuntimeError):
    """カードの中身ではなく、道具や環境の不具合（D25 §3.5 の注記。`TOOL_FAILURE`）。

    型板・研究ポリシー・登録簿が読めない、既存の宣言が読めないなど。カードを棄却にせず、
    実行器が失敗として数える。
    """


class RepoView(Protocol):
    """ある時点のリポジトリのファイルの読み方（パスはリポジトリの根からの相対。区切りは `/`）。"""

    def read(self, path: str) -> bytes | None:
        """ファイルのバイト列（無ければ `None`）。"""
        ...

    def list(self, prefix: str) -> tuple[str, ...]:
        """`prefix` の下のファイルのパス（辞書順）。"""
        ...


@dataclass(frozen=True, slots=True)
class DirView:
    """作業木のディレクトリから読む。"""

    root: Path

    def read(self, path: str) -> bytes | None:
        target = self.root / path
        return target.read_bytes() if target.is_file() else None

    def list(self, prefix: str) -> tuple[str, ...]:
        base = self.root / prefix
        if base.is_file():
            return (prefix,)
        if not base.is_dir():
            return ()
        found = (
            file.relative_to(self.root).as_posix() for file in base.rglob("*") if file.is_file()
        )
        return tuple(sorted(found))


@dataclass(frozen=True, slots=True)
class DictView:
    """辞書で持つ時点（試験と、生成物を重ねて読むとき）。"""

    files: Mapping[str, bytes] = field(default_factory=dict)

    def read(self, path: str) -> bytes | None:
        return self.files.get(path)

    def list(self, prefix: str) -> tuple[str, ...]:
        return tuple(sorted(path for path in self.files if path.startswith(prefix)))


@dataclass(frozen=True, slots=True)
class OverlayView:
    """`base` の上に `extra` のファイルを重ねた時点（生成物をコミットの前に読むため）。"""

    base: RepoView
    extra: Mapping[str, bytes]

    def read(self, path: str) -> bytes | None:
        if path in self.extra:
            return self.extra[path]
        return self.base.read(path)

    def list(self, prefix: str) -> tuple[str, ...]:
        added = {path for path in self.extra if path.startswith(prefix)}
        return tuple(sorted(set(self.base.list(prefix)) | added))
