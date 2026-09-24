"""後続確認と `CONFIRMED` 経路の制御（D05 §7.7、§6.11）。

確認待ちの戦略（発注方針が `AwaitConfirmation`、D04 §10.1）では、取引機会が生まれてから
確認足の確定ごとに確認部品を評価し、成立すれば機会を `CONFIRMED` へ進め（遷移10）、確認期限に
到達すれば `EXPIRED` で終端する（遷移11）。制御はすべてコンパイル結果の確認の計画
（`ConfirmationPlan`、D05 §5.3）の1件を読んで行い、宣言と契約を実行時に読み直さない。

本モジュールは**記録の型**（確認試行 `ConfirmationAttempt`）と、評価器（`evaluator`）の手順が
使う**判定の規則**を純粋関数として置く。手順そのもの（いつ判定し、いつ要求を作るか）は評価器が
`step` の中で行う。`waiting` と同じ切り分けである。

## 確認の開始足（D05 §7.7、上位設計書 §4.3.13）

取引機会が**実際に生成された判断時刻**に利用可能な、確認足の系列の最新の確定足を開始足とし、
一度決めたら変えない。読めなければ開始足なし（`None`）とし、古い足へ黙って戻らない。開始足が
無い機会は、次の確認足から確認する。

## 確認評価を作る対象（D05 §7.7 の表、§6.11）

確認待ち（`OPEN`）の機会だけを対象にする。対象の足が開始足と同じで、開始足で確認しない設定
（`include_start_bar=False`）なら要求を作らない。数えるのは確認部品の番（P4）であり、同じ
`step` の P3 で生まれたばかりの機会も含む。

## 期限の数え方（D05 §7.7、Q19 決定）

本数で書いた期限は**確認足の系列**の足の終了予定で数え、機会を生成した足の次の確定足を1本目と
する（開始足で確認するかどうかに依らない）。判定はライフサイクル検査で行い、確認の評価より先に
来るので、`bars=n` の n 本目の確認足は確認に使われない。

## 確認試行（D05 §7.7、Q26 決定）

`(取引機会, 確認足)` の組につき1件とし、決着したら**同じ1件の結末を置き換える**。待機に入った
試行は `WAITING` で始まり、再開して決着したときに置き換わる。エンジンへは、その `step` で作った・
書き換えた試行だけを `RuntimeStepResult.confirmation_attempts` で返す（D05 §6.2 の手順10）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import OpportunityId, RequestId
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.bar import BarKey
from odyssey_fx.strategy.compiler.compiled import ConfirmationPlan
from odyssey_fx.strategy.declarations.validation import require_instance
from odyssey_fx.strategy.runtime.waiting import WaitDeadline, resolve_deadline

__all__ = [
    "ConfirmationAttempt",
    "ConfirmationAttemptOutcome",
    "confirmation_deadline",
    "wants_confirmation",
]


class ConfirmationAttemptOutcome(Enum):
    """確認試行の結末（D05 §3・§7.7 の表）。"""

    #: `confirmed=True` の確認結果が出た（遷移10）。
    CONFIRMED = "CONFIRMED"
    #: `confirmed=False` の確認結果が出た。機会は `OPEN` のまま次の確認足を待つ。
    NOT_CONFIRMED = "NOT_CONFIRMED"
    #: 入力不足で評価を見送った。機会は `OPEN` のまま。
    SKIPPED = "SKIPPED"
    #: 入力不足で待機に入った（**途中経過**。決着したら置き換わる）。
    WAITING = "WAITING"
    #: 確認足が追い越されて要求が閉じた。機会は `OPEN` のまま。
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True, slots=True)
class ConfirmationAttempt:
    """確認試行1件（D05 §3・§7.7、判断履歴の表18 の行）。

    主キーは `(opportunity_id, bar_key)` である（D06 §9.2）。同じ機会・同じ確認足について作る
    評価要求は1件だけなので、試行も1件である。待機をはさんでも行は増えず、その確認足について
    の最後の結末が残る。
    """

    opportunity_id: OpportunityId
    bar_key: BarKey
    request_id: RequestId
    outcome: ConfirmationAttemptOutcome

    def __post_init__(self) -> None:
        require_instance(self.opportunity_id, OpportunityId, "ConfirmationAttempt.opportunity_id")
        require_instance(self.bar_key, BarKey, "ConfirmationAttempt.bar_key")
        require_instance(self.request_id, RequestId, "ConfirmationAttempt.request_id")
        require_instance(self.outcome, ConfirmationAttemptOutcome, "ConfirmationAttempt.outcome")

    @property
    def key(self) -> tuple[OpportunityId, BarKey]:
        """主キー `(opportunity_id, bar_key)`（D06 §9.2 の表18）。"""
        return (self.opportunity_id, self.bar_key)


def confirmation_deadline(plan: ConfirmationPlan, created: UtcTime) -> WaitDeadline:
    """確認期限を機会の生成時点で絶対の形へ解決する（D05 §7.7 の表、Q19 決定）。

    本数の期限は確認足の系列で数える（`WaitUntilBars(series=確認足の系列, remaining=n)`）。
    経過時間の期限は生成した判断時刻に足した時刻にする。数え始めは生成した `step` の後の
    ライフサイクル検査であり、生成した足の次の確定足が1本目になる。
    """
    return resolve_deadline(plan.deadline, started=created, series=plan.series)


def wants_confirmation(
    plan: ConfirmationPlan, start_bar: BarKey | None, target_bar: BarKey | None
) -> bool:
    """確認待ちの機会について、この確認足で確認評価の要求を作るか（D05 §7.7）。

    対象の足が開始足と同じなら `include_start_bar` に従う。開始足が無い機会（生成時に確認足が
    読めなかった）は、開始足にあたる足が無いので、どの確認足でも評価する。対象の足が
    確認足の系列でなければ、それは確認の起動ではない（コンパイラの検査 b が確認部品の足の
    確定の系列を1つに限っているので、通常は起きない）。
    """
    if target_bar is None:
        raise KernelValueError(
            "a confirmation request is started by a bar close of the confirmation series (D05 §7.7)"
        )
    if target_bar.series != plan.series:
        return False
    if start_bar is not None and target_bar == start_bar:
        return plan.include_start_bar
    return True
