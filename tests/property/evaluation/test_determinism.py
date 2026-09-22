"""評価の決定論（D07 §9.1・§9.2）。

条件は5つある。整列鍵で並べ替える、丸めず除算を1回だけ行う、`Decimal` は文字列で持つ、
壁時計の時刻・乱数・絶対パスを入れない、0件の鍵も行として出す。ここでは**同じ入力から
同じ結果が出ること**と、**行の入力順を入れ替えても結果が変わらないこと**を、行の並びを
ランダムに入れ替えて確かめる。
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.common.refs import CodeDigest, ContentDigest
from odyssey_fx.evaluation.application.evaluate_run import EvaluateRun
from odyssey_fx.evaluation.application.manifest import METRIC_SET_VERSION
from tests.fixtures.evaluation import traces

_CODE_DIGEST = CodeDigest(digest=ContentDigest.sha256("d" * 64))

#: 並びを入れ替えても結果が変わってはいけない表。
_SHUFFLED_TABLES = (
    TraceTable.LEDGER_SNAPSHOTS,
    TraceTable.FILLS,
    TraceTable.POSITIONS,
    TraceTable.ORDERS,
    TraceTable.ORDER_REQUESTS,
    TraceTable.ATTEMPT_DECISIONS,
    TraceTable.EVALUATIONS,
    TraceTable.OPPORTUNITY_TRANSITIONS,
    TraceTable.INTRABAR_RESOLUTIONS,
)


def _evaluate(tables: dict[TraceTable, list[traces.Row]]) -> object:
    manifest = traces.manifest_for()
    repository = traces.repository_for(tables, manifest=manifest)
    result = traces.result_for(manifest)
    return EvaluateRun(evaluation_code_digest=_CODE_DIGEST).evaluate(
        result, repository, METRIC_SET_VERSION
    )


def test_the_same_input_twice_gives_the_same_result_digest() -> None:
    """同じ入力で2回評価すると結果のダイジェストが一致する（D07 §9.2）。"""
    manifest = traces.manifest_for()
    first = _evaluate(traces.t01_tables(str(manifest.run_id)))
    second = _evaluate(traces.t01_tables(str(manifest.run_id)))
    assert first.manifest.result_digest == second.manifest.result_digest  # type: ignore[attr-defined]
    assert first.metrics == second.metrics  # type: ignore[attr-defined]
    assert first.categories == second.categories  # type: ignore[attr-defined]
    assert first.trades == second.trades  # type: ignore[attr-defined]
    assert first.fill_diagnostics == second.fill_diagnostics  # type: ignore[attr-defined]


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(min_value=0, max_value=10_000))
def test_the_row_order_of_the_trace_does_not_change_the_result(seed: int) -> None:
    """判断履歴の行の入力順を入れ替えても結果が変わらない（D07 §9.1 の条件1）。

    Parquet の格納順（書き出しの並列度や圧縮設定）に結果が依存しないことを、順序を
    入れ替えた入力で確かめる。台帳 snapshot の並びだけは警告検査 C7 に現れるので、
    検査の結果は比べずに指標・集計・取引・診断とダイジェストを比べる。
    """
    manifest = traces.manifest_for()
    baseline = _evaluate(traces.t01_tables(str(manifest.run_id)))

    shuffled = traces.t01_tables(str(manifest.run_id))
    for index, table in enumerate(_SHUFFLED_TABLES):
        rows = shuffled[table]
        if len(rows) > 1:
            offset = (seed + index) % len(rows)
            shuffled[table] = rows[offset:] + rows[:offset]
    permuted = _evaluate(shuffled)

    assert permuted.metrics == baseline.metrics  # type: ignore[attr-defined]
    assert permuted.categories == baseline.categories  # type: ignore[attr-defined]
    assert permuted.trades == baseline.trades  # type: ignore[attr-defined]
    assert permuted.fill_diagnostics == baseline.fill_diagnostics  # type: ignore[attr-defined]
