# How to implement pagination, authentication, and dynamic image URLs

`inspect` は有限の manifest を発見する段階、`create_image_request` は画像 fetch 直前に request を組み立てる段階、`recover_image_request` は AuthFlow refresh 後にも HTTP status `>=400` が残るときに一度だけ replacement request を返す段階である。transport failure は recovery hook の対象ではない。

1. cursor/page API は `inspect` 内で停止条件、重複排除、上限を持って完結させる。
2. `ImageResource.url` には、画像を一意に解決する non-empty string locator を置く。canonical URL、`image:42` のような ID、`/placeholder/42` のような placeholder を使えるが、`create_image_request` が実 request を組み立てるのに十分に安定していなければならない。`ImageResource.metadata` には inspection-time の非秘密補助情報を置け、request hook と transform context から読める。locator、`image_id`、image/manifest metadata に token、cookie、署名 URL、鍵などの秘密値を置かない。
3. 実 URL が短命なら `create_image_request` で locator を解釈して直前 API を呼び、送信用の absolute HTTP(S) `RequestSpec.url` を発行する。transform に request-time data が必要なら、`ImageFetchRequest(RequestSpec(...), {"key": "value"})` を返す。成功時、site transform は `TransformContext.transport_metadata` から AuthFlow 後の初回 request、redirect 後の最終 request、response URL/header と `plugin_data` を読める。processor に raw 値を渡すには `image_processors.transport_metadata_access` で processor/site の組を明示許可する。未許可 processor は redacted snapshot だけを得る。core は locator を解釈せず、`RequestSpec(url=image.url)` が HTTP(S) でなければ fetch 前に失敗する。`request_concurrency` は HTTP transport だけを直列化し、hook の間には保持されないため、並列 image job が先に複数の URL を発行することは防がない。URL の TTL が短く、発行から最終 fetch までの順序保証が必要なら、selected plugin の `download_policy.preserve_image_start_order: true`（または chapter/image concurrency とも `1`）を使う。現行 API は発行と最終 fetch を一つの atomic reservation にする機構を提供しない。
4. CDN URL が失効した response だけを `recover_image_request` で再発行する。
5. credential と refresh は `AuthFlow` に閉じ込め、追加 origin は明示的に opt-in する。

呼出回数、auth/recovery 順、並行性、完全な signature は [execution lifecycle](../explanation/execution-lifecycle.md) と [plugin hook reference](../reference/plugin-hooks.md) を使用する。
