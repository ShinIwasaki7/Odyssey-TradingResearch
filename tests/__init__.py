"""テスト一式（D01 §9）。

`tests.fixtures.synthetic` を1つのモジュール名で解決できるよう、パッケージとして宣言する。
これがないと、型検査が同じファイルを `synthetic.market` と `tests.fixtures.synthetic.market`
の2通りに解決してしまう。
"""
