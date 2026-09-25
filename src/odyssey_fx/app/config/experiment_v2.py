"""実験設定の書式 v2 の読込（D07 §18、2026-09-25 の人間の決定4・Q11・Q12）。

書式 v2（`schema_version: 2`）は、書式 v1 のキー（実行の本体）に次を足す（D07 §18.2）。

| キー | 何を書くか |
|---|---|
| `hypothesis` | 検証したい仮説（必須。空白だけは拒否） |
| `research_policy` | 研究ポリシーファイルの版参照 `{id, version}`（必須） |
| `strategy` | 戦略ファイルへのパス（必須。Q11 決定） |
| `environment` | 取引カレンダー・時間足定義・銘柄仕様の3つのパス（必須） |
| `delay_scenario` | 遅延シナリオ（任意。**書かないことだけが遅延なし**） |
| `evaluation` | 指標集合の版 `{metric_set_version}`（必須） |
| `search_plan` / `split` | 段階4 で書けるのは `NONE` だけ（必須） |

**パスはリポジトリの根からの相対パス**で書く（D07 §18.2）。読込時に実体を読む。

**`id` / `version` / `hypothesis` / `research_policy` / `evaluation` / `search_plan` / `split`
は `ConfigDigest` に入らない**（D07 §18.2）。これらは `ExperimentConfig` に載せず、本モジュールの
読込結果 `ExperimentV2` の側に置く。実行の本体は書式 v1 と同じ関数（`resolve_run_body`）で
解決するので、同じ実行条件を書けば v1 と同じ `ConfigDigest` になる（D07 §18.5）。

**読込時に拒否するもの**（D07 §18.6）: 未宣言キー・型不一致・`schema_version` が 1・2 以外、
空の仮説、`NONE` 以外の探索計画・分割、空の遅延規則・確率的遅延・負の遅延、無い・読めない
パス、この実装の持たない指標集合の版。研究ポリシーの検査（D07 §20）と実行前のデータ能力検査
（D06 §10.5）は読込では行わない。
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Final, Literal

from pydantic import Field

from odyssey_fx.app.config.calendars import load_calendar, load_timeframes
from odyssey_fx.app.config.experiment import (
    NO_DELAY_REF,
    ExperimentConfig,
    RunBodyModel,
    resolve_run_body,
)
from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.app.config.strategy_file import load_strategy_file
from odyssey_fx.app.config.strategy_parts import parse_series
from odyssey_fx.app.config.symbols import load_symbol_specs
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.refs import PolicyRef
from odyssey_fx.common.symbol import Symbol, SymbolSpec
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.schedule import (
    DelayRule,
    DelayScenario,
    FixedSeriesDelay,
    InjectedBarDelay,
    SeededRandomDelay,
)
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from odyssey_fx.strategy.catalog.registry import ComponentRegistry
from odyssey_fx.strategy.declarations.duration import format_duration, parse_duration

__all__ = [
    "SEARCH_PLAN_NONE",
    "SPLIT_NONE",
    "SUPPORTED_SCHEMA_VERSIONS",
    "ExperimentEnvironment",
    "ExperimentV2",
    "ResearchPolicyRef",
    "experiment_schema_version",
    "load_experiment_v2",
]

#: この実装が読む実験設定の形式版（D07 §18.5。v1 の読込は残す、Q12 決定）。
SUPPORTED_SCHEMA_VERSIONS: Final[frozenset[int]] = frozenset({1, 2})

#: 書式 v2 の版。
_SCHEMA_VERSION = 2

#: 段階4 で書ける探索計画・分割の語彙（D07 §18.2）。単一実行であることの明示であり、
#: 段階5 で D09 が語彙を足す。
SEARCH_PLAN_NONE: Final = "NONE"
SPLIT_NONE: Final = "NONE"

#: 書式でも受け付けない遅延規則の区分（D03 §3.6、D07 §18.3）。
_SEEDED_RANDOM_DELAY: Final = "SEEDED_RANDOM_DELAY"


# --- 設定ファイルの形（Pydantic）--------------------------------------------


class _ResearchPolicyRefModel(StrictModel):
    id: str
    version: int


class _EnvironmentModel(StrictModel):
    calendar: str
    timeframes: str
    symbols: str


class _EvaluationModel(StrictModel):
    metric_set_version: int


class _FixedSeriesDelayModel(StrictModel):
    kind: Literal["FIXED_SERIES_DELAY"]
    series: str
    delay: str


class _InjectedBarDelayModel(StrictModel):
    kind: Literal["INJECTED_BAR_DELAY"]
    series: str
    bar_start: str
    delay: str


class _DelayScenarioModel(StrictModel):
    id: str
    version: int
    rules: list[
        Annotated[_FixedSeriesDelayModel | _InjectedBarDelayModel, Field(discriminator="kind")]
    ]


class _ExperimentV2Model(RunBodyModel):
    schema_version: int
    id: str
    version: int
    hypothesis: str
    research_policy: _ResearchPolicyRefModel
    strategy: str
    environment: _EnvironmentModel
    evaluation: _EvaluationModel
    search_plan: str
    split: str
    delay_scenario: _DelayScenarioModel | None = None


# --- 読込結果（外へ出る型）---------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResearchPolicyRef:
    """研究ポリシーファイルの版参照（D07 §18.2・§20.2）。

    書式 v2 は版参照だけを持つ。ファイルの読込と検査は実験の記録票と研究ポリシーの実装
    （D07 §19・§20）が行う。
    """

    policy_id: str
    version: int


@dataclass(frozen=True, slots=True)
class ExperimentEnvironment:
    """`environment` の3つのパスから読んだもの（D07 §18.2）。

    パスそのものは識別に使わない（同じ内容を別の場所に置いても同じ実験である。D07 §18.2）。
    パスは人が辿るための記録として持つ。
    """

    calendar: TradingCalendar
    timeframe_defs: Mapping[str, TimeframeDefinition]
    symbol_specs: Mapping[Symbol, SymbolSpec]
    calendar_path: Path
    timeframes_path: Path
    symbols_path: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "timeframe_defs", MappingProxyType(dict(self.timeframe_defs)))
        object.__setattr__(self, "symbol_specs", MappingProxyType(dict(self.symbol_specs)))


@dataclass(frozen=True, slots=True)
class ExperimentV2:
    """書式 v2 の実験設定の読込結果（D07 §18）。

    `experiment` は書式 v1 と同じ解決済みの値（`ConfigDigest` の材料）であり、残りの項目は
    `ConfigDigest` に入らない（D07 §18.2）。
    """

    experiment: ExperimentConfig
    hypothesis: str
    research_policy: ResearchPolicyRef
    strategy_path: Path
    environment: ExperimentEnvironment
    metric_set_version: int
    search_plan: str
    split: str


# --- 読込 -------------------------------------------------------------------


def experiment_schema_version(path: Path) -> int:
    """実験設定の形式版を読む（D07 §18.5 の「`schema_version` で分岐する」）。

    1・2 以外は拒否する（D07 §18.6 の1）。中身の検証は各版の読込が行う。
    """
    payload = load_yaml_mapping(path)
    version = payload["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise ConfigError(f"{path}: `schema_version` は整数で書くこと（{version!r}）")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ConfigError(
            f"{path}: `schema_version` が {version} だが、この実装が読む実験設定の版は"
            f" {sorted(SUPPORTED_SCHEMA_VERSIONS)} である（未知の版は拒否する、D01 §10.1）"
        )
    return version


def _resolve_path(repo_root: Path, text: str, label: str, path: Path) -> Path:
    """リポジトリの根からの相対パスを実体の位置へ解決する（D07 §18.2）。

    絶対パスと、根の外を指すパスは拒否する。根の外を指せると、同じ設定ファイルが置き場所
    によって別のファイルを読むことになる。
    """
    relative = Path(text)
    if relative.is_absolute():
        raise ConfigError(f"{path}: `{label}` はリポジトリの根からの相対パスで書くこと（{text!r}）")
    root = repo_root.resolve()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise ConfigError(f"{path}: `{label}` がリポジトリの根 {root} の外を指している（{text!r}）")
    if not resolved.exists():
        raise ConfigError(f"{path}: `{label}` が指す {resolved} が無い")
    return resolved


def _environment_of(model: _EnvironmentModel, repo_root: Path, path: Path) -> ExperimentEnvironment:
    calendar_path = _resolve_path(repo_root, model.calendar, "environment.calendar", path)
    timeframes_path = _resolve_path(repo_root, model.timeframes, "environment.timeframes", path)
    symbols_path = _resolve_path(repo_root, model.symbols, "environment.symbols", path)
    return ExperimentEnvironment(
        calendar=load_calendar(calendar_path),
        timeframe_defs=load_timeframes(timeframes_path),
        symbol_specs=load_symbol_specs(symbols_path),
        calendar_path=calendar_path,
        timeframes_path=timeframes_path,
        symbols_path=symbols_path,
    )


def _reject_unwritable_delay(payload: Mapping[str, Any], path: Path) -> None:
    """書式が受け付けない遅延の書き方を、理由の分かる形で先に拒否する（D07 §18.3・§18.6）。

    - `delay_scenario: null`: 遅延なしを表すのは**書かない形だけ**である。null も受けると
      同じ意味の書き方が2つになる。
    - 確率的遅延（`SEEDED_RANDOM_DELAY`）: D03 §3.6 のとおり初版では受け付けない。区分タグの
      検証に任せると「未知の区分」としか言えないので、理由をここで示す。
    """
    if "delay_scenario" not in payload:
        return
    scenario = payload["delay_scenario"]
    if scenario is None:
        raise ConfigError(
            f"{path}: `delay_scenario` に null は書けない。遅延なしは `delay_scenario` を"
            " 書かない形だけで表す（D07 §18.3）"
        )
    rules = scenario.get("rules") if isinstance(scenario, dict) else None
    if not isinstance(rules, list):
        return
    for index, rule in enumerate(rules):
        if isinstance(rule, dict) and rule.get("kind") == _SEEDED_RANDOM_DELAY:
            raise ConfigError(
                f"{path}: delay_scenario.rules[{index}]: 確率的遅延（{_SEEDED_RANDOM_DELAY}）は"
                " 初版では受け付けない（D03 §3.6、D07 §18.3）"
            )


def _delay_scenario_of(
    model: _DelayScenarioModel,
    timeframe_defs: Mapping[str, TimeframeDefinition],
    path: Path,
) -> DelayScenario:
    """遅延シナリオを型へ構築する（D03 §3.6、D07 §18.3）。

    遅延の期間は D04 §13.1 の `<正の整数><単位>` で書く（D07 §18.2）。この書き方は負の値も
    0 も書けないので、負の遅延は読込の時点で拒否される。規則の型も非負を構築時に検査する。

    遅延は戦略向けの公開時刻だけを動かす。執行系列に当てても約定の時刻は変わらない（D07
    §18.3、上位設計書 §4.7.12、D06 §6.4）。執行用データの可用性と戦略向けの公開遅延は別の
    ものなので、執行系列への遅延も受け付ける。
    """
    if not model.rules:
        raise ConfigError(
            f"{path}: `delay_scenario.rules` が空である。遅延なしは `delay_scenario` を"
            " 書かない形だけで表す（D07 §18.3）"
        )
    rules: list[FixedSeriesDelay | InjectedBarDelay] = []
    for index, rule in enumerate(model.rules):
        label = f"delay_scenario.rules[{index}]"
        try:
            series = parse_series(rule.series, timeframe_defs)
            delay = parse_duration(rule.delay)
            if isinstance(rule, _FixedSeriesDelayModel):
                rules.append(FixedSeriesDelay(series=series, delay=delay))
            else:
                rules.append(
                    InjectedBarDelay(
                        series=series, bar_start=UtcTime.parse(rule.bar_start), delay=delay
                    )
                )
        except (KernelValueError, ConfigError) as exc:
            raise ConfigError(f"{path}: {label} を読めない: {exc}") from exc
    normalized = [repr(_rule_declaration(rule)) for rule in rules]
    if len(set(normalized)) != len(normalized):
        raise ConfigError(f"{path}: `delay_scenario.rules` に同じ規則が2度書かれている")
    try:
        return DelayScenario(id=model.id, version=model.version, rules=tuple(rules))
    except KernelValueError as exc:
        raise ConfigError(f"{path}: 遅延シナリオを読めない: {exc}") from exc


def _rule_declaration(rule: DelayRule) -> dict[str, str]:
    """遅延規則1件の正規形（書き方の揺れを除いた宣言）。"""
    if isinstance(rule, SeededRandomDelay):  # pragma: no cover - 読込が拒否済み
        raise ConfigError("確率的遅延は初版では受け付けない（D03 §3.6）")
    declaration = {
        "kind": (
            "FIXED_SERIES_DELAY" if isinstance(rule, FixedSeriesDelay) else "INJECTED_BAR_DELAY"
        ),
        "series": str(rule.series),
        "delay": format_duration(rule.delay),
    }
    if isinstance(rule, InjectedBarDelay):
        declaration["bar_start"] = str(rule.bar_start)
    return declaration


def _delay_rules_declaration(scenario: DelayScenario) -> list[dict[str, str]]:
    """遅延シナリオの版参照のダイジェストの材料（D07 §18.3）。

    規則の並びは意味を持たない（同じ足に複数の規則が当たれば最大の遅延を採る。D03 §3.6）
    ので、**正規形の文字列順に整列**して入れる。書いた順を入れると、同じ実行条件が並べ替え
    だけで別の `ConfigDigest` と `run_id` になる（D07 §18.5）。期間は `format_duration` の
    正規形（`120s` と `2m` は同じ）、時刻は UTC の正規形にする。規則の重複は
    `_delay_scenario_of` が拒否している。
    """
    return sorted((_rule_declaration(rule) for rule in scenario.rules), key=repr)


def _delay_ref_of(scenario: DelayScenario, path: Path) -> PolicyRef:
    """遅延シナリオの版参照（D07 §18.3）。

    **シナリオの id と版をそのまま載せ、ダイジェストは規則の正規形から作る**（戦略の参照
    `StrategyRef` と同じ作り方）。manifest の参照からどのシナリオかが読める。遅延なしの
    参照（`NO_DELAY_REF`）は書式 v1 と同じ値のまま変えない（D07 §18.5）。
    """
    try:
        return PolicyRef(
            policy_kind="delay",
            policy_id=scenario.id,
            version=scenario.version,
            digest=digest(_delay_rules_declaration(scenario)),
        )
    except KernelValueError as exc:
        raise ConfigError(
            f"{path}: 遅延シナリオの id と版は識別子として書くこと（小文字・数字・下線）: {exc}"
        ) from exc


def load_experiment_v2(
    path: Path,
    *,
    repo_root: Path,
    registry: ComponentRegistry,
    metric_set_versions: Collection[int],
) -> ExperimentV2:
    """書式 v2 の実験設定を読む（D07 §18）。

    `repo_root` はパス（戦略ファイル・環境の3つ）を解決する基点。`metric_set_versions` は
    この実装が式を持つ指標集合の版で、評価の実装から呼び出し側が渡す（`app.config` が評価の
    版を決め打ちしないため）。
    """
    payload: dict[str, Any] = load_yaml_mapping(path)
    _reject_unwritable_delay(payload, path)
    model = validate(_ExperimentV2Model, payload, path)
    require_schema_version(model.schema_version, _SCHEMA_VERSION, path)

    if not model.hypothesis.strip():
        raise ConfigError(f"{path}: `hypothesis` が空である。検証したい仮説を書くこと（D07 §18.2）")
    for key, value, allowed in (
        ("search_plan", model.search_plan, SEARCH_PLAN_NONE),
        ("split", model.split, SPLIT_NONE),
    ):
        if value != allowed:
            raise ConfigError(
                f"{path}: `{key}` が {value!r} だが、書式 v2 の段階4 の範囲で書けるのは"
                f" {allowed!r} だけである。探索計画と分割の語彙は段階5 で足す（D07 §18.2）"
            )
    metric_set_version = model.evaluation.metric_set_version
    if metric_set_version not in metric_set_versions:
        raise ConfigError(
            f"{path}: 指標集合の版 {metric_set_version} の式はこの実装に無い。"
            f" この実装が持つのは {sorted(metric_set_versions)} である（D07 §18.6）"
        )

    environment = _environment_of(model.environment, repo_root, path)
    strategy_path = _resolve_path(repo_root, model.strategy, "strategy", path)
    strategy = load_strategy_file(strategy_path, registry, environment.timeframe_defs)

    if model.delay_scenario is None:
        delay_scenario = None
        delay_ref = NO_DELAY_REF
    else:
        delay_scenario = _delay_scenario_of(
            model.delay_scenario,
            environment.timeframe_defs,
            path,
        )
        delay_ref = _delay_ref_of(delay_scenario, path)

    experiment = resolve_run_body(
        model,
        payload,
        path,
        environment.timeframe_defs,
        experiment_id=model.id,
        version=model.version,
        strategy=strategy,
        delay_scenario=delay_scenario,
        delay_ref=delay_ref,
    )
    return ExperimentV2(
        experiment=experiment,
        hypothesis=model.hypothesis,
        research_policy=ResearchPolicyRef(
            policy_id=model.research_policy.id, version=model.research_policy.version
        ),
        strategy_path=strategy_path,
        environment=environment,
        metric_set_version=metric_set_version,
        search_plan=model.search_plan,
        split=model.split,
    )
