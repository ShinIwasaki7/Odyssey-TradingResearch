"""カードの機械の検査（D25 §3.5）と、改善の変更の提案の検査（D25 §3.9）。

**カード**（段 1 の取り出しの直後と、発想役のカードを待ち行列に入れる前。第3.5節・第5.3節の 5）は、
次をこの順に当て、最初の不合格の理由の識別子で棄却する（棄却の型）。

| 順 | 検査 | 理由の識別子 |
|---|---|---|
| 1 | 書式（`card.parse_card`）、同じ識別子のカード・実験・戦略が無い（第3.2節） | `CARD_FORMAT` |
| 2 | 部品がカタログにあり、版が合う | `CARD_COMPONENT_UNKNOWN` |
| 3 | 履歴窓が日足 60 本以下（格子のすべての点で。`history`） | `CARD_HISTORY_TOO_LONG` |
| 4 | 格子が 4〜6 試行（決定 99）、研究ポリシーの試行の上限以下 | `CARD_GRID_SIZE` |
| 5 | 証拠の見込みが `UNLIKELY` でない | `CARD_EVIDENCE_UNLIKELY` |
| 6 | 既出の規則と重ならない（`normalize`。通貨の違いは数える。決定 173） | `CARD_DUPLICATE_RULE` |
| 7 | 生成・読込（照合 C1〜C7 を含む）・全試行のコンパイル・複雑さ | `CARD_PREREG_REJECTED` |

- 3 はコンパイルした試行の窓を測る。コンパイルに通らない試行は 3 では測らずに 4〜6 へ進み、7 で
  落ちる（本 PR の仮置き）。
- 7 の「準備の確かめ」（`composition.prepare_search`。snapshot を開く）は実行器（PR 8a）が行う。本
  モジュールは CI と同じく snapshot を読まない範囲（読込とコンパイル）だけを確かめる（D25 §3.8）。
- 型板・研究ポリシー・既存の宣言が読めないなど、カードの中身でない原因は `ToolFailure` を投げる
  （棄却にしない。`TOOL_FAILURE`）。

**変更の提案**（段 6）は `changes.parse_change` の静的な検査の後に操作を当て、(5) 部品・履歴窓・
試行 4〜6・既出の規則（同じカードの前の版を除く）を `CHANGE_OUT_OF_RULES`、生成・読込・コンパイル・
複雑さを `CHANGE_PREREG_REJECTED` で落とす（どれも停止の型。第3.5節の注記）。
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from odyssey_fx.app.composition import complexity_profiles, trial_strategy
from odyssey_fx.app.config.experiment_v2 import (
    ExperimentV2,
    experiment_v2_from_texts,
)
from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.research_policy import load_research_policy_registry, verify_registered
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.evaluation.domain.metrics import METRIC_SET_VERSIONS
from odyssey_fx.evaluation.domain.research_policy import (
    check_complexity,
    check_trial_count,
    measure_complexity,
)
from odyssey_fx.evaluation.domain.search import enumerate_assignments
from odyssey_fx.evaluation.domain.status import CheckOutcome
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.catalog.registry import ContractKey
from odyssey_fx.strategy.compiler.compiled import CompiledStrategy, CompileSucceeded
from odyssey_fx.strategy.compiler.validate import compile_strategy
from tools.ops.hypothesis_cards.card import Card, CardFormatError, parse_card
from tools.ops.hypothesis_cards.changes import (
    CHANGE_OUT_OF_RULES,
    CHANGE_PREREG_REJECTED,
    Change,
    ChangeRejected,
    LoopState,
    apply_change,
    parse_change,
)
from tools.ops.hypothesis_cards.frozen import thaw
from tools.ops.hypothesis_cards.generate import (
    Preregistration,
    change_path,
    experiment_path,
    generate,
    select_policy,
    state_at,
)
from tools.ops.hypothesis_cards.history import HISTORY_LIMIT_DAILY_BARS, measure_history
from tools.ops.hypothesis_cards.normalize import NormalizationError, rule_digest
from tools.ops.hypothesis_cards.repo import (
    CARD_PLACES,
    EXPERIMENTS_DIR,
    HYPOTHESES_DIR,
    POLICY_DIR,
    STRATEGIES_DIR,
    RepoView,
    ToolFailure,
)

__all__ = [
    "CARD_COMPONENT_UNKNOWN",
    "CARD_DUPLICATE_RULE",
    "CARD_EVIDENCE_UNLIKELY",
    "CARD_FORMAT",
    "CARD_GRID_SIZE",
    "CARD_HISTORY_TOO_LONG",
    "CARD_PREREG_REJECTED",
    "GRID_TRIALS",
    "ChangeScreening",
    "Screening",
    "Verification",
    "check_change",
    "known_rule_digests",
    "screen_card",
    "verify_preregistration",
]

CARD_FORMAT: Final = "CARD_FORMAT"
CARD_COMPONENT_UNKNOWN: Final = "CARD_COMPONENT_UNKNOWN"
CARD_HISTORY_TOO_LONG: Final = "CARD_HISTORY_TOO_LONG"
CARD_GRID_SIZE: Final = "CARD_GRID_SIZE"
CARD_EVIDENCE_UNLIKELY: Final = "CARD_EVIDENCE_UNLIKELY"
CARD_DUPLICATE_RULE: Final = "CARD_DUPLICATE_RULE"
CARD_PREREG_REJECTED: Final = "CARD_PREREG_REJECTED"
#: 1 実験の試行の数（決定 99。格子 4〜6）。
GRID_TRIALS: Final = range(4, 7)


# --- 読込と全試行のコンパイル ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Verification:
    """生成物の読込と全試行のコンパイルの結果（`problems` が空なら合格）。"""

    problems: tuple[str, ...]
    trial_count: int = 0
    rejected_count: int = 0
    compiled: tuple[CompiledStrategy, ...] = ()
    loaded: ExperimentV2 | None = field(default=None, compare=False)

    @property
    def passed(self) -> bool:
        return not self.problems


def _text(view: RepoView, extra: Mapping[str, bytes], path: str) -> str:
    raw = extra.get(path)
    if raw is None:
        raw = view.read(path)
    if raw is None:
        raise ToolFailure(f"{path} が無い")
    return raw.decode("utf-8")


def verify_preregistration(view: RepoView, prereg: Preregistration) -> Verification:
    """生成物を書式 v2 の読込に通し、格子の全試行をコンパイルし、複雑さと試行の数を確かめる。

    ファイルに書き出さずに、記録票の本文から組み立て直すときと同じ読込
    （`experiment_v2_from_texts`）を使う。研究ポリシーは版の登録簿とも照合する。前提のファイルの
    実ファイルとの照合と準備の確かめ（snapshot を開く）は、実行器が実行用の作業木で
    `load_experiment_v2` と `prepare_search` で行う。
    """
    files = prereg.files
    experiment_text = files[prereg.experiment_path].decode("utf-8")
    try:
        payload = load_yaml_mapping(Path(prereg.experiment_path), text=experiment_text)
    except ConfigError as exc:  # pragma: no cover - 生成の道具が読み直して確かめ済み
        raise ToolFailure(str(exc)) from exc
    environment = payload["environment"]
    policy = select_policy(view)
    texts = {
        "experiment": experiment_text,
        "strategy": _text(view, files, prereg.strategy_path),
        "research_policy": _text(view, files, policy.path),
        "calendar": _text(view, files, environment["calendar"]),
        "timeframes": _text(view, files, environment["timeframes"]),
    }
    prefix = environment["symbols"].rstrip("/") + "/"
    symbol_texts = {
        Path(path).stem: _text(view, files, path)
        for path in view.list(prefix)
        if path.endswith(".yaml") and "/" not in path[len(prefix) :]
    }
    try:
        loaded = experiment_v2_from_texts(
            texts, symbol_texts, registry=INITIAL_CATALOG, metric_set_versions=METRIC_SET_VERSIONS
        )
    except (ConfigError, KernelValueError) as exc:
        return Verification(problems=(f"読込に通らない: {exc}",))
    registry_path = f"{POLICY_DIR}/registry.yaml"
    try:
        entries = load_research_policy_registry(
            Path(registry_path), text=_text(view, files, registry_path)
        )
        verify_registered(loaded.policy, entries, registry_path=Path(registry_path))
    except ConfigError as exc:
        raise ToolFailure(f"研究ポリシーが版の登録簿と合わない: {exc}") from exc
    search = loaded.search
    if search is None:
        return Verification(problems=("探索の実験として読めない",))
    problems: list[str] = []
    limit = loaded.policy.trial_limit
    count = search.plan.trial_count
    if limit is None or check_trial_count(count, limit).outcome is not CheckOutcome.PASSED:
        # 上限を超える格子は列挙もコンパイルもしない（直積を作らない）。
        return Verification(
            problems=("試行の数が研究ポリシーの上限を超える（事前検査 P7）",),
            trial_count=count,
            loaded=loaded,
        )
    assignments = enumerate_assignments(search.plan)
    timeframes = {
        definition.ref: definition for definition in loaded.environment.timeframe_defs.values()
    }
    compiled: list[CompiledStrategy] = []
    rejected = 0
    for index, assignment in enumerate(assignments):
        try:
            outcome = compile_strategy(
                trial_strategy(loaded.body.strategy, assignment), INITIAL_CATALOG, timeframes
            )
        except KernelValueError as exc:
            problems.append(f"試行 {index} の宣言を組み立てられない: {exc}")
            rejected += 1
            continue
        if not isinstance(outcome, CompileSucceeded):
            problems.append(f"試行 {index} がコンパイルに通らない")
            rejected += 1
            continue
        profiles, unmeasured, causes = complexity_profiles(outcome.compiled, INITIAL_CATALOG)
        measures = measure_complexity(profiles, unmeasured_outputs=unmeasured)
        result = check_complexity(measures, loaded.policy.limits, unmeasured_causes=causes)
        if result.outcome is not CheckOutcome.PASSED:
            problems.append(f"試行 {index} の複雑さが上限の外（事前検査 P6）")
        compiled.append(outcome.compiled)
    return Verification(
        problems=tuple(problems),
        trial_count=len(assignments),
        rejected_count=rejected,
        compiled=tuple(compiled),
        loaded=loaded,
    )


# --- 個々の検査 ------------------------------------------------------------------------


def _unknown_components(strategy: Mapping[str, Any]) -> list[str]:
    unknown: list[str] = []
    for item in strategy.get("components") or []:
        name, version = item.get("component"), item.get("component_version")
        if (
            not isinstance(name, str)
            or isinstance(version, bool)
            or not isinstance(version, int)
            or ContractKey(name, version) not in INITIAL_CATALOG
        ):
            unknown.append(f"部品 {name!r} の版 {version!r} がカタログに無い")
    return unknown


def _history_problems(verification: Verification) -> list[str]:
    loaded = verification.loaded
    if loaded is None:
        return []
    timeframes = {
        definition.ref: definition for definition in loaded.environment.timeframe_defs.values()
    }
    problems: list[str] = []
    for compiled in verification.compiled:
        measure = measure_history(compiled, timeframes)
        if measure.unmeasured:
            problems.append(
                f"履歴窓を日足の本数に直せない入力がある（{', '.join(measure.unmeasured)}）"
            )
        elif not measure.within_limit:
            problems.append(f"履歴窓が日足 {HISTORY_LIMIT_DAILY_BARS} 本を超える試行がある")
    return list(dict.fromkeys(problems))


def _count(state: LoopState) -> int:
    count = 1
    for axis in state.axes:
        count *= len(axis.values)
    return count


def _grid_problems(state: LoopState, view: RepoView) -> list[str]:
    count = _count(state)
    problems: list[str] = []
    if count not in GRID_TRIALS:
        problems.append("格子の試行の数が 4〜6 でない（決定 99）")
    limit = select_policy(view).policy.trial_limit
    if limit is not None and count > limit:
        problems.append("格子の試行の数が研究ポリシーの試行の上限を超える")
    return problems


def _strategy_body(data: Mapping[str, Any], path: str) -> Mapping[str, Any]:
    if not isinstance(data.get("components"), list):
        raise ToolFailure(f"{path}: 戦略の宣言の形でない")
    return data


def known_rule_digests(
    view: RepoView, *, exclude_card: str | None = None, exclude_experiment: str | None = None
) -> dict[str, list[str]]:
    """既出の規則のダイジェスト → 出どころのパスの並び（第3.5節の 6）。

    `configs/strategies/` の全宣言と、`taken/`・`done/` の全カードの戦略。`exclude_card` の
    カードと、`exclude_experiment` の実験の戦略の宣言（`<識別子>_v<版>.yaml`）は除く（改善の版の
    検査で、同じカードの前の版に当てないため）。読めない宣言は道具の失敗。
    """
    found: dict[str, list[str]] = {}

    def add(body: Mapping[str, Any], path: str) -> None:
        try:
            digest = rule_digest(body)
        except NormalizationError as exc:
            raise ToolFailure(f"{path}: 正規化できない: {exc}") from exc
        found.setdefault(digest, []).append(path)

    for path in view.list(f"{STRATEGIES_DIR}/"):
        if not path.endswith(".yaml"):
            continue
        if exclude_experiment is not None and Path(path).name.startswith(f"{exclude_experiment}_v"):
            continue
        add(_strategy_body(_load(view, path), path), path)
    for place in ("taken", "done"):
        for path in view.list(f"{HYPOTHESES_DIR}/{place}/"):
            if not path.endswith(".yaml") or path.endswith(".outcome.yaml"):
                continue
            if exclude_card is not None and Path(path).name == f"{exclude_card}.yaml":
                continue
            strategy = _load(view, path).get("strategy")
            if not isinstance(strategy, Mapping):
                raise ToolFailure(f"{path}: カードの `strategy` が読めない")
            add(strategy, path)
    return found


def _load(view: RepoView, path: str) -> dict[str, Any]:
    raw = view.read(path)
    if raw is None:  # pragma: no cover - 一覧に出たパス
        raise ToolFailure(f"{path} が無い")
    try:
        return load_yaml_mapping(Path(path), text=raw.decode("utf-8"))
    except (ConfigError, UnicodeDecodeError) as exc:
        raise ToolFailure(f"{path} を読めない: {exc}") from exc


def _duplicates(
    body: Mapping[str, Any], view: RepoView, *, card_id: str, experiment_id: str, own: bool
) -> list[str]:
    try:
        digest = rule_digest(body)
    except NormalizationError as exc:
        return [f"戦略を正規化できない: {exc}"]
    known = known_rule_digests(
        view,
        exclude_card=card_id if own else None,
        exclude_experiment=experiment_id if own else None,
    )
    return [f"既出の規則と同じ（{path}）" for path in known.get(digest, [])]


def _identifier_collisions(card: Card, view: RepoView, path: str | None) -> list[str]:
    problems: list[str] = []
    for place in CARD_PLACES:
        other = f"{HYPOTHESES_DIR}/{place}/{card.card_id}.yaml"
        if other != path and view.read(other) is not None:
            problems.append(f"同じ識別子のカードが既にある（{other}）")
    if view.read(experiment_path(card.experiment_id, 1)) is not None:
        problems.append(
            f"同じ識別子の実験が既にある（{EXPERIMENTS_DIR}/{card.experiment_id}.yaml）"
        )
    if any(
        Path(item).name.startswith(f"{card.experiment_id}_v")
        for item in view.list(f"{STRATEGIES_DIR}/")
    ):
        problems.append("同じ識別子の戦略の宣言が既にある")
    return problems


# --- カードの検査 -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Screening:
    """カードの機械の検査の結果。`reason` が `None` なら合格（`prereg` は版 1 の生成物）。"""

    reason: str | None
    problems: tuple[str, ...] = ()
    card: Card | None = None
    prereg: Preregistration | None = None
    verification: Verification | None = None

    @property
    def passed(self) -> bool:
        return self.reason is None


def screen_card(
    view: RepoView,
    raw: bytes,
    *,
    path: str | None = None,
    allowed_read_paths: Collection[str] | None = None,
) -> Screening:
    """第3.5節の検査 1〜7（準備の確かめを除く）を順に当てる。

    `path` はカードの置き場（`research/hypotheses/queue/<カード>.yaml`。ファイル名の検査と、同じ
    識別子の検査で自分を除くのに使う）。`allowed_read_paths` は発想役の読む束の一覧（第5.2節）。
    """
    try:
        card = parse_card(raw, path=path, allowed_read_paths=allowed_read_paths)
    except CardFormatError as exc:
        return Screening(CARD_FORMAT, exc.problems)
    collisions = _identifier_collisions(card, view, path)
    if collisions:
        return Screening(CARD_FORMAT, tuple(collisions), card)
    unknown = _unknown_components(card.strategy)
    if unknown:
        return Screening(CARD_COMPONENT_UNKNOWN, tuple(unknown), card)
    state, _ = state_at(card, 1, {})
    grid = _grid_problems(state, view)
    limit = select_policy(view).policy.trial_limit
    if limit is not None and _count(state) > limit:
        # 研究ポリシーの上限を超える格子は、履歴窓を測るための列挙とコンパイルをせずに 4 で落とす
        # （3 は測れないので不合格にしない。検査 3 と 7 の順の仮置きと同じ扱い）。
        return Screening(CARD_GRID_SIZE, tuple(grid), card)
    prereg = generate(view, card=raw, version=1, changes={})
    verification = verify_preregistration(view, prereg)
    history = _history_problems(verification)
    if history:
        return Screening(CARD_HISTORY_TOO_LONG, tuple(history), card, prereg, verification)
    if grid:
        return Screening(CARD_GRID_SIZE, tuple(grid), card, prereg, verification)
    if card.reaches_floor == "UNLIKELY":
        return Screening(
            CARD_EVIDENCE_UNLIKELY,
            ("証拠の要件に届く見込みが UNLIKELY（仮置き 7）",),
            card,
            prereg,
            verification,
        )
    duplicates = _duplicates(
        thaw(card.strategy), view, card_id=card.card_id, experiment_id=card.experiment_id, own=False
    )
    if duplicates:
        return Screening(CARD_DUPLICATE_RULE, tuple(duplicates), card, prereg, verification)
    if not verification.passed:
        return Screening(CARD_PREREG_REJECTED, verification.problems, card, prereg, verification)
    return Screening(None, (), card, prereg, verification)


# --- 変更の提案の検査 ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChangeScreening:
    """変更の提案の検査の結果。

    `reason` が `None` なら合格で、`decision` が `CHANGE` なら `prereg` が次の版の生成物、
    `FINISH`・`AMBIGUOUS` なら生成物は無い（実行器が段 7 へ進むか、停止にする）。
    """

    reason: str | None
    problems: tuple[str, ...] = ()
    change: Change | None = None
    prereg: Preregistration | None = None
    verification: Verification | None = None

    @property
    def passed(self) -> bool:
        return self.reason is None


def check_change(
    view: RepoView,
    *,
    card: bytes,
    change: bytes,
    from_version: int,
    previous_changes: Mapping[int, bytes] | None = None,
) -> ChangeScreening:
    """版 `from_version` の結果を読んで書いた変更の提案を検査し、通れば次の版を生成する。

    `previous_changes` は版 2〜`from_version` の材料の変更の提案（省くと `view` から読む）。生成は
    `view` の `research/loops/<カード>/run_v<from_version>.yaml`（前の版の実行記録）を系譜に使う。
    """
    parsed_card = parse_card(card)
    try:
        parsed = parse_change(
            change, card_id=parsed_card.card_id, expected_from_version=from_version
        )
    except ChangeRejected as exc:
        return ChangeScreening(exc.reason, exc.problems)
    if parsed.decision != "CHANGE":
        return ChangeScreening(None, (), parsed)
    materials: dict[int, bytes] = (
        dict(previous_changes)
        if previous_changes is not None
        else {
            version: raw
            for version in range(2, from_version + 1)
            if (raw := view.read(change_path(parsed_card.card_id, version))) is not None
        }
    )
    before, _ = state_at(parsed_card, from_version, materials)
    try:
        after = apply_change(before, parsed)
    except ChangeRejected as exc:
        return ChangeScreening(exc.reason, exc.problems, parsed)
    static = _unknown_components(after.strategy) + _grid_problems(after, view)
    if static:
        return ChangeScreening(CHANGE_OUT_OF_RULES, tuple(static), parsed)
    materials[parsed.to_version] = change
    prereg = generate(view, card=card, version=parsed.to_version, changes=materials)
    verification = verify_preregistration(view, prereg)
    problems = _history_problems(verification)
    problems += _duplicates(
        thaw(after.strategy),
        view,
        card_id=parsed_card.card_id,
        experiment_id=parsed_card.experiment_id,
        own=True,
    )
    if problems:
        return ChangeScreening(CHANGE_OUT_OF_RULES, tuple(problems), parsed, prereg, verification)
    if not verification.passed:
        return ChangeScreening(
            CHANGE_PREREG_REJECTED, verification.problems, parsed, prereg, verification
        )
    return ChangeScreening(None, (), parsed, prereg, verification)
