"""改善の変更の提案の書式・静的な検査・適用（D25 §3.9、決定 102・103、仮置き 6・12）。

分析役は版ごとに変更の提案を 1 つ書く（束の `out/change.yaml`）。実行器が検査し、通れば
`research/loops/<カード>/change_v<次の版>.yaml` に写して改善後の事前登録を生成する。

本モジュールは、生成の前に当てられる検査（第3.9節の (1)〜(4)・(6)・(7)）と、操作の適用を持つ。
適用の後の検査（(5) 部品・履歴窓・試行 4〜6・複雑さ、と生成・読込・コンパイル）は
`screening.check_change` が当てる。

| 検査 | 不合格の理由の識別子 |
|---|---|
| (1) 書式と型（項目・語彙・カードの識別子・`from_version`） | `CHANGE_OUT_OF_RULES` |
| (2) `operations` が全部 `change_kind` の種類（1 種類。決定 102） | `CHANGE_OUT_OF_RULES` |
| (3) 次の版が 3 を超えない（再評価 2 回まで。決定 103） | `CHANGE_OUT_OF_RULES` |
| (4) `FINISH` が版 1 の後でない（仮置き 12） | `CHANGE_OUT_OF_RULES` |
| (6) 操作の値が書いてある（決定 67 の形） | `VALUE_MISSING` |
| (7) 自由文（`reason`・`prediction`）に数字が無い（仮置き 6） | `CHANGE_OUT_OF_RULES` |

`decision` は `CHANGE`（変更する）・`FINISH`（終える。版 2 の後から）・`AMBIGUOUS`（規則に無い判断が
要る。理由の識別子 `reason_id` を書く。第5.4節）。`FINISH`・`AMBIGUOUS` の形は本 PR の仮置き。

操作（第3.9節の表。どれも型のある値だけ）:

- `PARAMETERS`: `set_axis_values`（`instance`・`parameter`・`values`。既にある軸の値を置き換える）、
  `add_axis`（同じ項目。無い軸を足す）、`remove_axis`（`instance`・`parameter`）。
- `STRATEGY_CODE`: `set_parameter`（`instance`・`parameter`・`value`。D04 の形 `{type, value}`。
  軸でないパラメータ）、`add_component`（`component`。D04 の形の使用箇所 1 つ。`instance_id` は
  新しい名前）、`remove_component`（`instance`）、`rewire_input`（`instance`・`input`・`sources`。
  接続元の並びを置き換える）、`set_role`（`role`・`source`。`<instance>.<出力>`。`null` は任意の
  役割を外す）。

各操作の項目の名前と、`role` の語彙は第3.9節の表の具体化（本 PR の仮置き）。
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from tools.ops.hypothesis_cards.card import Axis, Card, has_digit

__all__ = [
    "CHANGE_OUT_OF_RULES",
    "CHANGE_PREREG_REJECTED",
    "MAX_VERSION",
    "OPERATION_KINDS",
    "VALUE_MISSING",
    "Change",
    "ChangeRejected",
    "LoopState",
    "apply_change",
    "initial_state",
    "parse_change",
]

CHANGE_OUT_OF_RULES: Final = "CHANGE_OUT_OF_RULES"
VALUE_MISSING: Final = "VALUE_MISSING"
CHANGE_PREREG_REJECTED: Final = "CHANGE_PREREG_REJECTED"
#: 版の最大（元の版 1 と、再評価 2 回。決定 103）。
MAX_VERSION: Final = 3
_SCHEMA_VERSION: Final = 1
_HEX64: Final = re.compile(r"^[0-9a-f]{64}$")
_IDENT: Final = re.compile(r"^[A-Z][A-Z0-9_]*$")

#: 操作 → 種類（第3.9節の表）。
OPERATION_KINDS: Final[Mapping[str, str]] = {
    "set_axis_values": "PARAMETERS",
    "add_axis": "PARAMETERS",
    "remove_axis": "PARAMETERS",
    "set_parameter": "STRATEGY_CODE",
    "add_component": "STRATEGY_CODE",
    "remove_component": "STRATEGY_CODE",
    "rewire_input": "STRATEGY_CODE",
    "set_role": "STRATEGY_CODE",
}
#: 操作ごとの項目（名前の項目, 値の項目）。値の項目が無い操作は `VALUE_MISSING`（決定 67 の形）。
_OPERATION_FIELDS: Final[Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]]] = {
    "set_axis_values": (("instance", "parameter"), ("values",)),
    "add_axis": (("instance", "parameter"), ("values",)),
    "remove_axis": (("instance", "parameter"), ()),
    "set_parameter": (("instance", "parameter"), ("value",)),
    "add_component": ((), ("component",)),
    "remove_component": (("instance",), ()),
    "rewire_input": (("instance", "input"), ("sources",)),
    "set_role": (("role",), ("source",)),
}
#: 役割の名前（D04 §3 の名前付きフィールド）。外せるのは任意の役割だけ。
_ROLES: Final = ("market_state", "trigger", "execution_filter", "order", "protection", "exit")
_OPTIONAL_ROLES: Final = ("market_state", "execution_filter")
_DECISIONS: Final = ("CHANGE", "FINISH", "AMBIGUOUS")


class ChangeRejected(ValueError):
    """変更の提案が規則の外（停止の型。`reason` は理由の識別子）。"""

    def __init__(self, reason: str, problems: Sequence[str]) -> None:
        super().__init__(f"{reason}: " + "; ".join(problems))
        self.reason = reason
        self.problems = tuple(problems)


@dataclass(frozen=True, slots=True)
class Change:
    """読んだ変更の提案（`raw` は写すファイルのバイト列。注記に SHA-256 を書く）。"""

    raw: bytes
    card_id: str
    from_version: int
    decision: str
    change_kind: str | None
    operations: tuple[Mapping[str, Any], ...]
    reason: str | None
    reason_id: str | None
    prediction: str | None
    memo_sha256: str

    @property
    def to_version(self) -> int:
        return self.from_version + 1


@dataclass(frozen=True, slots=True)
class LoopState:
    """ある版の事前登録の材料（戦略の宣言の本体・格子・戦略の版）。"""

    strategy: Mapping[str, Any]
    axes: tuple[Axis, ...]
    strategy_version: int


def initial_state(card: Card) -> LoopState:
    """版 1 の材料（カードの戦略と格子をそのまま。決定 171）。"""
    return LoopState(
        strategy=copy.deepcopy(dict(card.strategy)), axes=card.axes, strategy_version=1
    )


def _require(data: Mapping[str, Any], keys: Sequence[str], optional: Sequence[str]) -> list[str]:
    problems = [f"`{key}`: 必須の項目が無い" for key in keys if key not in data]
    problems += [
        f"`{key}`: 書式に無い項目" for key in data if key not in keys and key not in optional
    ]
    return problems


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def parse_change(raw: bytes, *, card_id: str, expected_from_version: int) -> Change:
    """変更の提案を読み、静的な検査（(1)〜(4)・(6)・(7)）を当てる。

    `expected_from_version` は、いま結果を読んだ版（実行器の状態の版）。違えば (1) の不合格。
    不合格は `ChangeRejected`（理由の識別子つき）。
    """
    try:
        data = load_yaml_mapping(Path("change.yaml"), text=raw.decode("utf-8"))
    except (ConfigError, UnicodeDecodeError):
        raise ChangeRejected(CHANGE_OUT_OF_RULES, ["YAML として読めない"]) from None
    decision = data.get("decision")
    base = ("schema_version", "card", "from_version", "decision", "memo_sha256")
    if decision == "CHANGE":
        problems = _require(data, (*base, "change_kind", "operations", "reason", "prediction"), ())
    elif decision == "FINISH":
        problems = _require(data, (*base, "reason"), ("prediction",))
    elif decision == "AMBIGUOUS":
        problems = _require(data, (*base, "reason_id"), ("reason",))
    else:
        problems = [f"`decision`: 語彙（{' / '.join(_DECISIONS)}）の外"]
    version = data.get("schema_version")
    if isinstance(version, bool) or version != _SCHEMA_VERSION:
        problems.append(f"`schema_version`: {_SCHEMA_VERSION} でない")
    if data.get("card") != card_id:
        problems.append("`card`: このカードの識別子でない")
    from_version = data.get("from_version")
    if isinstance(from_version, bool) or not isinstance(from_version, int):
        problems.append("`from_version`: 整数でない")
        from_version = 0
    elif from_version != expected_from_version:
        problems.append("`from_version`: いま結果を読んだ版でない")
    memo = data.get("memo_sha256")
    if not isinstance(memo, str) or _HEX64.match(memo) is None:
        problems.append("`memo_sha256`: SHA-256（16 進 64 文字の小文字）でない")
        memo = ""
    for key in ("reason", "prediction"):
        if key in data and not _nonempty_text(data[key]):
            problems.append(f"`{key}`: 空でない文字列でない")
    reason_id = data.get("reason_id")
    if decision == "AMBIGUOUS" and (not isinstance(reason_id, str) or not _IDENT.match(reason_id)):
        problems.append("`reason_id`: 理由の識別子（英大文字・数字・_）でない")
    change_kind = data.get("change_kind")
    operations_raw = data.get("operations")
    operations: list[Mapping[str, Any]] = []
    if decision == "CHANGE":
        if change_kind not in ("PARAMETERS", "STRATEGY_CODE"):
            problems.append("`change_kind`: 語彙（PARAMETERS / STRATEGY_CODE）の外")
        if not isinstance(operations_raw, list) or not operations_raw:
            problems.append("`operations`: 1 つ以上の並びでない")
        else:
            for index, operation in enumerate(operations_raw):
                if not isinstance(operation, dict) or operation.get("op") not in OPERATION_KINDS:
                    problems.append(f"`operations[{index}].op`: 操作の語彙の外")
                else:
                    operations.append(operation)
    if problems:
        raise ChangeRejected(CHANGE_OUT_OF_RULES, problems)
    # (2) 1 種類だけ（決定 102）。
    mixed = [
        f"`operations[{index}]` が `change_kind` の種類でない"
        for index, operation in enumerate(operations)
        if OPERATION_KINDS[operation["op"]] != change_kind
    ]
    if mixed:
        raise ChangeRejected(CHANGE_OUT_OF_RULES, [*mixed, "変更は 1 回 1 種類（決定 102）"])
    # (3) 版は 3 まで（再評価 2 回まで。決定 103）。
    if decision == "CHANGE" and from_version + 1 > MAX_VERSION:
        raise ChangeRejected(
            CHANGE_OUT_OF_RULES, ["版が 3 を超える（再評価は 2 回まで。決定 103）"]
        )
    # (4) 終えるのは版 2 の後から（仮置き 12）。
    if decision == "FINISH" and from_version < 2:
        raise ChangeRejected(CHANGE_OUT_OF_RULES, ["1 回目の改善は必ず行う（版 1 の後に終えない）"])
    # (6) 値が書いてある（決定 67 の形）。
    missing: list[str] = []
    shape: list[str] = []
    for index, operation in enumerate(operations):
        names, values = _OPERATION_FIELDS[operation["op"]]
        missing += [
            f"`operations[{index}].{key}`: 値が無い"
            for key in values
            if key not in operation or (operation[key] is None and key != "source")
        ]
        shape += _require(operation, ("op", *names), values)
    if missing:
        raise ChangeRejected(VALUE_MISSING, missing)
    if shape:
        raise ChangeRejected(CHANGE_OUT_OF_RULES, [f"操作の項目: {item}" for item in shape])
    # (7) 自由文に数字が無い（仮置き 6）。
    digits = [
        f"自由文 `{key}` に数字がある（仮置き 6）"
        for key in ("reason", "prediction")
        if isinstance(data.get(key), str) and has_digit(data[key])
    ]
    if digits:
        raise ChangeRejected(CHANGE_OUT_OF_RULES, digits)
    return Change(
        raw=raw,
        card_id=card_id,
        from_version=from_version,
        decision=str(decision),
        change_kind=change_kind if isinstance(change_kind, str) else None,
        operations=tuple(operations),
        reason=data.get("reason") if isinstance(data.get("reason"), str) else None,
        reason_id=reason_id if isinstance(reason_id, str) else None,
        prediction=data.get("prediction") if isinstance(data.get("prediction"), str) else None,
        memo_sha256=memo,
    )


# --- 適用 ---------------------------------------------------------------------------


def _scalar_values(value: object, where: str) -> tuple[bool | int | float | str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, bool | int | float | str) for item in value)
    ):
        raise ChangeRejected(CHANGE_OUT_OF_RULES, [f"{where}: 値の 1 つ以上の並びでない"])
    return tuple(value)


def _components(strategy: dict[str, Any]) -> list[dict[str, Any]]:
    components = strategy.get("components")
    if not isinstance(components, list):
        raise ChangeRejected(CHANGE_OUT_OF_RULES, ["戦略の `components` が並びでない"])
    return components


def _component(strategy: dict[str, Any], instance: object, where: str) -> dict[str, Any]:
    for item in _components(strategy):
        if isinstance(item, dict) and item.get("instance_id") == instance:
            return item
    raise ChangeRejected(CHANGE_OUT_OF_RULES, [f"{where}: 使用箇所 {instance!r} が戦略に無い"])


def _apply_parameters(axes: list[Axis], operation: Mapping[str, Any], where: str) -> None:
    key = (operation["instance"], operation["parameter"])
    position = next(
        (index for index, axis in enumerate(axes) if (axis.instance, axis.parameter) == key), None
    )
    op = operation["op"]
    if op == "remove_axis":
        if position is None:
            raise ChangeRejected(CHANGE_OUT_OF_RULES, [f"{where}: 外す軸が無い"])
        del axes[position]
        return
    values = _scalar_values(operation["values"], f"{where}.values")
    if op == "set_axis_values":
        if position is None:
            raise ChangeRejected(CHANGE_OUT_OF_RULES, [f"{where}: 値を変える軸が無い"])
        axes[position] = replace(axes[position], values=values)
        return
    if position is not None:
        raise ChangeRejected(CHANGE_OUT_OF_RULES, [f"{where}: 足す軸が既にある"])
    if not isinstance(key[0], str) or not isinstance(key[1], str):
        raise ChangeRejected(CHANGE_OUT_OF_RULES, [f"{where}: 軸の名前が文字列でない"])
    axes.append(Axis(key[0], key[1], values))


def _apply_strategy(
    strategy: dict[str, Any], axes: Sequence[Axis], operation: Mapping[str, Any], where: str
) -> None:
    op = operation["op"]
    if op == "set_parameter":
        if any(
            (axis.instance, axis.parameter) == (operation["instance"], operation["parameter"])
            for axis in axes
        ):
            raise ChangeRejected(
                CHANGE_OUT_OF_RULES, [f"{where}: 軸のパラメータは `set_parameter` で変えない"]
            )
        value = operation["value"]
        if not isinstance(value, dict) or set(value) != {"type", "value"}:
            raise ChangeRejected(
                CHANGE_OUT_OF_RULES, [f"{where}.value: `{{type, value}}` の形でない"]
            )
        component = _component(strategy, operation["instance"], where)
        parameters = component.setdefault("parameters", {})
        if not isinstance(parameters, dict):
            raise ChangeRejected(CHANGE_OUT_OF_RULES, [f"{where}: `parameters` が mapping でない"])
        parameters[operation["parameter"]] = copy.deepcopy(value)
    elif op == "add_component":
        added = operation["component"]
        if not isinstance(added, dict) or not isinstance(added.get("instance_id"), str):
            raise ChangeRejected(
                CHANGE_OUT_OF_RULES, [f"{where}.component: `instance_id` を持つ mapping でない"]
            )
        if any(
            isinstance(item, dict) and item.get("instance_id") == added["instance_id"]
            for item in _components(strategy)
        ):
            raise ChangeRejected(CHANGE_OUT_OF_RULES, [f"{where}: 同じ名前の使用箇所が既にある"])
        _components(strategy).append(copy.deepcopy(added))
    elif op == "remove_component":
        component = _component(strategy, operation["instance"], where)
        if any(axis.instance == operation["instance"] for axis in axes):
            raise ChangeRejected(
                CHANGE_OUT_OF_RULES, [f"{where}: 格子の軸を持つ使用箇所は外さない"]
            )
        _components(strategy).remove(component)
    elif op == "rewire_input":
        component = _component(strategy, operation["instance"], where)
        sources = operation["sources"]
        if (
            not isinstance(sources, list)
            or not sources
            or not all(isinstance(s, str) for s in sources)
        ):
            raise ChangeRejected(CHANGE_OUT_OF_RULES, [f"{where}.sources: 文字列の並びでない"])
        inputs = component.get("inputs")
        if not isinstance(inputs, dict) or operation["input"] not in inputs:
            raise ChangeRejected(CHANGE_OUT_OF_RULES, [f"{where}: つなぎ替える入力が無い"])
        inputs[operation["input"]] = list(sources)
    elif op == "set_role":
        role, source = operation["role"], operation["source"]
        if role not in _ROLES:
            raise ChangeRejected(CHANGE_OUT_OF_RULES, [f"{where}.role: 役割の語彙の外"])
        roles = strategy.get("roles")
        if not isinstance(roles, dict):
            raise ChangeRejected(CHANGE_OUT_OF_RULES, ["戦略の `roles` が mapping でない"])
        if source is None:
            if role not in _OPTIONAL_ROLES:
                raise ChangeRejected(CHANGE_OUT_OF_RULES, [f"{where}: 必須の役割は外さない"])
            roles.pop(role, None)
        elif isinstance(source, str):
            roles[role] = source
        else:
            raise ChangeRejected(CHANGE_OUT_OF_RULES, [f"{where}.source: 文字列か null でない"])


def apply_change(state: LoopState, change: Change) -> LoopState:
    """変更の提案の操作を書いた順に当て、次の版の材料を返す（`CHANGE` だけ）。

    `PARAMETERS` は格子だけを変え、戦略の版を上げない。`STRATEGY_CODE` は戦略の宣言の本体を変え、
    戦略の版を 1 つ上げる（第3.9節）。当てられない操作は `ChangeRejected`（`CHANGE_OUT_OF_RULES`）。
    """
    if change.decision != "CHANGE":
        raise ChangeRejected(CHANGE_OUT_OF_RULES, ["`CHANGE` でない提案は当てない"])
    strategy = copy.deepcopy(dict(state.strategy))
    axes = list(state.axes)
    for index, operation in enumerate(change.operations):
        where = f"`operations[{index}]`"
        if change.change_kind == "PARAMETERS":
            _apply_parameters(axes, operation, where)
        else:
            _apply_strategy(strategy, axes, operation, where)
    if not axes:
        raise ChangeRejected(CHANGE_OUT_OF_RULES, ["格子の軸が無くなる"])
    bumped = state.strategy_version + (1 if change.change_kind == "STRATEGY_CODE" else 0)
    return LoopState(strategy=strategy, axes=tuple(axes), strategy_version=bumped)
