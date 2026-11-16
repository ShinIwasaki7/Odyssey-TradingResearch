"""リポジトリにある生成物（仮説カードから作った事前登録）の読込と全試行のコンパイル。

設計は D25 §6.3 の 4。CI の検査 4（PR 6）は作り直しのバイト単位の一致だけを確かめ、全生成物の
読込と全試行のコンパイルはこの試験が確かめる。

- 生成の印（先頭の行の `# generated-by: research_runner`）を持つ実験設定をすべて、
  `experiment run` と同じ読込（`load_experiment_v2`。照合 C1〜C7・登録簿・前提のファイルの
  照合を含む）に通す。
- 格子の全試行をコンパイルし、複雑さと試行の数が上限以下であることを確かめる。
- 生成の印を持つ戦略の宣言は、どれかの生成した実験設定から指されていること。

まだ生成物が無いあいだは、見つけた件数が 0 であることだけを確かめる（試験が空振りしても落ちない）。
snapshot も成績も読まない。
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from odyssey_fx.app.composition import complexity_profiles, trial_strategy
from odyssey_fx.app.config.experiment_v2 import load_experiment_v2
from odyssey_fx.evaluation.domain.metrics import METRIC_SET_VERSIONS
from odyssey_fx.evaluation.domain.research_policy import (
    check_complexity,
    check_trial_count,
    measure_complexity,
)
from odyssey_fx.evaluation.domain.search import enumerate_assignments
from odyssey_fx.evaluation.domain.status import CheckOutcome
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.compiler.compiled import CompileSucceeded
from odyssey_fx.strategy.compiler.validate import compile_strategy

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
MARK: Final = b"# generated-by: research_runner "


def _generated(directory: str) -> list[Path]:
    return sorted(
        path
        for path in (REPO_ROOT / directory).glob("*.yaml")
        if path.read_bytes().startswith(MARK)
    )


EXPERIMENTS: Final = _generated("configs/experiments")
STRATEGIES: Final = _generated("configs/strategies")


def test_the_discovery_finds_every_generated_file() -> None:
    """生成の印を持たない既存の事前登録（順 5・8・10）は対象にしない。"""
    assert all(path.name.startswith("c_") for path in EXPERIMENTS + STRATEGIES)


@pytest.mark.parametrize("path", EXPERIMENTS, ids=[path.name for path in EXPERIMENTS])
def test_every_generated_experiment_loads_and_every_trial_compiles(path: Path) -> None:
    loaded = load_experiment_v2(
        path, repo_root=REPO_ROOT, registry=INITIAL_CATALOG, metric_set_versions=METRIC_SET_VERSIONS
    )
    search = loaded.search
    assert search is not None and search.final_holdout is not None
    assignments = enumerate_assignments(search.plan)
    assert 4 <= len(assignments) <= 6
    limit = loaded.policy.trial_limit
    assert limit is not None
    assert check_trial_count(len(assignments), limit).outcome is CheckOutcome.PASSED
    timeframes = {d.ref: d for d in loaded.environment.timeframe_defs.values()}
    for assignment in assignments:
        outcome = compile_strategy(
            trial_strategy(loaded.body.strategy, assignment), INITIAL_CATALOG, timeframes
        )
        assert isinstance(outcome, CompileSucceeded), outcome
        profiles, unmeasured, causes = complexity_profiles(outcome.compiled, INITIAL_CATALOG)
        measures = measure_complexity(profiles, unmeasured_outputs=unmeasured)
        result = check_complexity(measures, loaded.policy.limits, unmeasured_causes=causes)
        assert result.outcome is CheckOutcome.PASSED


def test_every_generated_strategy_is_referenced_by_a_generated_experiment() -> None:
    referenced: set[str] = set()
    for path in EXPERIMENTS:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("strategy: "):
                referenced.add(line.split('"')[1])
    assert {path.relative_to(REPO_ROOT).as_posix() for path in STRATEGIES} <= referenced
