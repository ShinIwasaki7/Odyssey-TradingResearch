"""探索の実験設定と試験用の研究ポリシー版 3 をテストの中のリポジトリの根に作る（D09 §5.6・§13）。

探索の実験設定は、同梱の検証戦略 B の単一実行の実験設定（`strategy_b_t02_none.yaml`）を複製し、
D09 §5.6 の「次の実験の始め方」のとおり `id`・研究ポリシーの版・`search_plan`・`split`・
`final_holdout` だけを書き換え、`run_interval` を外して作る（区間は fold が持つ）。

試験用の研究ポリシー版 3 は `research_policies.policy_v3_text` の期間分割だけを差し替え、T02 の
人工データの 12 日（2015-01-04T22:00Z〜2015-01-16T22:00Z）を選定区間 4 日・検証区間 4 日の
2 fold に分ける（fold 0 = 選定 [0,4) 日・検証 [4,8) 日、fold 1 = 選定 [4,8) 日・検証 [8,12) 日。
fold 0 の検証区間と fold 1 の選定区間が同じ区間になる。D09 §6.2）。**テスト用であり研究
ポリシーではない**。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from tests.fixtures.evaluation.research_policies import (
    append_registry_entry,
    policy_v3_text,
    write_policy,
)

__all__ = [
    "BASE_EXPERIMENT",
    "CANDIDATE_PLACEHOLDER",
    "DEFAULT_AXES",
    "TWO_FOLD_SPLIT",
    "install_policy_v3",
    "search_experiment_text",
    "write_search_experiment",
]

#: 複製の元にする単一実行の実験設定（リポジトリの根からの相対パス）。
BASE_EXPERIMENT: Final = "configs/experiments/strategy_b_t02_none.yaml"

#: 2 fold の期間分割（試験用。`policy_v3_text` の `split` の行を差し替える）。
TWO_FOLD_SPLIT: Final = (
    "  split:\n"
    '    range: {start: "2015-01-04T22:00:00Z", end: "2015-01-16T22:00:00Z"}\n'
    '    train_length: "4d"\n'
    '    validation_length: "4d"\n'
    "    window: ROLLING\n"
    '    purge: "0s"\n'
    "    partial_fold: AT_START\n"
    "    min_folds: 2"
)

#: 既定の2軸（検証戦略 B の15分足 EMA の期間と、損切りの安値の本数）。期間 40 は
#: `window_bars (60) >= 2 * period` を満たさないのでコンパイルが拒否する（D05 §4.5。試行の失敗）。
DEFAULT_AXES: Final = (
    "    - {instance: m15_ema, parameter: period, values: [10, 40]}\n"
    "    - {instance: stop_level, parameter: lookback, values: [10, 20]}"
)

_RUN_INTERVAL: Final = (
    "# 12 日間（T02 §1.1。T01 と同じ区間）。\n"
    "run_interval:\n"
    '  start: "2015-01-04T22:00:00Z"\n'
    '  end: "2015-01-16T22:00:00Z"\n'
)
_SINGLE_RUN_KEYS: Final = 'search_plan: "NONE"\nsplit: "NONE"\n'

#: 候補の組の snapshot の既定値（設定の読込だけを確かめるテスト用。snapshot は開かない）。
CANDIDATE_PLACEHOLDER: Final = "c" * 64

_SNAPSHOT_LINE: Final = re.compile(r'^snapshot: "([0-9a-f]{64})"\n', re.MULTILINE)


def install_policy_v3(
    repo: Path, *, version: int = 3, split: str = TWO_FOLD_SPLIT, trials: int = 100
) -> None:
    """作業場に試験用の研究ポリシー版 3 を書き、作業場の登録簿に1行足す（D09 §10.9）。"""
    text = policy_v3_text(version=version, trials=trials, replace={"split": split})
    write_policy(repo, "research_policy", version, text)
    append_registry_entry(repo, "research_policy", version)


def search_experiment_text(
    base: str,
    *,
    experiment_id: str = "strategy_b_search",
    policy_version: int = 3,
    axes: str = DEFAULT_AXES,
    max_trials: int = 4,
    final_holdout: str = "NONE",
    candidate_snapshot: str = CANDIDATE_PLACEHOLDER,
) -> str:
    """単一実行の実験設定の本文から探索の実験設定の本文を作る（D09 §5.6）。

    単数の `snapshot` を両組の `snapshots: {standard, candidate}` に書き換える（2026-10-21 改訂。
    標準の組は元の snapshot、候補の組は `candidate_snapshot`）。
    """
    assert _RUN_INTERVAL in base and _SINGLE_RUN_KEYS in base
    text = base.replace(_RUN_INTERVAL, "")
    match = _SNAPSHOT_LINE.search(text)
    assert match is not None, "単一実行の実験設定に snapshot の行が無い"
    text = _SNAPSHOT_LINE.sub(
        f'snapshots:\n  standard: "{match.group(1)}"\n  candidate: "{candidate_snapshot}"\n',
        text,
        count=1,
    )
    text = text.replace("id: strategy_b_t02_none\n", f"id: {experiment_id}\n")
    text = text.replace(
        'research_policy: {id: "research_policy", version: 1}',
        f'research_policy: {{id: "research_policy", version: {policy_version}}}',
    )
    return text.replace(
        _SINGLE_RUN_KEYS,
        "search_plan:\n"
        "  kind: GRID\n"
        f"  max_trials: {max_trials}\n"
        "  axes:\n"
        f"{axes}\n"
        "split: STANDARD\n"
        f"final_holdout: {final_holdout}\n",
    )


def write_search_experiment(
    repo: Path,
    name: str = "strategy_b_search.yaml",
    *,
    experiment_id: str = "strategy_b_search",
    policy_version: int = 3,
    axes: str = DEFAULT_AXES,
    max_trials: int = 4,
    final_holdout: str = "NONE",
    candidate_snapshot: str | None = None,
) -> Path:
    """作業場に探索の実験設定を書き、その位置を返す（元は作業場の `BASE_EXPERIMENT`）。

    `candidate_snapshot` を省くと、作業場の `candidate_snapshot.txt`（`t02_workspace.
    install_candidate` が書く）があればその識別子を、無ければ `CANDIDATE_PLACEHOLDER` を使う。
    """
    if candidate_snapshot is None:
        marker = repo / "candidate_snapshot.txt"
        candidate_snapshot = (
            marker.read_text(encoding="utf-8").strip()
            if marker.is_file()
            else CANDIDATE_PLACEHOLDER
        )
    base = (repo / BASE_EXPERIMENT).read_text(encoding="utf-8")
    path = repo / "configs/experiments" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    text = search_experiment_text(
        base,
        experiment_id=experiment_id,
        policy_version=policy_version,
        axes=axes,
        max_trials=max_trials,
        final_holdout=final_holdout,
        candidate_snapshot=candidate_snapshot,
    )
    path.write_text(text, encoding="utf-8")
    return path
