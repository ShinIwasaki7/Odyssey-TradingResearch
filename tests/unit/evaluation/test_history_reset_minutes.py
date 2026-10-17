"""準備の期間の合計の数え方（D07 §28.3、D12-120。人間の決定の原文は D12 §0.1.30）。"""

from __future__ import annotations

from datetime import UTC, datetime

from odyssey_fx.evaluation.application.evaluate_run import union_minutes


def _t(text: str) -> datetime:
    return datetime.fromisoformat(f"2026-01-23T{text}:00").replace(tzinfo=UTC)


def test_spans_are_clipped_to_the_run_and_overlaps_are_counted_once() -> None:
    """run 区間 10:00〜12:00 との重なりを取り、重なる期間は和集合で 1 回だけ数える。"""
    spans = [
        (_t("09:00"), _t("10:30")),  # run の前から: 10:00〜10:30 の 30 分
        (_t("10:15"), _t("10:45")),  # 前の行と重なる: 10:30〜10:45 の 15 分だけ足す
        (_t("11:50"), _t("13:00")),  # run の後へ: 11:50〜12:00 の 10 分
        (_t("12:30"), _t("13:00")),  # run の外: 数えない
    ]
    assert union_minutes(spans, _t("10:00"), _t("12:00")) == 55


def test_whole_minutes_are_rounded_down() -> None:
    """整数分への丸めは切り捨て（59 秒は 0 分、1 分 59 秒は 1 分）。"""
    lo = _t("10:00")
    assert union_minutes([(lo, lo.replace(second=59))], lo, _t("12:00")) == 0
    assert union_minutes([(lo, lo.replace(minute=1, second=59))], lo, _t("12:00")) == 1
