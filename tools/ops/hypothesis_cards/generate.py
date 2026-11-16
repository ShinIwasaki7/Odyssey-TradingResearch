"""カードから事前登録（戦略の宣言と実験設定）を作る・作り直す。

設計は D25 §3.4・§3.8（決定 171・172・174）。**判断を入れず、写す・決まった値を入れるだけ**
（D25-1 (a)）。同じ入力（カード・変更の提案・その時点のリポジトリの研究ポリシーと登録簿と型板と
実行記録）からは同じバイト列が出る（`emit`）。CI の検査 4（作り直しの一致。PR 6）は、
`regenerate` を呼んで生成物とバイト単位で比べる。

生成物の項目と出どころ:

- 戦略の `schema_version`・`strategy_id`・`version`: `1`、実験と戦略の識別子（第3.2節）、戦略の版
  （版 1 は 1。`STRATEGY_CODE` の改善で 1 つ上げる）。
- 戦略の宣言の本体: カードの `strategy` をそのまま（改善の版は変更の提案の操作を当てたもの）。
- 実験の `schema_version`・`id`・`version`: `2`、実験の識別子、版（改善のたびに上げる）。
- `lineage`（版 2 から）: 前の版の実行記録 `run_v<前の版>.yaml` の記録票の識別子と実行番号、
  変更の種類、変更の理由の文。
- `hypothesis`: カードの `rule` ＋ 定型の結び（仮置き 11）。
- `research_policy`: 登録簿の用途 `STANDARD` の最大の版（仮置き 10）。
- `strategy`: 生成した（または前の版の）戦略の宣言のパス。
- `environment`・`execution_series`・`resolution_hierarchy`・`seed`: 通貨ごとの型板（仮置き 9）。
  `seed` は 0。
- `snapshots`・`conversion_snapshots`・`account`・`risk_policy`・`execution_policy`・`cost_model`・
  `conversion_policy`: 研究ポリシーの前提の記録と比較の前提の、その通貨の値（照合 C1〜C7 を必ず
  満たす）。口座の識別子・注文の有効時間・参照の気配の出どころは比較の前提に無いので型板から取る
  （本 PR の仮置き）。
- `evaluation`: 研究ポリシーの評価基準が使う指標を持つ最小の指標集合の版（版 5 以上は指標集合 4
  以上。D24 §8.1 の E11）。
- `search_plan`: `{kind: GRID, max_trials: <列挙した試行の数>, axes: <格子>}`。`split`: `STANDARD`。
- `final_holdout`: 最終検証を予定する宣言を必ず書く（決定 174）。
- `favored_regimes`: カードにあれば写す（D24 の仮置き 16 が D25 に送った規則。本 PR の仮置き）。
- 先頭のコメント: 生成の印（`# generated-by: research_runner card=… card_sha256=…
  generator=…`）と出どころの参照。

**置き場**（D25 の仮置き 8）: 戦略は `configs/strategies/<識別子>_v<戦略の版>.yaml`、実験は版 1 が
`configs/experiments/<識別子>.yaml`、版 2 からは `configs/experiments/<識別子>_v<版>.yaml`
（順 8・10 の前例の名前。本 PR の仮置き）。版 n の生成物は、実験設定と、版 n で戦略の版が上がった
とき（版 1 と `STRATEGY_CODE` の改善）の戦略の宣言である。`PARAMETERS` の改善では戦略の宣言を
作らず、前の版の戦略の宣言のパスを指す。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from odyssey_fx.app.config.experiment_v2 import standard_metrics
from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.research_policy import (
    load_research_policy,
    load_research_policy_registry,
)
from odyssey_fx.evaluation.domain.metrics import (
    METRIC_SET_V4_ADDED,
    METRIC_SET_VERSIONS,
    metric_ids_of,
)
from odyssey_fx.evaluation.domain.research_policy import ResearchPolicy
from odyssey_fx.evaluation.domain.search import EvaluationStandardV5
from tools.ops.hypothesis_cards.card import Card, parse_card
from tools.ops.hypothesis_cards.changes import (
    Change,
    LoopState,
    apply_change,
    initial_state,
    parse_change,
)
from tools.ops.hypothesis_cards.emit import dump_document
from tools.ops.hypothesis_cards.repo import (
    EXPERIMENTS_DIR,
    LOOPS_DIR,
    POLICY_DIR,
    STRATEGIES_DIR,
    TEMPLATES_DIR,
    RepoView,
    ToolFailure,
)

__all__ = [
    "FINAL_HOLDOUT_END",
    "FINAL_HOLDOUT_START",
    "HYPOTHESIS_SUFFIX",
    "PolicyChoice",
    "PreregChecks",
    "Preregistration",
    "Template",
    "change_path",
    "experiment_path",
    "generate",
    "generator_digest",
    "header_line",
    "load_template",
    "load_changes",
    "prereg_note",
    "prereg_note_path",
    "regenerate",
    "run_record_path",
    "select_policy",
    "state_at",
    "strategy_path",
]

#: 実験設定の `hypothesis` の定型の結び（仮置き 11。前例の文の形）。
HYPOTHESIS_SUFFIX: Final = "は、費用を引いた後も、両組で共通基準を満たす"
#: 最終検証を予定する宣言（決定 174。D22 §1.4 の形）。
FINAL_HOLDOUT_START: Final = "2024-01-01T00:00:00Z"
FINAL_HOLDOUT_END: Final = "2100-01-01T00:00:00Z"
_POLICY_ID: Final = "research_policy"
_TEMPLATE_SCHEMA_VERSION: Final = 1
_PACKAGE_DIR: Final = Path(__file__).resolve().parent


# --- 置き場 -------------------------------------------------------------------------


def strategy_path(experiment_id: str, strategy_version: int) -> str:
    return f"{STRATEGIES_DIR}/{experiment_id}_v{strategy_version}.yaml"


def experiment_path(experiment_id: str, version: int) -> str:
    suffix = "" if version == 1 else f"_v{version}"
    return f"{EXPERIMENTS_DIR}/{experiment_id}{suffix}.yaml"


def prereg_note_path(card_id: str, version: int) -> str:
    return f"{LOOPS_DIR}/{card_id}/prereg_v{version}.yaml"


def change_path(card_id: str, version: int) -> str:
    """版 `version` の材料の変更の提案（`version` は 2 か 3）。"""
    return f"{LOOPS_DIR}/{card_id}/change_v{version}.yaml"


def run_record_path(card_id: str, version: int) -> str:
    return f"{LOOPS_DIR}/{card_id}/run_v{version}.yaml"


# --- 生成の道具のダイジェストと生成の印 ---------------------------------------------------


def generator_digest() -> str:
    """生成の道具のダイジェスト（このパッケージの `.py` の名前と SHA-256 の並びの SHA-256）。

    D25 §3.4 の 2「生成の道具のダイジェスト（道具のファイルの SHA-256）」。道具を直すと値が変わる
    （過去の生成物は作り直さない。D25 §3.4 の 3）。
    """
    lines = [
        f"{path.name}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\n"
        for path in sorted(_PACKAGE_DIR.glob("*.py"))
    ]
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def header_line(card_id: str, card_sha256: str, generator: str) -> str:
    """生成物の先頭の生成の印（D25 §3.8。PR 6 の検査 4 と同じ字面。`# ` を除いた形）。"""
    return (
        f"generated-by: research_runner card={card_id} card_sha256={card_sha256}"
        f" generator={generator}"
    )


def _comments(card: Card, generator: str, kind: str) -> list[str]:
    return [
        header_line(card.card_id, hashlib.sha256(card.raw).hexdigest(), generator),
        f"順 11 の実行器が仮説カード {card.card_id} から生成した{kind}（D25 §3.8）。",
        "手で直さない（CI がカードから作り直してバイト単位で一致を確かめる。D25 §3.4）。",
        "成績の数値を書かない。",
    ]


# --- 型板と研究ポリシー -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Template:
    """通貨ごとの型板（仮置き 9）。"""

    symbol: str
    environment: Mapping[str, str]
    execution_series: str
    resolution_hierarchy: tuple[str, ...]
    seed: int
    account_id: str
    execution_policy: Mapping[str, str]


def _read_yaml(view: RepoView, path: str) -> dict[str, Any]:
    raw = view.read(path)
    if raw is None:
        raise ToolFailure(f"{path} が無い")
    try:
        return load_yaml_mapping(Path(path), text=raw.decode("utf-8"))
    except (ConfigError, UnicodeDecodeError) as exc:
        raise ToolFailure(f"{path} を読めない: {exc}") from exc


def load_template(view: RepoView, symbol: str) -> Template:
    """`configs/runner/experiment_templates/<通貨>.yaml` を読む（無い・形が違えば道具の失敗）。"""
    path = f"{TEMPLATES_DIR}/{symbol}.yaml"
    data = _read_yaml(view, path)
    keys = {
        "schema_version",
        "symbol",
        "environment",
        "execution_series",
        "resolution_hierarchy",
        "seed",
        "account_id",
        "execution_policy",
    }
    environment = data.get("environment")
    execution = data.get("execution_policy")
    hierarchy = data.get("resolution_hierarchy")
    if (
        set(data) != keys
        or data["schema_version"] != _TEMPLATE_SCHEMA_VERSION
        or data["symbol"] != symbol
        or not isinstance(environment, dict)
        or set(environment) != {"calendar", "timeframes", "symbols"}
        or not all(isinstance(v, str) for v in environment.values())
        or not isinstance(data["execution_series"], str)
        or not data["execution_series"].startswith(f"{symbol}/")
        or not isinstance(hierarchy, list)
        or not hierarchy
        or not all(isinstance(v, str) for v in hierarchy)
        or data["seed"] != 0
        or isinstance(data["seed"], bool)
        or not isinstance(data["account_id"], str)
        or not isinstance(execution, dict)
        or set(execution) != {"entry_valid_for", "close_valid_for", "reference_quote_source"}
        or not all(isinstance(v, str) for v in execution.values())
    ):
        raise ToolFailure(f"{path}: 型板の形でない（D25 の仮置き 9）")
    return Template(
        symbol=symbol,
        environment=dict(environment),
        execution_series=data["execution_series"],
        resolution_hierarchy=tuple(hierarchy),
        seed=0,
        account_id=data["account_id"],
        execution_policy=dict(execution),
    )


@dataclass(frozen=True, slots=True)
class PolicyChoice:
    """生成の時点の現行の研究ポリシー（`raw` はファイルの値、`policy` は読込の結果）。"""

    version: int
    path: str
    raw: Mapping[str, Any]
    policy: ResearchPolicy


def select_policy(view: RepoView) -> PolicyChoice:
    """登録簿の用途 `STANDARD` の最大の版を選んで読む（仮置き 10）。

    第 5 版より前かどうかで取り出しを止めるのは実行器（PR 8a）の役目で、ここでは止めない。
    """
    registry_path = f"{POLICY_DIR}/registry.yaml"
    raw_registry = view.read(registry_path)
    if raw_registry is None:
        raise ToolFailure(f"{registry_path} が無い")
    try:
        entries = load_research_policy_registry(
            Path(registry_path), text=raw_registry.decode("utf-8")
        )
    except (ConfigError, UnicodeDecodeError) as exc:
        raise ToolFailure(f"{registry_path} を読めない: {exc}") from exc
    versions = [
        entry.version
        for entry in entries
        if entry.policy_id == _POLICY_ID
        and entry.purpose is not None
        and entry.purpose.value == "STANDARD"
    ]
    if not versions:
        raise ToolFailure("登録簿に用途 STANDARD の研究ポリシーが無い")
    version = max(versions)
    path = f"{POLICY_DIR}/{_POLICY_ID}_v{version}.yaml"
    raw_text = view.read(path)
    if raw_text is None:
        raise ToolFailure(f"{path} が無い")
    try:
        text = raw_text.decode("utf-8")
        policy = load_research_policy(Path(path), policy_id=_POLICY_ID, version=version, text=text)
        raw = load_yaml_mapping(Path(path), text=text)
    except (ConfigError, UnicodeDecodeError) as exc:
        raise ToolFailure(f"{path} を読めない: {exc}") from exc
    return PolicyChoice(version=version, path=path, raw=raw, policy=policy)


def _metric_set_version(policy: ResearchPolicy) -> int:
    """評価基準が使う指標を持つ最小の指標集合の版（版 5 以上は #21〜#23 を持つ版。E11）。"""
    standard = policy.evaluation_standard
    if standard is None:
        raise ToolFailure(f"研究ポリシーの版 {policy.version} は評価基準の群を持たない")
    needed = set(standard_metrics(standard))
    if isinstance(standard, EvaluationStandardV5):
        needed |= set(METRIC_SET_V4_ADDED)
    for version in sorted(METRIC_SET_VERSIONS):
        if needed <= set(metric_ids_of(version)):
            return version
    raise ToolFailure("評価基準が使う指標を持つ指標集合の版が無い")


def _dig(raw: Mapping[str, Any], *keys: str) -> Any:
    value: Any = raw
    for key in keys:
        if not isinstance(value, Mapping) or key not in value:
            raise ToolFailure(
                f"研究ポリシーに `{'.'.join(keys)}` が無い（通貨ごとの形の版 4 以上）"
            )
        value = value[key]
    return value


def _comparison_values(choice: PolicyChoice, symbol: str) -> dict[str, Any]:
    """研究ポリシーの前提の記録と比較の前提から、その通貨の値を写す（照合 C1〜C7）。"""
    standard = _dig(choice.raw, "evaluation_standard")
    pair = _dig(standard, "basis", "symbols", symbol)
    conversion = _dig(standard, "basis", "conversion_snapshots")
    fixed = _dig(standard, "comparison_basis")
    costs = _dig(fixed, "symbols", symbol)
    account = _dig(fixed, "account")
    return {
        "snapshots": {"standard": pair["standard"], "candidate": pair["candidate"]},
        "conversion_snapshots": {
            "standard": conversion["standard"],
            "candidate": conversion["candidate"],
        },
        "account_currency": account["currency"],
        "initial_balance": account["initial_balance"],
        "risk_policy": {
            "trial_risk_rate": _dig(fixed, "risk_policy", "trial_risk_rate"),
            "account_risk_cap": _dig(fixed, "risk_policy", "account_risk_cap"),
        },
        "entry_delay_bars": _dig(fixed, "entry_delay_bars"),
        "adverse_fill_limit": costs["adverse_fill_limit"],
        "cost_model": {
            "commission_per_unit": _dig(fixed, "commission_per_unit"),
            "entry_slippage": costs["entry_slippage"],
            "close_slippage": costs["close_slippage"],
            "spread_offset": costs["spread_offset"],
        },
        "conversion_policy": {
            "pivot_currency": _dig(fixed, "conversion_policy", "pivot_currency"),
            "max_observation_skew": _dig(fixed, "conversion_policy", "max_observation_skew"),
        },
    }


# --- 版ごとの材料 -----------------------------------------------------------------------


def load_changes(view: RepoView, card_id: str, up_to: int) -> dict[int, bytes]:
    """版 2〜`up_to` の材料の変更の提案（`research/loops/<カード>/change_v<版>.yaml`）を読む。"""
    found: dict[int, bytes] = {}
    for version in range(2, up_to + 1):
        raw = view.read(change_path(card_id, version))
        if raw is None:
            raise ToolFailure(f"{change_path(card_id, version)} が無い")
        found[version] = raw
    return found


def state_at(
    card: Card, version: int, changes: Mapping[int, bytes]
) -> tuple[LoopState, list[Change]]:
    """版 `version` の材料（版 1 の材料に、版 2〜`version` の変更の提案を順に当てたもの）。"""
    state = initial_state(card)
    applied: list[Change] = []
    for target in range(2, version + 1):
        raw = changes.get(target)
        if raw is None:
            raise ToolFailure(f"版 {target} の変更の提案が無い")
        change = parse_change(raw, card_id=card.card_id, expected_from_version=target - 1)
        state = apply_change(state, change)
        applied.append(change)
    return state, applied


def _lineage(view: RepoView, card: Card, version: int, change: Change) -> dict[str, Any]:
    """前の版の実行記録から系譜を書く（記録票の識別子と実行番号。D13 §7.1）。"""
    path = run_record_path(card.card_id, version - 1)
    record = _read_yaml(view, path)
    record_id, execution = record.get("record_id"), record.get("execution")
    if (
        not isinstance(record_id, str)
        or isinstance(execution, bool)
        or not isinstance(execution, int)
    ):
        raise ToolFailure(f"{path}: `record_id`・`execution` が読めない")
    if not change.reason:
        raise ToolFailure("変更の理由の文が無い")
    return {
        "derived_from": {"experiment_id": record_id, "execution": execution},
        "change_kinds": [change.change_kind],
        "note": change.reason,
    }


# --- 生成 -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Preregistration:
    """版 1 つ分の生成の結果。`files` はこの版で足す生成物（パス → バイト列）。"""

    card_id: str
    version: int
    experiment_id: str
    strategy_version: int
    strategy_path: str
    experiment_path: str
    files: Mapping[str, bytes]
    card_sha256: str
    change_sha256: str | None
    generator_digest: str
    policy_version: int
    trial_count: int


def _strategy_document(card: Card, state: LoopState, generator: str) -> bytes:
    data: dict[str, Any] = {
        "schema_version": 1,
        "strategy_id": card.experiment_id,
        "version": state.strategy_version,
    }
    data.update(state.strategy)
    return dump_document(data, comments=_comments(card, generator, "戦略の宣言"))


def _trial_count(state: LoopState) -> int:
    count = 1
    for axis in state.axes:
        count *= len(axis.values)
    return count


def _experiment_document(
    view: RepoView,
    card: Card,
    version: int,
    state: LoopState,
    change: Change | None,
    generator: str,
) -> tuple[bytes, int, int]:
    template = load_template(view, card.symbol)
    choice = select_policy(view)
    values = _comparison_values(choice, card.symbol)
    data: dict[str, Any] = {
        "schema_version": 2,
        "id": card.experiment_id,
        "version": version,
    }
    if change is not None:
        data["lineage"] = _lineage(view, card, version, change)
    data["hypothesis"] = card.rule + HYPOTHESIS_SUFFIX
    data["research_policy"] = {"id": _POLICY_ID, "version": choice.version}
    data["strategy"] = strategy_path(card.experiment_id, state.strategy_version)
    data["environment"] = dict(template.environment)
    data["snapshots"] = values["snapshots"]
    # 決済通貨（通貨の組の後ろの 3 文字）が口座通貨と違う実験だけが換算の snapshot を書く
    # （D06 §19.2。書くかどうかは読込が確かめる）。
    if card.symbol[3:] != values["account_currency"]:
        data["conversion_snapshots"] = values["conversion_snapshots"]
    data["execution_series"] = template.execution_series
    data["resolution_hierarchy"] = list(template.resolution_hierarchy)
    data["seed"] = template.seed
    data["account"] = {
        "account_id": template.account_id,
        "currency": values["account_currency"],
        "initial_balance": values["initial_balance"],
    }
    data["risk_policy"] = values["risk_policy"]
    data["execution_policy"] = {
        "entry_delay_bars": values["entry_delay_bars"],
        "entry_valid_for": template.execution_policy["entry_valid_for"],
        "close_valid_for": template.execution_policy["close_valid_for"],
        "adverse_fill_limits": {card.symbol: values["adverse_fill_limit"]},
        "reference_quote_source": template.execution_policy["reference_quote_source"],
    }
    data["cost_model"] = values["cost_model"]
    data["conversion_policy"] = values["conversion_policy"]
    data["evaluation"] = {"metric_set_version": _metric_set_version(choice.policy)}
    trials = _trial_count(state)
    data["search_plan"] = {
        "kind": "GRID",
        "max_trials": trials,
        "axes": [axis.as_mapping() for axis in state.axes],
    }
    data["split"] = "STANDARD"
    data["final_holdout"] = {
        "interval": {"start": FINAL_HOLDOUT_START, "end": FINAL_HOLDOUT_END},
        "purpose": f"順 11 の実行器のカード {card.card_id} の研究候補の最終検証（D09 §9.10）",
    }
    if card.favored_regimes is not None:
        data["favored_regimes"] = list(card.favored_regimes)
    document = dump_document(data, comments=_comments(card, generator, "実験設定"))
    return document, trials, choice.version


def generate(
    view: RepoView,
    *,
    card: bytes,
    version: int,
    changes: Mapping[int, bytes] | None = None,
) -> Preregistration:
    """版 `version` の事前登録を作る。

    `changes` は版 2〜`version` の変更の提案（版 → バイト列）。省くと `view` の
    `research/loops/<カード>/change_v<版>.yaml` を読む。カードと変更の提案が書式に合わなければ
    `CardFormatError`・`ChangeRejected`、型板・研究ポリシー・実行記録が読めなければ `ToolFailure`。
    """
    if not 1 <= version <= 3:
        raise ToolFailure(f"版は 1〜3（{version}）")
    parsed = parse_card(card)
    materials = (
        dict(changes) if changes is not None else load_changes(view, parsed.card_id, version)
    )
    state, applied = state_at(parsed, version, materials)
    generator = generator_digest()
    current = applied[-1] if applied else None
    document, trials, policy_version = _experiment_document(
        view, parsed, version, state, current, generator
    )
    files: dict[str, bytes] = {}
    strategy_file = strategy_path(parsed.experiment_id, state.strategy_version)
    if version == 1 or (current is not None and current.change_kind == "STRATEGY_CODE"):
        files[strategy_file] = _strategy_document(parsed, state, generator)
    files[experiment_path(parsed.experiment_id, version)] = document
    return Preregistration(
        card_id=parsed.card_id,
        version=version,
        experiment_id=parsed.experiment_id,
        strategy_version=state.strategy_version,
        strategy_path=strategy_file,
        experiment_path=experiment_path(parsed.experiment_id, version),
        files=files,
        card_sha256=hashlib.sha256(card).hexdigest(),
        change_sha256=None if current is None else hashlib.sha256(current.raw).hexdigest(),
        generator_digest=generator,
        policy_version=policy_version,
        trial_count=trials,
    )


def regenerate(
    *, card: bytes, change: bytes | None, version: int, head: RepoView
) -> Mapping[str, bytes]:
    """CI の検査 4（作り直しの一致。PR 6 の `tools.ops.runner_ci.regen.Regenerator`）の呼び口。

    `change` は版 `version` の材料の変更の提案（版 1 は `None`）。それより前の版の変更の提案と
    実行記録は `head`（PR の先頭の時点）から読む。戻り値は生成物のパス → バイト列。
    """
    parsed = parse_card(card)
    materials = load_changes(head, parsed.card_id, version - 1) if version >= 2 else {}
    if version >= 2:
        if change is None:
            raise ToolFailure(f"版 {version} の変更の提案が渡されていない")
        materials[version] = change
    return generate(head, card=card, version=version, changes=materials).files


# --- 事前登録の注記 -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PreregChecks:
    """実行器が生成の後に当てた確かめの合否と件数と時間（注記に書く。D25 §3.8）。

    読込・全試行のコンパイル・照合（本パッケージの `screening.verify_preregistration`）と、準備の
    確かめ（`composition.prepare_search`。snapshot を開くので実行器が行う）。成績の数値を持たない。
    """

    trial_count: int
    rejected_count: int
    seconds: int
    max_rss_bytes: int


def prereg_note(prereg: Preregistration, checks: PreregChecks) -> bytes:
    """事前登録の注記 `research/loops/<カード>/prereg_v<版>.yaml` の本文（D25 §2.3・§3.4 の 2）。

    項目は PR 6 の検査 5（記録の書式）の形に合わせる。合否は通ったものだけを書く（通らなければ
    注記を作らず、カードの棄却か停止にする）。
    """
    data: dict[str, Any] = {
        "schema_version": 1,
        "card": prereg.card_id,
        "version": prereg.version,
        "experiment_id": prereg.experiment_id,
        "card_sha256": prereg.card_sha256,
        "change_sha256": prereg.change_sha256,
        "generator_digest": prereg.generator_digest,
        "generated": [
            {"path": path, "sha256": hashlib.sha256(body).hexdigest()}
            for path, body in sorted(prereg.files.items())
        ],
        "checks": {
            "load": "PASS",
            "compile": "PASS",
            "prepare": "PASS",
            "trial_count": checks.trial_count,
            "rejected_count": checks.rejected_count,
            "seconds": checks.seconds,
            "max_rss_bytes": checks.max_rss_bytes,
        },
    }
    return dump_document(data)
