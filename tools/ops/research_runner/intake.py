"""口ごとの取り出しの上限（人間の決定 256、D27 §4.6 と仮置き 20〜23、D25 §3.6・§3.7 の注記）。

人間の口・AI の口のそれぞれから、何枚まで仮説カードを取り出すか。

- **設定**（D27 の仮置き 20）: `configs/runner/intake.yaml` の
  `max_cards_per_mouth: {human: <正の整数か null>, ai: <正の整数か null>}`。実行器は 1 歩ごとに
  `origin/main` の時点のファイルを読む（`configs/runner/workers.yaml` と同じ扱い）。
  ファイルが無い・`max_cards_per_mouth` のキーが無い・口のキーが無い・値が null の口は上限なし
  （いまの動きと同じ。D27 の仮置き 20・23）。
- **壊れた設定**: YAML として読めない、知らないキー、値が範囲（1〜`MAX_CARDS_LIMIT`）の外の整数・
  整数でない（真偽値を含む）は `ToolFailure`。上限なしに倒さない。実行器はその 1 歩で取り出しと
  発想役の起動をせず、次の 1 歩で読み直す（`workers.yaml` の前例。本 PR の仮置き K1・K2）。
- **数え方**（D27 の仮置き 21）: 実行器の状態に取り出されたカード（C1 に入ったもの）すべてを、
  口ごとに数える。棄却（T3）・途中で終えたカードも数える。状態のファイルから数えるので、落ちた後の
  再開でも数はずれない。

人間が PR の merge で値を変える（定型 PR が変えられる置き場の外。D25 §6.2・§6.3 の 3）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from tools.ops.hypothesis_cards.card import MOUTHS
from tools.ops.hypothesis_cards.repo import RepoView, ToolFailure

__all__ = [
    "INTAKE_PATH",
    "MAX_CARDS_LIMIT",
    "MouthLimits",
    "full_mouths",
    "mouth_limits",
    "mouth_of_card",
]

#: 口ごとの上限の設定（`origin/main` の時点。D27 の仮置き 20）。
INTAKE_PATH: Final = "configs/runner/intake.yaml"
#: 上限の値の最大（これを超える値は壊れた設定。本 PR の仮置き K3）。
MAX_CARDS_LIMIT: Final = 100
_TOP_KEYS: Final = frozenset({"schema_version", "max_cards_per_mouth"})


@dataclass(frozen=True, slots=True)
class MouthLimits:
    """口 → 上限の枚数（口が無ければ上限なし）。作るときに写して読み取り専用にする（渡した辞書を
    後で変えても上限は変わらない）。"""

    caps: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "caps", MappingProxyType(dict(self.caps)))

    def cap(self, mouth: str) -> int | None:
        return self.caps.get(mouth)


UNLIMITED: Final = MouthLimits({})


def mouth_limits(view: RepoView) -> MouthLimits:
    """`configs/runner/intake.yaml` の上限（無ければ上限なし）。形が違えば `ToolFailure`。"""
    raw = view.read(INTAKE_PATH)
    if raw is None:
        return UNLIMITED
    try:
        data = load_yaml_mapping(Path(INTAKE_PATH), text=raw.decode("utf-8"))
    except (ConfigError, UnicodeDecodeError) as exc:
        raise ToolFailure(f"{INTAKE_PATH} を読めない: {exc}") from exc
    if not set(data) <= _TOP_KEYS or data.get("schema_version") != 1:
        raise ToolFailure(f"{INTAKE_PATH} の項目が違う")
    if "max_cards_per_mouth" not in data:
        return UNLIMITED
    table = data["max_cards_per_mouth"]
    if not isinstance(table, dict) or not set(table) <= set(MOUTHS):
        raise ToolFailure(f"{INTAKE_PATH} の max_cards_per_mouth は human・ai の対応表")
    caps: dict[str, int] = {}
    for mouth, value in table.items():
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise ToolFailure(f"{INTAKE_PATH} の {mouth} が整数か null でない")
        if not 1 <= value <= MAX_CARDS_LIMIT:
            raise ToolFailure(f"{INTAKE_PATH} の {mouth} が 1〜{MAX_CARDS_LIMIT} でない")
        caps[str(mouth)] = value
    return MouthLimits(caps)


def mouth_of_card(card_id: str) -> str | None:
    """カードの識別子の 1 文字目の口（`h-` → human、`a-` → ai。違えば `None`）。"""
    for mouth, letter in MOUTHS.items():
        if card_id.startswith(f"{letter}-"):
            return mouth
    return None


def full_mouths(limits: MouthLimits, taken_mouths: Iterable[str]) -> frozenset[str]:
    """上限に達した口（取り出した枚数 ≥ 上限）。`taken_mouths` は取り出したカードの口の並び。"""
    counts: dict[str, int] = {}
    for mouth in taken_mouths:
        counts[mouth] = counts.get(mouth, 0) + 1
    return frozenset(mouth for mouth, cap in limits.caps.items() if counts.get(mouth, 0) >= cap)
