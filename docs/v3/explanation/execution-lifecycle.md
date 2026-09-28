# Execution lifecycle and data flow

この文書は hook の順序、回数、失敗分岐、instance sharing、並行性の唯一の正本である。hook signature は [plugin hook reference](../reference/plugin-hooks.md)、request/persistence behavior は [runtime behavior](../reference/runtime-behavior.md) を参照する。

```text
URL
  -> candidate matches / matches_with_config
  -> selected site instance + config validation
  -> auth_flow(context)                         [operation ごとに 1 回]
  -> inspect(url, context)                      [operation ごとに 1 回]
  -> finite DownloadManifest
  -> bounded chapter jobs
  -> bounded image jobs
       -> create_image_request(image, context) [画像ごとに 1 回]
       -> auth apply / configured refresh retries
       -> HTTP request and normal retry
       -> recover_image_request(...)            [auth refresh 後の HTTP status >=400 に最大 1 回]
       -> site transform_image
       -> core validation/normalisation
       -> configured processor transforms
       -> allocation and save
  -> reports/events; cookie delta on service.close()
```

candidate matching のため temporary instance が作られ得る。選択された site instance はその operation の auth flow、inspection、request hooks、site transform で共有される。unselected instance の state に依存してはならない。

<a id="lifecycle-concurrency"></a>

`DownloadService.run()` と `check_updates()` は同一 service で直列化される。chapter は `download.chapter_concurrency`、image は chapter ごとの `download.image_concurrency_per_chapter` まで並行する。したがって `create_image_request` と site `transform_image` は並行呼出に安全でなければならない。configured processor は **同一 processor instance ごとに runtime lock で直列化** されるが、global mutable state や別 service instance との共有には依存してはならない。

selected site plugin の `download_policy` はこれらの上限を下げられるが、上げることはできない。`preserve_image_start_order: true` は両方を `1` として manifest の chapter/image 順に image job を開始する。operation-local `request_concurrency` は request hook、auth、recovery、画像 fetch を含む physical transport/retry を制限するが、hook の実行中に保持されない。これは hook 内の `context.requests.execute()` を自己デッドロックさせないためである。したがって image job が並列なら、短命 URL を `create_image_request` で発行した後、最終画像 request が他 job の transport 待ちになることがある。`request_concurrency: 1` だけでは発行から取得までを原子的にしない。短い TTL を扱う plugin は `preserve_image_start_order: true`、または chapter/image concurrency をともに `1` にする。

## Dynamic discovery and recovery

`inspect` は fetch 開始前に有限の `DownloadManifest` を返す。generic fallback は raw HTML を読むだけで JavaScript/headless browser/DOM automation を実行しない。infinite scroll/cursor pagination は plugin が `inspect` 内で API を反復し、停止条件、重複排除、上限を定義して complete manifest にする。core は browser scroll を後追いしない。

| situation | correct hook |
| --- | --- |
| page list を API から得る | `inspect` 内で全 known page を収集 |
| image ID から取得直前に実 URL を発行する | `ImageResource.url` に stable locator（ID または placeholder）と必要なら `image_id` を置き、`create_image_request` で actual absolute HTTP(S) `RequestSpec.url` |
| signed CDN URL が response failure で失効している | `recover_image_request` で replacement spec、最大一回 |

`create_image_request` は各 image ごとに一度だけ呼ばれる。auth failure response は `network.auth_refresh_attempts` の範囲で configured `AuthFlow.refresh` が先に試行される。refresh 後にも残った **HTTP response の status が `>=400` のときだけ** recovery hook が最大一度呼ばれる。DNS/connect/read timeout など transport failure には recovery hook は呼ばれない。recovery request にも通常の auth/validation が適用されるが、recovery を再帰的に呼ばない。

`ImageResource` の constructor は URL を検査せず、hook invoker は plugin が返した `ImageResource.url` を non-empty string locator として検査する。locator は canonical URL、ID、placeholder のいずれでもよいが、`inspect` が決定した完全な画像集合の各画像を `create_image_request` で解決するのに十分に安定していなければならない。`ImageResource.metadata` は各画像の inspection-time 非秘密情報を immutable に保持し、request hook と transform context に渡る。core は locator を解釈・送信しない。直前署名 URL は `create_image_request` が actual absolute HTTP(S) `RequestSpec.url` として発行し、失効後の一回限りの再発行は `recover_image_request` で解決する。locator、`image_id`、image/manifest metadata に token、cookie、署名 URL、鍵などの秘密値を置かないことは plugin author の責務である。`ImageArtifact.source_url` は実際の response URL ではなく manifest locator を保持し、成功時の最終 HTTP URL を public result DTO から取得する契約はない。browser/interactive feature が必要な site は manifest を偽装せず `UnsupportedSiteFeature` または明示的な authentication error として終了する。

## Result boundary

通常の fetch/process/save failure は `continue_on_image_error=true` なら `ImageOutcome.failure` に残る。authentication、configuration、plugin、storage safety、inter-process lock、fail-fast failure は partial `DownloadResult` を返さず operation を例外で終了する。`check_updates()` も complete `UpdateResult` または exception のいずれかである。
