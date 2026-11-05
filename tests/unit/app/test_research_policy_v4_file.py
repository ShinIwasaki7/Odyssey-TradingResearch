"""登録した研究ポリシー第 4 版のファイルと、それを指す探索の実験設定の読込（D18 §2〜§3・§6 の
RP4-1〜RP4-14。順 7 の第 4 版の実装 PR）。

- 第 4 版のファイルは登録簿の 1 行と一致し、8 通貨の対象系列・評価範囲・前提の記録・比較の前提を
  持つ（D18 §2・§3、決定 34〜36）。生成した fold は D18 §2.4 の表と一致する。
- 読込（`repo_root` を渡したとき）は、前提のファイルの SHA-256 を実ファイルと照合し、通貨ごとの
  4 つの値を銘柄の設定から pip の数で写した値と照合する（D18 §3.4）。
- 第 4 版を指す探索の実験は、執行系列の通貨で照合 C1〜C7（D18 §3.3）を通る値でだけ読める。
- 第 3 版のファイル・ダイジェストと、第 3 版を指す既存の実験設定は変わらない（D18 の仮置き 7）。

テストは設定ファイルと人工の実験設定だけを読み、snapshot も価格も戦略の成績も読まない。
"""

from __future__ import annotations

import shutil
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final

import pytest

from odyssey_fx.app.config.experiment_v2 import ExperimentV2, load_experiment_v2
from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.app.config.pip_copy import PriceUnitCosts, copy_costs_by_pips
from odyssey_fx.app.config.research_policy import (
    load_research_policy,
    load_research_policy_registry,
    research_policy_path,
    research_policy_registry_path,
    verify_registered,
)
from odyssey_fx.app.config.symbols import load_symbol_specs
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.metrics import METRIC_SET_VERSIONS
from odyssey_fx.evaluation.domain.policy_basis import (
    PerSymbolComparisonBasis,
    PerSymbolPolicyBasis,
)
from odyssey_fx.evaluation.domain.search import EvaluationStandard, StandardPurpose
from odyssey_fx.evaluation.domain.splits import folds_for_policy
from odyssey_fx.evaluation.domain.variant import Variant
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from tests.fixtures.evaluation.search_experiments import BASE_EXPERIMENT, search_experiment_text

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
POLICY_V4: Final = Path("configs/policies/research/research_policy_v4.yaml")
V3_DIGEST: Final = "f2e5d8f47489beac23d340438684aafc20c83ff7c7a1619e2aa6eb4b94ef1c3b"
DAY: Final = 86_400

#: 8 通貨の承認済みの snapshot の組（D18 §3.1 の表。受入れの記録から写した値）。
SNAPSHOTS: Final[dict[str, tuple[str, str]]] = {
    "USDJPY": (
        "ecaaf53fcdedbc1977184b390c36e2714c658829341eed3ec5d71588d3bfc622",
        "f5214ed8b5e4bc0ca669b1fdd770b8522cc3e924cb96e60f22b08e238c7f25ca",
    ),
    "EURJPY": (
        "db6cf465bcfd40ff250bdd70c254fbde3dd3c1c42d538f4b731456f21fc0380d",
        "50475022304808c7468b638bdc1536e2bef906e28b6a2618a03ed80bcd713545",
    ),
    "GBPJPY": (
        "455d1d38f924f2711aea51eebea7d5d8d24708505e64c7085c7b099333fa5188",
        "3141cedcd378652b1f66493b23b3e52be09efee0568d6929281704938ef6377b",
    ),
    "AUDJPY": (
        "dc411b9ec1054b073d0c610d74bad27a6801d85c9b46f76ff0a0ac705dc36843",
        "ce0aac848a58e2845b286bd839da421c213e918814a9911a52bbba6fa6825821",
    ),
    "EURUSD": (
        "4e6eb8d40df023685b6b0739bd202d132c97997de68e8d55f0b4114c89bb917b",
        "9c03bc1283e5c7474dc1602104d943fe7985b11c745dae3135813fc7ddee1b19",
    ),
    "GBPUSD": (
        "b9ca3a2142bf7c498c5b0b1fb803c0c44df6938dad76a11bd6203e9667437f6e",
        "35bcd732a0d696eb95766094e35e4aeab0cdb779e3abae1326691155c7f7732e",
    ),
    "AUDUSD": (
        "07362e2223e99b24030b61433c0cc6a78c1a34cbbe9708e1cc04adb744bfe75d",
        "b9d213ea6fd957a5d317b33c3472ef3c72a0292d0fed77a4b0003cbf65b074b1",
    ),
    "USDCHF": (
        "1139abd01c3d1f1d4c564e5f123e4a979b42b8ed8ae1fc8b15b0d27752472298",
        "3d5e44b84807ddf1e46251e9b430a6bcf0c62323062ceb07dfda1df83fcc6c62",
    ),
}
EIGHT: Final = tuple(SNAPSHOTS)
JPY_PAIRS: Final = ("USDJPY", "EURJPY", "GBPJPY", "AUDJPY")
#: 第3.2節の表（スプレッド・入場の滑り・決済の滑り・許容する不利約定の幅）。
JPY_COSTS: Final = ("0.02", "0.01", "0.01", "0.05")
OTHER_COSTS: Final = ("0.0002", "0.0001", "0.0001", "0.0005")


def _costs(symbol: str) -> tuple[str, str, str, str]:
    return JPY_COSTS if symbol in JPY_PAIRS else OTHER_COSTS


def _load_v4(*, text: str | None = None, root: Path = REPO_ROOT) -> EvaluationStandard:
    policy = load_research_policy(
        root / POLICY_V4, policy_id="research_policy", version=4, text=text, repo_root=root
    )
    assert policy.evaluation_standard is not None
    return policy.evaluation_standard


def _v4_text() -> str:
    return (REPO_ROOT / POLICY_V4).read_text(encoding="utf-8")


def _refused_v4(text: str, root: Path = REPO_ROOT) -> str:
    with pytest.raises(ConfigError) as caught:
        _load_v4(text=text, root=root)
    return str(caught.value)


def _replaced(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, old
    return text.replace(old, new)


def _per_symbol(
    standard: EvaluationStandard,
) -> tuple[PerSymbolPolicyBasis, PerSymbolComparisonBasis]:
    basis = standard.basis
    fixed = standard.comparison_basis
    assert isinstance(basis, PerSymbolPolicyBasis)
    assert isinstance(fixed, PerSymbolComparisonBasis)
    return basis, fixed


# --- RP4-1〜RP4-9: 第 4 版のファイル --------------------------------------------------------


def test_rp4_1_the_v4_file_matches_its_registry_line() -> None:
    policy = load_research_policy(
        REPO_ROOT / POLICY_V4, policy_id="research_policy", version=4, repo_root=REPO_ROOT
    )
    registry = research_policy_registry_path(REPO_ROOT)
    entry = verify_registered(
        policy, load_research_policy_registry(registry), registry_path=registry
    )
    assert entry.purpose is StandardPurpose.STANDARD
    assert policy.trial_limit == 100


def test_rp4_2_the_target_series_are_the_sixteen_series() -> None:
    basis, _ = _per_symbol(_load_v4())
    assert sorted(basis.target_series) == sorted(
        f"{symbol}/{timeframe}/bid" for symbol in EIGHT for timeframe in ("15m", "1h")
    )


def test_rp4_2_a_file_missing_one_series_is_refused() -> None:
    text = _replaced(_v4_text(), '      - "AUDUSD/1h/bid"\n', "")
    assert "AUDUSD" in _refused_v4(text)


def test_rp4_2_a_file_missing_one_symbol_in_the_target_series_is_refused() -> None:
    text = _replaced(_v4_text(), '      - "USDCHF/15m/bid"\n      - "USDCHF/1h/bid"\n', "")
    assert "basis.target_series の通貨" in _refused_v4(text)


def test_rp4_3_the_range_is_the_one_the_human_decided() -> None:
    split = _load_v4().split
    assert (str(split.range.start), str(split.range.end)) == (
        "2017-03-25T12:00:00Z",
        "2023-12-30T12:00:00Z",
    )
    assert (split.range.end.value - split.range.start.value) == timedelta(days=2471)


def test_rp4_4_the_basis_holds_eight_pairs_and_the_conversion_pair() -> None:
    basis, fixed = _per_symbol(_load_v4())
    assert {
        item.symbol: (str(item.snapshots.standard), str(item.snapshots.candidate))
        for item in basis.symbols
    } == SNAPSHOTS
    assert (
        str(basis.conversion_snapshots.standard),
        str(basis.conversion_snapshots.candidate),
    ) == SNAPSHOTS["USDJPY"]
    assert fixed.conversion_symbol == "USDJPY"


def test_rp4_4_a_conversion_pair_other_than_usdjpy_is_refused() -> None:
    usdjpy, eurusd = SNAPSHOTS["USDJPY"][0], SNAPSHOTS["EURUSD"][0]
    text = _replaced(
        _v4_text(),
        f'    conversion_snapshots:\n      standard: "{usdjpy}"',
        f'    conversion_snapshots:\n      standard: "{eurusd}"',
    )
    assert "basis.conversion_snapshots" in _refused_v4(text)


def test_rp4_5_symbols_missing_from_the_comparison_basis_are_refused() -> None:
    line = (
        '      GBPJPY: {spread_offset: "0.02", entry_slippage: "0.01", close_slippage: "0.01",'
        ' adverse_fill_limit: "0.05"}\n'
    )
    assert "comparison_basis.symbols の通貨" in _refused_v4(_replaced(_v4_text(), line, ""))


def test_rp4_5_symbols_missing_from_the_basis_are_refused() -> None:
    standard, candidate = SNAPSHOTS["AUDJPY"]
    block = f'      AUDJPY:\n        standard: "{standard}"\n        candidate: "{candidate}"\n'
    message = _refused_v4(_replaced(_v4_text(), block, ""))
    assert "AUDJPY" in message


def test_rp4_6_the_folds_are_the_table_of_section_2_4() -> None:
    split = _load_v4().split
    folds = folds_for_policy(split, research_until=UtcTime.parse("2024-01-01T00:00:00Z"))
    table = [
        ("2017-03-25", "2019-03-23", "2020-01-04", True),
        ("2018-01-06", "2020-01-04", "2021-01-02", False),
        ("2019-01-05", "2021-01-02", "2022-01-01", False),
        ("2020-01-04", "2022-01-01", "2022-12-31", False),
        ("2021-01-02", "2022-12-31", "2023-12-30", False),
    ]
    assert [
        (
            str(fold.train.start)[:10],
            str(fold.validation.start)[:10],
            str(fold.validation.end)[:10],
            fold.partial,
        )
        for fold in folds
    ] == table
    assert all(str(fold.train.end) == str(fold.validation.start) for fold in folds)
    # U1: 選定区間と検証区間の和集合が範囲に等しい（purge 0）。
    assert min(fold.train.start for fold in folds) == split.range.start
    assert max(fold.validation.end for fold in folds) == split.range.end
    # U2: 検証区間は隙間なくつながり、最初の選定区間の後ろの全体（1,743 日）を覆う。
    validations = [fold.validation for fold in folds]
    assert all(a.end == b.start for a, b in zip(validations, validations[1:], strict=False))
    covered = Interval(start=validations[0].start, end=validations[-1].end)
    assert str(covered.start) == "2019-03-23T12:00:00Z"
    assert covered.end.value - covered.start.value == timedelta(days=1743)
    assert split.train_seconds == 728 * DAY and split.validation_seconds == 364 * DAY


def test_rp4_7_the_symbol_costs_are_the_pip_copies_of_usdjpy() -> None:
    _, fixed = _per_symbol(_load_v4())
    specs = load_symbol_specs(REPO_ROOT / "configs/symbols")
    source = fixed.costs_of("USDJPY")
    assert source is not None
    source_costs = PriceUnitCosts(
        spread_offset=source.spread_offset,
        entry_slippage=source.entry_slippage,
        close_slippage=source.close_slippage,
        adverse_fill_limit=source.adverse_fill_limit,
    )
    for symbol in EIGHT:
        costs = fixed.costs_of(symbol)
        assert costs is not None
        written = (
            costs.spread_offset,
            costs.entry_slippage,
            costs.close_slippage,
            costs.adverse_fill_limit,
        )
        assert written == tuple(Decimal(value) for value in _costs(symbol)), symbol
        copied = copy_costs_by_pips(
            source_costs,
            source_spec=specs[Symbol("USDJPY")],
            target_spec=specs[Symbol(symbol)],
        )
        assert written == (
            copied.spread_offset,
            copied.entry_slippage,
            copied.close_slippage,
            copied.adverse_fill_limit,
        )
    assert (fixed.currency, fixed.initial_balance, fixed.commission_per_unit) == (
        "JPY",
        Decimal("1000000"),
        Decimal("0.001"),
    )
    assert (fixed.trial_risk_rate, fixed.account_risk_cap, fixed.entry_delay_bars) == (
        Decimal("0.02"),
        Decimal("0.20"),
        0,
    )
    assert (fixed.pivot_currency, fixed.max_observation_skew_seconds) == ("USD", 30 * 60)


def test_rp4_7_a_miscopied_value_is_refused() -> None:
    text = _replaced(
        _v4_text(),
        '      GBPUSD: {spread_offset: "0.0002"',
        '      GBPUSD: {spread_offset: "0.0003"',
    )
    message = _refused_v4(text)
    assert "comparison_basis.symbols.GBPUSD" in message and "spread_offset" in message


def _premise_workspace(tmp_path: Path) -> Path:
    """第 4 版のファイルと 16 個の前提のファイルを作業場に写す。"""
    standard = _load_v4()
    basis, _ = _per_symbol(standard)
    for relative in (str(POLICY_V4), *(item.path for item in basis.premises)):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / relative, target)
    return tmp_path


def test_rp4_8_the_premise_hashes_match_the_files() -> None:
    basis, _ = _per_symbol(_load_v4())
    assert len(basis.premises) == 16
    assert {item.name for item in basis.premises} >= {"verified_raw", "symbol_USDCHF"}


def test_rp4_8_a_changed_premise_file_is_refused(tmp_path: Path) -> None:
    root = _premise_workspace(tmp_path)
    changed = root / "configs/symbols/AUDUSD.yaml"
    changed.write_text(changed.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    message = _refused_v4(_v4_text(), root)
    assert "symbol_AUDUSD" in message and "SHA-256" in message


def test_rp4_8_a_missing_premise_file_is_refused(tmp_path: Path) -> None:
    root = _premise_workspace(tmp_path)
    (root / "configs/datasources/oanda_mt5_m1_verified_raw_2026-10-16.yaml").unlink()
    assert "verified_raw" in _refused_v4(_v4_text(), root)


def test_rp4_8_the_premise_check_is_skipped_without_a_repository_root(tmp_path: Path) -> None:
    """記録票の本文から組み立て直す経路（ファイルを読まない）は照合しない。"""
    root = _premise_workspace(tmp_path)
    (root / "configs/symbols/AUDUSD.yaml").unlink()
    policy = load_research_policy(root / POLICY_V4, policy_id="research_policy", version=4)
    assert policy.evaluation_standard is not None


def test_rp4_9_the_v3_file_and_its_digest_are_unchanged() -> None:
    policy = load_research_policy(
        research_policy_path(REPO_ROOT, "research_policy", 3),
        policy_id="research_policy",
        version=3,
        repo_root=REPO_ROOT,
    )
    assert policy.digest.hex == V3_DIGEST
    entries = load_research_policy_registry(research_policy_registry_path(REPO_ROOT))
    v3 = next(item for item in entries if item.version == 3)
    assert v3.digest.hex == V3_DIGEST


# --- RP4-10〜RP4-14: 第 4 版を指す探索の実験設定の照合 -------------------------------------------

_COPIED: Final = (
    "configs/calendars/fx_ny17_v1.yaml",
    "configs/calendars/timeframes_v1.yaml",
    "configs/strategies/strategy_b_v1.yaml",
    "configs/policies/research/research_policy_v1.yaml",
    "configs/policies/research/research_policy_v2.yaml",
    "configs/policies/research/research_policy_v3.yaml",
    "configs/policies/research/registry.yaml",
    BASE_EXPERIMENT,
)
AXES: Final = "    - {instance: m15_ema, parameter: period, values: [10, 20]}"


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = _premise_workspace(tmp_path_factory.mktemp("policy-v4"))
    for relative in _COPIED:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / relative, target)
    shutil.copytree(REPO_ROOT / "configs/symbols", root / "configs/symbols", dirs_exist_ok=True)
    strategy = (REPO_ROOT / "configs/strategies/strategy_b_v1.yaml").read_text(encoding="utf-8")
    for symbol in (*EIGHT, "EURGBP"):
        (root / f"configs/strategies/strategy_b_{symbol.lower()}.yaml").write_text(
            strategy.replace("USDJPY/", f"{symbol}/"), encoding="utf-8"
        )
    return root


def _experiment_text(symbol: str, *, snapshots: tuple[str, str] | None = None) -> str:
    """`symbol` を執行系列にした、第 4 版を指す正しい値の人工の探索の実験設定の本文。"""
    standard, candidate = snapshots or SNAPSHOTS.get(symbol, ("a" * 64, "b" * 64))
    spread, entry, close, adverse = _costs(symbol)
    base = (REPO_ROOT / BASE_EXPERIMENT).read_text(encoding="utf-8").replace("0" * 64, standard)
    text = search_experiment_text(
        base,
        experiment_id=f"v4_search_{symbol.lower()}",
        policy_version=4,
        axes=AXES,
        max_trials=2,
        candidate_snapshot=candidate,
    )
    for old, new in (
        ("evaluation: {metric_set_version: 2}", "evaluation: {metric_set_version: 3}"),
        (
            'strategy: "configs/strategies/strategy_b_v1.yaml"',
            f'strategy: "configs/strategies/strategy_b_{symbol.lower()}.yaml"',
        ),
        ('execution_series: "USDJPY/15m/bid"', f'execution_series: "{symbol}/15m/bid"'),
        ('  - "USDJPY/15m/bid"', f'  - "{symbol}/15m/bid"'),
        ('    USDJPY: "0.05"', f'    {symbol}: "{adverse}"'),
        ('  spread_offset: "0.02"', f'  spread_offset: "{spread}"'),
        ('  entry_slippage: "0.01"', f'  entry_slippage: "{entry}"'),
        ('  close_slippage: "0.01"', f'  close_slippage: "{close}"'),
        ('  max_observation_skew: "PT15M"', '  max_observation_skew: "PT30M"'),
    ):
        text = _replaced(text, old, new)
    if symbol not in JPY_PAIRS:
        usdjpy_standard, usdjpy_candidate = SNAPSHOTS["USDJPY"]
        text += (
            "conversion_snapshots:\n"
            f'  standard: "{usdjpy_standard}"\n'
            f'  candidate: "{usdjpy_candidate}"\n'
        )
    return text


def _write(root: Path, name: str, text: str) -> Path:
    path = root / "configs/experiments" / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _load(path: Path, root: Path) -> ExperimentV2:
    return load_experiment_v2(
        path, repo_root=root, registry=INITIAL_CATALOG, metric_set_versions=METRIC_SET_VERSIONS
    )


def _refused(root: Path, name: str, text: str) -> str:
    with pytest.raises(ConfigError) as caught:
        _load(_write(root, name, text), root)
    return str(caught.value)


@pytest.mark.parametrize("symbol", EIGHT)
def test_rp4_10_a_search_on_v4_with_the_right_values_loads(workspace: Path, symbol: str) -> None:
    loaded = _load(_write(workspace, f"ok_{symbol}", _experiment_text(symbol)), workspace)
    search = loaded.search
    assert search is not None and loaded.policy.version == 4
    assert len(search.split.folds) == 5
    assert str(loaded.body_of(Variant.CANDIDATE).snapshot_ref.snapshot_id) == SNAPSHOTS[symbol][1]
    if symbol in JPY_PAIRS:
        assert search.conversion_snapshots is None
    else:
        assert search.conversion_snapshots is not None
        conversion = loaded.body_of(Variant.CANDIDATE).conversion_snapshot_ref
        assert conversion is not None
        assert str(conversion.snapshot_id) == SNAPSHOTS["USDJPY"][1]


def test_rp4_11_c1_a_symbol_outside_the_eight_is_refused(workspace: Path) -> None:
    message = _refused(workspace, "c1", _experiment_text("EURGBP"))
    assert "照合 C1" in message and "EURGBP" in message


def test_rp4_11_c2_the_snapshots_of_another_symbol_are_refused(workspace: Path) -> None:
    text = _experiment_text("EURUSD", snapshots=SNAPSHOTS["GBPUSD"])
    message = _refused(workspace, "c2", text)
    assert "照合 C2" in message and "basis.symbols.EURUSD.standard" in message


def test_rp4_11_c2_swapped_snapshots_are_refused(workspace: Path) -> None:
    standard, candidate = SNAPSHOTS["AUDJPY"]
    message = _refused(
        workspace, "c2s", _experiment_text("AUDJPY", snapshots=(candidate, standard))
    )
    assert "照合 C2" in message


def test_rp4_11_c3_a_wrong_conversion_pair_is_refused(workspace: Path) -> None:
    usdjpy_standard, usdjpy_candidate = SNAPSHOTS["USDJPY"]
    text = _replaced(
        _experiment_text("GBPUSD"),
        f'  standard: "{usdjpy_standard}"\n  candidate: "{usdjpy_candidate}"\n',
        f'  standard: "{usdjpy_candidate}"\n  candidate: "{usdjpy_standard}"\n',
    )
    message = _refused(workspace, "c3", text)
    assert "照合 C3" in message and "conversion_snapshots.standard" in message


def test_rp4_11_c4_a_different_initial_balance_is_refused(workspace: Path) -> None:
    text = _replaced(
        _experiment_text("EURJPY"), '  initial_balance: "1000000"', '  initial_balance: "2000000"'
    )
    message = _refused(workspace, "c4", text)
    assert "照合 C4 account.initial_balance" in message


def test_rp4_11_c5_the_usdjpy_spread_on_eurusd_is_refused(workspace: Path) -> None:
    text = _replaced(
        _experiment_text("EURUSD"), '  spread_offset: "0.0002"', '  spread_offset: "0.02"'
    )
    message = _refused(workspace, "c5", text)
    assert "照合 C5 cost_model.spread_offset" in message


def test_rp4_11_c6_two_adverse_fill_limits_are_refused(workspace: Path) -> None:
    text = _replaced(
        _experiment_text("AUDUSD"),
        '    AUDUSD: "0.0005"',
        '    AUDUSD: "0.0005"\n    USDJPY: "0.05"',
    )
    message = _refused(workspace, "c6", text)
    assert "照合 C6 execution_policy.adverse_fill_limits" in message


def test_rp4_11_c6_a_different_adverse_fill_limit_is_refused(workspace: Path) -> None:
    text = _replaced(_experiment_text("USDCHF"), '    USDCHF: "0.0005"', '    USDCHF: "0.0006"')
    assert "照合 C6" in _refused(workspace, "c6v", text)


def test_rp4_12_a_jpy_pair_with_conversion_snapshots_is_refused(workspace: Path) -> None:
    usdjpy_standard, usdjpy_candidate = SNAPSHOTS["USDJPY"]
    text = _experiment_text("GBPJPY") + (
        f'conversion_snapshots:\n  standard: "{usdjpy_standard}"\n'
        f'  candidate: "{usdjpy_candidate}"\n'
    )
    assert "換算の snapshot" in _refused(workspace, "c3j", text)


def test_rp4_12_a_non_jpy_pair_without_conversion_snapshots_is_refused(workspace: Path) -> None:
    text = _experiment_text("USDCHF")
    text = text[: text.index("conversion_snapshots:\n")]
    assert "換算の snapshot" in _refused(workspace, "c3n", text)


@pytest.mark.parametrize("symbol", ["USDJPY", "EURUSD"])
def test_rp4_13_c7_a_fifteen_minute_limit_is_refused(workspace: Path, symbol: str) -> None:
    text = _replaced(
        _experiment_text(symbol),
        '  max_observation_skew: "PT30M"',
        '  max_observation_skew: "PT15M"',
    )
    message = _refused(workspace, f"c7_{symbol}", text)
    assert "照合 C7 conversion_policy.max_observation_skew" in message


@pytest.mark.parametrize("symbol", ["AUDJPY", "GBPUSD"])
def test_rp4_13_c7_a_pivot_other_than_usd_is_refused(workspace: Path, symbol: str) -> None:
    text = _replaced(_experiment_text(symbol), '  pivot_currency: "USD"', '  pivot_currency: "EUR"')
    message = _refused(workspace, f"c7p_{symbol}", text)
    assert "照合 C7 conversion_policy.pivot_currency" in message


@pytest.mark.parametrize(
    "name",
    ["h2_daily_uptrend_h1_ema_cross.yaml", "h2_daily_uptrend_h1_ema_cross_v2.yaml"],
)
def test_rp4_14_the_existing_searches_on_v3_still_load(name: str) -> None:
    loaded = _load(REPO_ROOT / "configs/experiments" / name, REPO_ROOT)
    assert loaded.search is not None
    assert (loaded.policy.version, loaded.policy.digest.hex) == (3, V3_DIGEST)
