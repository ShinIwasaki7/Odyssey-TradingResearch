"""fold の生成の性質（D09 §6.1 の検査2・3。段階5 実装 PR 1）。

任意の期間分割の標準規則について、生成した fold が次を満たすことを確かめる。

- 各 fold で `train.end + purge <= validation.start`（検査2）。
- 検証区間は fold の順に重ならず前へ進み、選定区間は後退しない（検査3）。
- すべての区間が評価範囲の中にあり、最後の fold の検証区間は評価範囲の終わりで終わる
  （最新の区間を必ず検証に使う）。
- 選定区間の長さは `ROLLING` なら `T` ちょうど、`EXPANDING` なら `T` 以上。
- 次の fold を採れなかった理由が生成の手順3 どおり（もう1つ前は範囲に収まらない）。
"""

from __future__ import annotations

from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.splits import SplitStandard, SplitWindow, generate_folds

_START = UtcTime.parse("2016-01-03T22:00:00Z")
_HOUR = 3600


@st.composite
def _standards(draw: st.DrawFn) -> SplitStandard:
    range_hours = draw(st.integers(min_value=1, max_value=24 * 120))
    return SplitStandard(
        range=Interval(start=_START, end=_START + timedelta(hours=range_hours)),
        train_seconds=draw(st.integers(min_value=1, max_value=24 * 60)) * _HOUR,
        validation_seconds=draw(st.integers(min_value=1, max_value=24 * 30)) * _HOUR,
        window=draw(st.sampled_from(list(SplitWindow))),
        purge_seconds=draw(st.integers(min_value=0, max_value=24 * 5)) * _HOUR,
        min_folds=1,
    )


@settings(max_examples=300, deadline=None)
@given(_standards())
def test_generated_folds_keep_purge_monotonicity_and_the_range(standard: SplitStandard) -> None:
    folds = generate_folds(standard)
    train_length = timedelta(seconds=standard.train_seconds)
    validation_length = timedelta(seconds=standard.validation_seconds)
    purge = timedelta(seconds=standard.purge_seconds)

    for index, fold in enumerate(folds):
        assert fold.fold_index == index
        assert fold.train.end + purge <= fold.validation.start
        assert fold.validation.duration == validation_length
        assert standard.range.start <= fold.train.start
        assert fold.validation.end <= standard.range.end
        if standard.window is SplitWindow.ROLLING:
            assert fold.train.duration == train_length
        else:
            assert fold.train.start == standard.range.start
            assert fold.train.duration >= train_length
    for previous, current in zip(folds, folds[1:], strict=False):
        assert previous.validation.end == current.validation.start
        assert previous.train.start <= current.train.start
        assert previous.train.end <= current.train.end

    if folds:
        assert folds[-1].validation.end == standard.range.end
    # 採らなかった最初の j（= fold の数）は、手順3 の条件を満たさない。
    next_validation_start = standard.range.end - validation_length * (len(folds) + 1)
    next_train_end = next_validation_start - purge
    if standard.window is SplitWindow.ROLLING:
        assert next_train_end - train_length < standard.range.start
    else:
        assert next_train_end - standard.range.start < train_length
