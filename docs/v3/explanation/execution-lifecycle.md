<a id="execution-lifecycle-and-data-flow"></a>

# 実行ライフサイクルとデータフロー

この文書は hook の順序、回数、失敗分岐、instance sharing、並行性の唯一の正本である。hook signature は [プラグインフックリファレンス](../reference/plugin-hooks.md)、request/persistence behavior は [実行時動作](../reference/runtime-behavior.md) を参照する。

```text
URL
  -> 候補の matches / matches_with_config
  -> 選択したサイトのインスタンス + 設定検証
  -> auth_flow(context)                         [操作ごとに1回]
  -> inspect(url, context)                      [操作ごとに1回]
  -> 有限の DownloadManifest
  -> 並列数を制限した章処理
  -> 並列数を制限した画像処理
       -> create_image_request(image, context) [画像ごとに 1 回]
       -> 認証の適用 / 設定に従う認証更新の再試行
       -> HTTP要求と通常の再試行
       -> recover_image_request(...)            [認証更新後もHTTPステータスが>=400の場合に最大1回]
       -> サイトの transform_image                 [生の ImageTransportMetadata]
       -> 本体の検証・正規化
       -> 設定したプロセッサーの変換       [許可一覧に応じた生・秘匿化済みのメタデータ]
       -> 最終的な画像検査                [ORIGINAL でも既定で全フレームを検査]
       -> 出力先の割当てと保存
  -> プロセッサーの cleanup_after_use
  -> 選択したサイトの cleanup_after_use
  -> レポート・イベント；service.close()でCookie差分を保存
```

candidate matching のため temporary instance が作られ得る。選択された site instance はその operation の auth flow、inspection、request hooks、site transform で共有される。unselected instance の state に依存してはならない。

<a id="lifecycle-concurrency"></a>

`DownloadService.download()` と `check_updates()` は同一 service で直列化される。chapter は `download.chapter_concurrency`、image は chapter ごとの `download.image_concurrency_per_chapter` まで並行する。したがって `create_image_request` と site `transform_image` は並行呼出に安全でなければならない。configured processor は **同一 processor instance ごとに runtime lock で直列化** されるが、global mutable state や別 service instance との共有には依存してはならない。

selected site plugin の `download_policy` はこれらの上限を下げられるが、上げることはできない。`preserve_image_start_order: true` は両方を `1` として manifest の chapter/image 順に image job を開始する。operation-local `request_concurrency` は request hook、auth、recovery、画像 fetch を含む physical transport/retry を制限するが、hook の実行中に保持されない。これは hook 内の `context.requests.execute()` を自己デッドロックさせないためである。したがって image job が並列なら、短命 URL を `create_image_request` で発行した後、最終画像 request が他 job の transport 待ちになることがある。`request_concurrency: 1` だけでは発行から取得までを原子的にしない。短い TTL を扱う plugin は `preserve_image_start_order: true`、または chapter/image concurrency をともに `1` にする。

<a id="dynamic-discovery-and-recovery"></a>

## 動的な検出と回復

`inspect` は fetch 開始前に有限の `DownloadManifest` を返す。generic fallback は raw HTML を読むだけで JavaScript/headless browser/DOM automation を実行しない。infinite scroll/cursor pagination は plugin が `inspect` 内で API を反復し、停止条件、重複排除、上限を定義して complete manifest にする。core は browser scroll を後追いしない。

| 状況 | 適切なフック |
| --- | --- |
| page list を API から得る | `inspect` 内で全 known page を収集 |
| image ID から取得直前に実 URL を発行する | `ImageResource.url` に stable locator（ID または placeholder）と必要なら `image_id` を置き、`create_image_request` で actual absolute HTTP(S) `RequestSpec.url` |
| signed CDN URL が response failure で失効している | `recover_image_request` で replacement spec、最大一回 |

`create_image_request` は各 image ごとに一度だけ呼ばれる。auth failure response は `network.auth_refresh_attempts` の範囲で configured `AuthFlow.refresh` が先に試行される。refresh 後にも残った **HTTP response の status が `>=400` のときだけ** recovery hook が最大一度呼ばれる。DNS/connect/read timeout など transport failure には recovery hook は呼ばれない。recovery request にも通常の auth/validation が適用されるが、recovery を再帰的に呼ばない。

`ImageResource` の constructor は URL を検査せず、hook invoker は plugin が返した `ImageResource.url` を non-empty string locator として検査する。locator は canonical URL、ID、placeholder のいずれでもよいが、`inspect` が決定した完全な画像集合の各画像を `create_image_request` で解決するのに十分に安定していなければならない。`ImageResource.metadata` は各画像の inspection-time 非秘密情報を immutable に保持し、request hook と transform context に渡る。core は locator を解釈・送信しない。直前署名 URL は `create_image_request` が actual absolute HTTP(S) `RequestSpec.url` として発行し、失効後の一回限りの再発行は `recover_image_request` で解決する。hook は `ImageFetchRequest` を返して request-time の `plugin_data` を transform に渡せる。成功した最後の physical response について core は AuthFlow 適用後の initial request、redirect 後の final request、response URL/header を `TransformContext.transport_metadata` に限定して渡す。site transform は raw 値を受け、processor は selected site ID を明示許可したときだけ raw、それ以外では redacted snapshot を受ける。`plugin_data` を含む raw metadata は artifact/result/event/log/CLI JSON に自動コピーされない。locator、`image_id`、image/manifest metadata に token、cookie、署名 URL、鍵などの秘密値を置かないことは plugin author の責務である。`ImageArtifact.source_url` は実際の response URL ではなく manifest locator を保持し、成功時の最終 HTTP URL を public result DTO から取得する契約はない。browser/interactive feature が必要な site は manifest を偽装せず `UnsupportedSiteFeatureError` または明示的な authentication error として終了する。

selected site instance は `download()` / `check_updates()` の runtime 利用区間で所有される。site plugin と configured processor は任意の `cleanup_after_use()` を実装でき、通常の戻り値または awaitable を返せる。runtime は instance を使い終えたときに一回だけこれを実行し、awaitable を await する。完了後に同じ instance の plugin hook を runtime が再び呼ぶことはないが、object の破棄や外部コードによる利用禁止を意味しない。temporary matcher instance には cleanup を呼ばず、`aclose()`、`close()`、`aclose_operation()`、`close_operation()` は cleanup hook として呼ばれない。processor cleanup が先、site cleanup が後である。正常終了時の cleanup error は `PluginError`、既存 error/cancellation があるときは主原因を保持し diagnostic に記録する。

<a id="result-boundary"></a>

## 結果の境界

追加ファイルを宣言する任意の site hook は download attempt の構成後、inspect 前に一度だけ段階を宣言する。core が `BEFORE_MANIFEST`、`AFTER_MANIFEST`、`BEFORE_IMAGE_REQUEST`、`AFTER_IMAGE_REQUEST`、`BEFORE_IMAGE_SAVE`、`AFTER_IMAGE_SAVE`、`AFTER_DOWNLOAD` の順で呼ぶ。画像取得後は認証更新・復旧を含む最終成功の後、保存前は変換の後、保存後は保存／skip 確定の後である。manifest 後と download 後は章ごと、画像の前後は処理対象画像ごとに呼ぶ。

各段階は `additional_files()` → core 取得 → `additional_file_received()` → core 保存／skip → `additional_file_saved()` の順で実行する。callback の await 完了を待って主処理へ進むため、取得した鍵は直後の request hook、画像取得後の補助データは site transform から利用できる。取得失敗時も received を一回呼ぶが保存と saved は呼ばない。callback 失敗でも保存を続ける。callback は保存先 lock の外で呼び、並行画像間では重なり得るため invocation ID と対象画像で plugin の状態を分離する。

`BEFORE_MANIFEST` の受け渡しは inspect 前に一度だけ行い、取得 bytes を operation 所有の一時領域に保持して、manifest 確定後に各章へ保存する。saved callback は元の段階と保存対象章を受け取る。一時領域は成功・例外・cancel で後始末する。章がなければ `no_target` を記録する。

workflow の保持画像は画像単位の hook と callback を再実行せず、その画像に対応する安全な追加結果を保持する。再取得画像は新しい試行 ID で再実行する。inspection、dry-run、更新確認はこの追加 hook を呼ばない。`AFTER_DOWNLOAD` は画像 failure を結果として返す通常完了でも呼ぶが、例外／cancel では呼ばず、processor／site cleanup より前に終了する。cleanup 自体の失敗によって全体が失敗する可能性は残る。

追加ファイルの取得・保存・既存ファイル読み込み・callback の失敗は `DownloadResult.additional_files` に分離し、画像の成功、失敗、終了コード、workflow 再試行判定を変更しない。必須データが得られなかったときは plugin が既存の request／transform hook で明示的に失敗させる。raw bytes、response、秘密の plugin state は公開結果へコピーしない。API と保存規則は [追加ファイル hook](../reference/plugin-hooks.md) を参照する。

通常の fetch/process/save failure は `continue_on_image_error=true` なら `ImageOutcome.failure` に残る。authentication、configuration、plugin、storage safety、inter-process lock、fail-fast failure は partial `DownloadResult` を返さず operation を例外で終了する。`check_updates()` も complete `UpdateResult` または exception のいずれかである。


## CLIの最終表示と終了処理

CLIは本処理の結果整形後にリソースを閉じ、workflowの履歴訂正を終えてから最終結果を表示します。JSONを出力する全コマンドで、本処理の`execution`と終了処理の`shutdown`を[1文書へまとめます](../reference/cli.md#cli-json)。本処理が成功し終了だけが失敗した場合も、成功結果を保持します。

本処理の例外・キャンセルは終了失敗より優先し、元の原因チェーンとworkflowの部分結果を保持します。待機側のキャンセルでも開始済みの終了処理を最後まで回収し、終了失敗があればそちらを優先します。主原因となる重大例外はJSON化しません。本処理の主原因がある場合の二次的な重大例外は、固定警告と終了側の中断状態で示します。logger終了の通常例外は警告だけとし、成功結果・終了コード・履歴を変更しません。
