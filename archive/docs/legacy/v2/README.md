# v2 archive

この directory は local plugin API v2 の設計資料、サンプル、fixture、test の履歴保存先である。
v3 では entry point、class descriptor、wheel sidecar、旧 configuration tree を読まない。

ここにある source と test は当時の API を記録するためのものであり、現在の checkout での実行や
修正を受け付ける互換性を保証しない。現行の仕様、運用、移行手順は
[v3 documentation](../../../../docs/v3/README.md) と
[テストと移行](../../../../docs/v3/testing-and-migration.md) を参照する。

[`site-plugin-examples.md`](site-plugin-examples.md) は対応する履歴上の plugin パターンを補足する。v2 の
source、fixture、test はそれぞれ `examples/legacy/v2/` と `tests/legacy/v2/` に隔離されている。
