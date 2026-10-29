"""封印の取得（一括取得・定期取得）の要求の一覧（計画）を Mac で作る（ADR-0037 第4項、D03 §16.5）。

窓（取得を許す期間の始まり・取得の開始時点・8 通貨）は版管理の設定 1 つ
``configs/acquisition/oanda_mt5_m1_sealed_window.yaml`` からだけ読む（``app.config.sealed_window``。
読込が取得の開始時点を merge のコミットの時刻からの計算と照合する）。窓の境目は、2024 年以降の
時刻の規則の表（``oanda_mt5_time_rules_sealed`` 版 1。承認済みの一覧と照合して読む）の**標準の組**で
API の時刻に直す（PR #109 の仮置き S2b-9）。作った計画は Windows の道具
（``mt5_api_probe.py sealedfetch``）が同じ窓の設定のファイルと照らしてから使う。データは読まない・
要求しない（AWS も使わない）。

- **一括取得**（``bulk``）: 窓 [allowed_start, acquisition_start)。1 要求 = 1 通貨 × API の時刻の
  1 か月（全期間取得と同じ。D12 付録 K-16）。最初の月は API の時刻で allowed_start を直した時刻から
  始め、許可の窓より前を要求しない（ADR-0037 第4項の 3）。窓の端の月の切れ端のうち、UTC で週末の
  休場（金曜 17:00〜日曜 17:00 ニューヨーク）に収まるものは要求しない（足が存在すべき分が無く、
  0 本の月になるため。PR #109 の仮置き S2b-14）。取得の開始時点の値では 34 か月 × 8 通貨
  = 272 要求。
- **定期取得**（``periodic``）: 初回（前の回の終わりを渡さない）は [取得の開始時点, 次の取引週の
  始まり)（D15-3b (a)「初回だけ 1 週間分」）。2 回目以降は [前の回の終わり, 実行の時点の月の始まり
  以前の最後の取引週の境目)（前の月の終わり以前の最後の取引週の境目。PR #109 の要決定 S2b-7 (a)）。
  1 要求 = 1 通貨 × 1 窓（月で割らない）。前の回の終わりから始めるので途切れも重なりも無い。
- 要求の上限 = 計画の数 ＋ 1 割の切り上げ（272 → 300、8 → 9。D15-18c (a)・PR #109 の
  仮置き S2b-10）。

出力は計画の JSON（要求の区間の時刻・件数・窓の設定と表の SHA-256）だけで、価格は扱わない。

実行の例::

    uv run python -m tools.survey.sealed_fetch_plan bulk --out sealed_plan.json
    uv run python -m tools.survey.sealed_fetch_plan periodic --first \\
        --run-at 2026-11-14T00:00:00Z --out sealed_plan.json
    uv run python -m tools.survey.sealed_fetch_plan periodic --after 2026-11-08T22:00:00Z \\
        --run-at 2026-12-05T00:00:00Z --out sealed_plan.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

from odyssey_fx.app.config.sealed_window import (
    SEALED_WINDOW_PATH,
    SealedWindowConfig,
    load_sealed_window,
)
from odyssey_fx.app.mt5 import SEALED_COUNTING_RULES, load_mt5_rules
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.access import (
    TRADING_WEEK_ZONE,
    is_trading_week_start,
    next_trading_week_start,
    trading_week_start,
)
from odyssey_fx.marketdata.domain.time_rule import TimeRuleTable, TimeRuleVariant
from tools.survey.mt5_api_probe import (
    SEALED_PAIRS,
    SEALED_PLAN_KIND,
    SEALED_STAGE_BULK,
    SEALED_STAGE_PERIODIC,
    SealedRequest,
    SealedWindowError,
    check_sealed_request,
    read_sealed_window,
    sealed_max_requests,
)

__all__ = [
    "SealedPlan",
    "bulk_requests",
    "build_plan",
    "periodic_window",
    "weekend_only",
]

_MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class SealedPlan:
    """計画（要求の一覧と記録）。"""

    stage: str
    window_start: datetime
    window_end: datetime
    requests: tuple[SealedRequest, ...]
    payload: dict[str, Any]


def _offset(table: TimeRuleTable, pair: str, api_day: datetime) -> int:
    row = table.row_for(pair, api_day.date())
    if row is None:
        raise SealedWindowError(
            f"時刻の規則の表 {table.table_id} 版 {table.version} は {pair} の {api_day.date()} を"
            " 覆わない。受入れられない期間なので計画を作らない（D03 §16.2.4）"
        )
    return row.offset_hours(TimeRuleVariant.STANDARD)


def to_api(table: TimeRuleTable, pair: str, utc: datetime) -> tuple[datetime, int]:
    """UTC の境目を標準の組で API の時刻に直す（PR #109 の仮置き S2b-9）。

    UTC の暦日で引いた行と、直した API の時刻の暦日で引いた行の変換が同じことを確かめる
    （検査 T4 と同じ考え方。境目は週末の休場の中にあるので一致するはず）。
    """
    offset = _offset(table, pair, utc)
    api = utc + timedelta(hours=offset)
    if _offset(table, pair, api) != offset:
        raise SealedWindowError(f"{pair} の境目 {utc.isoformat()} の変換が表の行の境目に掛かる")
    return api, offset


def from_api(
    table: TimeRuleTable, pair: str, api: datetime, *, exclusive_end: bool
) -> tuple[datetime, int]:
    """API の時刻を標準の組で UTC に直す（表は API の時刻の暦日で引く。終わりは 1 分前の暦日）。"""
    key = api - _MINUTE if exclusive_end else api
    offset = _offset(table, pair, datetime.fromtimestamp(int(key.timestamp()), UTC))
    return api - timedelta(hours=offset), offset


def weekend_only(utc_from: datetime, utc_to: datetime) -> bool:
    """[utc_from, utc_to) が 1 つの週末の休場（金曜 17:00〜日曜 17:00 ニューヨーク）に収まるか。"""
    week_open = next_trading_week_start(trading_week_start(UtcTime(utc_from))).value
    local_open = week_open.astimezone(TRADING_WEEK_ZONE)
    friday = datetime.combine(local_open.date() - timedelta(days=2), time(17, 0))
    friday_close = UtcTime.from_local(friday, TRADING_WEEK_ZONE).value
    return friday_close <= utc_from and utc_to <= week_open


def _month_start(year: int, month: int) -> datetime:
    return datetime(year, month, 1, tzinfo=UTC)


def _next_month(moment: datetime) -> datetime:
    year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
    return _month_start(year, month)


def _pairs(window: SealedWindowConfig) -> list[str]:
    """要求する通貨の順（USDJPY を先頭。全期間取得と同じ順。D03 §16.5.5 の 5）。"""
    names = {str(symbol) for symbol in window.symbols}
    return [pair for pair in SEALED_PAIRS if pair in names]


def bulk_requests(window: SealedWindowConfig, table: TimeRuleTable) -> list[SealedRequest]:
    """一括取得の要求（通貨の順、各通貨の中は古い月から）。"""
    if window.acquisition_start is None:
        raise SealedWindowError(
            "取得の開始時点が空。一括取得の窓の終わりが決まらない（ADR-0037 第4項の 3）"
        )
    out: list[SealedRequest] = []
    for pair in _pairs(window):
        api_start, _ = to_api(table, pair, window.allowed_start.value)
        api_end, _ = to_api(table, pair, window.acquisition_start.value)
        month = _month_start(api_start.year, api_start.month)
        while month < api_end:
            nxt = _next_month(month)
            lo, hi = max(month, api_start), min(nxt, api_end)
            utc_lo, off_lo = from_api(table, pair, lo, exclusive_end=False)
            utc_hi, off_hi = from_api(table, pair, hi, exclusive_end=True)
            if lo < hi and not weekend_only(utc_lo, utc_hi):
                tag = f"{month.year:04d}-{month.month:02d}"
                out.append(
                    SealedRequest(
                        rid=f"S-{pair}-{tag}",
                        pair=pair,
                        stage=SEALED_STAGE_BULK,
                        api_from=lo,
                        api_to=hi,
                        utc_from=utc_lo,
                        utc_to=utc_hi,
                        offset_from_hours=off_lo,
                        offset_to_hours=off_hi,
                        file=f"{pair}/{pair}_{tag}_m1.csv.gz",
                    )
                )
            month = nxt
    return out


def periodic_window(
    window: SealedWindowConfig, *, after: UtcTime | None, run_at: UtcTime
) -> tuple[UtcTime, UtcTime]:
    """定期取得の窓 [始まり, 終わり)（D15 §2.2 の S4a の行 v0.6、PR #109 の要決定 S2b-7 (a)）。

    ``after`` は前の回の窓の終わり（初回は ``None``）。初回は取得の開始時点から 1 週間、2 回目
    以降は ``after`` から、``run_at`` の月の始まり（UTC）以前の最後の取引週の境目まで。窓が
    ``run_at`` より後に掛かる・空になるときは作らない。
    """
    if window.acquisition_start is None:
        raise SealedWindowError("取得の開始時点が空。定期取得は始めない（ADR-0037 第4項の 3）")
    start = window.acquisition_start if after is None else after
    if start < window.acquisition_start or not is_trading_week_start(start):
        raise SealedWindowError(
            f"前の回の終わり {start} は取得の開始時点以後の取引週の境目でなければならない"
        )
    if start == window.acquisition_start:
        end = next_trading_week_start(start)
    else:
        month = run_at.value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = trading_week_start(UtcTime(month))
    if not start < end:
        raise SealedWindowError(f"取る期間が無い（窓 [{start}, {end})）。前の月の分は取り込み済み")
    if run_at < end:
        raise SealedWindowError(
            f"窓の終わり {end} が実行の時点 {run_at} より後。終わった週だけを取る"
        )
    return start, end


def periodic_requests(
    window: SealedWindowConfig, table: TimeRuleTable, start: UtcTime, end: UtcTime
) -> list[SealedRequest]:
    """定期取得の要求（1 通貨 × 1 窓）。"""
    out: list[SealedRequest] = []
    tag = f"{start.value:%Y%m%dT%H%MZ}-{end.value:%Y%m%dT%H%MZ}"
    for pair in _pairs(window):
        api_lo, off_lo = to_api(table, pair, start.value)
        api_hi, off_hi = to_api(table, pair, end.value)
        out.append(
            SealedRequest(
                rid=f"P-{pair}-{start.value:%Y%m%dT%H%MZ}",
                pair=pair,
                stage=SEALED_STAGE_PERIODIC,
                api_from=api_lo,
                api_to=api_hi,
                utc_from=start.value,
                utc_to=end.value,
                offset_from_hours=off_lo,
                offset_to_hours=off_hi,
                file=f"{pair}/{pair}_{tag}_m1.csv.gz",
            )
        )
    return out


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_plan(
    repo_root: Path,
    stage: str,
    *,
    after: UtcTime | None = None,
    run_at: UtcTime,
) -> SealedPlan:
    """窓の設定と表から計画を作り、道具の側と同じ検査（``check_sealed_request``）を通す。"""
    window_path = repo_root / SEALED_WINDOW_PATH
    window = load_sealed_window(window_path)
    tool_window = read_sealed_window(window_path)
    rules = load_mt5_rules(repo_root, SEALED_COUNTING_RULES)
    table = rules.table
    if stage == SEALED_STAGE_BULK:
        if after is not None:
            raise SealedWindowError("一括取得に前の回の終わりは無い")
        requests = bulk_requests(window, table)
        if window.acquisition_start is None:  # pragma: no cover - bulk_requests が止める
            raise SealedWindowError("取得の開始時点が空")
        start, end = window.allowed_start, window.acquisition_start
    elif stage == SEALED_STAGE_PERIODIC:
        start, end = periodic_window(window, after=after, run_at=run_at)
        requests = periodic_requests(window, table, start, end)
    else:
        raise SealedWindowError(f"知らない段: {stage}")
    for request in requests:
        check_sealed_request(request, tool_window, stage, run_at.value)
    payload: dict[str, Any] = {
        "kind": SEALED_PLAN_KIND,
        "schema": 1,
        "stage": stage,
        "decision": "ADR-0037",
        "window_config": SEALED_WINDOW_PATH.as_posix(),
        "window_config_sha256": tool_window.sha256,
        "time_rule_table": {
            "id": table.table_id,
            "version": table.version,
            "sha256": table.sha256,
            "variant": TimeRuleVariant.STANDARD.value,
        },
        "window": {"utc_from": str(start), "utc_to": str(end)},
        "previous_end": None if after is None else str(after),
        "run_at": str(run_at),
        "planned_requests": len(requests),
        "max_requests": sealed_max_requests(len(requests)),
        "requests": [request.payload() for request in requests],
    }
    return SealedPlan(
        stage=stage,
        window_start=start.value,
        window_end=end.value,
        requests=tuple(requests),
        payload=payload,
    )


def _summary(plan: SealedPlan) -> list[str]:
    """件数と時刻だけの要約（価格は扱わない）。"""
    pairs = sorted({request.pair for request in plan.requests})
    table = plan.payload["time_rule_table"]
    return [
        f"段: {plan.stage}",
        f"窓（UTC）: [{plan.window_start.isoformat()}, {plan.window_end.isoformat()})",
        f"通貨: {len(pairs)}（{','.join(pairs)}）",
        f"要求: {len(plan.requests)}（上限 {plan.payload['max_requests']}）",
        f"窓の設定の SHA-256: {plan.payload['window_config_sha256']}",
        f"時刻の規則の表: {table['id']} 版 {table['version']}（{table['sha256']}）",
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = parser.add_subparsers(dest="stage", required=True)
    bulk = sub.add_parser(
        SEALED_STAGE_BULK, help="一括取得の計画（窓 [allowed_start, acquisition_start)）"
    )
    bulk.add_argument("--out", type=Path, required=True)
    bulk.add_argument("--repo-root", type=Path, default=Path("."))
    bulk.add_argument("--run-at", help="実行の時点（UTC。既定は今）")
    periodic = sub.add_parser(SEALED_STAGE_PERIODIC, help="定期取得の計画")
    periodic.add_argument("--out", type=Path, required=True)
    periodic.add_argument("--repo-root", type=Path, default=Path("."))
    periodic.add_argument("--run-at", help="実行の時点（UTC。既定は今）")
    first = periodic.add_mutually_exclusive_group(required=True)
    first.add_argument("--first", action="store_true", help="初回（取得の開始時点から 1 週間）")
    first.add_argument("--after", help="前の回の窓の終わり（UTC。前の回の取り込みの記録の値）")
    args = parser.parse_args(argv)
    run_at = UtcTime.parse(args.run_at) if args.run_at else UtcTime(datetime.now(UTC))
    after = None
    if args.stage == SEALED_STAGE_PERIODIC and args.after:
        after = UtcTime.parse(args.after)
    plan = build_plan(args.repo_root, args.stage, after=after, run_at=run_at)
    args.out.write_text(
        json.dumps(plan.payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    for line in _summary(plan):
        print(line)
    print(f"計画: {args.out}（SHA-256 {_sha256(args.out)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
