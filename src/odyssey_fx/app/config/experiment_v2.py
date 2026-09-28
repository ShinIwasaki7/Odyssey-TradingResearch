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
from odyssey_fx.app.config.research_policy import load_research_policy, research_policy_path
from odyssey_fx.app.config.strategy_file import load_strategy_file
from odyssey_fx.app.config.strategy_parts import parse_series
from odyssey_fx.app.config.symbols import load_symbol_spec, symbol_spec_files
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.refs import PolicyRef
from odyssey_fx.common.symbol import Symbol, SymbolSpec
from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.domain.research_policy import ResearchPolicy
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.schedule import (
    DelayRule,
    DelayScenario,
    FixedSeriesDelay,
    InjectedBarDelay,
    SeededRandomDelay,
)
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from odyssey_fx.strategy.catalog.registry import ComponentRegistry
from odyssey_fx.strategy.declarations.duration import format_duration, parse_duration
from odyssey_fx.strategy.declarations.evaluation import OnBarClose
from odyssey_fx.strategy.declarations.refs import MarketDataRef

__all__ = [
    "SEARCH_PLAN_NONE",
    "SPLIT_NONE",
    "SUPPORTED_SCHEMA_VERSIONS",
    "TEXT_ROLES",
    "ExperimentEnvironment",
    "ExperimentV2",
    "ResearchPolicyRef",
    "experiment_schema_version",
    "experiment_v2_from_texts",
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
    パスは人が辿るための記録として持つ。記録票の本文から組み立て直したとき（D07 §21.2 の
    手順3）はパスが無いので `None` である。
    """

    calendar: TradingCalendar
    timeframe_defs: Mapping[str, TimeframeDefinition]
    symbol_specs: Mapping[Symbol, SymbolSpec]
    calendar_path: Path | None
    timeframes_path: Path | None
    symbols_path: Path | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "timeframe_defs", MappingProxyType(dict(self.timeframe_defs)))
        object.__setattr__(self, "symbol_specs", MappingProxyType(dict(self.symbol_specs)))


@dataclass(frozen=True, slots=True)
class ExperimentV2:
    """書式 v2 の実験設定の読込結果（D07 §18）。

    `experiment` は書式 v1 と同じ解決済みの値（`ConfigDigest` の材料）であり、残りの項目は
    `ConfigDigest` に入らない（D07 §18.2）。

    実験の記録票（D07 §19.2）の材料も持つ。`policy` は読んだ研究ポリシー、`values` は実験
    設定の YAML を読み込んだ値（記録票の識別の入力 2 の元）、`texts` は役割名
    （`experiment` / `strategy` / `research_policy` / `calendar` / `timeframes`）から本文への
    対応、`symbol_texts` は読んだ銘柄仕様の本文である（記録票に入れるのは実行と換算に使う
    銘柄のものだけで、その選択は合成が行う）。
    """

    experiment: ExperimentConfig
    hypothesis: str
    research_policy: ResearchPolicyRef
    strategy_path: Path | None
    environment: ExperimentEnvironment
    metric_set_version: int
    search_plan: str
    split: str
    policy: ResearchPolicy
    values: Mapping[str, Any]
    texts: Mapping[str, str]
    symbol_texts: Mapping[Symbol, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", _frozen(self.values))
        object.__setattr__(self, "texts", MappingProxyType(dict(self.texts)))
        object.__setattr__(self, "symbol_texts", MappingProxyType(dict(self.symbol_texts)))


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


def _series_the_run_reads(experiment: ExperimentConfig) -> frozenset[SeriesId]:
    """run が読む系列: 戦略が入力・起動条件で読む系列、執行系列、解像度階層の各段。"""
    found: set[SeriesId] = {
        experiment.execution_series,
        *experiment.execution_policy.resolution_hierarchy.levels,
    }
    for instance in experiment.strategy.components:
        for binding in instance.inputs.values():
            found.update(
                source.series for source in binding.sources if isinstance(source, MarketDataRef)
            )
        found.update(
            trigger.series
            for trigger in instance.evaluation.triggers
            if isinstance(trigger, OnBarClose)
        )
    return frozenset(found)


def _reject_rules_hitting_nothing(
    scenario: DelayScenario,
    experiment: ExperimentConfig,
    calendar: TradingCalendar,
    timeframe_defs: Mapping[str, TimeframeDefinition],
    path: Path,
) -> None:
    """どの足にも当たらない遅延規則を読込時に拒否する（段階4 実装 PR 4 の仮置き）。

    当たらない規則は run を遅延なしと同じに動かすのに、遅延シナリオの版参照は遅延ありとして
    manifest と `run_id` に記録される（PR #45 のレビューで PR 4 へ送られた指摘）。読込の時点で
    設定ファイルだけから分かる2つを拒否する。

    - **run が読まない系列への規則**。遅延は戦略向けの公開時刻だけを動かすので、戦略が読まず、
      執行系列でも解像度階層の段でもない系列に当てても、何も変わらない。
    - **足の開始でない時刻への特定の足の遅延**（`INJECTED_BAR_DELAY`）。足の鍵は開始時刻との
      完全一致で引く（D03 §3.6）ので、時間足の整列とカレンダーで決まる足の開始でない時刻
      （例: 日足 NY17 の 21:00Z）はどの足にも当たらない。

    snapshot に足が実在するかは読込では分からない（snapshot を開かない）。
    """
    read = _series_the_run_reads(experiment)
    for index, rule in enumerate(scenario.rules):
        label = f"delay_scenario.rules[{index}]"
        if not isinstance(rule, (FixedSeriesDelay, InjectedBarDelay)):  # pragma: no cover
            continue
        if rule.series not in read:
            raise ConfigError(
                f"{path}: {label} の系列 {rule.series} はこの run が読まない（戦略の入力・"
                "起動条件・執行系列・解像度階層のどれでもない）。当てても何も変わらないのに、"
                "遅延ありの run として記録される"
            )
        if isinstance(rule, InjectedBarDelay):
            definition = timeframe_defs[rule.series.timeframe.id]
            interval = definition.expected_interval(calendar, rule.bar_start)
            if interval is None or interval.start != rule.bar_start:
                raise ConfigError(
                    f"{path}: {label} の bar_start {rule.bar_start} は {rule.series} の足の開始"
                    "ではない（時間足の整列とカレンダーで決まる足の開始と一致しない、または休場）。"
                    "足は開始時刻との完全一致で引くので、この規則はどの足にも当たらない（D03 §3.6）"
                )


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


#: 記録票の `resolved_files` の役割名（D07 §19.2）。
ROLE_EXPERIMENT: Final = "experiment"
ROLE_STRATEGY: Final = "strategy"
ROLE_RESEARCH_POLICY: Final = "research_policy"
ROLE_CALENDAR: Final = "calendar"
ROLE_TIMEFRAMES: Final = "timeframes"

#: 本文から組み立て直すときに揃っていなければならない役割（銘柄仕様は別に渡す）。
TEXT_ROLES: Final = (
    ROLE_EXPERIMENT,
    ROLE_STRATEGY,
    ROLE_RESEARCH_POLICY,
    ROLE_CALENDAR,
    ROLE_TIMEFRAMES,
)


def _frozen(value: Any) -> Any:
    """YAML から読んだ値を、書き換えられない形（mapping の読み取り専用の写しと tuple）にする。"""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _frozen(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_frozen(item) for item in value)
    return value


def _read_text(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"`{label}` が指す {path} を読めない: {exc}") from exc


def _validated(
    payload: dict[str, Any], path: Path, metric_set_versions: Collection[int]
) -> _ExperimentV2Model:
    """書式 v2 の形と、読込時に拒否する値（D07 §18.6）を確かめる。"""
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
    return model


def _assemble(
    *,
    path: Path,
    payload: dict[str, Any],
    model: _ExperimentV2Model,
    texts: Mapping[str, str],
    labels: Mapping[str, Path],
    symbol_texts: Mapping[str, tuple[Path, str]],
    paths: tuple[Path | None, Path | None, Path | None, Path | None],
    registry: ComponentRegistry,
) -> ExperimentV2:
    """読んだ本文から書式 v2 の読込結果を組み立てる。

    ファイルから読むときも、記録票の本文から読むときも同じ関数を通す。
    """
    calendar_path, timeframes_path, symbols_path, strategy_path = paths
    timeframe_defs = load_timeframes(labels[ROLE_TIMEFRAMES], text=texts[ROLE_TIMEFRAMES])
    specs: dict[Symbol, SymbolSpec] = {}
    for label, text in symbol_texts.values():
        spec = load_symbol_spec(label, text=text)
        if spec.symbol in specs:  # pragma: no cover - ファイル名と銘柄の一致を強制済み
            raise ConfigError(f"{label}: 銘柄 {spec.symbol} の仕様が2度現れる")
        specs[spec.symbol] = spec
    if not specs:
        raise ConfigError(f"{path}: 銘柄仕様（`*.yaml`）が1件も無い（D03 §9）")
    environment = ExperimentEnvironment(
        calendar=load_calendar(labels[ROLE_CALENDAR], text=texts[ROLE_CALENDAR]),
        timeframe_defs=timeframe_defs,
        symbol_specs=specs,
        calendar_path=calendar_path,
        timeframes_path=timeframes_path,
        symbols_path=symbols_path,
    )
    strategy = load_strategy_file(
        labels[ROLE_STRATEGY], registry, timeframe_defs, text=texts[ROLE_STRATEGY]
    )
    policy = load_research_policy(
        labels[ROLE_RESEARCH_POLICY],
        policy_id=model.research_policy.id,
        version=model.research_policy.version,
        text=texts[ROLE_RESEARCH_POLICY],
    )

    if model.delay_scenario is None:
        delay_scenario = None
        delay_ref = NO_DELAY_REF
    else:
        delay_scenario = _delay_scenario_of(model.delay_scenario, timeframe_defs, path)
        delay_ref = _delay_ref_of(delay_scenario, path)

    experiment = resolve_run_body(
        model,
        payload,
        path,
        timeframe_defs,
        experiment_id=model.id,
        version=model.version,
        strategy=strategy,
        delay_scenario=delay_scenario,
        delay_ref=delay_ref,
    )
    if delay_scenario is not None:
        _reject_rules_hitting_nothing(
            delay_scenario, experiment, environment.calendar, timeframe_defs, path
        )
    return ExperimentV2(
        experiment=experiment,
        hypothesis=model.hypothesis,
        research_policy=ResearchPolicyRef(
            policy_id=model.research_policy.id, version=model.research_policy.version
        ),
        strategy_path=strategy_path,
        environment=environment,
        metric_set_version=model.evaluation.metric_set_version,
        search_plan=model.search_plan,
        split=model.split,
        policy=policy,
        values=payload,
        texts=texts,
        # ファイル名（拡張子を除く）と銘柄の一致は `load_symbol_spec` が強制している。
        symbol_texts={symbol: symbol_texts[str(symbol)][1] for symbol in specs},
    )


def load_experiment_v2(
    path: Path,
    *,
    repo_root: Path,
    registry: ComponentRegistry,
    metric_set_versions: Collection[int],
) -> ExperimentV2:
    """書式 v2 の実験設定を読む（D07 §18）。

    `repo_root` はパス（戦略ファイル・環境の3つ・研究ポリシーファイル）を解決する基点。
    `metric_set_versions` はこの実装が式を持つ指標集合の版で、評価の実装から呼び出し側が渡す
    （`app.config` が評価の版を決め打ちしないため）。

    研究ポリシーは版参照 `{id, version}` から `configs/policies/research/<id>_v<version>.yaml`
    を読む（D07 §20.2）。
    """
    if not path.is_file():
        raise ConfigError(f"設定ファイルが見つからない: {path}")
    experiment_text = _read_text(path, "--experiment")
    payload: dict[str, Any] = load_yaml_mapping(path, text=experiment_text)
    model = _validated(payload, path, metric_set_versions)

    calendar_path = _resolve_path(
        repo_root, model.environment.calendar, "environment.calendar", path
    )
    timeframes_path = _resolve_path(
        repo_root, model.environment.timeframes, "environment.timeframes", path
    )
    symbols_path = _resolve_path(repo_root, model.environment.symbols, "environment.symbols", path)
    strategy_path = _resolve_path(repo_root, model.strategy, "strategy", path)
    policy_path = research_policy_path(
        repo_root.resolve(), model.research_policy.id, model.research_policy.version
    )
    if not policy_path.is_file():
        raise ConfigError(
            f"{path}: 研究ポリシー {model.research_policy.id} v{model.research_policy.version}"
            f" のファイル {policy_path} が無い（D07 §20.2）"
        )
    if not symbols_path.is_dir():
        raise ConfigError(f"銘柄仕様のディレクトリが見つからない: {symbols_path}")

    labels = {
        ROLE_EXPERIMENT: path,
        ROLE_STRATEGY: strategy_path,
        ROLE_RESEARCH_POLICY: policy_path,
        ROLE_CALENDAR: calendar_path,
        ROLE_TIMEFRAMES: timeframes_path,
    }
    texts = {ROLE_EXPERIMENT: experiment_text}
    for role in TEXT_ROLES[1:]:
        texts[role] = _read_text(labels[role], role)
    symbol_texts = {
        file.stem: (file, _read_text(file, "environment.symbols"))
        for file in symbol_spec_files(symbols_path)
    }
    return _assemble(
        path=path,
        payload=payload,
        model=model,
        texts=texts,
        labels=labels,
        symbol_texts=symbol_texts,
        paths=(calendar_path, timeframes_path, symbols_path, strategy_path),
        registry=registry,
    )


def experiment_v2_from_texts(
    texts: Mapping[str, str],
    symbol_texts: Mapping[str, str],
    *,
    registry: ComponentRegistry,
    metric_set_versions: Collection[int],
) -> ExperimentV2:
    """記録票が保存した本文から書式 v2 の読込結果を組み立てる（D07 §21.2 の手順3）。

    本文を**ファイルシステムへ書き戻さずに**、ファイルから読むときと同じ読込条件と検証を通す。
    `texts` は役割名から本文への対応（`TEXT_ROLES` がすべて要る）、`symbol_texts` は銘柄名から
    銘柄仕様の本文への対応。実験設定の本文にあるパス（`strategy` と `environment`）は使わない
    （パスは識別に入らない。D07 §19.2）。
    """
    missing = [role for role in TEXT_ROLES if role not in texts]
    if missing:
        raise ConfigError(f"記録票の本文に役割 {missing} が無い（D07 §19.2）")
    labels = {role: Path(f"resolved_files[{role}]") for role in TEXT_ROLES}
    path = labels[ROLE_EXPERIMENT]
    payload: dict[str, Any] = load_yaml_mapping(path, text=texts[ROLE_EXPERIMENT])
    model = _validated(payload, path, metric_set_versions)
    return _assemble(
        path=path,
        payload=payload,
        model=model,
        texts=texts,
        labels=labels,
        symbol_texts={
            name: (Path(f"{name}.yaml"), text) for name, text in sorted(symbol_texts.items())
        },
        paths=(None, None, None, None),
        registry=registry,
    )
