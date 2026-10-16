"""MT5 API による全期間取得（8 通貨 × 2017-01〜2023-12 の 1 分足）の計画と見積もり（付録 K-16）。

Mac で実行する計算だけの道具。**AWS・MT5 の端末・配布 CSV・取得済みの価格は使わない**。
入力は付録 K に記録済みの実測の数（足の本数・ファイルの大きさ・時刻・費用の単価）と、
この道具が作る人工の 1 分足（乱数の歩み。価格の水準に意味は無い）の圧縮後の大きさだけ。

出すもの:

- 要求の一覧（通貨 × 月。API の時刻の値で [月初 00:00, 翌月初 00:00)）と、その数。
  どの要求も終わりが ``mt5_api_probe.REQUEST_END_LIMIT``（2024-01-01T00:00Z）以下であることを
  確かめる。
- 行数・保存の大きさ（CSV と gzip の CSV）・端末の同期量・ディスク・取得時間・
  回収の時間（経路ごと）・インスタンス時間・費用（想定と上限）・予算との関係。

単価は ``tools/survey/aws_mt5_probe/lib.sh`` の ``PRICE_*`` と ``TAX_RATE`` を読む
（同じ値を 2 か所に書かない）。

実行（リポジトリの根で）::

    uv run python -m tools.survey.mt5_full_fetch_estimate            # Markdown の表
    uv run python -m tools.survey.mt5_full_fetch_estimate --json     # 同じ数の JSON
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import random
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.survey.mt5_api_probe import OTHER_PAIRS, RATE_FIELDS, REQUEST_END_LIMIT, USDJPY

LIB_SH = Path(__file__).resolve().parent / "aws_mt5_probe" / "lib.sh"

#: 取得する通貨（付録 K-6.3 の 8 通貨。端末の銘柄名は ``<通貨>.cl``）。
PAIRS: tuple[str, ...] = (USDJPY, *OTHER_PAIRS)
#: 研究期間（API の時刻の値の月）。2024 年以降は要求しない（D12-21）。
FIRST_MONTH = (2017, 1)
LAST_MONTH = (2023, 12)

#: 1 分足 1 本の保存の列（道具の ``RATE_FIELDS`` と同じ。real_volume は FX では 0。付録 K-6.1）。
SAVE_FIELDS: tuple[str, ...] = RATE_FIELDS


# --- 付録 K に記録済みの実測（出典を併記。価格の値は含まない） ---------------------------------


@dataclass(frozen=True)
class Measured:
    """見積もりに使う実測の数と出典。"""

    #: USDJPY の M1 2017.01.01〜2019.12.31 の Request で端末が示した本数（付録 K-11.1）。
    usdjpy_bars_2017_2019: int = 1_114_328
    usdjpy_months_2017_2019: int = 36
    #: 1 か月の本数の上限側（23 取引日 × 1,440 分。1 か月の平日は最大 23 日）。
    bars_per_month_upper: int = 23 * 1_440
    #: 端末の 1 分足の履歴の 1 通貨あたりの大きさの最大
    #: （付録 K-7・K-9.1。2012〜2026 の 15 ファイル）。
    terminal_history_mb_per_pair: float = 297.0
    #: 2024 年以降の tick の自動の取り寄せ（付録 K-7。8 通貨で計 約 830 MB。
    #: 起こりうる最大として数える）。
    terminal_ticks_mb_total: float = 830.0
    #: 履歴の取り寄せの後の C ドライブの使用量（付録 K-9.1。60 GB の中。8 通貨の履歴を含む）。
    windows_used_gb_after_sync: float = 24.75
    volume_gb: int = 60
    #: 道具が残りの要求を出さずに止まるディスクの空き（付録 K-8.1）。
    stop_free_gb: float = 3.0
    #: 段 G の 112 要求（各 1 週、足 5,215〜7,195 本）にかかった秒
    #: （最初と最後の executed_at_utc。付録 K-9.3）。
    g_requests: int = 112
    g_seconds: float = 15.7
    #: SSM の出力で分けて受け取る回収の速さ（段 G の zip 1,702,949 バイトを、
    #: 最後の要求 02:30:06 から Mac に置いた 02:44:45 までの 879 秒以内で受け取った。
    #: zip を作る時間を含むので下限側）。
    ssm_collect_bytes: int = 1_702_949
    ssm_collect_seconds: float = 879.0
    #: これまでの AWS の費用の累計（税込み。付録 K-11.4）。
    spent_usd: float = 0.55
    trial_budget_usd: float = 5.0
    total_budget_usd: float = 100.0

    @property
    def bars_per_month_expected(self) -> float:
        return self.usdjpy_bars_2017_2019 / self.usdjpy_months_2017_2019

    @property
    def ssm_bytes_per_second(self) -> float:
        return self.ssm_collect_bytes / self.ssm_collect_seconds

    @property
    def api_seconds_per_request(self) -> float:
        return self.g_seconds / self.g_requests


MEASURED = Measured()


# --- 要求の一覧 -----------------------------------------------------------------------------


@dataclass(frozen=True)
class MonthRequest:
    """1 要求 = 1 通貨 × 1 か月（API の時刻の値で [start, end)）。"""

    pair: str
    year: int
    month: int
    start: datetime
    end: datetime

    @property
    def rid(self) -> str:
        return f"F-{self.pair}-{self.year:04d}-{self.month:02d}"


def _months() -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    y, m = FIRST_MONTH
    while (y, m) <= LAST_MONTH:
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def request_plan(pairs: Sequence[str] = PAIRS) -> list[MonthRequest]:
    """通貨の順（``PAIRS``）、各通貨の中は新しい月から（付録 K-6.3 と同じ向き）。"""
    plan: list[MonthRequest] = []
    for pair in pairs:
        for y, m in reversed(_months()):
            start = datetime(y, m, 1, tzinfo=UTC)
            ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
            end = datetime(ny, nm, 1, tzinfo=UTC)
            if end > REQUEST_END_LIMIT:
                raise ValueError(f"{pair} {y}-{m:02d}: 2024-01-01T00:00Z を超える要求は作らない")
            plan.append(MonthRequest(pair, y, m, start, end))
    return plan


# --- 人工の 1 分足で保存の大きさを測る ------------------------------------------------------


def synthetic_month_csv(rows: int, digits: int, step_points: int, seed: int) -> bytes:
    """人工の 1 分足の CSV（列は ``SAVE_FIELDS``。価格は乱数の歩みで、水準に意味は無い）。

    価格は道具と同じく Python の float の最短の表記で書く（``mt5_api_probe._write_rows`` と同じ）。
    """
    rng = random.Random(seed)
    point = 10.0**-digits
    level = 100_000  # 点の数（桁数 3 なら 100.000、5 なら 1.00000 に当たる）
    t = 1_483_228_800  # 2017-01-01T00:00Z（時刻の並びの形を作るだけ）
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(SAVE_FIELDS)
    for _ in range(rows):
        o = level
        moves = [o + rng.randint(-step_points, step_points) for _ in range(4)]
        h, lo, c = max(o, *moves), min(o, *moves), moves[-1]
        level = c
        w.writerow(
            [
                t,
                round(o * point, digits),
                round(h * point, digits),
                round(lo * point, digits),
                round(c * point, digits),
                rng.randint(1, 300),
                rng.randint(3, 50),
                0,
            ]
        )
        t += 60
    return buf.getvalue().encode("utf-8")


@dataclass(frozen=True)
class SizePerRow:
    raw: float
    gz: float


def measure_size_per_row(rows: int = 30_000) -> SizePerRow:
    """桁数 3・5 と、値動きの小さい・大きい場合の 4 通りで、1 行あたりのバイト数の最大を返す。"""
    raw_max = gz_max = 0.0
    for digits in (3, 5):
        for step in (5, 40):
            data = synthetic_month_csv(rows, digits, step, seed=digits * 100 + step)
            raw_max = max(raw_max, len(data) / rows)
            gz_max = max(gz_max, len(gzip.compress(data, compresslevel=6, mtime=0)) / rows)
    return SizePerRow(raw=raw_max, gz=gz_max)


# --- 単価と費用 -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Prices:
    instance_hour: float
    gp3_gb_month: float
    ipv4_hour: float
    egress_gb: float
    credit_vcpu_hour: float
    tax_rate: float


def read_prices(lib_sh: Path = LIB_SH) -> Prices:
    """lib.sh の ``readonly PRICE_*="..."`` と ``TAX_RATE`` を読む。"""
    text = lib_sh.read_text(encoding="utf-8")

    def get(name: str) -> float:
        m = re.search(rf'^readonly {name}="([0-9.]+)"', text, re.MULTILINE)
        if not m:
            raise ValueError(f"lib.sh に {name} が無い")
        return float(m.group(1))

    return Prices(
        instance_hour=get("PRICE_INSTANCE_HOUR"),
        gp3_gb_month=get("PRICE_GP3_GB_MONTH"),
        ipv4_hour=get("PRICE_IPV4_HOUR"),
        egress_gb=get("PRICE_EGRESS_GB"),
        credit_vcpu_hour=get("PRICE_CREDIT_VCPU_HOUR"),
        tax_rate=get("TAX_RATE"),
    )


#: S3（東京、標準）の単価。**作業役の知識の値で、価格表との照合はしていない**
#: （金額が小さいので見積もりに足すだけ）。
S3_GB_MONTH = 0.025
S3_PUT_PER_1000 = 0.0047
S3_GET_PER_1000 = 0.00037


@dataclass(frozen=True)
class Cost:
    hours: float
    instance: float
    disk: float
    ipv4: float
    credit: float
    egress: float
    s3: float
    subtotal: float
    tax: float
    total: float


def cost_usd(
    prices: Prices,
    hours: float,
    disk_gb: float,
    egress_gb: float,
    credit_unlimited: bool = False,
    s3_gb: float = 0.0,
    s3_hours: float = 0.0,
    s3_puts: int = 0,
    s3_gets: int = 0,
) -> Cost:
    """K-4 と同じ式（月は 720 時間）。

    CPU のクレジットは、標準にし損ねて全時間で基準を超えた場合だけ数える。
    """
    instance = prices.instance_hour * hours
    disk = disk_gb * prices.gp3_gb_month * hours / 720
    ipv4 = prices.ipv4_hour * hours
    credit = 2 * prices.credit_vcpu_hour * hours if credit_unlimited else 0.0
    egress = prices.egress_gb * egress_gb
    s3 = (
        s3_gb * S3_GB_MONTH * s3_hours / 720
        + s3_puts / 1000 * S3_PUT_PER_1000
        + s3_gets / 1000 * S3_GET_PER_1000
    )
    subtotal = instance + disk + ipv4 + credit + egress + s3
    tax = subtotal * prices.tax_rate
    return Cost(hours, instance, disk, ipv4, credit, egress, s3, subtotal, tax, subtotal + tax)


# --- 見積もり全体 ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Phase:
    name: str
    expected_min: float
    cap_min: float


#: 作業の段と時間（分）。想定は付録 K-9.1・K-11.1 の実績から、上限は打ち切りの判断に使う値【提案】。
def phases(api_minutes: float) -> list[Phase]:
    return [
        Phase("起動・準備（10_launch・20_prepare・見張り。K-11.1 は 18 分）", 20, 30),
        Phase("人間の RDP の作業（8 通貨の Request を含む。準備と並行できない部分）", 35, 60),
        Phase("端末の履歴の取り寄せの待ち（8 通貨。前回は約 20 分）", 20, 40),
        Phase("API の取得 672 要求（書き出し・検算を含む）", api_minutes, 45),
        Phase("Windows の上の検算・manifest・zip", 5, 15),
        Phase("回収（経路による。下の表）", 10, 30),
        Phase("片付け・残りの確認", 5, 10),
    ]


def estimate(m: Measured = MEASURED, prices: Prices | None = None) -> dict[str, Any]:
    prices = prices or read_prices()
    plan = request_plan()
    n_req = len(plan)
    months_per_pair = n_req // len(PAIRS)
    rows_expected = m.bars_per_month_expected * n_req
    rows_upper = float(m.bars_per_month_upper * n_req)
    size = measure_size_per_row()
    gb = 1e9
    csv_gb = {"expected": rows_expected * size.raw / gb, "upper": rows_upper * size.raw / gb}
    gz_gb = {"expected": rows_expected * size.gz / gb, "upper": rows_upper * size.gz / gb}

    history_gb = m.terminal_history_mb_per_pair * len(PAIRS) / 1000
    ticks_gb = m.terminal_ticks_mb_total / 1000
    # Windows の使用量: 履歴の取り寄せ後の実測（履歴を含む）+ tick の自動の取り寄せ（最大）
    # + 出力（gzip の CSV）+ 回収用の zip（gzip の中身なのでほぼ同じ大きさ）。
    used_upper = m.windows_used_gb_after_sync + ticks_gb + 2 * gz_gb["upper"]
    free_upper = m.volume_gb - used_upper

    # API の要求の時間: 段 G の実測（1 要求 0.14 秒、7,000 本前後）を本数で比例させ、書き出しの時間
    # （gzip の CSV。作業役の見込み 1 秒あたり 10 万行）を足す。
    api_sec = m.api_seconds_per_request * n_req * (m.bars_per_month_expected / 7_000)
    write_sec = rows_upper / 100_000
    api_minutes = (api_sec + write_sec) / 60

    collect: dict[str, dict[str, float | str]] = {}
    gz_bytes_upper = gz_gb["upper"] * gb
    collect["SSM の出力で分けて受け取る（今の 40_collect.sh）"] = {
        "hours": gz_bytes_upper / m.ssm_bytes_per_second / 3600,
        "basis": f"実測 {m.ssm_bytes_per_second:,.0f} バイト/秒（付録 K-9.3 の段 G の回収）",
    }
    for label, mb_s in (("1 MB/秒の場合", 1.0), ("10 MB/秒の場合", 10.0)):
        collect[f"S3 か RDP のフォルダの共有か SSM のポート転送（{label}。未測定）"] = {
            "hours": gz_bytes_upper / (mb_s * 1e6) / 3600,
            "basis": "仮定（測っていない）",
        }

    ph = phases(api_minutes)
    exp_h = sum(p.expected_min for p in ph) / 60
    cap_h = sum(p.cap_min for p in ph) / 60
    egress_exp = gz_gb["expected"] + 0.1  # 回収 + RDP の画面・SSM の応答（K-9.4 は 0.1 GB 以下）
    egress_up = csv_gb["upper"] + 1.0  # 圧縮しないまま送った場合 + RDP の画面の余裕
    # S3 を経路にした場合の分（置く期間は最大 48 時間、要求は 1 ファイル 1 回 + 予備 10）。
    s3_gb, s3_hours, s3_ops = gz_gb["upper"], 48.0, n_req + 10
    cost_exp = cost_usd(
        prices, exp_h, m.volume_gb, egress_exp, False, s3_gb, s3_hours, s3_ops, s3_ops
    )
    cost_up = cost_usd(prices, 6.0, m.volume_gb, egress_up, True, s3_gb, s3_hours, s3_ops, s3_ops)
    forgot_per_hour = cost_usd(prices, 1.0, m.volume_gb, 0.0).total

    def budget(c: Cost) -> dict[str, float]:
        after = m.spent_usd + c.total
        return {
            "after": after,
            "trial_left": m.trial_budget_usd - after,
            "total_left": m.total_budget_usd - after,
        }

    return {
        "requests": n_req,
        "pairs": len(PAIRS),
        "months_per_pair": months_per_pair,
        "first_request": plan[0].rid,
        "last_request": plan[-1].rid,
        "max_request_end": max(r.end for r in plan).isoformat(),
        "bars_per_month_expected": m.bars_per_month_expected,
        "bars_per_month_upper": m.bars_per_month_upper,
        "rows_expected": rows_expected,
        "rows_upper": rows_upper,
        "bytes_per_row": asdict(size),
        "csv_gb": csv_gb,
        "gz_gb": gz_gb,
        "terminal_history_gb": history_gb,
        "terminal_ticks_gb_max": ticks_gb,
        "windows_used_gb_upper": used_upper,
        "windows_free_gb_upper": free_upper,
        "disk_enough": free_upper > m.stop_free_gb,
        "api_minutes": api_minutes,
        "collect": collect,
        "phases": [asdict(p) for p in ph],
        "hours_expected": exp_h,
        "hours_cap": cap_h,
        "egress_gb": {"expected": egress_exp, "upper": egress_up},
        "cost_expected": asdict(cost_exp),
        "cost_upper": asdict(cost_up),
        "forgot_per_hour": forgot_per_hour,
        "budget_expected": budget(cost_exp),
        "budget_upper": budget(cost_up),
        "spent_usd": m.spent_usd,
    }


def render_markdown(e: Mapping[str, Any]) -> str:
    gz, csvg = e["gz_gb"], e["csv_gb"]
    ce, cu = e["cost_expected"], e["cost_upper"]
    be, bu = e["budget_expected"], e["budget_upper"]
    lines = [
        "| 項目 | 想定 | 上限 |",
        "|---|---|---|",
        f"| 要求 | {e['requests']}（{e['pairs']} 通貨 × {e['months_per_pair']} か月） | — |",
        f"| 行数 | {e['rows_expected'] / 1e6:.1f} 百万 | {e['rows_upper'] / 1e6:.1f} 百万 |",
        f"| 保存（CSV） | {csvg['expected']:.2f} GB | {csvg['upper']:.2f} GB |",
        f"| 保存（gzip の CSV） | {gz['expected']:.2f} GB | {gz['upper']:.2f} GB |",
        "| 端末の同期（1 分足の履歴 / tick の自動の取り寄せ） | "
        f"{e['terminal_history_gb']:.2f} GB | "
        f"{e['terminal_history_gb']:.2f} + {e['terminal_ticks_gb_max']:.2f} GB |",
        f"| Windows の使用 / 空き（60 GB） | — | {e['windows_used_gb_upper']:.1f} / "
        f"{e['windows_free_gb_upper']:.1f} GB |",
        f"| API の取得の時間 | {e['api_minutes']:.1f} 分 | 45 分（打ち切り） |",
        f"| インスタンス時間 | {e['hours_expected']:.2f} 時間 | "
        f"{e['hours_cap']:.2f} 時間（段の上限の和） |",
        f"| 送信 | {e['egress_gb']['expected']:.2f} GB | {e['egress_gb']['upper']:.2f} GB |",
        f"| 費用（税込み） | {ce['total']:.2f} | "
        f"{cu['total']:.2f}（6 時間・クレジット無制限の誤り込み） |",
        f"| 累計（既 {e['spent_usd']:.2f}） | {be['after']:.2f} | {bu['after']:.2f} |",
        f"| 試験予算 5 の残り | {be['trial_left']:.2f} | {bu['trial_left']:.2f} |",
        f"| 全体予算 100 の残り | {be['total_left']:.2f} | {bu['total_left']:.2f} |",
        f"| 止め忘れ（1 時間） | {e['forgot_per_hour']:.3f} | — |",
        "",
        "| 回収の経路 | 時間（gzip の上限の量） | 根拠 |",
        "|---|---|---|",
    ]
    for name, v in e["collect"].items():
        lines.append(f"| {name} | {v['hours']:.2f} 時間 | {v['basis']} |")
    lines += ["", "| 段 | 想定（分） | 上限（分） |", "|---|---|---|"]
    for p in e["phases"]:
        lines.append(f"| {p['name']} | {p['expected_min']:.0f} | {p['cap_min']:.0f} |")
    b = e["bytes_per_row"]
    lines += [
        "",
        f"- 1 行あたり（人工データの最大）: CSV {b['raw']:.1f} バイト、gzip {b['gz']:.1f} バイト。",
        f"- 要求の範囲: 最初 {e['first_request']}、最後 {e['last_request']}、"
        f"終わりの最大 {e['max_request_end']}。",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument("--json", action="store_true", help="JSON で出す")
    args = p.parse_args(argv)
    e = estimate()
    if args.json:
        json.dump(e, sys.stdout, ensure_ascii=False, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_markdown(e))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
