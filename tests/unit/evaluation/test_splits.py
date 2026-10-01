"""期間分割の標準規則と fold の生成（D09 §6.1。段階5 実装 PR 1）。

生成の手順1〜4（検証区間を評価範囲の終わりから詰める、選定区間の終わり＝検証区間の始まり −
purge、`ROLLING` / `EXPANDING` の採否、古い順に番号）と、研究ポリシーを読むときの検査
（1・5・7 の型の検査、2・3 の不変条件、6・7 の `folds_for_policy`）を固定する。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.splits import (
    Fold,
    SplitStandard,
    SplitStandardViolation,
    SplitWindow,
    check_fold_invariants,
    folds_for_policy,
    generate_folds,
)

DAY = 86_400
R0 = UtcTime.parse("2020-01-01T00:00:00Z")


def _at(days: float) -> UtcTime:
    return R0 + timedelta(seconds=int(days * DAY))


def _standard(
    *,
    days: int = 10,
    train: int = 4,
    validation: int = 2,
    window: SplitWindow = SplitWindow.ROLLING,
    purge_seconds: int = 0,
    min_folds: int = 1,
) -> SplitStandard:
    return SplitStandard(
        range=Interval(start=R0, end=_at(days)),
        train_seconds=train * DAY,
        validation_seconds=validation * DAY,
        window=window,
        purge_seconds=purge_seconds,
        min_folds=min_folds,
    )


def _spans(folds: tuple[Fold, ...]) -> list[tuple[int, float, float, float, float]]:
    return [
        (
            fold.fold_index,
            (fold.train.start - R0) / timedelta(days=1),
            (fold.train.end - R0) / timedelta(days=1),
            (fold.validation.start - R0) / timedelta(days=1),
            (fold.validation.end - R0) / timedelta(days=1),
        )
        for fold in folds
    ]


def test_rolling_folds_are_packed_from_the_end_and_numbered_oldest_first() -> None:
    """ROLLING: 検証区間を終わりから詰め、端数は始まり側（選定区間になるだけの側）へ寄せる。

    評価範囲 10 日・選定 4 日・検証 2 日・purge 0 → 検証区間は [8,10)・[6,8)・[4,6) の3つ。
    次の [2,4) は選定区間 [-2,2) が範囲の外なので止まる。番号は古い順に 0 から。
    """
    assert _spans(generate_folds(_standard())) == [
        (0, 0, 4, 4, 6),
        (1, 2, 6, 6, 8),
        (2, 4, 8, 8, 10),
    ]


def test_expanding_folds_fix_the_train_start_at_the_range_start() -> None:
    """EXPANDING: 選定区間の始まりを評価範囲の始まりに固定し、長さが T 以上の間だけ採る。"""
    assert _spans(generate_folds(_standard(window=SplitWindow.EXPANDING))) == [
        (0, 0, 4, 4, 6),
        (1, 0, 6, 6, 8),
        (2, 0, 8, 8, 10),
    ]


def test_purge_leaves_a_gap_between_train_and_validation() -> None:
    """purge は選定区間の終わりと検証区間の始まりのあいだの空白（D09 §6.3）。"""
    folds = generate_folds(_standard(purge_seconds=DAY))
    assert _spans(folds) == [(0, 1, 5, 6, 8), (1, 3, 7, 8, 10)]
    for fold in folds:
        assert fold.validation.start - fold.train.end == timedelta(days=1)


def test_a_range_too_short_for_one_fold_generates_none() -> None:
    """選定区間と検証区間が評価範囲に入らなければ fold は0個（検査7 は `folds_for_policy`）。"""
    assert generate_folds(_standard(days=5, train=4, validation=2)) == ()


def test_huge_lengths_generate_no_folds_without_overflowing() -> None:
    """どれほど大きい長さでも桁あふれせず、fold は0個（その違反は検査7 として報告される）。"""
    huge = 10**15
    for values in (
        {"train_seconds": huge},
        {"validation_seconds": huge},
        {"purge_seconds": huge},
    ):
        standard = SplitStandard(
            **{
                "range": Interval(start=R0, end=_at(10)),
                "train_seconds": 4 * DAY,
                "validation_seconds": 2 * DAY,
                "window": SplitWindow.EXPANDING,
                "purge_seconds": 0,
                "min_folds": 1,
                **values,
            }  # type: ignore[arg-type]
        )
        assert generate_folds(standard) == ()
        with pytest.raises(SplitStandardViolation, match="検査7"):
            folds_for_policy(standard, research_until=_at(100))


def test_the_same_standard_always_generates_the_same_folds() -> None:
    """同じ規則からは常に同じ fold（D09 §6.1。入力は `SplitStandard` だけの純粋関数）。"""
    assert generate_folds(_standard()) == generate_folds(_standard())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("train_seconds", 0),
        ("validation_seconds", 0),
        ("purge_seconds", -1),
        ("min_folds", 0),
        ("train_seconds", True),
    ],
)
def test_the_standard_rejects_non_positive_lengths_negative_purge_and_zero_min_folds(
    field: str, value: object
) -> None:
    """検査1（長さが正）・5（purge は 0 以上）・7 の後半（`min_folds` は 1 以上）。"""
    values: dict[str, object] = {
        "range": Interval(start=R0, end=_at(10)),
        "train_seconds": 4 * DAY,
        "validation_seconds": 2 * DAY,
        "window": SplitWindow.ROLLING,
        "purge_seconds": 0,
        "min_folds": 1,
    }
    values[field] = value
    with pytest.raises(KernelValueError):
        SplitStandard(**values)  # type: ignore[arg-type]


def test_the_range_must_end_before_the_research_history_boundary() -> None:
    """検査6: 評価範囲の終わりが研究履歴の期間境界**より前**（等しいことも許さない）。"""
    standard = _standard()
    with pytest.raises(SplitStandardViolation, match="検査6"):
        folds_for_policy(standard, research_until=_at(10))
    assert len(folds_for_policy(standard, research_until=_at(10) + timedelta(seconds=1))) == 3


def test_fewer_folds_than_min_folds_is_a_policy_error() -> None:
    """検査7: 生成した fold の数が `min_folds` 以上。"""
    with pytest.raises(SplitStandardViolation, match="検査7"):
        folds_for_policy(_standard(min_folds=4), research_until=_at(100))
    assert len(folds_for_policy(_standard(min_folds=3), research_until=_at(100))) == 3


def test_broken_invariants_are_structural_errors() -> None:
    """検査2・3 は生成で成り立つ不変条件で、破れていれば構造エラー（D09 §6.1）。"""
    overlap = Fold(
        fold_index=0,
        train=Interval(start=_at(0), end=_at(5)),
        validation=Interval(start=_at(4), end=_at(6)),
    )
    with pytest.raises(KernelValueError, match="検査2"):
        check_fold_invariants((overlap,), 0)

    first = Fold(
        fold_index=0,
        train=Interval(start=_at(2), end=_at(4)),
        validation=Interval(start=_at(4), end=_at(6)),
    )
    backward_validation = Fold(
        fold_index=1,
        train=Interval(start=_at(3), end=_at(5)),
        validation=Interval(start=_at(5), end=_at(7)),
    )
    with pytest.raises(KernelValueError, match="検査3"):
        check_fold_invariants((first, backward_validation), 0)

    backward_train = Fold(
        fold_index=1,
        train=Interval(start=_at(1), end=_at(6)),
        validation=Interval(start=_at(6), end=_at(8)),
    )
    with pytest.raises(KernelValueError, match="検査3"):
        check_fold_invariants((first, backward_train), 0)

    with pytest.raises(KernelValueError, match="fold indices"):
        check_fold_invariants((Fold(1, first.train, first.validation),), 0)
