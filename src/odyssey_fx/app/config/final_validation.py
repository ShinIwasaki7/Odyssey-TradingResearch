"""最終検証の申請が読む人間の記録のファイル（YAML）の読込（D09 §9.10.10 の 5）。

- **申告のファイル**（D09 §9.10.10 の 6、`--declaration`）は、人間の決定 150「申告を無くす」
  （ADR-0045）により読まない。既にある `research/declarations/` の下のファイルは記録として版管理に
  残す（申告の撤去の PR 4b）。
- **候補の固定の記録**（前向き検証の規則 F1 が形と置き場を決める。仮置き R2-8）: F1 は未承認なので、
  R4 の仮置きの形で読む。置き場 `research/fixations/<experiment_id>_<execution>.yaml`（版管理。
  origin の既定ブランチ上のものだけを読む）、項目は固定時点 `t_fix`・長さ `length_days`（下限 364。
  D15-11 (a)）・組ごとの固定した試行 `fixed_trials`。前向き検証の開始は固定時点の後の最初の完全な
  取引週の始まり（ADR-0038 第5項の 2）として導く（記録に書かない）。

値は件数・時刻・文だけで、価格を含まない。
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
    "FIXATIONS_DIRECTORY",
    "MIN_LENGTH_DAYS",
    "Fixation",
    "fixation_path",
    "load_fixation",
]

#: 候補の固定の記録の置き場（R4 の仮置き。F1 が決めたら合わせる）。
FIXATIONS_DIRECTORY: Final = Path("research") / "fixations"
#: 長さ `L` の下限（D15-11 (a)）。
MIN_LENGTH_DAYS: Final = 364

_SCHEMA_VERSION: Final = 1


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
