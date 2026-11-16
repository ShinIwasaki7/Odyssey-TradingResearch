"""候補の固定の規則の計算（D09 §9.11.5・§9.11.4、受入テスト F-3・F-4 の計算の部分）。

順 11 の PR 4c。
人工の値だけを使う（頻度は D09 §9.11.5 の値の例の表の値。成績・価格は読まない）。固定時点を版管理の
履歴から導く部分（F-4 の git の部分）は `tests/integration/app/test_candidate_fixation.py`。
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.candidate_fixation import (
    FixCheckItem,
    FixCheckOutcome,
    FixCheckReason,
    FixVerdict,
    fix_verdict,
    forward_period,
    forward_start,
    frequency_per_365_days,
    length_for_rate,
    required_length_days,
    reservation_released,
)
from odyssey_fx.evaluation.domain.forward_validation import (
    FrequencySource,
    LengthMethod,
    LengthRule,
    MinTradesSource,
    PairCombination,
)
from odyssey_fx.evaluation.domain.variant import Paired
from odyssey_fx.marketdata.domain.access import is_trading_week_start

#: 前向き検証の規則の版 1 の長さの規則（D09 §9.11.12 の 1 の表の値）。
_RULE = LengthRule(
    method=LengthMethod.POISSON,
    probability=Decimal("0.95"),
    min_days=364,
    min_trades_source=MinTradesSource.RESEARCH_POLICY_MIN_VALIDATION_TRADES_TOTAL,
    frequency_source=FrequencySource.ADOPTION_REVIEW_FIXED_TRIAL_VALIDATION_FOLDS,
    pair_combination=PairCombination.MAX,
    precision_digits=50,
)


def _t(text: str) -> UtcTime:
    return UtcTime.parse(text)


# --- F-3: 長さの計算 -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rate", "expected"),
    [(3, 1600), (4, 1200), (6, 800), (12, 400), (24, 364), (52, 364)],
)
def test_f3_length_for_the_rates_of_the_table(rate: int, expected: int) -> None:
    """F-3: `N` = 8 で `r` = 3・4・6・12・24・52 のとき `L` = 1,600・1,200・800・400・364・364。"""
    assert length_for_rate(Decimal(rate), minimum_trades=8, rule=_RULE) == expected


@pytest.mark.parametrize(("rate", "before_floor"), [(24, 200), (52, 93)])
def test_f3_the_floor_of_364_days_is_applied_after_the_poisson_length(
    rate: int, before_floor: int
) -> None:
    """下限を当てる前の日数（D09 §9.11.5 の表の中の列）が下限より短い組は 364 日になる。"""
    lowered = LengthRule(
        method=_RULE.method,
        probability=_RULE.probability,
        min_days=1,
        min_trades_source=_RULE.min_trades_source,
        frequency_source=_RULE.frequency_source,
        pair_combination=_RULE.pair_combination,
        precision_digits=_RULE.precision_digits,
    )
    assert length_for_rate(Decimal(rate), minimum_trades=8, rule=lowered) == before_floor
    assert length_for_rate(Decimal(rate), minimum_trades=8, rule=_RULE) == 364


def test_f3_a_zero_rate_stops_the_length() -> None:
    """F-3: `r` = 0 なら長さは求まらない（固定できない）。"""
    assert length_for_rate(Decimal(0), minimum_trades=8, rule=_RULE) is None
    rates: Paired[Decimal | None] = Paired(standard=Decimal(12), candidate=Decimal(0))
    assert required_length_days(rates, minimum_trades=Paired(8, 8), rule=_RULE) is None
    unknown: Paired[Decimal | None] = Paired(standard=Decimal(12), candidate=None)
    assert required_length_days(unknown, minimum_trades=Paired(8, 8), rule=_RULE) is None


def test_f3_the_larger_length_of_the_two_pairs_is_the_candidates_length() -> None:
    rates: Paired[Decimal | None] = Paired(standard=Decimal(12), candidate=Decimal(4))
    assert required_length_days(rates, minimum_trades=Paired(8, 8), rule=_RULE) == 1200


def test_f3_the_rate_is_trades_per_365_days_of_all_validation_folds() -> None:
    """頻度 `r` = 件数の合計 ÷ 検証区間の日数の合計 × 365（決定 72）。"""
    folds = [
        Interval(start=_t("2020-01-01T00:00:00Z"), end=_t("2020-07-01T00:00:00Z")),
        Interval(
            start=_t("2021-01-01T00:00:00Z"), end=_t("2021-01-01T00:00:00Z") + timedelta(days=183)
        ),
    ]
    days = (folds[0].end - folds[0].start).days + 183
    assert days == 365
    assert frequency_per_365_days(12, folds) == Decimal(12)
    assert frequency_per_365_days(0, folds) == Decimal(0)
    assert frequency_per_365_days(3, []) is None


def test_f3_the_probability_comparison_is_at_least() -> None:
    """比較は「確率以上」（仮置き F1-5）。確率を少し上げると長さが伸びる。"""
    stricter = LengthRule(
        method=_RULE.method,
        probability=Decimal("0.96"),
        min_days=_RULE.min_days,
        min_trades_source=_RULE.min_trades_source,
        frequency_source=_RULE.frequency_source,
        pair_combination=_RULE.pair_combination,
        precision_digits=_RULE.precision_digits,
    )
    stricter_length = length_for_rate(Decimal(4), minimum_trades=8, rule=stricter)
    assert stricter_length is not None and stricter_length > 1200


def test_f3_inputs_out_of_range_are_structural_errors() -> None:
    with pytest.raises(KernelValueError):
        length_for_rate(Decimal(3), minimum_trades=0, rule=_RULE)
    with pytest.raises(KernelValueError):
        frequency_per_365_days(-1, [])


# --- F-4: 開始・期間の終わり（夏時間の切替の週、境目ちょうど）-----------------------------------


def test_f4_the_start_is_the_first_new_york_sunday_1700_after_t_fix() -> None:
    # 2025-01-10（金）の後の最初の日曜 17:00（冬時間 = 22:00Z）
    assert forward_start(_t("2025-01-10T00:00:00Z")) == _t("2025-01-12T22:00:00Z")
    # 夏時間（17:00 = 21:00Z）
    assert forward_start(_t("2025-07-02T12:00:00Z")) == _t("2025-07-06T21:00:00Z")


def test_f4_t_fix_exactly_on_a_week_boundary_starts_at_the_next_boundary() -> None:
    """境目ちょうどなら次の境目（仮置き F1-4。ADR-0037 第2項の 2 と同じ扱い）。"""
    boundary = _t("2025-01-12T22:00:00Z")
    assert is_trading_week_start(boundary)
    assert forward_start(boundary) == _t("2025-01-19T22:00:00Z")


def test_f4_the_start_across_the_daylight_saving_switch() -> None:
    """夏時間の始まりの週（2025-03-09）: 境目は 22:00Z から 21:00Z に変わる。"""
    assert forward_start(_t("2025-03-05T00:00:00Z")) == _t("2025-03-09T21:00:00Z")
    # 夏時間の終わりの週（2025-11-02）: 21:00Z から 22:00Z へ。
    assert forward_start(_t("2025-10-29T00:00:00Z")) == _t("2025-11-02T22:00:00Z")


def test_f4_the_end_is_the_first_week_boundary_at_or_after_start_plus_length() -> None:
    """`E` は `S + L` 以後の最初の取引週の境目（決定 76＝要決定 F1-F (b)、仮置き F1-6）。"""
    period = forward_period(_t("2025-01-10T00:00:00Z"), 364)
    # S + 364 日 = 2026-01-11T22:00Z（日曜 17:00 の冬時間。ちょうど境目）
    assert period.start == _t("2025-01-12T22:00:00Z")
    assert period.end == _t("2026-01-11T22:00:00Z")
    longer = forward_period(_t("2025-01-10T00:00:00Z"), 365)
    # S + 365 日は月曜なので、次の境目（2026-01-18 の日曜 17:00）に切り上げる。
    assert longer.end == _t("2026-01-18T22:00:00Z")
    assert is_trading_week_start(longer.end)


def test_f4_the_end_rounding_across_the_daylight_saving_switch() -> None:
    """`S + L` の UTC の時刻は、夏時間の切替をまたぐと境目と 1 時間ずれる。"""
    # 冬時間の S（22:00Z）+ 420 日 = 2026-03-08T22:00Z は夏時間の 18:00（境目の 1 時間後）。
    # 次の境目（2026-03-15 の 17:00 = 21:00Z）に切り上げる。
    winter = forward_period(_t("2025-01-10T00:00:00Z"), 420)
    assert winter.start == _t("2025-01-12T22:00:00Z")
    assert winter.end == _t("2026-03-15T21:00:00Z")
    # 夏時間の S（21:00Z）+ 483 日 = 2026-11-01T21:00Z は冬時間の 16:00（境目の 1 時間前）。
    # 同じ日の境目（17:00 = 22:00Z）に寄せる。
    summer = forward_period(_t("2025-07-02T12:00:00Z"), 483)
    assert summer.start == _t("2025-07-06T21:00:00Z")
    assert summer.end == _t("2026-11-01T22:00:00Z")
    # 切替の日に境目ちょうどに当たるとき（夏時間の始まりの日の 17:00 = 21:00Z）はそのまま。
    exact = forward_period(_t("2025-03-05T00:00:00Z"), 364)
    assert exact.start == _t("2025-03-09T21:00:00Z")
    assert exact.end == _t("2026-03-08T21:00:00Z")


# --- 予約を解く条件（決定 74）と合否の合わせ方 ----------------------------------------------------


def test_reservations_are_released_by_the_decided_conditions() -> None:
    assert not reservation_released([], declared_used_for_experiment=False)
    assert not reservation_released(
        [("COMPLETED", "MEETS_STANDARD")], declared_used_for_experiment=False
    )
    for result in ("FAILED_AFTER_CONSUMPTION", "WITHDRAWN", "DECLARED_USED"):
        assert reservation_released([(result, "")], declared_used_for_experiment=False)
    assert reservation_released(
        [("COMPLETED", "INSUFFICIENT_EVIDENCE")], declared_used_for_experiment=False
    )
    assert reservation_released([], declared_used_for_experiment=True)


def _all(passed: bool = True, **failed: tuple[FixCheckReason, ...]) -> list[FixCheckOutcome]:
    return [
        FixCheckOutcome(item, failed.get(item.name, ()) if passed else ()) for item in FixCheckItem
    ]


def test_the_verdict_defers_only_when_item_6_alone_fails_with_a_deferrable_reason() -> None:
    assert fix_verdict(_all()) is FixVerdict.PASSED
    shorter = _all(WINDOW_LONG_ENOUGH=(FixCheckReason.WINDOW_SHORTER_THAN_L,))
    assert fix_verdict(shorter) is FixVerdict.DEFERRED
    pending = _all(WINDOW_LONG_ENOUGH=(FixCheckReason.EARLIER_CANDIDATE_PENDING,))
    assert fix_verdict(pending) is FixVerdict.DEFERRED
    broken = _all(WINDOW_LONG_ENOUGH=(FixCheckReason.WINDOW_NOT_DERIVABLE,))
    assert fix_verdict(broken) is FixVerdict.REFUSED
    both = _all(
        WINDOW_LONG_ENOUGH=(FixCheckReason.WINDOW_SHORTER_THAN_L,),
        RESERVATIONS_ENDED=(FixCheckReason.RESERVATION_NOT_ENDED,),
    )
    assert fix_verdict(both) is FixVerdict.REFUSED
    with pytest.raises(KernelValueError):
        fix_verdict(_all()[:7])


def test_the_verdict_defers_when_only_an_earlier_pending_candidate_blocks_items_6_and_7() -> None:
    """人間の決定 210: 先の候補の最終検証が終わっていないことだけが理由なら延期。"""
    pending = FixCheckReason.EARLIER_CANDIDATE_PENDING
    only = _all(WINDOW_LONG_ENOUGH=(pending,), RESERVATIONS_ENDED=(pending,))
    assert fix_verdict(only) is FixVerdict.DEFERRED
    # 先の候補の予約と、それ以外の候補の終わっていない予約が混じれば固定しない。
    mixed = _all(
        WINDOW_LONG_ENOUGH=(pending,),
        RESERVATIONS_ENDED=(FixCheckReason.RESERVATION_NOT_ENDED, pending),
    )
    assert fix_verdict(mixed) is FixVerdict.REFUSED
    # 他の項目が 1 つでも外れれば固定しない。
    other = _all(
        RESEARCH_POLICY_CURRENT=(FixCheckReason.RESEARCH_POLICY_NOT_CURRENT,),
        WINDOW_LONG_ENOUGH=(pending,),
        RESERVATIONS_ENDED=(pending,),
    )
    assert fix_verdict(other) is FixVerdict.REFUSED
    # 項目 7 の延期にする理由は「先の候補が終わっていない」だけ（窓の理由は延期にしない）。
    misplaced = _all(RESERVATIONS_ENDED=(FixCheckReason.WINDOW_SHORTER_THAN_L,))
    assert fix_verdict(misplaced) is FixVerdict.REFUSED
