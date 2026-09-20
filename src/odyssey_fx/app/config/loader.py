"""設定ファイルの安全な読込（D01 §10.1、ADR-0018）。

D01 §10.1 が定める読込条件を**この1モジュールで強制する**。読込条件は次のとおり。

- YAML 1.2 相当の安全な読込（任意オブジェクトを構築しないローダ）。
- カスタムタグ禁止。
- 重複キーはエラー。
- merge key（`<<`）、anchor / alias の展開など、展開後の内容がレビュー時に分かりにくい
  機能は禁止。
- 全設定ファイルに `schema_version` 必須。未知の版・未宣言キー・型不一致は拒否。

`yaml` を import してよいのは `app.config` とその配下だけで、契約
"F5c: config parsers only in app.config"（D01 §6、ADR-0026）が機械検査する。

**数値は文字列のまま読む**。YAML の浮動小数として読むと二進浮動小数の誤差が入り、Decimal
の厳密さが失われる（ADR-0012）。安全な読込でも `1.5` は `float` になるので、Decimal に
なるべき値は設定ファイル側で文字列として書き、Pydantic モデルでも `str` として受ける。
浮動小数で書かれた価格は「型不一致」として拒否される（D01 §10.1）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

__all__ = ["ConfigError", "load_yaml_mapping"]


class ConfigError(ValueError):
    """設定ファイルの読込・検証の失敗（D01 §10.1）。

    どのファイルの何が条件に反したかをメッセージに含める。設定の誤りは人間が直すもの
    なので、原因の特定に足りる情報を残す。
    """


def _peek_event(loader: Any) -> Any:
    """次に来る事象（イベント）を消費せずに覗く。

    PyYAML の `peek_event` は型スタブに注釈が無く、strict な型検査では「注釈のない関数の
    呼び出し」になる。呼び出しをこの1関数に閉じ込め、型検査の抑制コメントを本体に
    散らさない。
    """
    return loader.peek_event()


def _at(marked: Any) -> str:
    """事象・ノードの位置を「line N」の形で表す（位置が無ければ「位置不明」）。

    設定の誤りは人間が直すものなので、どの行かを必ず添える。PyYAML は位置情報を
    持たない場合があるので、その場合も失敗の説明が成立するようにしておく。
    """
    mark = getattr(marked, "start_mark", None)
    line = getattr(mark, "line", None)
    return "位置不明" if line is None else f"line {line + 1}"


class _StrictSafeLoader(yaml.SafeLoader):
    """D01 §10.1 の読込条件を強制するローダ。

    `yaml.SafeLoader` は任意オブジェクトを構築しない（＝安全な読込）が、重複キー・
    anchor / alias・merge key はそのまま受け入れる。それらを拒否するため、キーの構築と
    alias の解決を上書きする。

    カスタムタグは `SafeLoader` が既に拒否する（未知のタグに構築子がないため
    `ConstructorError` になる）。本ローダは `SafeLoader` に構築子を追加しないので、
    その拒否がそのまま効く。
    """

    def compose_node(self, parent: yaml.nodes.Node | None, index: Any) -> yaml.nodes.Node:
        """anchor（`&name`）と alias（`*name`）を拒否する（D01 §10.1）。

        どちらも「展開後の内容がレビュー時に分かりにくい機能」に当たる。alias だけを
        禁じても anchor が残れば、後から alias を足した設定が静かに通ってしまうので、
        **anchor を宣言した時点で**止める。判定は次に来る事象（イベント）に対して行う。
        PyYAML のノードは anchor を保持しないため、ノードができてからでは調べられない。
        """
        event: Any = _peek_event(self)
        anchor = getattr(event, "anchor", None)
        if isinstance(event, yaml.events.AliasEvent):
            raise ConfigError(
                f"{_at(event)}: YAML の alias（`*{anchor}`）は使えない。"
                " 展開後の内容がレビュー時に分かりにくいため（D01 §10.1）"
            )
        if anchor is not None:
            raise ConfigError(
                f"{_at(event)}: YAML の anchor（`&{anchor}`）は使えない。"
                " 展開後の内容がレビュー時に分かりにくいため（D01 §10.1）"
            )
        # `compose_node` が `None` を返すのは、alias が未知の anchor を指す場合だけで
        # ある。その経路は上で既に止めているので、ここでは必ずノードが返る。
        composed: Any = super().compose_node(parent, index)
        node: yaml.nodes.Node = composed
        return node

    def construct_mapping(self, node: yaml.nodes.MappingNode, deep: bool = False) -> dict[Any, Any]:
        """重複キーと merge key（`<<`）を拒否する（D01 §10.1）。"""
        seen: set[Any] = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                raise ConfigError(
                    f"{_at(key_node)}: YAML の merge key（`<<`）は"
                    " 使えない。展開後の内容がレビュー時に分かりにくいため（D01 §10.1）"
                )
            key = self.construct_object(key_node, deep=True)
            if not isinstance(key, str):
                raise ConfigError(
                    f"{_at(key_node)}: 設定ファイルのキーは文字列で"
                    f" なければならない（{key!r} が与えられた）"
                )
            if key in seen:
                raise ConfigError(
                    f"{_at(key_node)}: キー {key!r} が重複している。 重複キーはエラー（D01 §10.1）"
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    """設定ファイルを読み、最上位の mapping を返す（D01 §10.1）。

    `schema_version` の存在だけをここで確かめる。中身の検証（未宣言キー・型不一致）は
    Pydantic モデルが行う（`extra="forbid"` と型注釈）。

    返す構造の値は YAML が与えたまま（文字列・整数・真偽値・入れ子）であり、Decimal 化・
    期間の解析・`ZoneInfo` の構築は各設定のモデルから domain 型への変換が行う。
    """
    if not isinstance(path, Path):  # pragma: no cover - 呼び出し側が Path を渡す
        raise ConfigError(f"load_yaml_mapping には Path を渡すこと（{type(path).__name__}）")
    if not path.is_file():
        raise ConfigError(f"設定ファイルが見つからない: {path}")

    text = path.read_text(encoding="utf-8")
    try:
        loaded = yaml.load(text, Loader=_StrictSafeLoader)  # noqa: S506 - 安全な読込（上記）
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: YAML として読めない: {exc}") from exc

    if loaded is None:
        raise ConfigError(f"{path}: 設定ファイルが空である")
    if not isinstance(loaded, dict):
        raise ConfigError(
            f"{path}: 最上位は mapping でなければならない（{type(loaded).__name__} が与えられた）"
        )
    if "schema_version" not in loaded:
        raise ConfigError(
            f"{path}: `schema_version` が無い。全設定ファイルに必須（D01 §10.1、ADR-0018）"
        )
    return loaded
