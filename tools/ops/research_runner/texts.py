"""実行器が書く文の型: 定型 PR の枝・題・本文（D25 §6.2）、関所 1 の PR（§6.5）、関所 2 の問いの
issue と答えの読み方（§4.7）、週の要約（§7）。

定型 PR の本文は CI の検査 7（`tools.ops.runner_ci.routine.check_body`）が読む型どおりに書く:
項目は `種類`・`カード`・`版`・`実験`・`時刻`・`件数`・定型の文・`SHA-256:` と、足した・変えた
ファイルごとの `- <パス>: <SHA-256>` だけ。台帳の行（判定の値を含む）を本文に写さない（決定 110）。

どの文にも成績の数値・判定の値を書かない（件数・時間・合否・識別子だけ）。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from tools.ops.research_runner.reasons import Option, shown_identifier
from tools.ops.runner_ci.routine import FIXED_SENTENCE, Kind

__all__ = [
    "Answer",
    "PullText",
    "STOP_LABEL",
    "WEEKLY_LABEL",
    "WEEKLY_TITLE",
    "WeeklyCounts",
    "cards_pull",
    "exec_pull",
    "final_pull",
    "fix_pull",
    "parse_answer",
    "prereg_pull",
    "question_body",
    "question_title",
    "report_pull",
    "weekly_body",
]

STOP_LABEL: Final = "runner-stop"
WEEKLY_LABEL: Final = "runner-weekly"
WEEKLY_TITLE: Final = "[runner] 週の要約"


@dataclass(frozen=True, slots=True)
class PullText:
    branch: str
    title: str
    body: str


def _hash_lines(files: Mapping[str, bytes]) -> list[str]:
    return [f"- {path}: {hashlib.sha256(data).hexdigest()}" for path, data in sorted(files.items())]


def _routine_body(
    *,
    kind: Kind,
    card: str,
    version: int | None,
    experiment: str | None,
    at: str,
    counts: Sequence[str],
    files: Mapping[str, bytes],
) -> str:
    lines = [f"種類: {kind.value}", f"カード: {card}"]
    if version is not None:
        lines.append(f"版: {version}")
    if experiment is not None:
        lines.append(f"実験: {experiment}")
    lines += [f"時刻: {at}", f"件数: {'、'.join(counts)}", FIXED_SENTENCE, "SHA-256:"]
    lines += _hash_lines(files)
    return "\n".join(lines) + "\n"


def prereg_pull(
    *, card: str, version: int, experiment: str, at: str, trials: int, files: Mapping[str, bytes]
) -> PullText:
    """事前登録の定型 PR（`files` は足した・変えたファイル。移動の先のカードを含む）。"""
    return PullText(
        branch=f"runner/prereg-{card}-v{version}",
        title=f"[runner] {Kind.PREREG.value} {card} v{version}",
        body=_routine_body(
            kind=Kind.PREREG,
            card=card,
            version=version,
            experiment=experiment,
            at=at,
            counts=[f"試行 {trials}"],
            files=files,
        ),
    )


def cards_pull(
    *, stamp: str, cards: Sequence[str], at: str, files: Mapping[str, bytes]
) -> PullText:
    """発想役のカードだけの事前登録の定型 PR（枝 `runner/cards-<時刻>`）。"""
    return PullText(
        branch=f"runner/cards-{stamp}",
        title=f"[runner] {Kind.PREREG.value} cards-{stamp}",
        body=_routine_body(
            kind=Kind.PREREG,
            card=", ".join(sorted(cards)),
            version=None,
            experiment=None,
            at=at,
            counts=[f"カード {len(cards)}"],
            files=files,
        ),
    )


def exec_pull(
    *, card: str, version: int, experiment: str, at: str, lines: int, files: Mapping[str, bytes]
) -> PullText:
    """実行記録の定型 PR（枝 `runner/exec`。台帳の追記と数値なしの実行記録）。"""
    return PullText(
        branch="runner/exec",
        title=f"[runner] {Kind.EXEC.value} {card} v{version}",
        body=_routine_body(
            kind=Kind.EXEC,
            card=card,
            version=version,
            experiment=experiment,
            at=at,
            counts=[f"行 {lines}"],
            files=files,
        ),
    )


def final_pull(
    *,
    card: str,
    version: int,
    experiment: str,
    execution: int,
    at: str,
    lines: int,
    files: Mapping[str, bytes],
) -> PullText:
    """最終検証の台帳の行の定型 PR（枝 `runner/final-<実験>-<実行番号>`。仮置き 22）。"""
    return PullText(
        branch=f"runner/final-{experiment}-{execution}",
        title=f"[runner] {Kind.EXEC.value} {card} v{version}",
        body=_routine_body(
            kind=Kind.EXEC,
            card=card,
            version=version,
            experiment=experiment,
            at=at,
            counts=[f"行 {lines}"],
            files=files,
        ),
    )


def report_pull(
    *, card: str, version: int, experiment: str, at: str, files: Mapping[str, bytes]
) -> PullText:
    """一巡の報告の定型 PR（棄却・終えたカードの移動と終わり方も同じ種類。

    版 0 は走らせずに終えたカード）。
    """
    return PullText(
        branch=f"runner/report-{card}",
        title=f"[runner] {Kind.REPORT.value} {card} v{version}",
        body=_routine_body(
            kind=Kind.REPORT,
            card=card,
            version=version,
            experiment=experiment,
            at=at,
            counts=["カード 1"],
            files=files,
        ),
    )


def fix_pull(
    *, card: str, experiment: str, execution: int, at: str, check_lines: Sequence[str]
) -> PullText:
    """関所 1 の固定の記録の PR（定型でない。自動 merge を付けない。Codex を求めない。D25 §6.5）。

    `check_lines` は固定の前の確かめの出力（日数・合否・理由の識別子だけ。D09 §9.11.10）。
    """
    body = [
        "【何を決めるか】この候補の最終検証を始めてよいか。merge で固定時点が決まり、"
        "封印の期間を 1 つ使う（元に戻せない）。",
        f"【背景】カード {card}・実験 {experiment}・実行 {execution}・{at}。"
        "審査が「進める」になり、"
        "固定の前の機械の確かめ（8 項目）を通った。",
        "1. merge する（推奨） — 最終検証が始まる（関所 1。決定 143）",
        "2. merge せずに閉じる — 候補を進めずに、このカードの一巡を終える",
        "返信は不要（merge か閉じるかで答える）。成績の数値・判定の値は書いていない。",
        "",
        "固定の前の確かめの出力:",
        "```",
        *check_lines,
        "```",
        "",
        "本 PR は実行器が作った関所 1 の PR（D25 §6.5）。Codex のレビューは求めない（決定 187）。",
    ]
    return PullText(
        branch=f"candidate-fix/{experiment}-{execution}",
        title=f"[関所 1] {experiment} 実行 {execution} の候補の固定（merge = 最終検証の go）",
        body="\n".join(body) + "\n",
    )


# --- 関所 2 の問い（D25 §4.7） ----------------------------------------------------------


def question_title(reason: str, card: str | None) -> str:
    return f"[runner] 止まった: {shown_identifier(reason)} {card if card is not None else '全体'}"


def question_body(
    *,
    question: str,
    reason: str,
    card: str | None,
    stage: str,
    version: int | None,
    at: str,
    options: Sequence[Option],
) -> str:
    """無人運転の取り決め §2 の様式の問い（背景はカード・段・版・時刻・理由の識別子だけ）。"""
    background = [
        f"カード {card}" if card is not None else "全体",
        stage,
        *([f"版 {version}"] if version else []),
        at,
        shown_identifier(reason),
    ]
    lines = [f"【何を決めるか】{question}", f"【背景】{'・'.join(background)}"]
    lines += [
        f"{index}. {option.label} — {option.impact}" for index, option in enumerate(options, 1)
    ]
    lines.append(
        "返信は番号だけで可。「他:」で始めれば自由記述を受け付ける（実行器は自由記述を読めないので、"
        "止まったまま司令塔・設計役の作業になる）。"
    )
    return "\n".join(lines) + "\n"


@dataclass(frozen=True, slots=True)
class Answer:
    comment: str
    number: int
    at: str


_NUMBER_ONLY: Final = re.compile(r"[1-9]")


def parse_answer(body: str, *, choices: int) -> int | None:
    """本文が番号 1 つだけ（前後の空白は許す）なら番号、そうでなければ `None`。

    「他:」で始まる自由記述も `None`（答えとして扱わない）。
    """
    text = body.strip()
    if _NUMBER_ONLY.fullmatch(text) is None:
        return None
    number = int(text)
    return number if 1 <= number <= choices else None


# --- 週の要約（D25 §7） ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WeeklyCounts:
    taken_human: int = 0
    taken_ai: int = 0
    finished: int = 0
    rejected: int = 0
    queue_human: int = 0
    queue_ai: int = 0
    runs_done: int = 0
    runs_failed: int = 0
    runs_paused: int = 0
    run_hours: float = 0.0
    gate_pass: int = 0
    gate_fail: int = 0
    entrance_pass: int = 0
    entrance_fail: int = 0
    review_advance: int = 0
    review_not: int = 0
    review_deferred: int = 0
    stops_new: Mapping[str, int] | None = None
    stops_waiting: int = 0
    ideator_runs: int = 0
    cards_written: int = 0
    cards_discarded: int = 0
    disk_free_gb: float = 0.0
    deleted_files: int = 0
    deleted_gb: float = 0.0
    four_weeks: bool = False
    needs_declaration: int = 0


def weekly_body(*, start: str, end: str, counts: WeeklyCounts) -> str:
    """週の要約のコメント（型どおり。件数・時間・合否だけ）。"""
    stops = counts.stops_new or {}
    stop_items = "、".join(f"{key} {value}" for key, value in sorted(stops.items())) or "なし"
    lines = [
        f"週: {start}〜{end}",
        f"カード: 取り出した {counts.taken_human + counts.taken_ai}（人間 {counts.taken_human}・"
        f"AI {counts.taken_ai}）、終えた {counts.finished}、棄却 {counts.rejected}、"
        f"待ち行列 人間 {counts.queue_human}・AI {counts.queue_ai}",
        f"実験: 完了 {counts.runs_done}、失敗 {counts.runs_failed}、一時停止 {counts.runs_paused}、"
        f"計算時間 合計 {counts.run_hours:.1f} 時間",
        f"関門（段 8）: 合格 {counts.gate_pass}、不合格 {counts.gate_fail}",
        f"審査の入口: 通った {counts.entrance_pass}、通らない {counts.entrance_fail}／審査: 進める "
        f"{counts.review_advance}、進めない {counts.review_not}、保留 {counts.review_deferred}",
        f"止まり: 新しく {sum(stops.values())}（{stop_items}）、答え待ち {counts.stops_waiting}",
        f"発想役: 起動 {counts.ideator_runs}、書いたカード {counts.cards_written}、"
        f"捨てたカード {counts.cards_discarded}",
        f"ディスク: 空き {counts.disk_free_gb:.1f} GB、自動で消した {counts.deleted_files}・"
        f"{counts.deleted_gb:.1f} GB",
    ]
    if counts.needs_declaration:
        lines.append(f"申告の撤去を待つ審査: {counts.needs_declaration}")
    if counts.four_weeks:
        lines.append("前の関所の答えから 4 週が経った")
    return "\n".join(lines) + "\n"
