"""依存グラフと評価順（D05 §5.4、D04 §12 #6）。

節点は使用箇所、辺は「どちらが先に評価されていなければならないか」である。辺は5種類ある。

| 種類 | いつ引くか |
|---|---|
| `EXPLICIT_INPUT` | 入力が他の使用箇所の出力に接続されている |
| `FILL_TRIGGER` | 注文意図を出す使用箇所 → 約定通知で起動する使用箇所 |
| `POSITION_CONTEXT` | 注文意図を出す使用箇所 → 現在の建玉を読む使用箇所 |
| `MARKET_STATE` | 市場状態の使用箇所 → 取引機会を出す使用箇所（段階3。D04 §12 v1.9 の3本目） |
| `OPPORTUNITY_CONTEXT` | 取引機会を出す使用箇所 → 取引機会を読む使用箇所（段階3。D04 §12 v1.13） |

後ろ4つが**エンジン上の因果辺**である（D04 §12）。明示的な接続だけを辺にすると、注文が
エンジンを一周して戻ってくる帰還路を見逃す。たとえば注文意図を出す使用箇所が約定通知でも
起動する宣言は自己ループであり、これがないとコンパイラが通してしまう。市場状態の辺は、
ランタイムが取引機会の生成時に取引許可を役割フィールド経由で読む（明示の接続が無い）ために
要る（D05 §7.6、Q10 決定）。辺が無いと、市場状態が取引機会より後に評価される評価順が通って
しまう（D05 §5.6 の検査 d）。

**種類を残したまま保持する**のは、閉路が明示接続によるものか帰還路によるものかを誤りの
説明に書けるようにするためである。

評価順は**トポロジカル順、同順位は使用箇所 ID の Unicode コードポイント順**とする
（D05 §5.4 v1.3）。「同順位」は同じ段、すなわち上流を評価し終えた時点で同時に評価できる
使用箇所の集まりである（Kahn 法で段ごとにまとめ、段の中を ID 順に並べる）。並びを固定しない
と、同じ宣言から出力の通し番号が変わり、「同一入力の再実行で判断履歴が一致する」（全体計画
§8.2）を満たせない。時間足の大小から順序を推測しない。

取引機会の辺は、確認部品が同じ判断時点で生まれた取引機会を開始足で確認する（D05 §7.7）
ために要る。取引機会を読む入力（`RuntimeInputRef(OPPORTUNITY)`）は明示の接続を持たないので、
辺が無いと確認部品が取引機会を出す部品より前に評価される評価順が通ってしまう
（2026-09-24 の人間の決定）。

口座を読む入力（`RuntimeInputRef(ACCOUNT)`）には辺を引かない。特定の注文に由来しない
ためである（D04 §12）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.strategy.declarations.definition import StrategyDefinition
from odyssey_fx.strategy.declarations.evaluation import OnRuntimeEvent
from odyssey_fx.strategy.declarations.refs import OutputRef, RuntimeInputRef, RuntimeTarget
from odyssey_fx.strategy.declarations.validation import (
    normalized_unique,
    require_identifier,
    require_instance,
    require_tuple_of,
)

__all__ = [
    "DependencyEdge",
    "DependencyGraph",
    "EdgeKind",
    "build_dependency_graph",
]


class EdgeKind(Enum):
    """辺の種類（D05 §5.4）。"""

    #: 入力が他の使用箇所の出力に接続されている（明示辺）。
    EXPLICIT_INPUT = "EXPLICIT_INPUT"
    #: 約定による起動（因果辺。D04 §12）。
    FILL_TRIGGER = "FILL_TRIGGER"
    #: 現在の建玉の参照（因果辺。D04 §12）。
    POSITION_CONTEXT = "POSITION_CONTEXT"
    #: 市場状態の適用（因果辺。D04 §12 v1.9、D05 §7.6）。
    MARKET_STATE = "MARKET_STATE"
    #: 取引機会の参照（因果辺。D04 §12 v1.13、D05 §7.7）。
    OPPORTUNITY_CONTEXT = "OPPORTUNITY_CONTEXT"


@dataclass(frozen=True, slots=True)
class DependencyEdge:
    """「`source_instance` を先に評価する」という1本の辺（D05 §5.4）。"""

    source_instance: str
    target_instance: str
    kind: EdgeKind

    def __post_init__(self) -> None:
        require_identifier(self.source_instance, "DependencyEdge.source_instance")
        require_identifier(self.target_instance, "DependencyEdge.target_instance")
        require_instance(self.kind, EdgeKind, "DependencyEdge.kind")

    def __str__(self) -> str:
        return f"{self.source_instance} -> {self.target_instance} ({self.kind.value})"


@dataclass(frozen=True, slots=True)
class DependencyGraph:
    """使用箇所の依存関係（D05 §5.4）。"""

    nodes: tuple[str, ...]
    edges: tuple[DependencyEdge, ...]

    def __post_init__(self) -> None:
        require_tuple_of(self.nodes, str, "DependencyGraph.nodes")
        require_tuple_of(self.edges, DependencyEdge, "DependencyGraph.edges")
        known = set(self.nodes)
        for edge in self.edges:
            for label, instance_id in (
                ("source_instance", edge.source_instance),
                ("target_instance", edge.target_instance),
            ):
                if instance_id not in known:
                    raise KernelValueError(
                        f"DependencyEdge.{label} {instance_id!r} is not a node of the graph"
                    )
        object.__setattr__(
            self, "nodes", normalized_unique(self.nodes, key=str, label="DependencyGraph.nodes")
        )
        object.__setattr__(
            self,
            "edges",
            normalized_unique(
                self.edges,
                key=lambda edge: (edge.source_instance, edge.target_instance, edge.kind.value),
                label="DependencyGraph.edges",
            ),
        )

    def evaluation_order(self) -> tuple[str, ...]:
        """トポロジカル順、同順位は使用箇所 ID 順（D05 §5.4）。

        「同順位」は**同じ段**、すなわち上流をすべて評価し終えた時点で同時に評価できる
        使用箇所の集まりを指す。段ごとに ID 順へ並べ替えて連結する。並びを固定しないと、
        同じ宣言から出力の通し番号が変わり、「同一入力の再実行で判断履歴が一致する」
        （全体計画 §8.2）を満たせない。

        閉路があれば `find_cycle()` が経路を返すので、こちらは閉路がないことを前提に並びだけ
        を返す。閉路がある状態で呼ばれたら `KernelValueError`（コンパイラは循環検出を先に
        通す）。
        """
        incoming: dict[str, int] = dict.fromkeys(self.nodes, 0)
        outgoing: dict[str, list[str]] = {node: [] for node in self.nodes}
        # 同じ2点を結ぶ辺が種類違いで2本あっても、順序の制約としては1本ぶんである。
        seen_pairs: set[tuple[str, str]] = set()
        for edge in self.edges:
            pair = (edge.source_instance, edge.target_instance)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            outgoing[edge.source_instance].append(edge.target_instance)
            incoming[edge.target_instance] += 1

        order: list[str] = []
        current = sorted(node for node in self.nodes if incoming[node] == 0)
        while current:
            order.extend(current)
            following: list[str] = []
            for node in current:
                for target in outgoing[node]:
                    incoming[target] -= 1
                    if incoming[target] == 0:
                        following.append(target)
            current = sorted(following)
        if len(order) != len(self.nodes):
            raise KernelValueError(
                "evaluation_order() requires an acyclic graph; run find_cycle() first"
            )
        return tuple(order)

    def find_cycle(self) -> tuple[DependencyEdge, ...] | None:
        """閉路を1つ見つけて、その経路の辺を返す（D05 §5.4）。無ければ `None`。

        経路そのものを返すのは、誤りの説明に「どの接続が閉じているか」を書けるように
        するためである（D05 §5.2）。
        """
        adjacency: dict[str, list[DependencyEdge]] = {node: [] for node in self.nodes}
        for edge in self.edges:
            adjacency[edge.source_instance].append(edge)

        WHITE, GREY, BLACK = 0, 1, 2
        color = dict.fromkeys(self.nodes, WHITE)
        path: list[DependencyEdge] = []

        def visit(node: str) -> tuple[DependencyEdge, ...] | None:
            color[node] = GREY
            for edge in adjacency[node]:
                target = edge.target_instance
                if color[target] == GREY:
                    # `target` から今の経路をたどり直して閉路の部分だけを取り出す。
                    start = next(
                        (
                            index
                            for index, item in enumerate(path)
                            if item.source_instance == target
                        ),
                        len(path),
                    )
                    return tuple(path[start:]) + (edge,)
                if color[target] == WHITE:
                    path.append(edge)
                    found = visit(target)
                    if found is not None:
                        return found
                    path.pop()
            color[node] = BLACK
            return None

        for node in self.nodes:
            if color[node] == WHITE:
                found = visit(node)
                if found is not None:
                    return found
        return None


def _explicit_edges(definition: StrategyDefinition, nodes: set[str]) -> list[DependencyEdge]:
    """明示辺を引く。同じ2点を結ぶ明示辺は1本にまとめる。

    1つの使用箇所が同じ上流を2つの入力（または1つの入力の2つの接続元）で読むと、同じ辺が
    2度現れる。辺の集合は重複を拒否する（D04 §3）ので、まとめずに渡すと正しい宣言が
    コンパイル中の例外になる。
    """
    edges: list[DependencyEdge] = []
    seen: set[tuple[str, str]] = set()
    for instance in definition.components:
        for binding in instance.inputs.values():
            for source in binding.sources:
                if isinstance(source, OutputRef) and source.instance_id in nodes:
                    pair = (source.instance_id, instance.instance_id)
                    if pair in seen:
                        continue
                    seen.add(pair)
                    edges.append(
                        DependencyEdge(
                            source_instance=source.instance_id,
                            target_instance=instance.instance_id,
                            kind=EdgeKind.EXPLICIT_INPUT,
                        )
                    )
    return edges


def _causal_edges(
    definition: StrategyDefinition, order_producers: Iterable[str]
) -> list[DependencyEdge]:
    """約定と建玉のエンジン上の因果辺2本を引く（D04 §12）。"""
    edges: list[DependencyEdge] = []
    producers = tuple(order_producers)
    for instance in definition.components:
        starts_on_fill = any(
            isinstance(trigger, OnRuntimeEvent) for trigger in instance.evaluation.triggers
        )
        reads_position = any(
            isinstance(source, RuntimeInputRef) and source.target is RuntimeTarget.POSITION
            for binding in instance.inputs.values()
            for source in binding.sources
        )
        for producer in producers:
            if starts_on_fill:
                edges.append(DependencyEdge(producer, instance.instance_id, EdgeKind.FILL_TRIGGER))
            if reads_position:
                edges.append(
                    DependencyEdge(producer, instance.instance_id, EdgeKind.POSITION_CONTEXT)
                )
    return edges


def _market_state_edges(
    nodes: set[str], market_state_role: OutputRef | None, trigger_role: OutputRef | None
) -> list[DependencyEdge]:
    """市場状態の使用箇所 → 取引機会を出す使用箇所の因果辺を引く（D04 §12 #11、D05 §7.6）。

    `market_state` 役割が無い戦略（検証戦略 A）では引かないので、段階2 の循環検出と評価順は
    変わらない（D04 §12）。
    """
    if market_state_role is None or trigger_role is None:
        return []
    source = market_state_role.instance_id
    target = trigger_role.instance_id
    if source not in nodes or target not in nodes:
        return []
    return [DependencyEdge(source, target, EdgeKind.MARKET_STATE)]


def _opportunity_edges(
    definition: StrategyDefinition, nodes: set[str], trigger_role: OutputRef | None
) -> list[DependencyEdge]:
    """取引機会を出す使用箇所 → 取引機会を読む使用箇所の因果辺を引く（D04 §12、D05 §7.7）。

    `RuntimeInputRef(OPPORTUNITY)` を入力に持つ使用箇所が終点になる。ランタイムが供給する
    取引機会は `trigger` 役割の出力から組み立てたものなので、起点は `trigger` 役割の使用箇所
    である。取引機会を読む使用箇所が無い戦略（検証戦略 A）では引かないので、段階2 の
    循環検出と評価順は変わらない。
    """
    if trigger_role is None or trigger_role.instance_id not in nodes:
        return []
    source = trigger_role.instance_id
    edges: list[DependencyEdge] = []
    for instance in definition.components:
        reads_opportunity = any(
            isinstance(item, RuntimeInputRef) and item.target is RuntimeTarget.OPPORTUNITY
            for binding in instance.inputs.values()
            for item in binding.sources
        )
        if reads_opportunity:
            edges.append(DependencyEdge(source, instance.instance_id, EdgeKind.OPPORTUNITY_CONTEXT))
    return edges


def build_dependency_graph(
    definition: StrategyDefinition,
    *,
    order_role: OutputRef,
    market_state_role: OutputRef | None = None,
    trigger_role: OutputRef | None = None,
) -> DependencyGraph:
    """宣言から依存グラフを組み立てる（D05 §5.4、D04 §12 #6・#11）。

    `order_role` は注文意図の役割が指す出力で、その使用箇所が約定と建玉の因果辺の起点になる。
    `market_state_role` と `trigger_role` は市場状態と取引機会の役割が指す出力で、両方が
    あるとき市場状態の因果辺を引く。`trigger_role` があるときは、取引機会を読む使用箇所への
    因果辺も引く。
    """
    nodes = {instance.instance_id for instance in definition.components}
    edges = _explicit_edges(definition, nodes)
    producers = [order_role.instance_id] if order_role.instance_id in nodes else []
    edges.extend(_causal_edges(definition, producers))
    edges.extend(_market_state_edges(nodes, market_state_role, trigger_role))
    edges.extend(_opportunity_edges(definition, nodes, trigger_role))
    return DependencyGraph(nodes=tuple(sorted(nodes)), edges=tuple(edges))
