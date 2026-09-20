"""設定ファイルの読込条件の単体テスト（D01 §10.1、ADR-0018）。

D01 §10.1 が定める読込条件が、字面の約束ではなく実際に効いていることを確かめる。

- 安全な読込（任意オブジェクトを構築しない）。カスタムタグは拒否される。
- 重複キーはエラー。
- merge key（`<<`）と anchor / alias は拒否される。
- `schema_version` は必須で、未知の版は拒否される。
- 未宣言キーと型不一致は拒否される。
- 浮動小数として書かれた価格は拒否される（ADR-0012）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping


def write(tmp_path: Path, text: str, name: str = "config.yaml") -> Path:
    """一時ディレクトリへ設定ファイルを書く。"""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --- 受理される形 -----------------------------------------------------------


def test_a_plain_mapping_with_a_schema_version_is_read(tmp_path: Path) -> None:
    path = write(tmp_path, "schema_version: 1\nid: sample\nvalues:\n  - a\n  - b\n")
    assert load_yaml_mapping(path) == {"schema_version": 1, "id": "sample", "values": ["a", "b"]}


def test_numbers_written_as_strings_stay_strings(tmp_path: Path) -> None:
    """文字列で書かれた数値は文字列のまま返る（ADR-0012 の Decimal 化の前提）。"""
    path = write(tmp_path, 'schema_version: 1\nprice_tick: "0.001"\n')
    assert load_yaml_mapping(path)["price_tick"] == "0.001"


# --- 重複キー ---------------------------------------------------------------


def test_a_duplicate_key_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "schema_version: 1\nid: first\nid: second\n")
    with pytest.raises(ConfigError, match="重複"):
        load_yaml_mapping(path)


def test_a_duplicate_key_in_a_nested_mapping_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "schema_version: 1\nnested:\n  a: 1\n  a: 2\n")
    with pytest.raises(ConfigError, match="重複"):
        load_yaml_mapping(path)


# --- anchor / alias / merge key ---------------------------------------------


def test_an_anchor_is_rejected(tmp_path: Path) -> None:
    """anchor の時点で止める。alias を後から足した設定が静かに通らないようにするため。"""
    path = write(tmp_path, "schema_version: 1\nbase: &shared\n  a: 1\n")
    with pytest.raises(ConfigError, match="anchor"):
        load_yaml_mapping(path)


def test_an_alias_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "schema_version: 1\nbase: &shared\n  a: 1\ncopy: *shared\n")
    with pytest.raises(ConfigError, match="anchor|alias"):
        load_yaml_mapping(path)


def test_a_merge_key_is_rejected(tmp_path: Path) -> None:
    """merge key は anchor を伴うので、どちらの検査で止まっても条件は満たされる。"""
    path = write(tmp_path, "schema_version: 1\nbase: &b\n  a: 1\nchild:\n  <<: *b\n  c: 2\n")
    with pytest.raises(ConfigError, match="anchor|alias|merge"):
        load_yaml_mapping(path)


def test_a_merge_key_without_an_anchor_is_rejected(tmp_path: Path) -> None:
    """anchor を使わずに書いた merge key も拒否される。"""
    path = write(tmp_path, "schema_version: 1\nchild:\n  <<: {a: 1}\n  c: 2\n")
    with pytest.raises(ConfigError, match="merge"):
        load_yaml_mapping(path)


# --- カスタムタグと安全な読込 -----------------------------------------------


def test_a_custom_tag_is_rejected(tmp_path: Path) -> None:
    """任意オブジェクトの構築を行わない（D01 §10.1 の安全な読込）。"""
    path = write(tmp_path, "schema_version: 1\nvalue: !!python/object/apply:os.system ['echo']\n")
    with pytest.raises(ConfigError):
        load_yaml_mapping(path)


def test_an_unknown_tag_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "schema_version: 1\nvalue: !Custom {a: 1}\n")
    with pytest.raises(ConfigError):
        load_yaml_mapping(path)


# --- schema_version と最上位の形 --------------------------------------------


def test_a_missing_schema_version_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "id: sample\n")
    with pytest.raises(ConfigError, match="schema_version"):
        load_yaml_mapping(path)


def test_an_empty_file_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "")
    with pytest.raises(ConfigError, match="空"):
        load_yaml_mapping(path)


def test_a_top_level_sequence_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "- a\n- b\n")
    with pytest.raises(ConfigError, match="mapping"):
        load_yaml_mapping(path)


def test_a_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="見つからない"):
        load_yaml_mapping(tmp_path / "absent.yaml")


def test_broken_yaml_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "schema_version: 1\n  bad indent: [\n")
    with pytest.raises(ConfigError, match="YAML"):
        load_yaml_mapping(path)


def test_a_non_string_key_is_rejected(tmp_path: Path) -> None:
    """キーは文字列に限る（未宣言キーの検査がキーの型に左右されないようにするため）。"""
    path = write(tmp_path, "schema_version: 1\n1: value\n")
    with pytest.raises(ConfigError, match="キーは文字列"):
        load_yaml_mapping(path)
