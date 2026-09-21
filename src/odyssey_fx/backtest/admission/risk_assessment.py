"""リスク審査と数量決定（D06 §6.4・§6.5、上位設計書 §4.7.9 C・§4.7.10）。

式と数値は上位設計書で確定済みで、本モジュールが足すのは**適用の順**と、各段で記録する値
である。手順は8段あり、途中で拒否して終わることがある（手順4 の保護水準の妥当性違反、
手順6 の最小数量未満）。その先の手順が作る値は**存在しない**ので、省略可能にして
`reached_step` にどこまで進んだかを入れる。

| 手順 | 内容 |
|---|---|
| 1 | 受付判断時の `balance` を読む。非正なら拒否（`RISK`） |
| 2 | 試行予算・口座残余・受付予算を求める |
| 3 | 参照価格を固定する（買いは ask、売りは bid） |
| 4 | 保護水準の妥当性を検査する（違反は `PROTECTION_INVALID`） |
| 5 | 損切りを価格刻みへ丸め、許容不利価格と1通貨あたりの予約リスクを求める |
| 6 | 予算に収まる最大の数量を**切り下げ**で求める |
| 7 | 丸め後の数量で再計算し、口座制約と同時保持枠を再検査する |
| 8 | 到達した段までの入力と結果を記録する |

段階2はレバレッジ・証拠金の検査を行わない（ADR-0015 の縦断範囲に証拠金モデルが無く、検査に
使う値が存在しない）。`checks` に「未実施」を入れず、検査そのものを持たない。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

from odyssey_fx.backtest.domain.account import AccountLedger
from odyssey_fx.backtest.domain.orders import EntryRequest, OrderSide, ReferenceQuote
from odyssey_fx.backtest.domain.policies import CostModel, RiskPolicy
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import AttemptId, EvidenceId
from odyssey_fx.common.money import (
    ConversionRate,
    Money,
    Price,
    PriceOffset,
    Quantity,
    RoundingDirection,
    decimal_from_int,
    kernel_context,
)
from odyssey_fx.common.reason import Reason, ReasonCode, RiskRejectionDetail
from odyssey_fx.common.refs import PolicyRef
from odyssey_fx.common.symbol import SymbolSpec

__all__ = [
    "AdmissionBudget",
    "RiskAssessment",
    "RiskAssessmentRef",
    "RiskCheckResult",
    "assess_entry",
    "round_adverse_limit",
    "round_stop",
]

#: 段階2の同時保持上限（建玉＋未終端のエントリー注文、D06 §8.2）。
POSITION_SLOT_LIMIT = decimal_from_int(1)


@dataclass(frozen=True, slots=True)
class RiskCheckResult:
    """1件の検査の名前・上限・観測値（D06 §6.4）。

    `limit` と `observed` は金額か比率のいずれかで、**両者の型は一致**していなければ
    ならない（D02 §8.2 の `RiskRejectionDetail` と同じ組で作れるようにするため）。
    """

    check: str
    passed: bool
    limit: Money | Decimal
    observed: Money | Decimal

    def __post_init__(self) -> None:
        # 同じ不変条件を2か所に書かないよう、D02 の詳細型に検査させる。
        RiskRejectionDetail(check=self.check, limit=self.limit, observed=self.observed)
        if not isinstance(self.passed, bool):
            raise KernelValueError("RiskCheckResult.passed must be a bool")

    def as_detail(self) -> RiskRejectionDetail:
        """拒否理由へ添える型付き詳細（D02 §8.2）。"""
        return RiskRejectionDetail(check=self.check, limit=self.limit, observed=self.observed)


@dataclass(frozen=True, slots=True)
class AdmissionBudget:
    """受付判断に使った予算の内訳（D06 §6.4 の手順2）。"""

    balance: Money
    trial_budget: Money
    account_remaining: Money
    admission_budget: Money
    consumed: Money

    def __post_init__(self) -> None:
        for name in (
            "balance",
            "trial_budget",
            "account_remaining",
            "admission_budget",
            "consumed",
        ):
            if not isinstance(getattr(self, name), Money):
                raise KernelValueError(f"AdmissionBudget.{name} must be a Money")


@dataclass(frozen=True, slots=True)
class RiskAssessmentRef:
    """審査記録への参照（D06 §3）。実体と同じ識別子だけを持つ。"""

    assessment_id: EvidenceId

    def __post_init__(self) -> None:
        if not isinstance(self.assessment_id, EvidenceId):
            raise KernelValueError("RiskAssessmentRef.assessment_id must be an EvidenceId")


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """審査の不変記録（D06 §6.4 の手順8）。到達しなかった手順の項目は `None`。"""

    assessment_id: EvidenceId
    attempt_id: AttemptId
    policy_ref: PolicyRef
    reached_step: int
    budget: AdmissionBudget
    reference_quote: ReferenceQuote
    stop_before_rounding: Price
    checks: tuple[RiskCheckResult, ...] = ()
    stop_after_rounding: Price | None = None
    adverse_fill_limit: Price | None = None
    quantity_step: Decimal | None = None
    quantity: Quantity | None = None
    conversion: ConversionRate | None = None
    cost_budget: Money | None = None
    reservation_amount: Money | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.assessment_id, EvidenceId):
            raise KernelValueError("RiskAssessment.assessment_id must be an EvidenceId")
        if not isinstance(self.attempt_id, AttemptId):
            raise KernelValueError("RiskAssessment.attempt_id must be an AttemptId")
        if not isinstance(self.policy_ref, PolicyRef):
            raise KernelValueError("RiskAssessment.policy_ref must be a PolicyRef")
        if isinstance(self.reached_step, bool) or not isinstance(self.reached_step, int):
            raise KernelValueError("RiskAssessment.reached_step must be an int")
        if not 3 <= self.reached_step <= 8:
            raise KernelValueError(
                "RiskAssessment is only written for attempts that reached step 3 (the reference"
                f" quote); got reached_step={self.reached_step}"
            )
        if not isinstance(self.budget, AdmissionBudget):
            raise KernelValueError("RiskAssessment.budget must be an AdmissionBudget")
        if not isinstance(self.reference_quote, ReferenceQuote):
            raise KernelValueError("RiskAssessment.reference_quote must be a ReferenceQuote")
        if not isinstance(self.stop_before_rounding, Price):
            raise KernelValueError("RiskAssessment.stop_before_rounding must be a Price")
        if not isinstance(self.checks, tuple):
            raise KernelValueError("RiskAssessment.checks must be a tuple")

    @property
    def ref(self) -> RiskAssessmentRef:
        """この記録への参照。"""
        return RiskAssessmentRef(assessment_id=self.assessment_id)


def round_stop(stop: Price, side: OrderSide, spec: SymbolSpec) -> Price:
    """初期の損切りを価格刻みへ丸める（D06 §6.5）。買いは `DOWN`、売りは `UP`。

    意図より近い損切りへ黙って変更しないための向きである（上位設計書 §4.7.3）。
    """
    direction = RoundingDirection.DOWN if side is OrderSide.BUY else RoundingDirection.UP
    return stop.round_to_tick(spec.price_tick, direction)


def round_adverse_limit(limit: PriceOffset, spec: SymbolSpec) -> PriceOffset:
    """許容不利約定幅 Δ を**値幅を広げない向き**へ丸める（D06 §6.5）。"""
    with localcontext(kernel_context()):
        value = limit.value
    rounded = Price(value).round_to_tick(spec.price_tick, RoundingDirection.DOWN)
    return PriceOffset(rounded.value)


def _rejection(check: RiskCheckResult, code: ReasonCode = ReasonCode.RISK) -> Reason:
    """検査結果から拒否理由を作る。"""
    if code is ReasonCode.RISK:
        return Reason(code=code, detail=check.as_detail())
    return Reason(code=code)


def assess_entry(
    request: EntryRequest,
    *,
    attempt_id: AttemptId,
    assessment_id: EvidenceId,
    ledger: AccountLedger,
    risk_policy: RiskPolicy,
    policy_ref: PolicyRef,
    cost_model: CostModel,
    symbol_spec: SymbolSpec,
    reference_quote: ReferenceQuote,
    decision_bid: Price,
    adverse_fill_limit: PriceOffset,
    conversion: ConversionRate,
) -> tuple[RiskAssessment, Reason | None]:
    """エントリー要求を審査する（D06 §6.4 の手順1〜8）。

    戻り値は `(審査記録, 拒否理由)`。拒否理由が `None` なら受付できる。
    `reference_quote` は買いなら ask、売りなら bid で（上位設計書 §4.7.9 C）、`decision_bid`
    は同じ足の bid そのものである。保護水準の検査（手順4）はここから売却側・購入側の価格を
    作る。
    """
    currency = ledger.currency
    balance = ledger.balance
    checks: list[RiskCheckResult] = []

    # 手順1・2: 予算。
    with localcontext(kernel_context()):
        trial = Money(balance.amount * risk_policy.trial_risk_rate, currency)
        cap = Money(balance.amount * risk_policy.account_risk_cap, currency)
    consumed = ledger.consumed()
    zero = ledger.zero()
    remaining = cap - consumed
    if remaining < zero:
        remaining = zero
    admission_budget = trial if trial < remaining else remaining
    budget = AdmissionBudget(
        balance=balance,
        trial_budget=trial,
        account_remaining=remaining,
        admission_budget=admission_budget,
        consumed=consumed,
    )

    def record(step: int) -> RiskAssessment:
        return RiskAssessment(
            assessment_id=assessment_id,
            attempt_id=attempt_id,
            policy_ref=policy_ref,
            reached_step=step,
            budget=budget,
            reference_quote=reference_quote,
            stop_before_rounding=request.protection.stop_loss,
            checks=tuple(checks),
        )

    if balance.amount <= 0:
        checks.append(
            RiskCheckResult(check="positive_balance", passed=False, limit=zero, observed=balance)
        )
        return record(3), _rejection(checks[-1])

    # 手順4: 保護水準の妥当性（買いは判断時 bid より下、売りは ask より上）。
    # 比べる価格は手順3 で固定した参照価格と**同じ足**から取り、買いの検査には bid
    # （売却側）、売りの検査には ask（購入側）を使う（D06 §6.4 の手順4）。
    stop = request.protection.stop_loss
    if request.side is OrderSide.BUY:
        comparand = decision_bid
        valid = stop < comparand
    else:
        comparand = cost_model.spread_model.ask_from_bid(decision_bid)
        valid = stop > comparand
    checks.append(
        RiskCheckResult(
            check="protection_direction",
            passed=valid,
            limit=comparand.value,
            observed=stop.value,
        )
    )
    if not valid:
        return record(4), _rejection(checks[-1], ReasonCode.PROTECTION_INVALID)

    # 手順5: 丸めと1通貨あたりの予約リスク。
    rounded_stop = round_stop(stop, request.side, symbol_spec)
    limit_offset = round_adverse_limit(adverse_fill_limit, symbol_spec)
    direction = decimal_from_int(1 if request.side is OrderSide.BUY else -1)
    with localcontext(kernel_context()):
        price_limit_value = reference_quote.price.value + direction * limit_offset.value
    price_limit = Price(price_limit_value)
    with localcontext(kernel_context()):
        risk_per_unit = direction * (price_limit.value - rounded_stop.value)
    if risk_per_unit <= 0:
        checks.append(
            RiskCheckResult(
                check="risk_per_unit",
                passed=False,
                limit=decimal_from_int(0),
                observed=risk_per_unit,
            )
        )
        return record(5), _rejection(checks[-1])
    with localcontext(kernel_context()):
        per_unit = risk_per_unit + cost_model.budget_per_unit()

    # 手順6: 予算に収まる最大の数量（切り下げ）。
    with localcontext(kernel_context()):
        raw_units = admission_budget.amount / per_unit
    quantity = (
        None
        if raw_units <= 0
        else Quantity(raw_units).round_down_to_step(symbol_spec.quantity_step)
    )
    if quantity is None or quantity.units < symbol_spec.min_quantity:
        observed = decimal_from_int(0) if quantity is None else quantity.units
        checks.append(
            RiskCheckResult(
                check="min_quantity",
                passed=False,
                limit=symbol_spec.min_quantity,
                observed=observed,
            )
        )
        assessment = RiskAssessment(
            assessment_id=assessment_id,
            attempt_id=attempt_id,
            policy_ref=policy_ref,
            reached_step=6,
            budget=budget,
            reference_quote=reference_quote,
            stop_before_rounding=stop,
            stop_after_rounding=rounded_stop,
            adverse_fill_limit=price_limit,
            quantity_step=symbol_spec.quantity_step,
            checks=tuple(checks),
        )
        return assessment, _rejection(checks[-1])

    # 手順7: 丸め後の数量での再検査。
    with localcontext(kernel_context()):
        reservation_amount = Money(per_unit * quantity.units, currency)
    costs = cost_model.budget_for(quantity, currency)
    budget_check = RiskCheckResult(
        check="admission_budget",
        passed=reservation_amount <= admission_budget,
        limit=admission_budget,
        observed=reservation_amount,
    )
    checks.append(budget_check)
    slot_used = decimal_from_int(len(ledger.open_positions()) + len(ledger.pending_entry_orders()))
    slot_check = RiskCheckResult(
        check="position_slot",
        passed=slot_used < POSITION_SLOT_LIMIT,
        limit=POSITION_SLOT_LIMIT,
        observed=slot_used,
    )
    checks.append(slot_check)

    assessment = RiskAssessment(
        assessment_id=assessment_id,
        attempt_id=attempt_id,
        policy_ref=policy_ref,
        reached_step=7 if not (budget_check.passed and slot_check.passed) else 8,
        budget=budget,
        reference_quote=reference_quote,
        stop_before_rounding=stop,
        stop_after_rounding=rounded_stop,
        adverse_fill_limit=price_limit,
        quantity_step=symbol_spec.quantity_step,
        quantity=quantity,
        conversion=conversion,
        cost_budget=costs,
        reservation_amount=reservation_amount,
        checks=tuple(checks),
    )
    for check in (budget_check, slot_check):
        if not check.passed:
            return assessment, _rejection(check)
    return assessment, None
