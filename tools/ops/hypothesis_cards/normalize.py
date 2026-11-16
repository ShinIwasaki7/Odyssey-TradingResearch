"""既出の規則との重なりの検査のための正規化（D25 §3.5 の 6、仮置き 29。決定 173）。

戦略の宣言（D04 の形の本体）から、**インスタンスの名前とパラメータの値を外し**、部品の名前・版・
接続（どの出力がどの入力へ）・起動の時点と系列・役割の割当で正規化した形のダイジェストを作る。
部品の組み合わせと接続が同じなら同じ規則、値だけの違いも同じ規則（D22 の仮置き 28 の読み方）。

**通貨の違いは数える**（決定 173）: 市場データの参照（`market:EURUSD/1h/bid:CLOSE`）と起動の系列
（`EURUSD/1h/bid`）は字面のまま残す。通貨が違えば別の規則になる。

正規化の手順:

1. 使用箇所ごとに、名前を使わない「形の印」を作る: 部品の名前と版、入力の名前ごとの接続元の並び
   （書いた順。部品の出力は「接続元の形の印と出力の名前」に置き換える。市場データ・実行時の文脈は
   字面のまま）、起動の並び（`when` と系列・入力の名前・出来事。起動の名前は外す）。パラメータは
   名前も値も外す（書いたか既定値かは値の違いである）。
2. 部品の出力への接続は、接続元の形の印を先に作ってから使う（接続は取引機会の前から後へ流れる
   ので、輪があれば道具の外の誤り）。
3. 役割（`roles`）は「接続元の形の印と出力の名前」に、取引機会の有効性の束縛（`opportunity_validity`
   の `source`）も同じに置き換える。入場の方針は種類（`entry_policy.kind`）だけを残す（期限などの
   値は外す）。同時の保持（`opportunity_concurrency`）は値なので外す。
4. 使用箇所の形の印の多重集合（並べ替えた並び）と、役割・束縛・入場の方針の種類を、正規の JSON に
   して SHA-256 をとる。

入場の方針の種類と束縛の接続元を残すのは、仮置き 29 の列挙（部品・接続・起動・役割）の外である
（本 PR の仮置き。確認待ちの有無で取引機会の扱いが変わるため、同じ規則として弾かない側に倒した）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Final

__all__ = ["NormalizationError", "rule_digest"]

_OUTPUT_PREFIX: Final = "output:"


class NormalizationError(ValueError):
    """正規化できない宣言（接続が輪になる・書式が壊れている）。"""


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _split_output(reference: str) -> tuple[str, str]:
    """`<instance>.<port>` を分ける。"""
    instance, dot, port = reference.partition(".")
    if not dot or not instance or not port:
        raise NormalizationError(f"出力の参照の形でない: {reference!r}")
    return instance, port


class _Labeler:
    def __init__(self, components: list[Mapping[str, Any]]) -> None:
        self._by_id: dict[str, Mapping[str, Any]] = {}
        for item in components:
            instance = item.get("instance_id")
            if not isinstance(instance, str):
                raise NormalizationError("使用箇所に `instance_id` が無い")
            if instance in self._by_id:
                raise NormalizationError(f"使用箇所 {instance!r} が 2 度ある")
            self._by_id[instance] = item
        self._labels: dict[str, str] = {}
        self._visiting: set[str] = set()

    def instances(self) -> tuple[str, ...]:
        return tuple(self._by_id)

    def output(self, reference: str) -> list[str]:
        instance, port = _split_output(reference)
        return [self.label(instance), port]

    def _source(self, source: object) -> object:
        if isinstance(source, str) and source.startswith(_OUTPUT_PREFIX):
            return ["output", *self.output(source[len(_OUTPUT_PREFIX) :])]
        return source

    def label(self, instance: str) -> str:
        if instance in self._labels:
            return self._labels[instance]
        item = self._by_id.get(instance)
        if item is None:
            raise NormalizationError(f"接続先の使用箇所 {instance!r} が宣言に無い")
        if instance in self._visiting:
            raise NormalizationError(f"使用箇所 {instance!r} の接続が輪になる")
        self._visiting.add(instance)
        inputs = item.get("inputs") or {}
        if not isinstance(inputs, Mapping):
            raise NormalizationError(f"使用箇所 {instance!r} の `inputs` が mapping でない")
        wired: dict[str, list[object]] = {}
        for name, sources in inputs.items():
            listed = sources if isinstance(sources, list) else [sources]
            wired[str(name)] = [self._source(source) for source in listed]
        triggers = item.get("triggers") or []
        if not isinstance(triggers, list):
            raise NormalizationError(f"使用箇所 {instance!r} の `triggers` が並びでない")
        shaped_triggers = sorted(
            _canonical(
                {key: value for key, value in trigger.items() if key != "name"}
                if isinstance(trigger, Mapping)
                else trigger
            )
            for trigger in triggers
        )
        shape = {
            "component": item.get("component"),
            "component_version": item.get("component_version"),
            "inputs": wired,
            "triggers": shaped_triggers,
        }
        self._visiting.discard(instance)
        self._labels[instance] = _sha(shape)
        return self._labels[instance]


def rule_digest(body: Mapping[str, Any]) -> str:
    """戦略の宣言の本体（またはファイルの全体）の正規化した形のダイジェスト（16 進 64 文字）。"""
    components = body.get("components")
    if not isinstance(components, list) or not all(isinstance(c, Mapping) for c in components):
        raise NormalizationError("`components` が mapping の並びでない")
    labeler = _Labeler(components)
    labels = sorted(labeler.label(instance) for instance in labeler.instances())
    roles = body.get("roles") or {}
    if not isinstance(roles, Mapping):
        raise NormalizationError("`roles` が mapping でない")
    shaped_roles = {
        str(role): (labeler.output(source) if isinstance(source, str) else source)
        for role, source in roles.items()
        if source is not None
    }
    validity = body.get("opportunity_validity") or {}
    bindings = validity.get("bindings") if isinstance(validity, Mapping) else None
    shaped_bindings = sorted(
        _canonical(labeler.output(item["source"]))
        for item in (bindings if isinstance(bindings, list) else [])
        if isinstance(item, Mapping) and isinstance(item.get("source"), str)
    )
    entry = body.get("entry_policy")
    entry_kind = entry.get("kind") if isinstance(entry, Mapping) else None
    return _sha(
        {
            "components": labels,
            "roles": shaped_roles,
            "validity_sources": shaped_bindings,
            "entry_policy_kind": entry_kind,
        }
    )
