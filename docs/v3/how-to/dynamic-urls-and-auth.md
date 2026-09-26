# How to implement pagination, authentication, and dynamic image URLs

`inspect` は有限の manifest を発見する段階、`create_image_request` は画像 fetch 直前に request を組み立てる段階、`recover_image_request` は AuthFlow refresh 後にも HTTP status `>=400` が残るときに一度だけ replacement request を返す段階である。transport failure は recovery hook の対象ではない。

1. cursor/page API は `inspect` 内で停止条件、重複排除、上限を持って完結させる。
2. `ImageResource.url` には実在する canonical absolute HTTP(S) URL を置き、placeholder を置かない。constructor/invoker は構文だけを検査するため、意味上の dummy を返さないのは plugin author の責務である。
3. 実 URL が短命なら `create_image_request` で直前 API を呼び `RequestSpec.url` を発行する。
4. CDN URL が失効した response だけを `recover_image_request` で再発行する。
5. credential と refresh は `AuthFlow` に閉じ込め、追加 origin は明示的に opt-in する。

呼出回数、auth/recovery 順、並行性、完全な signature は [execution lifecycle](../explanation/execution-lifecycle.md) と [plugin hook reference](../reference/plugin-hooks.md) を使用する。
