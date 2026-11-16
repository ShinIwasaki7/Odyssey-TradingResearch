"""候補の固定の規則の計算と、固定の前の機械的な確かめの結果の型（D09 §9.11.3〜§9.11.5・§9.11.10）。

順 11 の PR 4c（人間の決定 189。D25 §8、D23 §8）。前向き検証の規則（D09 §9.11）のうち**固定まで**
（受入テスト F-3〜F-5。F-6 の前向き検証の申請は入れない）を、時計・乱数・ファイルを読まない純粋な
関数で持つ。

- **長さ `L`**（D09 §9.11.5、決定 72、仮置き F1-5）: 平均 `r × d ÷ 365` のポアソン分布で最低件数
  `N` 以上になる確率が規則の確率以上になる最小の整数の日数 `d` と、下限の日数の大きい方。組ごとに
  求めて大きい方。計算は規則の桁数（10 進 50 桁）の局所の文脈で行う（グローバルな文脈に頼らない。
  ADR-0012）。`r` が 0 の組があれば求まらない（`None`）。
- **開始 `S` と期間の終わり `E`**（D09 §9.11.4 の 1、決定 76＝要決定 F1-F (b)、仮置き F1-4・F1-6）:
  `S` は固定時点より後の最初のニューヨークの日曜 17:00（ちょうど境目なら次の境目）、`E` は
  `S + L` 以後の最初の取引週の境目。
- **予約を解く条件**（決定 74＝要決定 F1-D (a)）: 最終検証の台帳の行から機械的に導く。
- **固定の前の確かめ**（D09 §9.11.10 の 8 項目）の結果の型と、合否の合わせ方（項目 6 の窓の長さ
  だけが外れたときは固定の延期。D15-15 (a)、D25 §6.5 の 3）。外れた理由が「審査の順で先の候補の
  最終検証が終わっていない」だけ（項目 6 と、その候補の予約による項目 7）のときも延期（人間の
  決定 210）。

どの値も成績・価格を含まない（日数・件数の下限・確率・時刻・理由の識別子だけ）。頻度 `r` と取引の
件数は計算の入力にだけ使い、結果の型には持たない（仮置き F1-5）。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Context, Decimal, localcontext
from enum import Enum
from typing import Final

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import decimal_from_int
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.forward_validation import CandidateFix, LengthRule
from odyssey_fx.evaluation.domain.variant import Paired
from odyssey_fx.marketdata.domain.access import (
    acquisition_start_after,
    next_trading_week_start,
    trading_week_start,
)

__all__ = [
    "DEFERRABLE_REASONS",
    "DEFERRABLE_RESERVATION_REASONS",
    "FixCheckItem",
    "FixCheckOutcome",
    "FixCheckReason",
    "FixVerdict",
    "ResolvedFix",
    "fix_verdict",
    "forward_period",
    "forward_start",
    "frequency_per_365_days",
    "length_for_rate",
    "required_length_days",
    "reservation_released",
]

_DAYS_PER_YEAR: Final = decimal_from_int(365)
_SECONDS_PER_DAY: Final = decimal_from_int(86400)
#: 長さの探索の上限（日。下限 364 日の 1,000 倍。これを超える `L` は求まらないものとして扱う）。
_MAX_SEARCH_DAYS: Final = 364_000


# --- 長さ L（D09 §9.11.5）------------------------------------------------------------------


def _require_count(value: object, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise KernelValueError(f"{label} must be an int >= {minimum}, got {value!r}")
    return value


def frequency_per_365_days(trade_total: int, validation: Sequence[Interval]) -> Decimal | None:
    """365 日あたりの取引の件数 `r`（決定 72: 件数の合計 ÷ 検証区間の日数の合計 × 365）。

    `validation` は研究履歴の全 fold の検証区間。日数は秒から求める（端数を含む）。区間が無い・
    長さが 0 なら `None`（求まらない）。計算は呼び出し側の局所の文脈で行うので、ここでは 50 桁の
    局所の文脈を当てる。
    """
    _require_count(trade_total, "frequency_per_365_days.trade_total", minimum=0)
    if not all(isinstance(item, Interval) for item in validation):
        raise KernelValueError("frequency_per_365_days.validation must be Intervals")
    with localcontext(Context(prec=50)):
        seconds = sum(
            (decimal_from_int(int((item.end - item.start).total_seconds())) for item in validation),
            decimal_from_int(0),
        )
        if seconds <= 0:
            return None
        return decimal_from_int(trade_total) * _DAYS_PER_YEAR * _SECONDS_PER_DAY / seconds


def _at_least(mean: Decimal, minimum_trades: int) -> Decimal:
    """平均 `mean` のポアソン分布で `minimum_trades` 件以上になる確率（局所の文脈の中で呼ぶ）。"""
    term = decimal_from_int(1)
    below = decimal_from_int(0)
    for k in range(minimum_trades):
        if k > 0:
            term = term * mean / decimal_from_int(k)
        below += term
    return decimal_from_int(1) - (-mean).exp() * below


def length_for_rate(rate: Decimal, *, minimum_trades: int, rule: LengthRule) -> int | None:
    """1 つの組の長さ（D09 §9.11.5 の式。下限の日数を当てた後の日数）。`rate` が 0 以下なら `None`。

    確率は `d` について単調に増えるので、条件を満たす最小の `d` を二分探索で求める。比較は「確率
    以上」（仮置き F1-5）。上限（下限の日数の 1,000 倍）でも届かなければ `None`。
    """
    if not isinstance(rate, Decimal) or not rate.is_finite():
        raise KernelValueError(f"length_for_rate.rate must be a finite Decimal, got {rate!r}")
    _require_count(minimum_trades, "length_for_rate.minimum_trades", minimum=1)
    if not isinstance(rule, LengthRule):
        raise KernelValueError("length_for_rate.rule must be a LengthRule")
    if rate <= 0:
        return None
    with localcontext(Context(prec=rule.precision_digits)):

        def reaches(days: int) -> bool:
            mean = rate * decimal_from_int(days) / _DAYS_PER_YEAR
            return _at_least(mean, minimum_trades) >= rule.probability

        if not reaches(_MAX_SEARCH_DAYS):
            return None
        low, high = 1, _MAX_SEARCH_DAYS
        while low < high:
            middle = (low + high) // 2
            if reaches(middle):
                high = middle
            else:
                low = middle + 1
    return max(rule.min_days, low)


def required_length_days(
    rates: Paired[Decimal | None], *, minimum_trades: Paired[int], rule: LengthRule
) -> int | None:
    """候補の長さ `L`（組ごとの長さの大きい方。`PairCombination.MAX`）。

    求まらない組があれば `None`。
    """
    lengths: list[int] = []
    for variant, rate in rates.items():
        if rate is None:
            return None
        length = length_for_rate(rate, minimum_trades=minimum_trades.of(variant), rule=rule)
        if length is None:
            return None
        lengths.append(length)
    return max(lengths)


# --- 開始 S と期間の終わり E（D09 §9.11.4、仮置き F1-4・F1-6）-----------------------------


def forward_start(t_fix: UtcTime) -> UtcTime:
    """開始 `S`: `T_fix` より後の最初のニューヨークの日曜 17:00（ちょうど境目なら次の境目）。"""
    return acquisition_start_after(t_fix)


def forward_period(t_fix: UtcTime, length_days: int) -> Interval:
    """前向きの期間 `P = [S, E)`（`E` は `S + L` 以後の最初の取引週の境目。決定 76）。"""
    _require_count(length_days, "forward_period.length_days", minimum=1)
    start = forward_start(t_fix)
    reach = start + timedelta(days=length_days)
    week = trading_week_start(reach)
    end = reach if week == reach else next_trading_week_start(week)
    return Interval(start=start, end=end)


@dataclass(frozen=True, slots=True)
class ResolvedFix:
    """origin の既定ブランチに入った固定の記録と、版管理の履歴から導いた固定時点（仮置き F1-3）。

    `commit` は main の first-parent の履歴で記録のファイルを初めて含んだコミット、`t_fix` はその
    committer の時刻。`S`・`E` は固定の記録と `T_fix` から導く（記録には書かない）。
    """

    record: CandidateFix
    path: str
    commit: str
    t_fix: UtcTime

    def __post_init__(self) -> None:
        if not isinstance(self.record, CandidateFix):
            raise KernelValueError("ResolvedFix.record must be a CandidateFix")
        if not isinstance(self.t_fix, UtcTime):
            raise KernelValueError("ResolvedFix.t_fix must be a UtcTime")
        for name in ("path", "commit"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise KernelValueError(f"ResolvedFix.{name} must be a non-empty str")

    @property
    def length_days(self) -> int:
        return self.record.length_days

    @property
    def fixed_trials(self) -> Paired[int]:
        """組ごとの固定した試行の番号。"""
        return self.record.fixed_trials.map(lambda _, pin: pin.trial_index)

    @property
    def forward_start(self) -> UtcTime:
        return forward_start(self.t_fix)

    @property
    def forward_period(self) -> Interval:
        return forward_period(self.t_fix, self.record.length_days)


# --- 予約を解く条件（決定 74）-----------------------------------------------------------------

#: 最終検証の台帳の結果のうち、それだけで予約を解くもの（決定 74 の条件）。
_RELEASING_RESULTS: Final = frozenset({"FAILED_AFTER_CONSUMPTION", "WITHDRAWN", "DECLARED_USED"})
#: 組の判定「共通基準を満たす（両組）」の値（D09 §7.12。`SearchVerdict.MEETS_STANDARD`）。
_MEETS_BOTH: Final = "MEETS_STANDARD"


def reservation_released(
    rows: Iterable[tuple[str, str]], *, declared_used_for_experiment: bool
) -> bool:
    """前向きの期間の予約が解かれたか（決定 74＝要決定 F1-D (a)）。

    `rows` はその候補の最終検証の台帳の行の `(結果, 組の判定)`。消費の後の失敗・取り下げ・申告で
    使用済みのどれかの行があるか、完了の行の組の判定が「満たす（両組）」でなければ解かれた。
    `declared_used_for_experiment` は同じ `experiment_id` のどれかの実行の申告で使用済みの行
    （D09 §9.10.10 の 7 の (2)）。置き換えられた固定の記録は呼び出し側が解かれたものとして扱う
    （仮置き F1-12）。
    """
    if declared_used_for_experiment:
        return True
    for result, combined in rows:
        if result in _RELEASING_RESULTS:
            return True
        if result == "COMPLETED" and combined != _MEETS_BOTH:
            return True
    return False


# --- 固定の前の確かめの結果（D09 §9.11.10）---------------------------------------------------


class FixCheckItem(Enum):
    """固定の前の確かめの 8 項目（D09 §9.11.10 の表の番号）。"""

    REVIEW_PROCEEDED = 1
    NO_OTHER_VALID_FIX = 2
    FORWARD_RULES_CURRENT = 3
    RESEARCH_POLICY_CURRENT = 4
    LENGTH_MATCHES = 5
    WINDOW_LONG_ENOUGH = 6
    RESERVATIONS_ENDED = 7
    FORMAT_AND_DIGESTS = 8


class FixCheckReason(Enum):
    """確かめが外れた理由の識別子（出力と固定の PR の本文に書く。数値を含まない）。"""

    # 項目 1
    REVIEW_NOT_PROCEEDED = "REVIEW_NOT_PROCEEDED"
    REVIEW_TRIALS_MISMATCH = "REVIEW_TRIALS_MISMATCH"
    # 項目 2
    OTHER_VALID_FIX_EXISTS = "OTHER_VALID_FIX_EXISTS"
    SUPERSEDES_MISMATCH = "SUPERSEDES_MISMATCH"
    SUPERSEDED_ALREADY_APPLIED = "SUPERSEDED_ALREADY_APPLIED"
    # 項目 3
    FORWARD_RULES_NOT_REGISTERED = "FORWARD_RULES_NOT_REGISTERED"
    FORWARD_RULES_NOT_CURRENT = "FORWARD_RULES_NOT_CURRENT"
    FORWARD_RULES_DIGEST_MISMATCH = "FORWARD_RULES_DIGEST_MISMATCH"
    # 項目 4
    RESEARCH_POLICY_NOT_CURRENT = "RESEARCH_POLICY_NOT_CURRENT"
    RESEARCH_POLICY_DIGEST_MISMATCH = "RESEARCH_POLICY_DIGEST_MISMATCH"
    # 項目 5
    FREQUENCY_UNAVAILABLE = "FREQUENCY_UNAVAILABLE"
    FREQUENCY_ZERO = "FREQUENCY_ZERO"
    LENGTH_MISMATCH = "LENGTH_MISMATCH"
    # 項目 6（延期できるもの）
    WINDOW_SHORTER_THAN_L = "WINDOW_SHORTER_THAN_L"
    NO_USABLE_WINDOW = "NO_USABLE_WINDOW"
    EARLIER_CANDIDATE_PENDING = "EARLIER_CANDIDATE_PENDING"
    # 項目 6（延期できないもの）
    WINDOW_NOT_DERIVABLE = "WINDOW_NOT_DERIVABLE"
    # 項目 7（終わっていない予約が審査の順で先の候補のもので、その候補の最終検証が終わって
    # いないときは、項目 6 と同じ `EARLIER_CANDIDATE_PENDING`。人間の決定 210）
    RESERVATION_NOT_ENDED = "RESERVATION_NOT_ENDED"
    # 項目 8
    RECORD_UNREADABLE = "RECORD_UNREADABLE"
    RECORD_PATH_MISMATCH = "RECORD_PATH_MISMATCH"
    DIGEST_MISMATCH = "DIGEST_MISMATCH"
    INPUT_NOT_ON_ORIGIN = "INPUT_NOT_ON_ORIGIN"


#: 項目 6 のうち、待てば解けうるので固定の延期にする理由（D15-15 (a)、D25 §6.5 の 3）。
DEFERRABLE_REASONS: Final = frozenset(
    {
        FixCheckReason.WINDOW_SHORTER_THAN_L,
        FixCheckReason.NO_USABLE_WINDOW,
        FixCheckReason.EARLIER_CANDIDATE_PENDING,
    }
)

#: 項目 7 のうち、固定の延期にする理由（人間の決定 210）。審査の順で先の候補の最終検証が終わって
#: いないだけなら、待てば解ける（機械で回復できる止まり方は関所を作らない。決定 188 と同じ考え）。
DEFERRABLE_RESERVATION_REASONS: Final = frozenset({FixCheckReason.EARLIER_CANDIDATE_PENDING})

#: 項目ごとの延期にする理由（ここに無い項目が外れたら固定しない）。
_DEFERRABLE_BY_ITEM: Final[dict[FixCheckItem, frozenset[FixCheckReason]]] = {
    FixCheckItem.WINDOW_LONG_ENOUGH: DEFERRABLE_REASONS,
    FixCheckItem.RESERVATIONS_ENDED: DEFERRABLE_RESERVATION_REASONS,
}


@dataclass(frozen=True, slots=True)
class FixCheckOutcome:
    """1 つの項目の確かめの結果（理由が空なら合格）。

    `detail` は人間が読む説明（日数・件数・識別子だけ）。
    """

    item: FixCheckItem
    reasons: tuple[FixCheckReason, ...]
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.item, FixCheckItem):
            raise KernelValueError("FixCheckOutcome.item must be a FixCheckItem")
        if not isinstance(self.reasons, tuple) or not all(
            isinstance(reason, FixCheckReason) for reason in self.reasons
        ):
            raise KernelValueError("FixCheckOutcome.reasons must be a tuple of FixCheckReason")
        if len(set(self.reasons)) != len(self.reasons):
            raise KernelValueError("FixCheckOutcome.reasons has duplicates")
        if not isinstance(self.detail, str):
            raise KernelValueError("FixCheckOutcome.detail must be a str")

    @property
    def passed(self) -> bool:
        return not self.reasons


class FixVerdict(Enum):
    """8 項目の合わせ方（D09 §9.11.10 の表の「外れたとき」）。"""

    #: すべて合格（固定の記録を作ってよい）。
    PASSED = "PASSED"
    #: 項目 6 だけが、延期できる理由で外れた（固定を延期する。PR は作らない・merge しない）。
    #: 項目 7 が「先の候補の最終検証が終わっていない」だけで外れたときも同じ（決定 210）。
    DEFERRED = "DEFERRED"
    #: それ以外の外れ（固定しない）。
    REFUSED = "REFUSED"


def fix_verdict(outcomes: Sequence[FixCheckOutcome]) -> FixVerdict:
    """8 項目の結果から合否を決める。8 項目がそろっていなければ構造の誤り。

    外れた項目のどれもが、その項目の延期にする理由だけで外れたときに延期（項目 6 は
    `DEFERRABLE_REASONS`、項目 7 は `DEFERRABLE_RESERVATION_REASONS`。決定 210）。他の理由が
    1 つでも混じれば固定しない。
    """
    if sorted(item.item.value for item in outcomes) != [item.value for item in FixCheckItem]:
        raise KernelValueError("fix_verdict requires exactly one outcome for each of the 8 items")
    failed = [item for item in outcomes if not item.passed]
    if not failed:
        return FixVerdict.PASSED
    if all(
        all(reason in _DEFERRABLE_BY_ITEM.get(item.item, frozenset()) for reason in item.reasons)
        for item in failed
    ):
        return FixVerdict.DEFERRED
    return FixVerdict.REFUSED
