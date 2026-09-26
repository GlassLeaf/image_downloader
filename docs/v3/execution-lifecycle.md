# 実行ライフサイクルとデータフロー

この文書は site plugin、image processor、transport、保存の実行順序の正本である。hook の型は [完全 API 参照](api-reference.md)、package の作り方は [plugin 作者ガイド](plugin-author-guide.md) を参照する。

## download operation

~~~text
URL
  -> candidate matches / matches_with_config
  -> selected site instance + config validation
  -> auth_flow(context)                    [operation ごとに 1 回]
  -> inspect(url, context)                 [operation ごとに 1 回]
  -> finite DownloadManifest
  -> chapter jobs (bounded concurrency)
  -> image jobs (chapter ごとの bounded concurrency)
       -> create_image_request(image, context) [画像 fetch 開始ごとに 1 回]
       -> auth apply / refresh
       -> HTTP request
       -> recover_image_request(...)          [HTTP failure response ごとに最大 1 回]
       -> site transform_image
       -> core validation / normalisation
       -> processor transforms
       -> output allocation and save
  -> reports, events, cookie persistence on service.close()
~~~

選択時には candidate の照合のため一時 instance が作られ得る。選ばれた site instance は同じ operation の <code>auth_flow</code>、<code>inspect</code>、request hook、transform hook で共有される。選ばれなかった instance の state に依存してはならない。

<code>DownloadService.run()</code> と <code>check_updates()</code> は同一 service 内で直列化される。一方、chapter は <code>download.chapter_concurrency</code>、画像は chapter ごとに <code>download.image_concurrency_per_chapter</code> まで並行して進む。したがって <code>create_image_request</code>、site transform、processor transform は並行呼出に安全でなければならない。

## hook の責務と境界

| hook | 呼出時点・回数 | 入力と返却 | 使うべき用途 |
| --- | --- | --- | --- |
| <code>matches</code> / <code>matches_with_config</code> | 選択時、候補ごと | bool | URL と非 secret 設定による副作用のない選択 |
| <code>auth_flow</code> | 選択後、inspect 前、operation ごとに 1 回 | AuthFlow または None | request へ認証を適用し、認証失敗時の更新を定義 |
| <code>inspect</code> | operation ごとに 1 回 | 完全な DownloadManifest | 作品、章、画像識別子を有限集合として発見 |
| <code>create_image_request</code> | 各画像 fetch の直前に 1 回 | RequestSpec | image ごとの header、referer、直前 API 解決 |
| <code>recover_image_request</code> | response を伴う request failure 後、最大 1 回 | 代替 RequestSpec または None | 期限切れ署名 URL を再発行して 1 回だけ再試行 |
| <code>transform_image</code> | HTTP response 取得後、画像ごと | ImageArtifact | site 固有の画像変換 |
| processor <code>transform</code> | site transform 後、processor ごと | ImageArtifact | 共通または processor 固有の後処理 |

<code>PluginExecutionContext</code> は request と secret capability を持つ。<code>TransformContext</code> はそれらを持たない。transform 中に API を呼んだり secret を読む設計は許可されない。

## inspect と動的 URL

<code>inspect</code> は fetch 開始前に有限の <code>DownloadManifest</code> を返す必要がある。<code>ImageResource.url</code> は空文字、None、placeholder ではなく絶対 HTTP(S) URL である必要がある。generic fallback は raw HTML を取得するだけで、JavaScript、headless browser、DOM 後処理を実行しない。

無限 scroll や API cursor pagination は、plugin が <code>inspect</code> 内で API を反復し、すべての既知ページを manifest に収集する。停止条件、重複排除、上限は plugin が定義する。ブラウザがさらに scroll して初めて現れる情報を core が後追い収集することはない。実例は [cursor API gallery](../../plugin-sources/cursor-api-gallery/cursor_api_gallery.py) を参照する。

次の三つを混同しない。

| 状況 | 正しい設計 |
| --- | --- |
| API でページ一覧を取る必要がある | inspect 内で API を呼び有限 manifest を組み立てる |
| manifest 時点では有効 URL が分からず、画像 ID から取得直前に発行する | ImageResource.url には有効な canonical URL を置き、create_image_request で API を呼んで実際の URL を含む RequestSpec を返す |
| CDN URL が失効し response が失敗する | recover_image_request で再発行した RequestSpec を返す。None は recovery なし |

<code>RequestSpec.url</code> は <code>ImageResource.url</code> と異なってよい。dummy URL を <code>ImageResource</code> に置くことは許可されない。

## request、認証、recovery の順序

request spec は validation 後に auth flow が適用される。auth failure と判定された response では、設定回数まで <code>AuthFlow.refresh</code> が先に試行される。HTTP status error を伴う最終 response に対して <code>recover_image_request</code> が呼ばれ、代替 spec が返れば一度だけ retry する。recovery が返す request にも通常の auth 適用・検証が行われる。recovery 自身を再帰的に呼ばない。

auth refresh 回数は <code>network.auth_refresh_attempts</code>、通常 request retry は network 設定に従う。plugin は request hook 内で共有 mutable state を使う場合、同時呼出を安全に扱う必要がある。

## 変換と保存

成功した image response は次の順に処理される。

1. site plugin の <code>transform_image</code>
2. core の画像 input validation と normalisation
3. configured processor chain の <code>transform</code>（processor instance ごとに直列化）
4. final validation、output path allocation、保存

site transform には選択済み site の設定が渡る。processor transform には processor 自身の設定と site manifest/catalog 情報が渡るが、network と secret capability は渡らない。

画像 fetch、process、save の通常失敗は <code>continue_on_image_error=true</code> なら <code>ImageOutcome.failure</code> として結果に残る。認証、plugin、設定、storage safety、inter-process lock の失敗、または fail-fast 設定は operation を例外で終了させ、部分的な <code>DownloadResult</code> は返さない。
サイトが提供しない optional capability は <code>UnsupportedSiteFeature</code> として明示し、generic fallback や browser automation が補完すると仮定してはならない。

## update operation

<code>check_updates()</code> は <code>run()</code> の一部ではない独立 operation である。選択、operation context、<code>UpdateProvider.check_updates</code>、scoped update state の比較・保存を実行し、完全な <code>UpdateResult</code> を返すか例外を送出する。部分結果は返さない。
