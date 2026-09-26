# v2 plugin 参考構成（履歴資料）

> 現行の新規 plugin は [`plugin-v3-template`](../../plugin-v3-template) と
> [v3 plugin 開発参照](../../../docs/v3/reference/plugin-hooks.md) を使用してください。この directory
> 内の entry point/descriptor 例は v3 では読み込まれず、現在の checkout での実行も保証しません。

`plugins/` には通常の entry point へ登録しない、6 種類の v2 `SitePlugin` 参考実装がある。
`fixtures/` と `tests/legacy/v2/test_site_plugin_examples.py` は当時の構成を記録するだけである。

6 種類の構成と責務は
[サイト別 plugin 構成例](../../../archive/docs/legacy/v2/site-plugin-examples.md)、公開契約は
[v2 plugin API](../../../archive/docs/legacy/v2/plugin-api-reference.md) を参照する。

実在する認証情報、Cookie、token、限定URL、アクセス制御の回避コードを参考実装へ含めてはいけません。
