"""最終検証の申請が読む人間の記録のファイル（YAML）の読込（D09 §9.10.10 の 5・6）。

- **申告のファイル**（`--declaration`。仮置き R2-6 の決定「このまま」）: 申請の時点で取り直す
  研究の外の相場の申告。置き場は `research/declarations/`（版管理）。項目は
  `used_outside_viewing`（真偽。必須）・対象の期間・質問の文・回答の原文・記録者。R4 の仮置きで
  候補を指す `experiment_id`・`execution` を足した（申告は候補ごとの事実なので、別の候補の申告を
  渡しても通らないようにする）。
- **候補の固定の記録**（前向き検証の規則 F1 が形と置き場を決める。仮置き R2-8）: F1 は未承認なので、
  R4 の仮置きの形で読む。置き場 `research/fixations/<experiment_id>_<execution>.yaml`（版管理。
  origin の既定ブランチ上のものだけを読む）、項目は固定時点 `t_fix`・長さ `length_days`（下限 364。
  D15-11 (a)）・組ごとの固定した試行 `fixed_trials`。前向き検証の開始は固定時点の後の最初の完全な
  取引週の始まり（ADR-0038 第5項の 2）として導く（記録に書かない）。

どちらも値は件数・時刻・真偽・文だけで、価格を含まない。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.variant import Paired
from odyssey_fx.marketdata.domain.access import acquisition_start_after

__all__ = [
    "DECLARATIONS_DIRECTORY",
    "FIXATIONS_DIRECTORY",
    "MIN_LENGTH_DAYS",
    "Declaration",
    "Fixation",
    "fixation_path",
    "load_declaration",
    "load_fixation",
]

#: 申告のファイルの置き場（仮置き R2-6）。
DECLARATIONS_DIRECTORY: Final = Path("research") / "declarations"
#: 候補の固定の記録の置き場（R4 の仮置き。F1 が決めたら合わせる）。
FIXATIONS_DIRECTORY: Final = Path("research") / "fixations"
#: 長さ `L` の下限（D15-11 (a)）。
MIN_LENGTH_DAYS: Final = 364

_SCHEMA_VERSION: Final = 1


class _PeriodModel(StrictModel):
    start: str
    end: str


class _DeclarationModel(StrictModel):
    schema_version: int
    experiment_id: str
    execution: int
    used_outside_viewing: bool
    period: _PeriodModel
    question: str
    answer: str
    recorded_by: str


class _FixedTrialsModel(StrictModel):
    standard: int
    candidate: int


class _FixationModel(StrictModel):
    schema_version: int
    experiment_id: str
    execution: int
    t_fix: str
    length_days: int
    fixed_trials: _FixedTrialsModel


@dataclass(frozen=True, slots=True)
class Declaration:
    """申告（仮置き R2-6）。`period` は申告が答えた相場の期間。"""

    experiment_id: str
    execution: int
    used_outside_viewing: bool
    period: Interval
    recorded_by: str


@dataclass(frozen=True, slots=True)
class Fixation:
    """候補の固定の記録（R4 の仮置き。F1 の形が決まるまで）。"""

    experiment_id: str
    execution: int
    t_fix: UtcTime
    length_days: int
    fixed_trials: Paired[int]

    @property
    def forward_start(self) -> UtcTime:
        """前向き検証の開始（固定時点の後の最初の完全な取引週の始まり。ADR-0038 第5項の 2）。"""
        return acquisition_start_after(self.t_fix)

    @property
    def forward_period(self) -> Interval:
        """前向き検証の期間 `[開始, 開始 + L)`（ADR-0038 第5項の 2、仮置き R2-8）。"""
        from datetime import timedelta

        start = self.forward_start
        return Interval(start=start, end=start + timedelta(days=self.length_days))


def _time(text: str, label: str, path: Path) -> UtcTime:
    try:
        return UtcTime.parse(text)
    except KernelValueError as exc:
        raise ConfigError(f"{path}: `{label}` が UTC の時刻として読めない: {exc}") from exc


def load_declaration(path: Path) -> Declaration:
    """申告のファイルを読む（無い・形が違うときは `ConfigError`。終了コード 2）。"""
    payload = load_yaml_mapping(path)
    model = validate(_DeclarationModel, payload, path)
    require_schema_version(model.schema_version, _SCHEMA_VERSION, path)
    if model.execution < 1:
        raise ConfigError(f"{path}: `execution` は 1 以上")
    for label, text in (
        ("experiment_id", model.experiment_id),
        ("question", model.question),
        ("answer", model.answer),
        ("recorded_by", model.recorded_by),
    ):
        if not text.strip():
            raise ConfigError(f"{path}: `{label}` が空である（作業役が人間の代わりに書けない）")
    start = _time(model.period.start, "period.start", path)
    end = _time(model.period.end, "period.end", path)
    if not start < end:
        raise ConfigError(f"{path}: `period` は start < end")
    return Declaration(
        experiment_id=model.experiment_id,
        execution=model.execution,
        used_outside_viewing=model.used_outside_viewing,
        period=Interval(start=start, end=end),
        recorded_by=model.recorded_by,
    )


def fixation_path(experiment_id: str, execution: int) -> Path:
    """候補の固定の記録の置き場（リポジトリの根からの相対パス。R4 の仮置き）。"""
    return FIXATIONS_DIRECTORY / f"{experiment_id}_{execution}.yaml"


def load_fixation(path: Path, *, text: str) -> Fixation:
    """origin の既定ブランチ上の固定の記録の本文を読む（形が違えば `ConfigError`）。"""
    payload = load_yaml_mapping(path, text=text)
    model = validate(_FixationModel, payload, path)
    require_schema_version(model.schema_version, _SCHEMA_VERSION, path)
    if model.length_days < MIN_LENGTH_DAYS:
        raise ConfigError(
            f"{path}: `length_days` {model.length_days} は下限 {MIN_LENGTH_DAYS} 日より短い"
            "（D15-11 (a)）"
        )
    if model.execution < 1 or model.fixed_trials.standard < 0 or model.fixed_trials.candidate < 0:
        raise ConfigError(f"{path}: `execution` は 1 以上、`fixed_trials` は 0 以上")
    return Fixation(
        experiment_id=model.experiment_id,
        execution=model.execution,
        t_fix=_time(model.t_fix, "t_fix", path),
        length_days=model.length_days,
        fixed_trials=Paired(
            standard=model.fixed_trials.standard, candidate=model.fixed_trials.candidate
        ),
    )
