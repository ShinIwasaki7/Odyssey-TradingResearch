"""候補の固定の記録（YAML）の書き出しと読込（D09 §9.11.12 の 2。順 11 の PR 4c）。

置き場は `research/candidate_fixes/<experiment_id>/<execution>/<fix_no>.yaml`（仮置き F1-2。
D25 §6.3 の検査 8 がこのパスを見る）。項目はすべて必須（`derived_from` と `supersedes` は
`null` を許す）。

- **書き出し** `candidate_fix_text`: 固定のコマンドが作る本文。値はダイジェスト（16 進
  64 文字）・整数・識別子と、記録者の文（JSON の文字列の書き方で引用する。YAML の二重引用の
  書き方と同じ）だけで、キーの順は D09 §9.11.12 の 2 の表の順に固定する（同じ値なら同じ
  バイト列）。
- **読込** `load_candidate_fix`: 本文を読み、型と型だけで決まる検査（`CandidateFix`）を当て、
  置き場のパス（実験の識別子・実行番号・`fix_no`）と中身が一致することを確かめる。どの誤りも
  構造の誤り（`ConfigError`）。

固定の記録には `T_fix`・`S`・`W`・`S'`・`E` を書かない（D09 §9.11.3・§9.11.6）。頻度 `r` と
取引の件数も書かない（仮置き F1-5。材料の識別子とダイジェストだけ）。成績・価格の値は持たない。
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Final

from odyssey_fx.app.config.forward_validation import CANDIDATE_FIXES_DIRECTORY
from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import ExperimentId
from odyssey_fx.common.refs import CompiledStrategyRef, ContentDigest
from odyssey_fx.evaluation.domain.forward_validation import (
    CANDIDATE_FIX_SCHEMA_VERSION,
    CandidateFix,
    CandidateFixId,
    FixCandidate,
    FixedTrialPin,
    FrequencyMaterialRef,
    VersionedRef,
)
from odyssey_fx.evaluation.domain.search import LineageSource
from odyssey_fx.evaluation.domain.variant import VARIANTS, Paired

__all__ = [
    "candidate_fix_relative_path",
    "candidate_fix_text",
    "load_candidate_fix",
    "parse_candidate_fix_path",
]

_FIX_FILE: Final = re.compile(r"(?P<fix_no>[1-9][0-9]*)\.yaml")
_EXECUTION: Final = re.compile(r"[1-9][0-9]*")
_HEX64: Final = re.compile(r"[0-9a-f]{64}")
_HEADER: Final = (
    "# 候補の固定の記録（D09 §9.11.12 の 2）。\n"
    "# 固定のコマンド（odyssey-fx experiment fix）が作った。\n"
    "# 固定時点 T_fix は、この記録を初めて含んだ main の first-parent のコミットの\n"
    "# committer の時刻（仮置き F1-3）。main に入った後は 1 バイトも変えない\n"
    "# （変えれば読込で拒否される）。直すときは新しい固定の記録を作る（D09 §9.11.9）。\n"
)


class _CandidateModel(StrictModel):
    experiment_id: str
    execution: int


class _PinModel(StrictModel):
    trial_index: int
    assignment_digest: str
    compiled_ref: str


class _TrialsModel(StrictModel):
    standard: _PinModel
    candidate: _PinModel


class _RefModel(StrictModel):
    id: str
    version: int
    digest: str


class _MaterialModel(StrictModel):
    material_id: str
    digest: str


class _SupersedesModel(StrictModel):
    experiment_id: str
    execution: int
    fix_no: int


class _FixModel(StrictModel):
    schema_version: int
    candidate: _CandidateModel
    fix_no: int
    fixed_trials: _TrialsModel
    record_sheet_digest: str
    research_policy: _RefModel
    forward_rules: _RefModel
    adoption_review_row_digest: str
    length_days: int
    frequency_source: _MaterialModel
    derived_from: _CandidateModel | None
    supersedes: _SupersedesModel | None
    fixed_by: str


def candidate_fix_relative_path(experiment_id: ExperimentId, execution: int, fix_no: int) -> str:
    """固定の記録のリポジトリの根からの相対パス（`/` 区切り）。"""
    return (
        PurePosixPath(CANDIDATE_FIXES_DIRECTORY.as_posix())
        / experiment_id.hex
        / str(execution)
        / f"{fix_no}.yaml"
    ).as_posix()


def parse_candidate_fix_path(rel: str) -> CandidateFixId | None:
    """相対パスから固定の記録の識別子を読む（置き場の形でなければ `None`）。"""
    parts = PurePosixPath(rel).parts
    prefix = PurePosixPath(CANDIDATE_FIXES_DIRECTORY.as_posix()).parts
    if len(parts) != len(prefix) + 3 or parts[: len(prefix)] != prefix:
        return None
    experiment, execution, name = parts[len(prefix) :]
    matched = _FIX_FILE.fullmatch(name)
    if not _HEX64.fullmatch(experiment) or not _EXECUTION.fullmatch(execution) or not matched:
        return None
    return CandidateFixId(
        experiment_id=ExperimentId(ContentDigest.sha256(experiment)),
        execution=int(execution),
        fix_no=int(matched.group("fix_no")),
    )


def _q(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def candidate_fix_text(record: CandidateFix) -> str:
    """固定の記録の本文（D09 §9.11.12 の 2 の表の順。同じ値なら同じバイト列）。"""
    if not isinstance(record, CandidateFix):
        raise KernelValueError("candidate_fix_text requires a CandidateFix")

    def pin(name: str, value: FixedTrialPin) -> list[str]:
        return [
            f"  {name}:",
            f"    trial_index: {value.trial_index}",
            f"    assignment_digest: {_q(value.assignment_digest.hex)}",
            f"    compiled_ref: {_q(value.compiled_ref.digest.hex)}",
        ]

    def ref(name: str, value: VersionedRef) -> list[str]:
        return [
            f"{name}:",
            f"  id: {value.ref_id}",
            f"  version: {value.version}",
            f"  digest: {_q(value.digest.hex)}",
        ]

    lineage = record.derived_from
    previous = record.supersedes
    lines = [
        f"schema_version: {CANDIDATE_FIX_SCHEMA_VERSION}",
        "candidate:",
        f"  experiment_id: {_q(record.candidate.experiment_id.hex)}",
        f"  execution: {record.candidate.execution}",
        f"fix_no: {record.fix_no}",
        "fixed_trials:",
        *(
            line
            for variant in VARIANTS
            for line in pin(variant.value, record.fixed_trials.of(variant))
        ),
        f"record_sheet_digest: {_q(record.record_sheet_digest.hex)}",
        *ref("research_policy", record.research_policy),
        *ref("forward_rules", record.forward_rules),
        f"adoption_review_row_digest: {_q(record.adoption_review_row_digest.hex)}",
        f"length_days: {record.length_days}",
        "frequency_source:",
        f"  material_id: {_q(record.frequency_source.material_id)}",
        f"  digest: {_q(record.frequency_source.digest.hex)}",
        *(
            ["derived_from: null"]
            if lineage is None
            else [
                "derived_from:",
                f"  experiment_id: {_q(lineage.experiment_id.hex)}",
                f"  execution: {lineage.execution}",
            ]
        ),
        *(
            ["supersedes: null"]
            if previous is None
            else [
                "supersedes:",
                f"  experiment_id: {_q(previous.experiment_id.hex)}",
                f"  execution: {previous.execution}",
                f"  fix_no: {previous.fix_no}",
            ]
        ),
        f"fixed_by: {_q(record.fixed_by)}",
    ]
    return _HEADER + "\n".join(lines) + "\n"


def _digest(text: str, label: str) -> ContentDigest:
    if not _HEX64.fullmatch(text):
        raise ConfigError(f"{label} は 16 進 64 文字（小文字）: {text!r}")
    return ContentDigest.sha256(text)


def _experiment(text: str, label: str) -> ExperimentId:
    return ExperimentId(_digest(text, label))


def load_candidate_fix(rel: str, *, text: str) -> CandidateFix:
    """固定の記録の本文を読む（D09 §9.11.12 の 2。置き場のパスと中身の一致も確かめる）。

    `rel` はリポジトリの根からの相対パス（`research/candidate_fixes/<id>/<実行>/<fix_no>.yaml`）。
    形が違う・項目の欠け・知らない項目・型の誤り・パスと中身の不一致は `ConfigError`。
    """
    path = Path(rel)
    expected = parse_candidate_fix_path(rel)
    if expected is None:
        raise ConfigError(
            f"{rel} は固定の記録の置き場 {CANDIDATE_FIXES_DIRECTORY.as_posix()}/<experiment_id>/"
            "<execution>/<fix_no>.yaml の形ではない（仮置き F1-2）"
        )
    payload = load_yaml_mapping(path, text=text)
    model = validate(_FixModel, payload, path)
    require_schema_version(model.schema_version, CANDIDATE_FIX_SCHEMA_VERSION, path)
    try:
        trials = model.fixed_trials
        record = CandidateFix(
            candidate=FixCandidate(
                experiment_id=_experiment(model.candidate.experiment_id, f"{rel}: candidate"),
                execution=model.candidate.execution,
            ),
            fix_no=model.fix_no,
            fixed_trials=Paired(
                standard=_pin(trials.standard, f"{rel}: fixed_trials.standard"),
                candidate=_pin(trials.candidate, f"{rel}: fixed_trials.candidate"),
            ),
            record_sheet_digest=_digest(model.record_sheet_digest, f"{rel}: record_sheet_digest"),
            research_policy=_ref(model.research_policy, f"{rel}: research_policy"),
            forward_rules=_ref(model.forward_rules, f"{rel}: forward_rules"),
            adoption_review_row_digest=_digest(
                model.adoption_review_row_digest, f"{rel}: adoption_review_row_digest"
            ),
            length_days=model.length_days,
            frequency_source=FrequencyMaterialRef(
                material_id=model.frequency_source.material_id,
                digest=_digest(model.frequency_source.digest, f"{rel}: frequency_source.digest"),
            ),
            derived_from=None
            if model.derived_from is None
            else LineageSource(
                experiment_id=_experiment(model.derived_from.experiment_id, f"{rel}: derived_from"),
                execution=model.derived_from.execution,
            ),
            supersedes=None
            if model.supersedes is None
            else CandidateFixId(
                experiment_id=_experiment(model.supersedes.experiment_id, f"{rel}: supersedes"),
                execution=model.supersedes.execution,
                fix_no=model.supersedes.fix_no,
            ),
            fixed_by=model.fixed_by,
        )
    except KernelValueError as exc:
        raise ConfigError(f"{rel}: 固定の記録として成立しない: {exc}") from exc
    if record.fix_id != expected:
        raise ConfigError(
            f"{rel}: 置き場のパスが指す ({expected.experiment_id.hex}, {expected.execution},"
            f" {expected.fix_no}) と中身の ({record.candidate.experiment_id.hex},"
            f" {record.candidate.execution}, {record.fix_no}) が違う（D09 §9.11.12 の 2）"
        )
    return record


def _pin(model: _PinModel, label: str) -> FixedTrialPin:
    return FixedTrialPin(
        trial_index=model.trial_index,
        assignment_digest=_digest(model.assignment_digest, f"{label}.assignment_digest"),
        compiled_ref=CompiledStrategyRef(_digest(model.compiled_ref, f"{label}.compiled_ref")),
    )


def _ref(model: _RefModel, label: str) -> VersionedRef:
    return VersionedRef(
        ref_id=model.id, version=model.version, digest=_digest(model.digest, f"{label}.digest")
    )
