"""仮説カードの道具（順 11 の PR 7）の試験用の人工のカードと作業場。

- `card_data`: 人工のカードの中身（既定は GBPUSD の 1 時間足の 2 本の EMA の交差。部品の組み合わせは
  順 10 の研究仮説 H7 の宣言と同じで、通貨だけが違う。決定 173 の「通貨が違えば別の規則」の確かめに
  使う）。値は書式を示すための仮の値で、成績を見て選んでいない。
- `card_bytes`: 中身をカードのバイト列にする（生成の道具と同じ書き方）。
- `workspace`: テストの中のリポジトリの根に、`configs/` の全体と、現行の研究ポリシーの前提のファイル
  を写す（`load_experiment_v2` が実ファイルと照合する。D18 §3.4）。
- `install_policy_v5`: 作業場に試験用の第 5 版を置き、登録簿に足す（第 4 版の本文に、第 5 版の
  損失の側の 3 群と局面の値を足したもの。研究ポリシーではない）。
- `run_record`: 一巡の実行記録 `run_v<版>.yaml` の人工の本文（系譜の材料）。
"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Any, Final

from odyssey_fx.app.config.loader import load_yaml_mapping
from tests.fixtures.evaluation.research_policies import (
    TEST_REGIMES,
    TEST_VALIDATION_V5_GROUPS,
    append_registry_entry,
)
from tools.ops.hypothesis_cards.emit import dump_document

__all__ = [
    "REPO_ROOT",
    "card_bytes",
    "card_data",
    "install_policy_v5",
    "run_record",
    "workspace",
]

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
_POLICY_V4: Final = "configs/policies/research/research_policy_v4.yaml"


def _components(symbol: str) -> list[dict[str, Any]]:
    h1 = f"{symbol}/1h/bid"
    bar = [{"name": "h1", "when": "bar_close", "series": h1}]
    return [
        {
            "instance_id": "fast_ema",
            "component": "ema",
            "component_version": 1,
            "inputs": {"prices": [f"market:{h1}:CLOSE"]},
            "parameters": {
                "period": {"type": "INT", "value": 20},
                "window_bars": {"type": "INT", "value": 60},
            },
            "triggers": copy.deepcopy(bar),
        },
        {
            "instance_id": "slow_ema",
            "component": "ema",
            "component_version": 1,
            "inputs": {"prices": [f"market:{h1}:CLOSE"]},
            "parameters": {
                "period": {"type": "INT", "value": 50},
                "window_bars": {"type": "INT", "value": 300},
            },
            "triggers": copy.deepcopy(bar),
        },
        {
            "instance_id": "stop_level",
            "component": "extreme_price",
            "component_version": 1,
            "inputs": {"prices": [f"market:{h1}:LOW"]},
            "parameters": {
                "lookback": {"type": "INT", "value": 20},
                "mode": {"type": "STR", "value": "MIN"},
            },
            "triggers": copy.deepcopy(bar),
        },
        {
            "instance_id": "entry_trigger",
            "component": "breakout_trigger",
            "component_version": 1,
            "inputs": {"price": ["output:fast_ema.value"], "level": ["output:slow_ema.value"]},
            "parameters": {"direction": {"type": "STR", "value": "LONG"}},
            "triggers": copy.deepcopy(bar),
        },
        {
            "instance_id": "entry_order",
            "component": "market_order_intent",
            "component_version": 1,
            "inputs": {"opportunity": ["output:entry_trigger.opportunity"]},
            "triggers": [{"name": "opp", "when": "input_event", "input_name": "opportunity"}],
        },
        {
            "instance_id": "initial_stop",
            "component": "level_stop_loss",
            "component_version": 1,
            "inputs": {
                "opportunity": ["output:entry_trigger.opportunity"],
                "level": ["output:stop_level.level"],
            },
            "triggers": [{"name": "opp", "when": "input_event", "input_name": "opportunity"}],
        },
        {
            "instance_id": "take_profit",
            "component": "fixed_rr_take_profit",
            "component_version": 1,
            "inputs": {"position": ["runtime:POSITION"]},
            "parameters": {"reward_risk": {"type": "FLOAT", "value": 1.5}},
            "triggers": [{"name": "filled", "when": "runtime_event", "event": "POSITION_OPENED"}],
        },
    ]


def card_data(
    *,
    symbol: str = "GBPUSD",
    mouth: str = "human",
    title: str = "dual-ema",
    created_at: str = "2026-11-20T03:00:00Z",
) -> dict[str, Any]:
    """人工のカードの中身（書き換えてから `card_bytes` に渡す）。"""
    letter = "h" if mouth == "human" else "a"
    data: dict[str, Any] = {
        "schema_version": 1,
        "id": f"{letter}-{created_at[:10].replace('-', '')}-{title}",
        "mouth": mouth,
        "created_at": created_at,
        "title": title,
        "symbol": symbol,
        "rule": (
            f"{symbol} の時間足の短い EMA が長い EMA を上抜けたら成行で買い、"
            "直近の安値に損切り、損切りの幅の R 倍に利確を置く規則"
        ),
        "strategy": {
            "entry_policy": {"kind": "IMMEDIATE"},
            "opportunity_validity": {"bindings": []},
            "components": _components(symbol),
            "roles": {
                "trigger": "entry_trigger.opportunity",
                "order": "entry_order.intent",
                "protection": "initial_stop.protection",
                "exit": "take_profit.action",
            },
            "opportunity_concurrency": {
                "max_active": 1,
                "on_new_trigger": "KEEP_EXISTING",
                "on_order_accepted": "KEEP_OTHERS",
            },
        },
        "grid": {
            "axes": [
                {"instance": "slow_ema", "parameter": "period", "values": [50, 100]},
                {"instance": "take_profit", "parameter": "reward_risk", "values": [1.5, 3.0]},
            ]
        },
        "prediction": "上昇の局面で取引が多く、横ばいで少ない",
        "inspiration": "二本の移動平均の交差の規則を別の通貨で確かめる",
        "evidence_estimate": {"frequency_band": "MEDIUM", "reaches_floor": "LIKELY"},
    }
    if mouth == "ai":
        data["read_scope"] = ["ledger/trial_ledger.jsonl"]
    return data


def card_bytes(data: dict[str, Any]) -> bytes:
    return dump_document(data)


def workspace(root: Path) -> Path:
    """`configs/` の全体と、第 4 版の研究ポリシーの前提のファイルを `root` に写す。"""
    shutil.copytree(REPO_ROOT / "configs", root / "configs")
    policy = load_yaml_mapping(REPO_ROOT / _POLICY_V4)
    for premise in policy["evaluation_standard"]["basis"]["premises"]:
        target = root / premise["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(REPO_ROOT / premise["path"], target)
    return root


def install_policy_v5(root: Path) -> None:
    """試験用の第 5 版を置き、登録簿に用途 STANDARD で足す（研究ポリシーではない）。"""
    text = (root / _POLICY_V4).read_text(encoding="utf-8")
    assert text.count("\nversion: 4\n") == 1
    text = text.replace("\nversion: 4\n", "\nversion: 5\n")
    marker = "  sufficiency:\n"
    assert text.count(marker) == 1
    text = text.replace(marker, TEST_VALIDATION_V5_GROUPS + "\n" + marker)
    text = text.rstrip("\n") + "\n" + TEST_REGIMES + "\n"
    target = root / "configs/policies/research/research_policy_v5.yaml"
    target.write_text("# テスト用。研究ポリシーではない。\n" + text, encoding="utf-8")
    append_registry_entry(root, "research_policy", 5)


def run_record(card_id: str, experiment_id: str, version: int, *, execution: int = 1) -> bytes:
    """一巡の実行記録の人工の本文（記録票の識別子は形だけの仮の値）。"""
    return dump_document(
        {
            "schema_version": 1,
            "card": card_id,
            "version": version,
            "experiment_id": experiment_id,
            "execution": execution,
            "started_at": "2026-11-21T00:00:00Z",
            "finished_at": "2026-11-21T12:00:00Z",
            "exit_code": 0,
            "max_rss_bytes": None,
            "record_id": "ab" * 32,
            "artifacts": [],
            "memo": None,
        }
    )
