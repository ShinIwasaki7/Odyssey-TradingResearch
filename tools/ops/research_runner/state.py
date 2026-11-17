"""実行器の状態（全体とカードごと）と、その置き場 `runs/runner/state/`
（D25 §2.3・§2.4・§4.1・§4.3）。

- **カードの状態**（表 2-1 の行）: C0 待ち 〜 C14 終わり。`return_phase` は停止（C13）の前の状態、
  `reasons` は未解除の理由の集合（理由の識別子と issue の番号の組。D25 §4.3）。
- **全体の状態**（表 2-2 の行）: G0 稼働（理由の集合が空）・G1 全体の停止中（空でない）・
  G2 動いていない（前回の「1 歩」の後に再起動した。最初の「1 歩」で G0 か G1 へ戻し、各カードに
  e17 を当てる）。
- **置き場**（仮置き 23）: `runs/runner/state/global.json` と
  `runs/runner/state/cards/<カード>.json`。版管理の外。書くときは一時名に書いてから置き換える
  （`run_experiment.py` の `_write_json` と同じ）。

状態は成績の数値を持たない（識別子・時刻・件数・終了コード・合否だけ）。
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Final

__all__ = [
    "CardPhase",
    "CardState",
    "GlobalPhase",
    "GlobalState",
    "OpenReason",
    "SCHEMA_VERSION",
    "StateStore",
    "format_time",
    "parse_time",
    "write_json",
]

SCHEMA_VERSION: Final = 1


class CardPhase(Enum):
    """カードの一巡の状態（D25 §4.1 の表）。"""

    C0 = "C0 待ち"
    C1 = "C1 検査中"
    C2 = "C2 登録 PR 待ち"
    C3 = "C3 起動待ち"
    C4 = "C4 実行中"
    C5 = "C5 一時停止"
    C6 = "C6 記録 PR 待ち"
    C7 = "C7 分析中"
    C8 = "C8 報告中"
    C9 = "C9 確かめ中"
    C10 = "C10 入口・審査中"
    C11 = "C11 関所 1 待ち"
    C12 = "C12 最終検証中"
    C13 = "C13 停止中"
    C14 = "C14 終わり"


class GlobalPhase(Enum):
    """実行器の全体の状態（D25 §4.3 の表 2-2）。"""

    G0 = "G0 稼働"
    G1 = "G1 全体の停止中"
    G2 = "G2 動いていない"


def format_time(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


@dataclass(slots=True)
class OpenReason:
    """未解除の理由 1 つ（D25 §4.3「未解除の理由の集合」の要素）。

    `issue` は問いの issue の番号、`seen_comment` は読み終えた最後のコメントの識別子（後から書かれた
    番号に従うため。D25 §4.7「答えの読み方」）、`answer` は最後に受けた番号。
    """

    reason: str
    issue: int | None = None
    detail: str = ""
    opened_at: str = ""
    seen_comment: str | None = None
    answer: int | None = None
    #: 全体の理由に当たったカード（`COST_RISK` の「そのカードを終えて続ける」の相手）。
    card: str | None = None
    #: 自動の確かめで外さない（人間の答えでだけ外す）。作業役の境目の破れで止めた
    #: `TOOL_FAILURE_GLOBAL`（人間の決定 245。後追いの PR の仮置き H1）。
    hold: bool = False

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> OpenReason:
        return cls(**{item.name: data[item.name] for item in fields(cls) if item.name in data})


@dataclass(slots=True)
class CardState:
    """カード 1 枚の一巡の状態（表 2-1 の行と、表 3 の値のうち実行器の状態に置くもの）。

    入れ子の値は JSON のまま持つ（`run`・`procs`・`prs`・`executions` など）。
    """

    card_id: str
    mouth: str
    phase: str = CardPhase.C0.name
    taken_at: str = ""
    card_sha256: str = ""
    symbol: str = ""
    experiment: str = ""
    #: いまの版（事前登録の前は 0）。
    version: int = 0
    return_phase: str | None = None
    reasons: list[OpenReason] = field(default_factory=list)
    #: 再試行の数（理由の識別子か `CI_FAILED:<PR>` などの鍵 → 回数。D25 §4.6.1）。
    retries: dict[str, int] = field(default_factory=dict)
    #: いま待っている PR（`kind`・`number`・`branch`・`purpose`）。
    pr: dict[str, Any] | None = None
    #: 探索の run の試み（`version`・`attempt`・`version_dir`・`launched_at`・`paused_since`・
    #: `paused_seconds`・`limit_seconds`・`estimate_seconds`・`slots`・`mode`・`retry_after`）。
    run: dict[str, Any] | None = None
    #: 切り離して起動した処理（名前 → `attempt`・`started_at`・`slots`・`state_dir`・`pid`）。
    procs: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: 空きを待つ予約（`review`・`final`・`reproduce`）。
    reservations: list[str] = field(default_factory=list)
    #: 版 → `{"experiment_id": <記録票の識別子>, "execution": <実行番号>}`。
    executions: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: 版 → 事前登録の merge のコミット。
    prereg_commits: dict[str, str] = field(default_factory=dict)
    #: 版 → 試行の数（見積りの材料）。
    trial_counts: dict[str, int] = field(default_factory=dict)
    #: 元の実験の再現の結果（`MATCH`・`MISMATCH`。未だなら `None`）。
    repro: str | None = None
    #: 段 8 の結果（`gate`・`verifier` の合否と理由の識別子）。
    checks: dict[str, Any] = field(default_factory=dict)
    verifier_failures: int = 0
    #: 審査の結果（`ADVANCE`・`NOT_ADVANCE`・`DEFERRED`）。
    review: str | None = None
    #: 停止の間に届いた処理の結果（名前 → 終了コード。T39 で続けて当てる）。
    late: dict[str, int] = field(default_factory=dict)
    #: 一巡の報告の材料（分析役の報告の出力の置き場とハッシュ）。
    report: dict[str, Any] = field(default_factory=dict)
    #: 主の作業木の確かめを待ち始めた時刻（`MAIN_CHECKOUT_NOT_READY`。仮置き 32）。
    main_wait_since: str | None = None
    #: 固定の延期の後にやり直す時刻（項目 6。D15-15 (a)）。
    fix_retry_after: str | None = None
    #: 終わり方の理由の識別子（`done/<カード>.outcome.yaml` に書いたもの）。
    outcome: str | None = None
    #: 分析役の次の起動に足す材料（T40 の前の提案と止まった理由）。
    redo_note: str | None = None
    #: 「このカードを終える」（T41・T12）の途中: 終わり方の理由の識別子（プロセスの終わりを待つ）。
    ending: str | None = None
    #: 理由の集合が空になったときに戻る先（答えの指す状態。既定は `return_phase`）。
    resume_to: str | None = None
    #: コミットを作った後、push と PR の作成がまだ終わっていない PR（`commit`・`branch`・`title`・
    #: `body`・`auto`・`record`）。次の 1 歩は同じコミットを送り直す（作り直さない）。
    sending: dict[str, Any] | None = None
    #: このカードの実行記録の要求（`version`・`purpose`）。段の移りと同じ保存で残し、実行記録の
    #: PR が入る（または足すものが無い）まで消さない。全体の待ち（`exec_queue`）はこの写し
    #: （毎回の 1 歩で照らし合わせる。D25 §2.4 の照合）。
    exec_requests: list[dict[str, Any]] = field(default_factory=list)

    @property
    def phase_enum(self) -> CardPhase:
        return CardPhase[self.phase]

    def reason_ids(self) -> set[str]:
        return {item.reason for item in self.reasons}

    def find_reason(self, identifier: str) -> OpenReason | None:
        return next((item for item in self.reasons if item.reason == identifier), None)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CardState:
        values = {item.name: data[item.name] for item in fields(cls) if item.name in data}
        values["reasons"] = [OpenReason.from_json(item) for item in data.get("reasons", [])]
        return cls(**values)


@dataclass(slots=True)
class GlobalState:
    """実行器の全体の状態（表 2-2 の行）。"""

    schema_version: int = SCHEMA_VERSION
    #: 前回の「1 歩」の時点の起動の時刻（再起動を見分ける。G2）。
    boot_id: str = ""
    reasons: list[OpenReason] = field(default_factory=list)
    #: 失敗の種類 → 連続の数（D25 §4.6 の `SAME_FAILURE_TWICE`）。
    failure_streaks: dict[str, int] = field(default_factory=dict)
    #: 棄却の連続の数（3 で `REJECT_THREE_IN_A_ROW`）。
    reject_streak: int = 0
    #: 取り出した順のカードの識別子（状態のファイルを持つカード）。
    cards: list[str] = field(default_factory=list)
    #: 実行記録の定型 PR（枝 `runner/exec`。同時に 1 つ。`card`・`version`・`purpose`・
    #: `number`・`commit`・`paths`。閉じられた PR を同じコミットで送り直すために `commit` と
    #: `paths` も持つ）。
    exec_pr: dict[str, Any] | None = None
    #: 実行記録の PR を待つ要求（`card`・`version`・`purpose`）。正本はカードの `exec_requests`
    #: で、これはその並び（毎回の 1 歩の初めに照らし合わせる）。
    exec_queue: list[dict[str, Any]] = field(default_factory=list)
    #: 台帳のロックを、書いているはずの処理が居ないのに見つけた時刻（残ったロックの見張り）。
    ledger_lock_since: str | None = None
    #: 実行用の作業木でコミットする直前から、push と PR の作成が終わるまでの実行記録
    #: （`card`・`version`・`purpose`・`paths`・`parent`（コミットの前の先頭）・`commit`（コミット。
    #: 書く前に止まったなら `None`））。次の 1 歩は push から続ける。
    exec_sending: dict[str, Any] | None = None
    #: 発想役（`running`・`attempt`・`after_done`（done/ に移った後の起動の予約の数）・
    #: `queue_empty_used`（空のときの 1 回を使ったか）・`extra`（人間の答えの 1 回））。
    ideator: dict[str, Any] = field(default_factory=dict)
    #: 発想役のカードの定型 PR（`runner/cards-<時刻>`）。
    cards_pr: dict[str, Any] | None = None
    #: 発想役のカードの定型 PR のうち、コミットの後に送信がまだ終わっていないもの（カードの
    #: `sending` と同じ形）。
    cards_sending: dict[str, Any] | None = None
    last_human_answer_at: str | None = None
    four_week_notice_for: str | None = None
    weekly_issue: int | None = None
    weekly_last_week: str | None = None
    #: 週の要約の issue の、読み終えた最後のコメント（「止めて」の割り込み。仮置き）。
    weekly_seen_comment: str | None = None
    #: 最後の「1 歩」の時刻。
    last_step_at: str | None = None

    @property
    def phase(self) -> GlobalPhase:
        return GlobalPhase.G1 if self.reasons else GlobalPhase.G0

    def reason_ids(self) -> set[str]:
        return {item.reason for item in self.reasons}

    def find_reason(self, identifier: str) -> OpenReason | None:
        return next((item for item in self.reasons if item.reason == identifier), None)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> GlobalState:
        values = {item.name: data[item.name] for item in fields(cls) if item.name in data}
        values["reasons"] = [OpenReason.from_json(item) for item in data.get("reasons", [])]
        return cls(**values)


def write_json(path: Path, data: dict[str, Any]) -> None:
    """一時名に書いてから置き換える（停電の途中でも壊れたファイルを残さない）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


@dataclass(frozen=True, slots=True)
class StateStore:
    """`runs/runner/state/` の読み書き。"""

    root: Path

    @property
    def global_path(self) -> Path:
        return self.root / "global.json"

    def card_path(self, card_id: str) -> Path:
        return self.root / "cards" / f"{card_id}.json"

    def load_global(self) -> GlobalState | None:
        if not self.global_path.is_file():
            return None
        return GlobalState.from_json(json.loads(self.global_path.read_text(encoding="utf-8")))

    def save_global(self, state: GlobalState) -> None:
        write_json(self.global_path, state.to_json())

    def load_card(self, card_id: str) -> CardState:
        return CardState.from_json(json.loads(self.card_path(card_id).read_text(encoding="utf-8")))

    def save_card(self, card: CardState) -> None:
        write_json(self.card_path(card.card_id), card.to_json())

    def cards(self, state: GlobalState) -> Iterator[CardState]:
        for card_id in state.cards:
            yield self.load_card(card_id)
