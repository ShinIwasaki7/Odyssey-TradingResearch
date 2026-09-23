"""入力が欠けたときの方針（D04 §6.3、Q8 決定）。

動作区分は `SKIP_EVALUATION` / `ERROR` / `WAIT_FOR_INPUT` / `USE_PREVIOUS` の4つだが、
**段階2の型は前2者だけ**を定義する（Q8 決定、選択肢1）。残る2区分は、待機期限・期限切れ
処理・対象区間・再開時刻・遡り上限・記録といったフィールドが「待機の意味論」と一体で
しか定まらないため、宣言形だけ先に決めても空の区分が残るだけになる。D05 v0.2 で
フィールドごと確定してから区分を追加し、その追加を保存形式の版
（`schema_version`、D04 §3）の引き上げとして扱う。

- `SkipEvaluation`: 評価を行わず、**評価記録として残す**。False や価格 0 の出力に変換
  しない（上位設計書 §4.3.15）。
- `Error`: 実行失敗として扱う。ランタイムは `Failed(Reason(DATA_ERROR, …))` を記録し、
  以降の評価を行わずに戻る（D05 §6.3）。run を終了させるのはエンジンの責務である。

どの欠損理由（D02 §8.3 の `MissingInputReason`）なら許容するかを宣言側で絞り込む機能は
段階2では持たない。絞り込みは待機条件と一体で決まるためである。
"""

from __future__ import annotations

from dataclasses import dataclass

from odyssey_fx.strategy.declarations.refs import require_kind

__all__ = ["Error", "MissingInputPolicy", "SkipEvaluation"]


@dataclass(frozen=True, slots=True)
class SkipEvaluation:
    """評価を見送り、見送ったことを評価記録に残す（D04 §6.3）。"""

    kind: str = "SKIP_EVALUATION"

    def __post_init__(self) -> None:
        require_kind(self.kind, "SKIP_EVALUATION", "SkipEvaluation.kind")


@dataclass(frozen=True, slots=True)
class Error:
    """欠損を実行失敗として扱う（D04 §6.3）。"""

    kind: str = "ERROR"

    def __post_init__(self) -> None:
        require_kind(self.kind, "ERROR", "Error.kind")


#: 区分タグ付き union（D04 §6.3）。段階2の2区分。
MissingInputPolicy = SkipEvaluation | Error
