"""紙上トレース T02 の run（検証戦略 B）をバックテストエンジンで通す組み立て（T02 §16・§17）。

T02 再現生成器（`t02_market`）の足に遅延シナリオ（D03 §3.6）を当て、**エンジンの全フェーズ**
（受付・約定・台帳・損切り水準の更新の適用）まで通す。戦略ランタイムには本物の as-of ビュー
（`runtime_harness.asof_view`）を渡す。待機・再開・追い越しの意味論は「期待される最新足」
（D03 §6.2）に依存するため、単純な偽のビューでは再現できない。

**コマンド経由ではなく、エンジンの利用口（`RunBacktest`）を直接呼ぶ**。段階2 の受入れテストは
設定ファイル → 受入れ → 承認 → run → 評価をコマンドで通したが、段階3 の時点の実験設定の書式
（書式 v1）は検証戦略 B の宣言（後続確認・待機・追従する損切り）も遅延シナリオも書けなかった。
設定の書式を広げるのは段階3 の完了条件（全体計画 §8.2）の外であり、本組み立ては T02 §17 の
「受入れテストが本書の検算値（第16節）を再現したとき」だけを担う。書式 v2（D07 §18）で書けるように
なった後も段階3 の受入テストは変えない（D08 §2.3 の2）。コマンド経由の run が本組み立てと同じ
判断履歴になることは `tests/integration/app/test_run_v2_strategy_b.py` が確かめる。

4つの遅延シナリオ（T02 §1.4）は**同じ素の足**に当てる（D08 §9.6 の方針2）。素の足は1度だけ
作り、シナリオごとに `available_at` だけを動かす。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import timedelta
from functools import cache
from pathlib import Path
from typing import Final

from odyssey_fx.backtest.application.run_backtest import RunBacktest
from odyssey_fx.backtest.domain.policies import RunConfig
from odyssey_fx.backtest.engine.loop import EngineContext, TraceOutputSink
from odyssey_fx.backtest.trace.manifest import config_digest_of
from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.common.ids import IdAllocator, SnapshotId
from odyssey_fx.common.refs import ContentDigest, PolicyRef, SnapshotRef, run_id
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.adapters.fs_store import (
    FileSystemResultRepository,
    FileSystemResultWriter,
    FileSystemTraceSink,
)
from odyssey_fx.evaluation.application.evaluate_run import EvaluateRun, EvaluationReport
from odyssey_fx.evaluation.application.manifest import METRIC_SET_VERSION
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.marketdata.domain.schedule import (
    DelayScenario,
    FixedSeriesDelay,
    InjectedBarDelay,
    SeriesSchedule,
)
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.compiler.compiled import CompiledStrategy, CompileSucceeded
from odyssey_fx.strategy.compiler.validate import compile_strategy
from odyssey_fx.strategy.runtime.evaluator import StrategyEvaluator
from tests.fixtures.acceptance import t02_market
from tests.fixtures.backtest.harness import (
    ACCOUNT,
    CODE_DIGEST,
    CONVERSION_POLICY,
    COST_MODEL,
    ENV_DIGEST,
    EXECUTION_POLICY,
    EXECUTION_SERIES,
    LOCK_DIGEST,
    RISK_POLICY,
    SYMBOL_SPEC,
    SYMBOL_SPEC_REF,
    CollectingResultWriter,
    CollectingTraceSink,
    FakeExecutionSeries,
    FakeFeed,
    RunOutput,
    calendar_ref_of,
)
from tests.fixtures.strategy.runtime_harness import asof_view
from tests.fixtures.strategy.strategy_b import (
    DAILY_SERIES,
    HOURLY_SERIES,
    M15_SERIES,
    TIMEFRAMES,
    strategy_b,
)
from tests.fixtures.synthetic import market

__all__ = [
    "CASES",
    "RUN_INTERVAL",
    "T02Case",
    "build_case",
    "delayed_bars",
    "evaluate_case",
    "raw_bars",
    "run_case",
]

#: run 区間（T02 §1.1）。
RUN_INTERVAL: Final = t02_market.RUN_INTERVAL

#: 15分足を作る区間。執行系列は run 区間を覆い、`m15_ema` の窓60本が `01-07 09:00Z` の前に
#: そろうこと（T02 §16 の不変条件4）だけが要る。15分足の表（T02 §2.3）は run 区間の前後だけを
#: 覆えばよい（T02 §16 の「公開関数」の行）ので、生成区間の全体では作らない。
_M15_WINDOW: Final = Interval(
    start=UtcTime.parse("2014-12-28T22:00:00Z"), end=t02_market.GENERATION_INTERVAL.end
)

#: 遅延シナリオの名前。T02 §1.4 の表の `DelayScenario.id`。
T02Case = str

#: T02 §1.4 の4ケース（遅延なし / 日足のみ2秒 / 待機期限を超える25時間 / D(Jan7) だけ25時間）。
CASES: Final[Mapping[T02Case, DelayScenario]] = {
    "none": DelayScenario(id="none", version=1, rules=()),
    "d1_2s": DelayScenario(
        id="d1_2s",
        version=1,
        rules=(FixedSeriesDelay(series=DAILY_SERIES, delay=timedelta(seconds=2)),),
    ),
    "d1_25h": DelayScenario(
        id="d1_25h",
        version=1,
        rules=(FixedSeriesDelay(series=DAILY_SERIES, delay=timedelta(hours=25)),),
    ),
    "d1_bar_hold": DelayScenario(
        id="d1_bar_hold",
        version=1,
        rules=(
            InjectedBarDelay(
                series=DAILY_SERIES,
                bar_start=UtcTime.parse("2015-01-06T22:00:00Z"),
                delay=timedelta(hours=25),
            ),
        ),
    ),
}

#: 時間足定義の版参照（`ConfigDigest` の対象、D06 §9.3）。検証戦略 B は3つの時間足を使う。
_TIMEFRAME_REFS: Final = tuple(definition.ref for definition in TIMEFRAMES.values())


def _digest(seed: str) -> ContentDigest:
    return ContentDigest.sha256(hashlib.sha256(seed.encode("utf-8")).hexdigest())


def _policy_ref(kind: str, seed: str) -> PolicyRef:
    return PolicyRef(policy_kind=kind, policy_id=f"{kind}_v1", version=1, digest=_digest(seed))


@cache
def raw_bars() -> Mapping[SeriesId, tuple[Bar, ...]]:
    """遅延を当てる前の**素の足**（4ケースで共有する。D08 §9.6 の方針2）。

    1時間足は生成区間の全体、日足は1時間足からの集約（T02 §16 の不変条件1）、15分足は
    `_M15_WINDOW`。
    """
    calendar = market.calendar()
    hourly = t02_market.bars_for("1h", market.TF_1H, calendar)
    quarter = t02_market.bars_for("15m", market.TF_15M, calendar, _M15_WINDOW)
    daily = t02_market.aggregate(hourly, market.TF_1D_NY17, calendar)
    return {DAILY_SERIES: daily, HOURLY_SERIES: hourly, M15_SERIES: quarter}


def delayed_bars(case: T02Case) -> Mapping[SeriesId, tuple[Bar, ...]]:
    """素の足にそのケースの遅延を当てた足（`available_at` だけが動く）。"""
    scenario = CASES[case]
    return {series: t02_market.apply_delay(bars, scenario) for series, bars in raw_bars().items()}


@cache
def _compiled() -> CompiledStrategy:
    outcome = compile_strategy(strategy_b(), INITIAL_CATALOG, TIMEFRAMES)
    assert isinstance(outcome, CompileSucceeded), outcome
    return outcome.compiled


def build_case(
    case: T02Case,
    *,
    snapshot_ref: SnapshotRef | None = None,
    policy_refs: Mapping[str, PolicyRef] | None = None,
) -> RunOutput:
    """そのケースで検証戦略 B の run を1回通す（呼ぶたびに新しく走らせる）。

    run の識別子は完全入力（遅延シナリオの参照を含む）から作る（ADR-0006）。4ケースは
    遅延シナリオの参照だけが違うので、識別子も違う。

    `snapshot_ref` と `policy_refs`（種別 `risk` / `execution` / `cost` / `conversion` /
    `delay` → 版参照）は、判断履歴に載る識別（根拠記録のポリシー参照など）を別の経路の run と
    揃えるときだけ渡す。書式 v2 の実験設定から `run` コマンドで通した run と判断履歴を
    突き合わせる統合テスト（D07 §17.2 の実装 PR 2）が使う。省略時は固定の値である。
    """
    bars = delayed_bars(case)
    compiled = _compiled()
    calendar = market.calendar()
    refs = {
        "risk": _policy_ref("risk", "r"),
        "execution": _policy_ref("execution", "e"),
        "cost": _policy_ref("cost", "c"),
        "conversion": _policy_ref("conversion", "v"),
        "delay": _policy_ref("delay", case),
        **(policy_refs or {}),
    }
    config = RunConfig(
        run_interval=RUN_INTERVAL,
        snapshot_ref=(
            SnapshotRef(snapshot_id=SnapshotId(_digest("t02")))
            if snapshot_ref is None
            else snapshot_ref
        ),
        compiled_ref=compiled.compiled_ref,
        account=ACCOUNT,
        risk_policy_ref=refs["risk"],
        execution_policy_ref=refs["execution"],
        cost_model_ref=refs["cost"],
        conversion_policy_ref=refs["conversion"],
        delay_scenario_ref=refs["delay"],
        execution_series=EXECUTION_SERIES,
    )
    calendar_ref = calendar_ref_of(calendar)
    config_digest = config_digest_of(
        config,
        symbol_spec_ref=SYMBOL_SPEC_REF,
        calendar_ref=calendar_ref,
        timeframe_def_refs=_TIMEFRAME_REFS,
    )
    allocator = IdAllocator(run_id(config_digest, CODE_DIGEST, LOCK_DIGEST, ENV_DIGEST))
    sink = TraceOutputSink()
    context = EngineContext(ACCOUNT)
    evaluator = StrategyEvaluator(
        compiled=compiled,
        registry=INITIAL_CATALOG,
        market_data=asof_view(bars),
        context=context,
        sink=sink,
        allocator=allocator,
    )
    trace_sink = CollectingTraceSink()
    result_writer = CollectingResultWriter()
    use_case = RunBacktest(
        runtime=evaluator,
        context=context,
        output_sink=sink,
        allocator=allocator,
        # 公開イベントの並び（D03 §7.1 の系列順: 名目長の降順）。同じ時刻のイベントは
        # 渡した順に並ぶので、日足 → 1時間足 → 15分足（執行系列）の順に渡す。
        feed=FakeFeed((*bars[DAILY_SERIES], *bars[HOURLY_SERIES]), bars[M15_SERIES]),
        execution_series=FakeExecutionSeries(
            bars[M15_SERIES],
            schedule=SeriesSchedule(
                series=EXECUTION_SERIES, timeframe_def=market.TF_15M, calendar=calendar
            ),
        ),
        calendar=calendar,
        risk_policy=RISK_POLICY,
        execution_policy=EXECUTION_POLICY,
        cost_model=COST_MODEL,
        conversion_policy=CONVERSION_POLICY,
        symbol_spec=SYMBOL_SPEC,
        symbol_spec_ref=SYMBOL_SPEC_REF,
        calendar_ref=calendar_ref,
        integrity=IntegrityReport(),
        trace_sink=trace_sink,
        result_writer=result_writer,
        code_digest=CODE_DIGEST,
        lock_digest=LOCK_DIGEST,
        env_digest=ENV_DIGEST,
        git_commit="0" * 40,
        git_dirty=False,
        timeframe_refs=_TIMEFRAME_REFS,
    )
    result = use_case.run(config, compiled)
    assert result_writer.manifest is not None
    return RunOutput(
        result=result,
        manifest=result_writer.manifest,
        tables=dict(trace_sink.tables),
        context=context,
    )


@cache
def run_case(case: T02Case) -> RunOutput:
    """`build_case` の結果を控える（同じケースを受入れテストと意味論テストで共有する）。"""
    return build_case(case)


def evaluate_case(case: T02Case, root: Path) -> EvaluationReport:
    """そのケースの run を `root` の下へ保存し、単一実行評価（D07）を通す。

    保存と読み戻しは本物の書き出し口（`evaluation.adapters.fs_store`）を使う。評価は保存
    済みの表だけを読む（D07 §4.1）ので、段階3 の判断履歴を段階2 の評価が読めることの確認
    にもなる（D07 §4.2 v1.4 の「段階3 で足される4表は読まない」）。
    """
    output = run_case(case)
    sink = FileSystemTraceSink(root=root, run_id=output.result.run_id)
    for table in TraceTable:
        sink.write(table, output.rows(table))
    FileSystemResultWriter(root=root).write(output.result, output.manifest)
    repository = FileSystemResultRepository(root=root)
    result = repository.read_result(output.result.run_id)
    return EvaluateRun(evaluation_code_digest=CODE_DIGEST).evaluate(
        result, repository, METRIC_SET_VERSION
    )
