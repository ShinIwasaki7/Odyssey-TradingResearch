"""止まる理由の識別子・止める範囲・回復の型・関所 2 の問い（D25 §4.6・§4.6.1・§4.7）。

理由ごとに 1 行を持つ表 `REASONS` が正本で、実行器の本体（`runner`）はこの表から問いの issue の
本文を作り、答えの番号を「答えの後にすること」（`Action`）に直す。

- **範囲**（D25-8 (a)。決定 179）: 全体（新しい起動をすべて止める）か、カード（そのカードの
  一巡だけ）。
  `LEDGER_CHECK_FAILED` はカードと全体の両方に足す（D25 §4.6 の注記）。
- **回復の型**（D25-16 (a)。決定 188、仮置き 32）: 再試行 1 回の後に停止・待つ・棄却・停止。
- **問い**（D25 §4.7 の表）: 【何を決めるか】・選択肢（推奨を先頭）・答えの後にすること。
- **自動で解ける理由**（D25 §4.7 の「自動」）: 答えが無くても毎回の「1 歩」で確かめを当てる。

成績の数値・判定の値は問いに書かない（背景はカードの識別子・段・版・時刻・理由の識別子だけ）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

__all__ = [
    "BOUNDARY_QUESTION",
    "Action",
    "Option",
    "Reason",
    "REASONS",
    "Recovery",
    "Scope",
    "options_for",
    "reason",
    "shown_identifier",
]


class Scope(Enum):
    """止める範囲（D25 §4.6 の「範囲」の列）。"""

    GLOBAL = "全体"
    CARD = "カード"
    BOTH = "カードと全体"


class Recovery(Enum):
    """回復の型（D25 §4.6.1）。"""

    STOP = "停止"
    RETRY_ONCE = "再試行 1 回 → 停止"
    WAIT = "待つ"
    REJECT = "棄却"


class Action(Enum):
    """答えの後に実行器がすること（D25 §4.7 の表の右端の列）。"""

    KEEP = "止めたまま"
    RESUME = "その理由を外す（G0 へ）"
    RESET_FAILURES = "失敗の連続の数を 0 に戻して外す"
    RESET_REJECTS = "棄却の連続の数を 0 に戻して外す"
    RESUME_RUN = "一時停止した run を再開する（T11）"
    END_RUN = "一時停止した run を終える（T12）"
    END_CARD_AND_RESUME = "当たったカードを終えて外す（T41 と G0）"
    RECHECK_DISK = "空きを測り直す"
    IDEATE_ONCE = "発想役を 1 回だけ起動する"
    OTHER_CHANGE = "範囲を変えずに別の変更を選ぶ（T40）"
    END_CARD = "このカードを終える（T41）"
    RERUN_SAME_VERSION = "同じ版を新しい実行番号で走らせ直す（T39 → C3）"
    REDO_STAGE = "止まった段を最初からやり直す（T39）"
    LEDGER_REDO = "台帳の確かめからやり直す（T39 → C6）"
    REWRITE_REPORT = "報告をもう一度書き直させる（T39 → C8）"
    REDO_REVIEW = "審査をやり直す（T39 → C10）"
    RECHECK = "確かめをやり直す（T39）"
    FINAL_RECOVERED = "回復した後に続ける"
    FINAL_RESTART = "状態 A なので最終検証を起動し直す"


@dataclass(frozen=True, slots=True)
class Option:
    label: str
    impact: str
    action: Action


@dataclass(frozen=True, slots=True)
class Reason:
    """理由 1 つの行。`options` の先頭が推奨。`auto` は答えが無くても確かめを当てる理由。"""

    identifier: str
    scope: Scope
    recovery: Recovery
    question: str
    options: tuple[Option, ...]
    auto: bool = False


#: 作業役の境目の破れで全体を止めたとき（`TOOL_FAILURE_GLOBAL`。人間の決定 245、後追いの PR の
#: 仮置き H1）の問いの文。選択肢は `TOOL_FAILURE_GLOBAL` の行のまま。
BOUNDARY_QUESTION: Final = (
    "作業役の後に、読むだけのはずの束の写しが変わっていた・増えていた・減っていた"
    "（作業役の書き込みの絞りが効いていない形跡。作業役の出力は使っていない）。"
    "止めたまま原因を調べるか"
)

_KEEP_INVESTIGATE: Final = Option(
    "止めたまま（推奨）", "司令塔・実装役が原因を調べるまで、このまま止まる", Action.KEEP
)
_END_CARD: Final = Option(
    "このカードを終える", "カードを done/ に移し、次のカードへ進む", Action.END_CARD
)
_OTHER_CHANGE_FIRST: Final = Option(
    "範囲を変えずに別の変更を選ぶ（推奨）",
    "分析役を前の提案と理由の識別子つきでもう一度起動する（版は増えない）",
    Action.OTHER_CHANGE,
)

_ROWS: Final = (
    Reason(
        "SAME_FAILURE_TWICE",
        Scope.GLOBAL,
        Recovery.STOP,
        "同じ種類の失敗が続いた。止めたまま原因を調べるか",
        (
            _KEEP_INVESTIGATE,
            Option(
                "続ける", "失敗の連続の数を 0 に戻して新しい起動を再開する", Action.RESET_FAILURES
            ),
        ),
    ),
    Reason(
        "RUN_OVER_ESTIMATE",
        Scope.CARD,
        Recovery.STOP,
        "見積りの 2 倍を超えて一時停止した run を再開するか",
        (
            Option(
                "再開する（推奨）",
                "同じ実行として続け、上限を「止めた時点までの時間＋見積り 1 倍」に更新する",
                Action.RESUME_RUN,
            ),
            Option(
                "終える（このカードを終える）",
                "run を終わらせ、台帳は開始の行だけが残る",
                Action.END_RUN,
            ),
        ),
    ),
    Reason(
        "COST_RISK",
        Scope.GLOBAL,
        Recovery.STOP,
        "費用が発生しうる段に来た。止めたままにするか",
        (
            Option("止めたまま（推奨）", "設計役に差し戻す", Action.KEEP),
            Option(
                "そのカードを終えて続ける",
                "当たったカードを終え、新しい起動を再開する",
                Action.END_CARD_AND_RESUME,
            ),
        ),
    ),
    Reason(
        "SEALED_NEEDED",
        Scope.GLOBAL,
        Recovery.STOP,
        "封印・手元の旧データに触れる段に来た。止めたままにするか",
        (
            Option("止めたまま（推奨）", "設計役に差し戻す", Action.KEEP),
            Option(
                "そのカードを終えて続ける",
                "当たったカードを終え、新しい起動を再開する",
                Action.END_CARD_AND_RESUME,
            ),
        ),
    ),
    Reason(
        "DISK_LOW",
        Scope.GLOBAL,
        Recovery.STOP,
        "自動で消してもディスクの空きが足りない",
        (
            Option(
                "止めたまま（推奨）", "人間が消す対象を決める（空けば自動で再開する）", Action.KEEP
            ),
            Option("続ける（空けた後）", "空きを測り直し、足りれば再開する", Action.RECHECK_DISK),
        ),
        auto=True,
    ),
    Reason(
        "USAGE_LIMIT",
        Scope.GLOBAL,
        Recovery.STOP,
        "利用上限で作業役が動けない",
        (
            Option(
                "回復を待って続ける（推奨）",
                "新しい起動を再開する（また上限なら新しく止まる）",
                Action.RESUME,
            ),
            Option("止めたまま", "このまま止まる", Action.KEEP),
        ),
    ),
    Reason(
        "REJECT_THREE_IN_A_ROW",
        Scope.GLOBAL,
        Recovery.STOP,
        "カードの棄却が 3 枚続いた",
        (
            Option("止めたまま（推奨）", "カードの書き方か検査を人間・設計役が見る", Action.KEEP),
            Option("続ける", "棄却の連続の数を 0 に戻して再開する", Action.RESET_REJECTS),
        ),
    ),
    Reason(
        "QUEUE_EMPTY",
        Scope.GLOBAL,
        Recovery.STOP,
        "待ち行列が空で、発想役もカードを書けなかった",
        (
            Option(
                "人間がカードを置くまで待つ（推奨）",
                "待ち行列にカードが入れば自動で再開する",
                Action.KEEP,
            ),
            Option("発想役をもう一度起動する", "発想役を 1 回だけ起動する", Action.IDEATE_ONCE),
        ),
        auto=True,
    ),
    Reason(
        "HUMAN_PAUSE",
        Scope.GLOBAL,
        Recovery.STOP,
        "人間が止めた",
        (
            Option("止めたまま（推奨）", "このまま止まる", Action.KEEP),
            Option("続ける", "新しい起動を再開する", Action.RESUME),
        ),
    ),
    Reason(
        "TOOL_FAILURE_GLOBAL",
        Scope.GLOBAL,
        Recovery.STOP,
        "実行器の前提の道具がそろっていない（例: 実行の包みが D26 の PR-B の前）",
        (
            _KEEP_INVESTIGATE,
            Option("続ける", "前提を確かめ直し、そろっていれば再開する", Action.RESUME),
        ),
        auto=True,
    ),
    Reason(
        "CHANGE_OUT_OF_RULES",
        Scope.CARD,
        Recovery.STOP,
        "改善の変更が承認済みの規則で決められない",
        (_OTHER_CHANGE_FIRST, _END_CARD),
    ),
    Reason(
        "VALUE_MISSING",
        Scope.CARD,
        Recovery.STOP,
        "変更の具体の値が分析メモに無い",
        (_OTHER_CHANGE_FIRST, _END_CARD),
    ),
    Reason(
        "CHANGE_PREREG_REJECTED",
        Scope.CARD,
        Recovery.STOP,
        "改善後の事前登録が読込・コンパイル・照合を通らない",
        (_OTHER_CHANGE_FIRST, _END_CARD),
    ),
    Reason(
        "CONVERSION_STALE",
        Scope.CARD,
        Recovery.STOP,
        "換算の上限で run が止まった（止まった実行も試行に数える）",
        (
            Option("このカードを終える（推奨）", "カードを done/ に移す", Action.END_CARD),
            Option(
                "範囲を変えずに別の変更を選ぶ",
                "分析役をもう一度起動する（版 3 では選べない）",
                Action.OTHER_CHANGE,
            ),
        ),
    ),
    Reason(
        "RUN_FAILED",
        Scope.CARD,
        Recovery.STOP,
        "run が失敗した",
        (
            Option(
                "同じ版を新しい実行番号で走らせ直す（推奨）",
                "包みのファイルを退避し、起動待ちに戻す",
                Action.RERUN_SAME_VERSION,
            ),
            _END_CARD,
        ),
    ),
    Reason(
        "TOOL_FAILURE",
        Scope.CARD,
        Recovery.RETRY_ONCE,
        "道具が失敗した",
        (
            Option("止まった段を最初からやり直す（推奨）", "止まる前の段に戻す", Action.REDO_STAGE),
            _END_CARD,
        ),
    ),
    Reason(
        "AMBIGUOUS_RULE",
        Scope.CARD,
        Recovery.STOP,
        "承認済みの規則の当て方が一意に決まらない",
        (
            Option("止めたまま（推奨）", "設計役に差し戻す", Action.KEEP),
            Option(
                "範囲を変えずに別の変更を選ぶ",
                "分析・改善の段で止まったときだけ選べる（版は増えない）",
                Action.OTHER_CHANGE,
            ),
            _END_CARD,
        ),
    ),
    Reason(
        "WORKER_FAILED",
        Scope.CARD,
        Recovery.RETRY_ONCE,
        "作業役が決まった出力を書かずに終わった（2 回目）",
        (
            Option(
                "止まった段を最初からやり直す（推奨）",
                "作業役をもう一度起動する",
                Action.REDO_STAGE,
            ),
            _END_CARD,
        ),
    ),
    Reason(
        "CI_FAILED",
        Scope.CARD,
        Recovery.RETRY_ONCE,
        "定型 PR が CI に通らない",
        (
            Option(
                "止めたまま（推奨）",
                "司令塔・実装役が原因を調べる（PR が直されて merge されれば自動で続ける）",
                Action.KEEP,
            ),
            Option("このカードを終える", "その PR を閉じ、カードを done/ に移す", Action.END_CARD),
        ),
        auto=True,
    ),
    Reason(
        "LEDGER_CHECK_FAILED",
        Scope.BOTH,
        Recovery.STOP,
        "コミットの前の台帳の確かめか、実行記録の書式の確かめに通らない",
        (
            _KEEP_INVESTIGATE,
            Option(
                "確かめからやり直す",
                "台帳の確かめ → コミット → 実行記録の PR をやり直す",
                Action.LEDGER_REDO,
            ),
            Option(
                "このカードを終える",
                "カードを終える（台帳の行は消さない。全体の理由は確かめが通るまで残る）",
                Action.END_CARD,
            ),
        ),
        auto=True,
    ),
    Reason(
        "REPRO_MISMATCH",
        Scope.CARD,
        Recovery.STOP,
        "元の実験の再現が一致しない",
        (_KEEP_INVESTIGATE, _END_CARD),
    ),
    Reason(
        "GATE_FAILED",
        Scope.CARD,
        Recovery.REJECT,
        "一巡の確かめ（関門の機械の項目）に通らない",
        (
            Option(
                "このカードを終える（記録は残す。推奨）", "カードを done/ に移す", Action.END_CARD
            ),
            Option("報告をもう一度書き直させる", "報告の段に戻す", Action.REWRITE_REPORT),
        ),
    ),
    Reason(
        "VERIFIER_FAILED",
        Scope.CARD,
        Recovery.REJECT,
        "確かめ役の 2 回目の不合格",
        (
            Option(
                "このカードを終える（記録は残す。推奨）", "カードを done/ に移す", Action.END_CARD
            ),
            Option("報告をもう一度書き直させる", "報告の段に戻す", Action.REWRITE_REPORT),
        ),
    ),
    Reason(
        "REVIEW_FAILED",
        Scope.CARD,
        Recovery.RETRY_ONCE,
        "審査を終えられない（審査の道具の読込の誤り・途中で止まった）",
        (
            Option("止めたまま（推奨）", "道具・入力を直すのを待つ", Action.KEEP),
            Option("審査をやり直す", "空きで審査を起動し直す", Action.REDO_REVIEW),
            Option("このカードを終える（候補を進めない）", "カードを終える", Action.END_CARD),
        ),
    ),
    Reason(
        "REVIEW_NEEDS_DECLARATION",
        Scope.CARD,
        Recovery.WAIT,
        "審査の道具が申告ファイルを求める（申告の撤去の PR 4b の前）",
        (
            Option(
                "止めたまま（推奨）", "申告の撤去が main に入れば自動で審査を起動する", Action.KEEP
            ),
            Option("審査をやり直す", "空きで審査を起動し直す", Action.REDO_REVIEW),
            Option("このカードを終える（候補を進めない）", "カードを終える", Action.END_CARD),
        ),
        auto=True,
    ),
    Reason(
        "REVIEW_DEFERRED",
        Scope.CARD,
        Recovery.STOP,
        "審査が保留になった",
        (
            Option("止めたまま（推奨）", "原因を直すのを待つ", Action.KEEP),
            Option("審査をやり直す", "同じ規則の版で審査を起動し直す", Action.REDO_REVIEW),
            Option("このカードを終える（候補を進めない）", "カードを終える", Action.END_CARD),
        ),
    ),
    Reason(
        "FIXATION_TOOL_MISSING",
        Scope.CARD,
        Recovery.WAIT,
        "審査が「進める」だが、固定の記録の道具がまだ無い",
        (
            Option(
                "固定の道具の実装を待つ（推奨）",
                "道具が main に入れば自動で固定の手前に戻す",
                Action.KEEP,
            ),
            Option("このカードを終える（候補を進めない）", "カードを終える", Action.END_CARD),
        ),
        auto=True,
    ),
    Reason(
        "FIXATION_CHECK_FAILED",
        Scope.CARD,
        Recovery.STOP,
        "候補の固定の前の確かめで止まった",
        (
            Option("止めたまま（推奨）", "人間が状態を確かめる", Action.KEEP),
            Option("確かめをやり直す", "固定の前の確かめからやり直す", Action.RECHECK),
            _END_CARD,
        ),
    ),
    Reason(
        "MAIN_CHECKOUT_NOT_READY",
        Scope.CARD,
        Recovery.WAIT,
        "最終検証の手前で、主の作業木が汚れている・origin と食い違う（1 日待った）",
        (
            Option("止めたまま（推奨）", "人間が主の作業木を確かめる", Action.KEEP),
            Option("確かめをやり直す", "主の作業木の確かめからやり直す", Action.RECHECK),
            Option("このカードを終える", "最終検証の前だけ選べる。カードを終える", Action.END_CARD),
        ),
    ),
    Reason(
        "FINAL_VALIDATION_FAILED",
        Scope.CARD,
        Recovery.STOP,
        "最終検証が終わらなかった",
        (
            Option(
                "止めたまま（推奨）",
                "人間が状態を確かめ、ロックや消費の後なら回復する",
                Action.KEEP,
            ),
            Option(
                "回復した後に続ける",
                "最終検証の台帳にこの実行の行があれば、その行の記録の PR を作る",
                Action.FINAL_RECOVERED,
            ),
            Option(
                "状態 A（消費の行が origin に無い）なので最終検証を起動し直す",
                "消費の行が無いことを確かめ直してから起動する",
                Action.FINAL_RESTART,
            ),
        ),
    ),
)

#: 理由の識別子 → 行。
REASONS: Final = {row.identifier: row for row in _ROWS}


def reason(identifier: str) -> Reason:
    """理由の行（表に無ければ `KeyError`）。"""
    return REASONS[identifier]


def options_for(identifier: str, *, version: int | None = None) -> tuple[Option, ...]:
    """問いに出す選択肢（推奨を先頭）。

    `CONVERSION_STALE` は版 3 なら「別の変更を選ぶ」を出さない（再評価は最大 2 回。決定 103）。
    """
    options = reason(identifier).options
    if identifier == "CONVERSION_STALE" and version is not None and version >= 3:
        return tuple(option for option in options if option.action is not Action.OTHER_CHANGE)
    return options


def shown_identifier(identifier: str) -> str:
    """issue の題・本文に出す理由の識別子（全体の道具の失敗も D25 §4.6.1 の `TOOL_FAILURE`）。"""
    return "TOOL_FAILURE" if identifier == "TOOL_FAILURE_GLOBAL" else identifier
