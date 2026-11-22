# プラグインAの実装サンプル

実サイトに近いログイン、章取得、更新確認、画像変換を想定したサンプルです。
ホスト名とサイト固有のHTML・APIは例示です。実サイトに適用する際は対象に合わせて変更してください。

- ベータv3の同期 `transform_image` に対応。同じインスタンスへ並行して呼べます。
- `structured_plugin_data`対応本体が必要です。章共通のplaceholderは`Chapter.plugin_data`、
  画像固有のiv・saltは`ImageResource.plugin_data`に設定し、変換では`context.plugin_data`から参照します。
  旧metadataからの自動移動はありません。[移行手順](../../docs/v3/how-to/structured-data-and-auth-snapshot-migration.md)を参照してください。
- WATは不変のレコード群と変換テーブルを返す関数へ変更し、追記型メモリを廃止しました。
- placeholderキャッシュは4項目・復号済みレコード16MiB以内。ロックで保護し、cleanupで解放します。
- WebPの可逆圧縮指定とタイル処理の順序を維持し、出力MIME・拡張子を修正しました。
- 認証は `AuthContext.requests` と `AuthMode.NONE` を使用します。初期化時にセッションを確認し、
  未ログインなら `AuthContext.secrets` の `email` / `password` でログインします。
  保存済みセッションの再利用、認証切れからの更新、明示的なセッション検証に対応します。
- 入力URLのホスト・スキームが異なっていても正規ホストへアクセスできるよう、
  `plugin.yaml` の `endpoints.site` に正規originを宣言しています。画像CDNは `endpoints.images` です。
- XSRFトークンは応答のCookieに加え、APIのドメイン・パスに一致する保存済みCookieから取得します。
  トークン欠落や不正なAPI応答は `PluginError` として報告します。

このフォルダーには配布用の署名済みmanifestを含めていません。適用する際は元パッケージの
メタデータとともに通常の手順で再署名し、plugin installでカタログpinを更新してください。

回帰テストではモックサイトを使用し、ログイン・再認証・対応origin・Cookie取得・更新確認を検証します。
画像変換の検証は調査用コピーとこのサンプルの両方に適用します。

```powershell
python -m pytest tests/v3/plugins/test_sample_plugin_a.py tests/v3/plugins/test_optimized_plugin_a.py -q
```

並行数の上限はコアの `media.transform_workers`（既定2、1〜4）で設定します。
画像の画素数上限は `media.max_image_pixels` で指定できます。既定Noneは維持しています。
これらはスレッドやプラグインを隔離するサンドボックスではありません。
