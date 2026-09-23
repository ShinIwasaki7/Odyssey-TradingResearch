"""設定ファイルの形を表す Pydantic モデルの共通の土台（D01 §10.1、ADR-0018）。

`pydantic` を import してよいのは `app.config` とその配下だけで、契約
"F5c: config parsers only in app.config"（D01 §6、ADR-0026）が機械検査する。

ここに置くのは、4種の設定（カレンダー・時間足定義・データソース列対応・銘柄仕様）が
共有する土台だけである。

- `StrictModel`: 未宣言キーと緩い型変換を拒否する設定を持つ基底。
- `validate`: 検証の失敗を `ConfigError` へ言い換える関数。Pydantic の例外型が
  `app.config` の外へ漏れないようにする。
- `require_schema_version`: 未知の形式版を拒否する。

**Pydantic モデルそのものは `app.config` の外へ出さない**（D01 §10.1）。モデルは設定
ファイルの形を表すだけで、意味を持つのは各パッケージの frozen dataclass へ変換した後の
値である。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from odyssey_fx.app.config.loader import ConfigError

__all__ = ["StrictModel", "require_schema_version", "validate"]


class StrictModel(BaseModel):
    """未宣言キーと緩い型変換を拒否するモデルの基底（D01 §10.1）。

    - `extra="forbid"`: 宣言していないキーを拒否する。綴りを誤ったキーが黙って無視され、
      既定値のまま動く事故を防ぐ。
    - `strict=True`: 緩い型変換を拒否する。浮動小数で書かれた価格が黙って文字列へ直される
      経路を塞ぐ（ADR-0012。価格・数量は設定ファイル側で文字列として書く決まり）。
    - `frozen=True`: 検証後のモデルを書き換えられないようにする。
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def validate[ModelT: StrictModel](
    model: type[ModelT], payload: dict[str, Any], path: Path
) -> ModelT:
    """Pydantic の検証を行い、失敗を設定エラーへ言い換える（D01 §10.1）。

    Pydantic の例外をそのまま外へ出すと、Pydantic の型が `app.config` の外へ漏れる。
    呼び出し側が受け取るのは、どのキーが何の条件に反したかを日本語で示す `ConfigError`
    だけである。
    """
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '(最上位)'}: {error['msg']}"
            for error in exc.errors()
        )
        raise ConfigError(f"{path}: 設定の内容が条件を満たさない: {details}") from None


def require_schema_version(actual: int, expected: int, path: Path) -> None:
    """未知の形式版を拒否する（D01 §10.1、ADR-0018）。"""
    if actual != expected:
        raise ConfigError(
            f"{path}: `schema_version` が {actual} だが、この実装が読むのは"
            f" {expected} である（未知の版は拒否する、D01 §10.1）"
        )
