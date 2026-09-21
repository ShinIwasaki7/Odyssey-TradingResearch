"""依存グラフと評価順（D05 §5.4、D04 §12 #6）。

節点は使用箇所、辺は「どちらが先に評価されていなければならないか」である。辺は3種類ある。

| 種類 | いつ引くか |
|---|---|
| `EXPLICIT_INPUT` | 入力が他の使用箇所の出力に接続されている |
| `FILL_TRIGGER` | 注文意図を出す使用箇所 → 約定通知で起動する使用箇所 |
| `POSITION_CONTEXT` | 注文意図を出す使用箇所 → 現在の建玉を読む使用箇所 |

後ろ2つが**エンジン上の因果辺**である（D04 §12）。明示的な接続だけを辺にすると、注文が
エンジンを一周して戻ってくる帰還路を見逃す。たとえば注文意図を出す使用箇所が約定通知でも
起動する宣言は自己ループであり、これがないとコンパイラが通してしまう。

**種類を残したまま保持する**のは、閉路が明示接続によるものか帰還路によるものかを誤りの
説明に書けるようにするためである。

評価順は**トポロジカル順、同順位は使用箇所 ID の Unicode コードポイント順**とする
（D05 §5.4 v1.3）。「同順位」は同じ段、すなわち上流を評価し終えた時点で同時に評価できる
使用箇所の集まりである（Kahn 法で段ごとにまとめ、段の中を ID 順に並べる）。並びを固定しない
と、同じ宣言から出力の通し番号が変わり、「同一入力の再実行で判断履歴が一致する」（全体計画
§8.2）を満たせない。時間足の大小から順序を推測しない。

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
    edges: list[DependencyEdge] = []
    for instance in definition.components:
        for binding in instance.inputs.values():
            for source in binding.sources:
                if isinstance(source, OutputRef) and source.instance_id in nodes:
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
    """エンジン上の因果辺2本を引く（D04 §12）。"""
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


def build_dependency_graph(
    definition: StrategyDefinition, *, order_role: OutputRef
) -> DependencyGraph:
    """宣言から依存グラフを組み立てる（D05 §5.4、D04 §12 #6）。

    `order_role` は注文意図の役割が指す出力で、その使用箇所が因果辺の起点になる。
    """
    nodes = {instance.instance_id for instance in definition.components}
    edges = _explicit_edges(definition, nodes)
    producers = [order_role.instance_id] if order_role.instance_id in nodes else []
    edges.extend(_causal_edges(definition, producers))
    return DependencyGraph(nodes=tuple(sorted(nodes)), edges=tuple(edges))
