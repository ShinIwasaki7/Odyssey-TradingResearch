"""品質確認のコマンドを組み立てる専用の入口（ADR-0036 第5項の 1・第7項・第9項）。

品質確認の許可を受け取るのは、品質確認のコマンド（受入れを含む）を組み立てる**この入口だけ**
である。run・探索の実験・最終検証を組み立てる入口（`app.composition` の他の関数）は品質確認の
許可を受け取らない（ADR-0036 第5項の 1）。本モジュールは `backtest`・`strategy` を import
しない（戦略を動かす経路が構造上無い。ADR-0036 第5項の 2。テストで検査する）。

手順（fail-closed。ADR-0036 第7項）:

1. 承認済みの許可の定義（`configs/quality_checks/`）を読み、関所（`HoldoutGate`）を作る。
2. 関所が許可の確認と閲覧の記録の開始の行を行い、読み取りの許可の値を返す。返らなければ
   道具を呼ばない（読み取りを始めない）。
3. 道具（`marketdata` の受入れ・品質確認の処理）に読み取りの許可の値を渡して呼ぶ。
4. 道具の終了（完了・途中で停止）を関所に渡し、終了の行を書く。書けなければ、道具の結果を
   返さずに失敗として終わる（PR #80 の仮置き 13）。

識別子（許可・閲覧・行）は乱数（128 ビット）で作り、時刻は実時計から取る（domain と
application は乱数と実時計を読まない。D01 §2.2 規則2）。
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from odyssey_fx.app.config.quality_checks import (
    QUALITY_CHECKS_DIRECTORY,
    load_quality_check_definitions,
)
from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.application.holdout_gate import HoldoutGate
from odyssey_fx.evaluation.domain.quality_permit import QualityCheckRequest, QualityReadRefused
from odyssey_fx.marketdata.adapters.quality_access_log_store import QualityAccessLogFile
from odyssey_fx.marketdata.domain.quality_access import QualityAccessResult, QualityReadPermit

__all__ = ["QualityCheckRefused", "run_quality_check"]


class QualityCheckRefused(Exception):
    """品質確認の読み取りを始めなかった、または終了の行を書けず結果を出さなかった。"""

    def __init__(self, refusal: QualityReadRefused) -> None:
        super().__init__(f"{refusal.kind.value}: {refusal.detail}")
        self.refusal = refusal


def _now() -> UtcTime:
    return UtcTime(datetime.now(UTC))


def _new_id() -> str:
    return secrets.token_hex(16)


def run_quality_check[T](
    *,
    repository_root: Path,
    request: QualityCheckRequest,
    tool: Callable[[QualityReadPermit], T],
    now: Callable[[], UtcTime] = _now,
    new_id: Callable[[], str] = _new_id,
) -> T:
    """関所の品質確認の許可の下で道具を 1 回動かし、その結果を返す（ADR-0036 第7項）。

    `repository_root` はリポジトリの根（許可の定義 `configs/quality_checks/` と記録
    `research/quality_access_log.jsonl` の置き場）。許可が出ない・記録に失敗したときは
    `QualityCheckRefused` を送出し、道具を呼ばない。道具が例外で止まったときは「途中で停止」の
    終了の行を書いてから例外をそのまま送出する（終了の行も書けなければ `QualityCheckRefused`）。
    """
    root = Path(repository_root)
    gate = HoldoutGate(
        load_quality_check_definitions(root / QUALITY_CHECKS_DIRECTORY),
        QualityAccessLogFile(root),
    )
    permit = gate.begin_quality_read(
        request,
        permit_id=new_id(),
        access_id=new_id(),
        row_id=new_id(),
        accessed_at=now(),
    )
    if isinstance(permit, QualityReadRefused):
        raise QualityCheckRefused(permit)
    try:
        value = tool(permit)
    except BaseException:
        stopped = gate.finish_quality_read(
            permit, result=QualityAccessResult.STOPPED, row_id=new_id(), finished_at=now()
        )
        if isinstance(stopped, QualityReadRefused):
            raise QualityCheckRefused(stopped) from None
        raise
    finished = gate.finish_quality_read(
        permit, result=QualityAccessResult.COMPLETED, row_id=new_id(), finished_at=now()
    )
    if isinstance(finished, QualityReadRefused):
        raise QualityCheckRefused(finished)
    return value
