"""期間分割の標準規則と fold の生成（D09 §6.1。v0.2）。

研究ポリシーの評価基準（版 3 以上）の `split` が期間分割の標準規則（`SplitStandard`）を持ち、
fold はそこから**決定論的に**生成する（2026-09-30 の人間の指示1）。実験設定は区間・長さ・
purge を書かないので、同じ研究ポリシーの版を指す探索の実験は、すべて同じ fold で評価される。

**fold の生成**（`generate_folds`。入力は `SplitStandard` だけ）: 評価範囲 `[R0, R1)`、選定区間
の長さ `T`、検証区間の長さ `V`、purge `P` として、`j = 0, 1, 2, …` の順に

1. 検証区間 `[R1 − (j+1)·V, R1 − j·V)`。
2. 選定区間の終わり `= 検証区間の始まり − P`。始まりは `ROLLING` なら `終わり − T`、
   `EXPANDING` なら `R0`。
3. `ROLLING` は選定区間の始まりが `R0` 以上、`EXPANDING` は選定区間の長さが `T` 以上のとき、
   この `j` を fold として採る。満たさない最初の `j` で生成を止める。
4. 採った fold を時間の古い順に並べ、`fold_index` を 0 から振る。

**研究ポリシーを読むときの検査**（D09 §6.1 の 1〜3・5〜7）のうち、本モジュールが持つもの:

- 検査 1・5 と 7 の後半（`min_folds` は 1 以上）: `SplitStandard` の構築時に確かめる。
- 検査 2・3（purge の不変条件と区間の単調性）: 生成の手順で成り立つので、生成の後に不変条件
  として確かめ、破れていれば構造エラー（`KernelValueError`）。
- 検査 6・7（評価範囲の終わりが研究履歴の境界より前、生成した fold の数が `min_folds` 以上）:
  `folds_for_policy` が確かめ、違反は `SplitStandardViolation`。研究履歴の境界は呼び出し側が
  渡す（D03 §3.8 の期間境界。domain は境界の値を決め打たない）。

長さはすべて秒数の整数で持つ（D09 §6.1。暦月・取引日では数えない）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.errors import EvaluationError

__all__ = [
    "Fold",
    "SplitStandard",
    "SplitStandardViolation",
    "SplitWindow",
    "check_fold_invariants",
    "folds_for_policy",
    "generate_folds",
]


class SplitStandardViolation(EvaluationError):
    """期間分割の標準規則が研究ポリシーを読むときの検査に反する（D09 §6.1 の検査6・7）。

    研究ポリシーのファイルの誤りであり、読込側（`app.config`）が設定の誤りに言い換える。
    """


class SplitWindow(Enum):
    """選定区間の取り方（D09 §6.1。v0.2）。"""

    #: 選定区間の長さを保って進める。
    ROLLING = "ROLLING"
    #: 選定区間の始まりを評価範囲の始まりに固定して伸ばす。
    EXPANDING = "EXPANDING"


def _require_int(value: object, label: str, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise KernelValueError(f"{label} must be an int, got {value!r}")
    if value < minimum:
        raise KernelValueError(f"{label} must be >= {minimum}, got {value} (D09 §6.1)")


@dataclass(frozen=True, slots=True)
class SplitStandard:
    """期間分割の標準規則（D09 §6.1・§3。v0.2）。

    - `range`: 評価範囲（`Interval` なので空でない。検査1）。
    - `train_seconds` / `validation_seconds`: 選定区間・検証区間の長さ（正。検査1）。
      検証区間の長さは fold のずらし幅でもある。
    - `window`: `ROLLING` か `EXPANDING`。
    - `purge_seconds`: 選定区間の終わりと検証区間の始まりのあいだの空白（0 以上。検査5）。
    - `min_folds`: 生成される fold の数の下限（1 以上。検査7 の後半）。
    """

    range: Interval
    train_seconds: int
    validation_seconds: int
    window: SplitWindow
    purge_seconds: int
    min_folds: int

    def __post_init__(self) -> None:
        if not isinstance(self.range, Interval):
            raise KernelValueError("SplitStandard.range must be an Interval")
        _require_int(self.train_seconds, "SplitStandard.train_seconds", minimum=1)
        _require_int(self.validation_seconds, "SplitStandard.validation_seconds", minimum=1)
        if not isinstance(self.window, SplitWindow):
            raise KernelValueError("SplitStandard.window must be a SplitWindow")
        _require_int(self.purge_seconds, "SplitStandard.purge_seconds", minimum=0)
        _require_int(self.min_folds, "SplitStandard.min_folds", minimum=1)


@dataclass(frozen=True, slots=True)
class Fold:
    """1つの fold（D09 §6.1・§3）。`train` は選定区間、`validation` は検証区間。"""

    fold_index: int
    train: Interval
    validation: Interval

    def __post_init__(self) -> None:
        _require_int(self.fold_index, "Fold.fold_index", minimum=0)
        if not isinstance(self.train, Interval) or not isinstance(self.validation, Interval):
            raise KernelValueError("Fold.train / Fold.validation must be Interval")


def check_fold_invariants(folds: tuple[Fold, ...], purge_seconds: int) -> None:
    """生成した fold の不変条件（D09 §6.1 の検査2・3）を確かめる。

    - 検査2: 各 fold で `train.end + purge <= validation.start`。
    - 検査3: 検証区間は fold の順に重ならず前へ進み、選定区間も fold の順に後退しない
      （始まりも終わりも前の fold 以上）。
    - `fold_index` は 0 から連番。

    生成の手順で成り立つ条件であり、破れていれば構造エラー（`KernelValueError`）。
    """
    if not isinstance(folds, tuple) or not all(isinstance(fold, Fold) for fold in folds):
        raise KernelValueError("check_fold_invariants requires a tuple of Fold")
    _require_int(purge_seconds, "purge_seconds", minimum=0)
    purge = timedelta(seconds=purge_seconds)
    for position, fold in enumerate(folds):
        if fold.fold_index != position:
            raise KernelValueError(
                f"fold indices must run 0, 1, 2, … in order, got {fold.fold_index} at {position}"
            )
        if not fold.train.end + purge <= fold.validation.start:
            raise KernelValueError(
                f"fold {fold.fold_index}: train.end + purge must be <= validation.start, got"
                f" {fold.train.end} + {purge_seconds}s and {fold.validation.start}"
                " (D09 §6.1 の検査2)"
            )
    for previous, current in zip(folds, folds[1:], strict=False):
        if not previous.validation.end <= current.validation.start:
            raise KernelValueError(
                f"fold {current.fold_index}: validation intervals must not overlap and must move"
                f" forward, got {previous.validation} then {current.validation} (D09 §6.1 の検査3)"
            )
        if not (
            previous.train.start <= current.train.start and previous.train.end <= current.train.end
        ):
            raise KernelValueError(
                f"fold {current.fold_index}: train intervals must not move backward, got"
                f" {previous.train} then {current.train} (D09 §6.1 の検査3)"
            )


def generate_folds(standard: SplitStandard) -> tuple[Fold, ...]:
    """期間分割の標準規則から fold を生成する（D09 §6.1 の生成の手順1〜4）。

    入力は `SplitStandard` だけの純粋関数で、同じ規則からは常に同じ fold が出る。生成の後に
    不変条件（検査2・3）を確かめる。fold が1つも作れない規則では空の tuple を返す（その規則の
    違反は `folds_for_policy` の検査7 が扱う）。
    """
    if not isinstance(standard, SplitStandard):
        raise KernelValueError("generate_folds requires a SplitStandard")
    start = standard.range.start
    end = standard.range.end
    train_length = timedelta(seconds=standard.train_seconds)
    validation_length = timedelta(seconds=standard.validation_seconds)
    purge = timedelta(seconds=standard.purge_seconds)

    taken: list[tuple[Interval, Interval]] = []
    step = 0
    while True:
        validation_end = end - validation_length * step
        validation_start = end - validation_length * (step + 1)
        train_end = validation_start - purge
        if standard.window is SplitWindow.ROLLING:
            train_start = train_end - train_length
            accepted = start <= train_start
        else:
            train_start = start
            accepted = train_end - start >= train_length
        if not accepted:
            break
        taken.append(
            (
                Interval(start=train_start, end=train_end),
                Interval(start=validation_start, end=validation_end),
            )
        )
        step += 1

    folds = tuple(
        Fold(fold_index=index, train=train, validation=validation)
        for index, (train, validation) in enumerate(reversed(taken))
    )
    check_fold_invariants(folds, standard.purge_seconds)
    return folds


def folds_for_policy(standard: SplitStandard, *, research_until: UtcTime) -> tuple[Fold, ...]:
    """研究ポリシーを読む時点の検査6・7 を当てて fold を返す（D09 §6.1）。

    - 検査6: 評価範囲の終わりが研究履歴の期間境界 `research_until`（D03 §3.8）**より前**。
      終わりちょうどに終わる足も run が読むので、等しいことも許さない。
    - 検査7: 生成した fold の数が `min_folds` 以上。

    違反は `SplitStandardViolation`（研究ポリシーのファイルの誤り）。
    """
    if not isinstance(research_until, UtcTime):
        raise KernelValueError("folds_for_policy requires research_until as a UtcTime")
    if not standard.range.end < research_until:
        raise SplitStandardViolation(
            f"評価範囲の終わり {standard.range.end} が研究履歴の期間境界 {research_until} より前で"
            " ない。評価範囲は研究履歴の中に置くこと（D09 §6.1 の検査6・§9.1）"
        )
    folds = generate_folds(standard)
    if len(folds) < standard.min_folds:
        raise SplitStandardViolation(
            f"生成される fold の数が {len(folds)} で、最小 fold 数 {standard.min_folds} に"
            " 届かない（D09 §6.1 の検査7）"
        )
    return folds
