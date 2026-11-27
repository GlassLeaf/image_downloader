<a id="how-to-implement-pagination-authentication-and-dynamic-image-urls"></a>

# ページ分割・認証・動的な画像URLを実装する

`inspect` は有限の manifest を発見する段階、`create_image_request` は画像 fetch 直前に request を組み立てる段階、`recover_image_request` は AuthFlow refresh 後にも HTTP status `>=400` が残るときに一度だけ replacement request を返す段階である。transport failure は recovery hook の対象ではない。

CLI の `image-downloader inspect URL` と library の `DownloadService.inspect()` は、manifest だけでなく request 組立ても検査する表示専用 operation である。既定では manifest 配列順に `create_image_request()` を一画像ずつ呼び、続けて `AuthFlow.apply()`、cookie jar／既定 header／query／form・JSON body の合成を行い、画像 body を送信せずに HTTP client の送信直前 snapshot を得る。`--manifest-only` または `resolve_image_requests=False` は request hook を一度も呼ばない。redirect 後 URL、response header、`recover_image_request()` の結果は response を必要とするため含まれない。短命 URL は表示した時点で失効し得る診断値であり、後続 download に再利用してはならない。

CLI は `--inspection-data url|http|all` で stdout の露出範囲を選ぶ。既定の `url` は送信直前 URL と位置・状態だけ、`http` は source `RequestSpec` と送信直前 method/header/cookie/body、`all` はさらに locator、manifest/image metadata、save option、`plugin_data` を raw で出す。`url` でも署名 query は秘密値になり得、`http`／`all` は credential や body を含み得るため stdout/stderr をログ、CI artifact、telemetry、共有端末、チケット、第三者サービスへ渡してはならない。core は image fetch、transform、processor、save、report、update state、event、notification を実行せず、cookie 変更と debug log を永続化しない。ただし plugin の `inspect()` / `create_image_request()` / 認証の初期化・検証・更新が行う補助 HTTP、ならびにサーバー側の token 発行・session 変更・アクセス記録までは取り消せない。plugin は画像 URL を raw client で直接 fetch してはならない。

1. cursor/page API は `inspect` 内で停止条件、重複排除、上限を持って完結させる。
2. `ImageResource.url` には、画像を一意に解決する non-empty string locator を置く。canonical URL、`image:42` のような ID、`/placeholder/42` のような placeholder を使えるが、`create_image_request` が実 request を組み立てるのに十分に安定していなければならない。`ImageResource.metadata` には inspection-time の非秘密補助情報を置け、request hook と transform context から読める。locator、`image_id`、image/manifest metadata に token、cookie、署名 URL、鍵などの秘密値を置かない。
3. 実 URL が短命なら `create_image_request` で locator を解釈して直前 API を呼び、送信用の absolute HTTP(S) `RequestSpec.url` を発行する。transform に request-time data が必要なら、`ImageFetchRequest(RequestSpec(...), {"key": "value"})` を返す。成功時、site transform は `TransformContext.transport_metadata` から AuthFlow 後の初回 request、redirect 後の最終 request、response URL/header と `plugin_data` を読める。processor に raw 値を渡すには `image_processors.transport_metadata_access` で processor/site の組を明示許可する。未許可 processor は redacted snapshot だけを得る。core は locator を解釈せず、`RequestSpec(url=image.url)` が HTTP(S) でなければ fetch 前に失敗する。`request_concurrency` は HTTP transport だけを直列化し、hook の間には保持されないため、並列 image job が先に複数の URL を発行することは防がない。URL の TTL が短く、発行から最終 fetch までの順序保証が必要なら、selected plugin の `download_policy.preserve_image_start_order: true`（または chapter/image concurrency とも `1`）を使う。現行 API は発行と最終 fetch を一つの atomic reservation にする機構を提供しない。
4. CDN URL が失効した response だけを `recover_image_request` で再発行する。
5. credential と refresh は `AuthFlow` に閉じ込め、追加 origin は明示的に opt-in する。

呼出回数、auth/recovery 順、並行性、完全な signature は [実行ライフサイクル](../explanation/execution-lifecycle.md) と [プラグインフックリファレンス](../reference/plugin-hooks.md) を使用する。

認証付きサンプルの`auth_context.credentials.get(name)`には、利用者が対象プロファイルの保管庫へ登録した値を渡す。
YAMLの参照名・`credential plugin set`・実行先の対応は[サンプルの登録手順](../../../plugin-sources/README.md#migrated-v2-pattern-samples)を参照する。
登録と実行でデータルート・プロファイルを揃える。OAuth更新結果の自動保存は行わない。

認証の状態管理・接続先宣言・確認頻度は[認証APIの移行手順](authentication-migration.md)を参照する。`apply()` は同期・通信なしとし、ログイン等は `AuthContext.requests` を利用する。設定可能な値と通常通信への適用範囲は[設定リファレンス](../reference/configuration.md#config-plugin-connections)を参照する。

<a id="endpoint-image-requests"></a>

## 接続先から画像要求を作る

`create_image_request()` / `recover_image_request()` の `PluginExecutionContext` でも、`inspect()` と同じ `context.endpoints` を使える。次は認証フローを持つサイトの作者YAMLで、画像本体を返すAPIのoriginを宣言する例である。

```yaml
config: {}
endpoints:
  api:
    origin: https://api.example.test
    auth: session
```

```python
from image_downloader import ImageResource, PluginExecutionContext, RequestSpec


async def create_image_request(
    self, image: ImageResource, context: PluginExecutionContext
) -> RequestSpec:
    return context.endpoints["api"].request(
        "/images/original.jpg",
        referer=image.referer,
    )
```

`.request()` にawaitは不要で、呼出しだけでは通信しない。上例は `https://api.example.test/images/original.jpg` の `RequestSpec` を返し、コアが認証準備・資格情報付与を経て取得する。固定画像パスは説明用であり、実装では画像を識別するlocator等から組み立てる。公開の接続先なら `auth: none` を使い、認証フローを通さない。

`/items` のようなAPIの応答がJSONなら、その要求を画像本体の要求として返さない。`inspect()` や要求フック内で `await context.requests.execute(context.endpoints["api"].request("/items"))` を呼び、解析後に画像本体の `RequestSpec` を返す。認証フック内部では通常の `context.requests` ではなく、引数の `auth_context.requests` を使う。

パスは単一の `/` で始める。絶対URL、`//other.example.test/...`、バックスラッシュを含むパスは拒否する。未宣言の `"api"` を参照するとKeyErrorになる。設定省略時も `context.endpoints["operation"]` は利用でき、operation開始URLと同じoriginの要求を構築できる。

絶対URLを直接 `RequestSpec` に渡しても、送信時のorigin解決・認証方針は同じである。接続先宣言はURLの自動書換えやパス単位の認証ルーティングをしない。共通 `authentication` の確認方針は `endpoints.<name>.verification` でoriginごとに部分的に上書きする。省略時は継承し、`before_request: false` だけでは継承した間隔確認は止まらない。

複数ホストへの対応には、`origin: "https://cdn.${operation.site_domain}"` のように起点変数を使える。起点がwww.example.comならcdn.example.comに解決する。候補が複数ならoriginを配列にでき、`origin: "https://*.images.${operation.site_domain}"` で直下の一階層にも対応できる。[変数・配列展開・優先順位](../reference/configuration.md#config-endpoint-patterns)を参照する。

複数候補やワイルドカードは送信先を自動選択しない。絶対画像URLが分かる場合はそのまま `RequestSpec` に渡すか、具体originを選んでパスを組み立てる。

```python
endpoint = context.endpoints["images"].for_origin("https://a.images.example.com")
request = endpoint.request("/images/original.jpg", referer=image.referer)
```

`.origins` は正規化した候補のtupleを返す。`.for_origin()` には候補に一致する具体originだけを渡す。一致しないoriginやパス付きURLは `ValueError` になる。複数候補・ワイルドカードの `.origin` や `.request()` も `ValueError` となる。
