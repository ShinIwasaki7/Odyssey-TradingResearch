"""mt5_full_fetch_estimate（全期間取得の計画と見積もり。付録 K-16）の単体テスト。

人工データだけを使う。
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.survey import mt5_full_fetch_estimate as est
from tools.survey.mt5_api_probe import RATE_FIELDS, REQUEST_END_LIMIT


def test_plan_is_8_pairs_by_84_months_inside_research_period() -> None:
    plan = est.request_plan()
    assert len(plan) == 672
    assert len({r.rid for r in plan}) == 672
    assert {r.pair for r in plan} == set(est.PAIRS) and len(est.PAIRS) == 8
    assert all(r.end <= REQUEST_END_LIMIT for r in plan)
    assert plan[0].rid == "F-USDJPY-2023-12" and plan[-1].rid == "F-AUDUSD-2017-01"


def test_plan_months_are_contiguous_without_gap_or_overlap() -> None:
    for pair in est.PAIRS:
        windows = sorted((r.start, r.end) for r in est.request_plan() if r.pair == pair)
        assert windows[0][0] == datetime(2017, 1, 1, tzinfo=UTC)
        assert windows[-1][1] == datetime(2024, 1, 1, tzinfo=UTC)
        for (_, end), (start, _) in zip(windows, windows[1:], strict=False):
            assert end == start


def test_plan_refuses_requests_after_2023(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(est, "LAST_MONTH", (2024, 1))
    with pytest.raises(ValueError, match="2024-01-01"):
        est.request_plan()


def test_prices_come_from_lib_sh() -> None:
    p = est.read_prices()
    assert (p.instance_hour, p.gp3_gb_month, p.ipv4_hour) == (0.0728, 0.096, 0.005)
    assert (p.egress_gb, p.credit_vcpu_hour, p.tax_rate) == (0.114, 0.096, 0.10)


def test_read_prices_fails_when_price_missing(tmp_path: Path) -> None:
    f = tmp_path / "lib.sh"
    f.write_text('readonly PRICE_INSTANCE_HOUR="0.0728"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="PRICE_GP3_GB_MONTH"):
        est.read_prices(f)


def test_cost_reproduces_recorded_estimates() -> None:
    p = est.read_prices()
    # 付録 K-9.4 の実績の概算（2.39 時間、60 GB、送信 0.1 GB）: 約 0.24
    assert est.cost_usd(p, 2.39, 60, 0.1).total == pytest.approx(0.239, abs=0.005)
    # 付録 K-4 の上限（6 時間、50 GB、送信 5 GB、クレジット無制限の誤り）: 約 2.45
    assert est.cost_usd(p, 6, 50, 5, credit_unlimited=True).total == pytest.approx(2.45, abs=0.01)
    # 止め忘れ 1 時間（60 GB）: 約 0.094（付録 K-10.3）
    assert est.cost_usd(p, 1, 60, 0).total == pytest.approx(0.094, abs=0.001)


def test_synthetic_csv_is_deterministic_and_has_tool_columns() -> None:
    a = est.synthetic_month_csv(100, 3, 5, seed=1)
    assert a == est.synthetic_month_csv(100, 3, 5, seed=1)
    rows = list(csv.reader(io.StringIO(a.decode("utf-8"))))
    assert tuple(rows[0]) == RATE_FIELDS
    assert len(rows) == 101
    assert all(r[-1] == "0" for r in rows[1:])  # real_volume は 0
    times = [int(r[0]) for r in rows[1:]]
    assert all(b - a == 60 for a, b in zip(times, times[1:], strict=False))


def test_estimate_numbers() -> None:
    e = est.estimate()
    assert e["requests"] == 672
    assert 20e6 < e["rows_expected"] <= e["rows_upper"] < 23e6
    assert e["gz_gb"]["upper"] < e["csv_gb"]["upper"] < 2.0
    assert e["disk_enough"] is True
    # 今の回収の方法（SSM の出力で分けて受け取る）では 1 日を超える → 別の経路が要る
    ssm = next(v for k, v in e["collect"].items() if k.startswith("SSM の出力"))
    assert ssm["hours"] > 24
    # 上限の費用でも試験予算 5 米ドルの中（累計 0.55 を含む）
    assert 0 < e["budget_upper"]["trial_left"] < e["budget_expected"]["trial_left"]
    assert e["cost_expected"]["total"] < e["cost_upper"]["total"] < 5


def test_cli_outputs_markdown_and_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert est.main([]) == 0
    assert "| 要求 | 672" in capsys.readouterr().out
    assert est.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["requests"] == 672


def test_tool_reads_no_market_data() -> None:
    src = Path(est.__file__).read_text(encoding="utf-8")
    assert "data/raw" not in src
    assert "copy_rates_range" not in src and "import MetaTrader5" not in src
