# plugin 作者ガイド

この文書は再編前リンク向けの概要である。package、manifest、署名、hook contract、plugin test は [plugin 開発参照](plugin-development-reference.md) が正本である。hook の実行順序と動的 URL の判断は [実行ライフサイクル](execution-lifecycle.md) を正本とする。

## package

site plugin は plugin root の <code>site_plugins/&lt;unit&gt;/</code>、image processor は <code>image_processor_plugins/&lt;unit&gt;/</code> に置く。各 unit は manifest、metadata、author defaults、entry module を持つ。署名、catalog、install、trust の規則は [plugin 開発参照](plugin-development-reference.md) を参照する。

[plugin template](../../examples/plugin-v3-template) は file layout と最小 contract を示す skeleton である。pagination、認証、動的 URL 解決を実装した完成例ではない。完成形の参照先は以下である。

| 要件 | 参照実装 |
| --- | --- |
| HTML の相対 URL と Referer | [generic CSS selector](../../plugin-sources/generic-css-selector/plugin.py) |
| API cursor pagination | [cursor API gallery](../../plugin-sources/cursor-api-gallery/cursor_api_gallery.py) |
| CSRF login | [CSRF login gallery](../../plugin-sources/csrf-login-gallery/csrf_login_gallery.py) |
| OAuth refresh | [OAuth media API](../../plugin-sources/oauth-media-api/oauth_media_api.py) |
| 期限付き CDN URL の再発行 | [signed CDN gallery](../../plugin-sources/signed-cdn-gallery/signed_cdn_gallery.py) |

## 実装 rules

- manifest の id、kind、entry point と Python class は一致させる。plugin に直接 filesystem、process、任意 socket capability を渡さない。
- <code>matches_with_config</code> と <code>validate_config</code> は synchronous である。前者は invalid private config にも耐え、副作用、secret、request を使わない。
- <code>inspect</code> は一つの完全な <code>DownloadManifest</code> を返す。章と画像の order は deterministic にする。
- <code>create_image_request</code> は画像ごと・並行に呼ばれる。直前 API 解決はここで行えるが、<code>ImageResource.url</code> 自体を placeholder にしてはならない。
- <code>recover_image_request</code> は response を伴う失敗の再発行だけに使い、None を返して core の通常失敗処理へ委ねる。
- <code>transform_image</code> と processor transform は <code>TransformContext</code> のみを使う。request/secret に依存する変換を設計しない。
- plugin 例外は <code>PluginError</code> として operation を中断し得る。期待される画像単位の fetch/process/save failure を独自に握りつぶさない。

設定、DTO、Protocol の完全な signature は [完全 API 参照](api-reference.md) を参照する。
