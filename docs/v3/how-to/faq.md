# FAQ: CLI、ライブラリ、plugin 開発

<a id="faq"></a>

この FAQ は API v3 の利用時によくある判断を、CLI 利用者、Python ライブラリ利用者、plugin 開発者の観点から要約するものである。設定値、API signature、例外、hook の順序・回数・並行性はここで再定義しない。各回答からリンクする [Reference](../reference/configuration.md) と [execution lifecycle](../explanation/execution-lifecycle.md) が正本である。

提示された質問には Q6 が含まれていないため、本書にも Q6 の項目はない。

## 共通 Q&A

### Q1. plugin ID を指定して実行できるか？

できる。CLI は `--plugin ID`、library は `plugin_id="ID"` を指定する。これは enabled な non-builtin site plugin を一つに限定するが、通常の `matches()`／`matches_with_config()` が URL を受理しなければ失敗する。matcher を明示的に bypass する必要がある運用・テストだけは `--force-plugin ID` または `force_plugin=True` を使う。fallback の明示 override と組み合わせることはできない。[plugin selection](../reference/plugin-hooks.md#plugin-selection) と [CLI options](../reference/cli.md#cli-options) を参照する。

### Q2. plugin 固有のユーザー設定を渡すには？

永続値は main/profile/site YAML の `plugin_settings.<plugin-id>.config` に置く。例えば host 固有の site layer では次のように書ける。

```yaml
# sites/example.test.yaml
plugin_settings:
  com.example.gallery:
    config:
      page_size: 50
```

一回だけの上書きには、repeatable な `--plugin-config ID=JSON` または JSON の `--plugin-config-file` を使う。

```powershell
image-downloader download https://example.test/gallery `
  --plugin-config 'com.example.gallery={"page_size":50}'
```

author default、永続設定、operation override の順に deep merge され、CLI override は YAML に保存されない。秘密値は `config` ではなく `secrets` の外部参照として指定する。[plugin settings and secrets](../reference/configuration.md#config-plugin-settings) と [CLI options](../reference/cli.md#cli-options) を参照する。

### Q3. plugin 側はユーザー設定をどう受け取るか？

`validate_config(config, app_settings)` でまず検証する。`inspect`、`create_image_request`、`recover_image_request`、`auth_flow` では `PluginExecutionContext.config`、site `transform_image` では `TransformContext.config` を読む。設定対応の選択 hook `matches_with_config` は引数 `config` を直接受け取る。これらは読み取り専用 snapshot であり、plugin が変更してはならない。[capability contexts](../reference/plugin-hooks.md#plugin-contexts) を参照する。

### Q4. `inspect()` が遅いと画像ダウンロードの開始も遅れるか？

はい。`inspect()` は全章・全画像を含む有限で完全な `DownloadManifest` を返してから、画像 job が開始される。大量 pagination では初回画像の開始が遅くなり得るが、現行 API に manifest をストリーミングしながら画像を保存する方式はない。plugin は必要なら `inspect()` 内で安全にページ取得を並列化してよいが、返却時には完全な manifest でなければならない。[dynamic discovery](../explanation/execution-lifecycle.md#dynamic-discovery-and-recovery) を参照する。

### Q5. URL を `create_image_request()` で作るなら、`inspect()` で URL を決めなくてよいか？

短命の署名 URL や画像ごとの header は、画像 fetch の直前に `create_image_request()` で決めてよい。`inspect()` は完全な画像集合と順序を決め、各 `ImageResource.url` には request を組み立てるために十分な non-empty string locator を入れる。canonical URL、`image:42` のような ID、`/placeholder/42` のような placeholder を使える。locator、`image_id`、manifest metadata に token、cookie、署名 URL、鍵などの秘密値を置いてはならない。core は locator を送信せず、`create_image_request()` が返す absolute HTTP(S) `RequestSpec.url` だけを送る。失効した署名 URL を HTTP `status >=400` 後に一度だけ再発行する場所は `recover_image_request()` である。[dynamic URL how-to](dynamic-urls-and-auth.md) を参照する。

### Q7. `Chapter.images` の並び順どおりに URL へアクセスするには？

全 plugin に影響させず、選択された plugin だけを manifest 順に直列化するには `download_policy.preserve_image_start_order: true` を設定する。

```yaml
plugin_settings:
  com.example.gallery:
    download_policy:
      preserve_image_start_order: true
```

これは chapter/image job をともに `1` とし、実際の image request hook の開始順を保持する。global に適用する場合は従来どおり `download.chapter_concurrency: 1` と `download.image_concurrency_per_chapter: 1` を使う。[concurrency](../explanation/execution-lifecycle.md#lifecycle-concurrency) を参照する。

### Q8. 画像 URL への並列アクセスを禁止するには？

選択 plugin の image job を直列にするには Q7 の `preserve_image_start_order` を使う。画像 URL 解決、認証、鍵取得、`inspect()` を含むその operation の全 HTTP transport も一件ずつにしたい場合は、同じ `download_policy` に `request_concurrency: 1` を置く。これは global `network.request_concurrency` を緩めず、selected operation だけをさらに制限する。

ただし `request_concurrency: 1` は HTTP transport の上限だけであり、`create_image_request()` の実行中には取得されない。複数 image job が並列なら、複数の署名 URL が発行された後に最終画像 request が待機し、短い TTL の URL が失効し得る。署名 URL の発行から画像取得までを画像ごとに直列化する保証が必要なら、`preserve_image_start_order: true`（または chapter/image concurrency をともに `1`）を使う。現行 API に、発行と最終取得を原子的に予約する機構はない。[configuration schema](../reference/configuration.md#config-schema) を参照する。

### Q9. 画像 URL の決定に鍵取得など別 URL へのアクセスが必要な場合は？

`create_image_request()` から `await context.requests.execute(RequestSpec(...))` を呼び、鍵、署名 URL、画像用 token を取得して最終 `RequestSpec` を返す。plugin は raw HTTP client を作らない。manifest locator は ID や placeholder に留め、取得した鍵・token・署名 URL を locator、`image_id`、image/manifest metadata へ保存してはならない。operation 内で共有する鍵を cache する場合、同 hook が並列に呼ばれるため lock または共有 `Future` で一回だけ取得する。`download_policy.request_concurrency: 1` は実 transport を直列にするが、hook 内の鍵取得を自己デッドロックさせない。一方、同設定だけでは署名 URL 発行と最終画像取得を不可分にはしない。URL の TTL が短い場合は `preserve_image_start_order: true` を併用し、画像 job 自体を直列化する。認証更新は `AuthFlow.refresh()`、失効済み署名 URL の HTTP failure 後の再発行は `recover_image_request()` を使う。[runtime transport](../reference/runtime-behavior.md#runtime-transport) を参照する。

### Q10. `auth_flow()` は何をするべきで、何をするべきでないか？

`auth_flow()` は operation ごとに一度、`AuthFlow` または `None` を返す同期 factory である。返した flow の `apply()` は credential を request に適用し、`is_auth_failure()` は独自の認証失敗を判定し、`refresh()` は失敗後に token 更新・ログインを行い再試行 request を返す。credential を追加 origin に送る必要があるときだけ `allowed_origins` を明示する。

`auth_flow()` 自体で画像取得、manifest 生成、変換、raw HTTP、credential の永続書込みをしてはならない。ネットワークを伴う更新は `refresh()` から `context.requests` を通して行う。[authentication hook](../reference/plugin-hooks.md#plugin-hooks) を参照する。

### Q11. `transform_image()` は並列安全か？　他画像の結果に依存してよいか？

並列安全でなければならない。選択済み site plugin の同じ instance に対し、複数画像の request hook と site transform が並列に呼ばれ得る。他画像の完了順、変換結果、共有可変 state に依存してはならない。processor transform には instance ごとの runtime lock があるが、site `transform_image()` にはない。[execution lifecycle](../explanation/execution-lifecycle.md#lifecycle-concurrency) を参照する。

### Q12. 変換時に、鍵ファイルなど画像取得時の別情報が必要なら？

`TransformContext` には network と secret capability がないが、`inspect()` 時点で確定した非秘密な画像補助情報は `ImageResource.metadata` に置き、`TransformContext.image_metadata` から読める。画像 request 時に取得した鍵・token・nonce など、transform だけに渡す値は `create_image_request()`／recovery が `ImageFetchRequest(request, plugin_data)` を返して渡す。成功後、site transform は `TransformContext.transport_metadata.plugin_data` と実際の response URL/header を読める。processor は利用者が processor/site の組を明示許可した場合だけ raw 値を読め、未許可なら redacted snapshot になる。`ImageArtifact.source_url` は manifest locator を保持し、結果 DTO には最終 response URL は出ない。鍵を header/cookie として使うだけなら、transform ではなく request hook または `AuthFlow` で完結させる。

複数画像の変換結果を集約する処理は現行 hook 契約の外であり、application 側に集約 hook を追加する設計が必要である。[capability contexts](../reference/plugin-hooks.md#plugin-contexts) を参照する。

### Q13. `ImageResource.metadata` と `ImageFetchRequest.plugin_data` はどう使い分けるか？

両者は immutable な `Mapping[str, str]` だが、決定時点と公開範囲が異なる。`ImageResource.metadata` は `inspect()` が manifest を作る時点で確定する**非秘密・安定した画像属性**であり、`DownloadResult.manifest` の画像にも含まれる。`ImageFetchRequest.plugin_data` は `create_image_request()` または `recover_image_request()` が画像 fetch 直前に得た**transform 専用の request-time data**であり、artifact、result、event、log、CLI JSON には自動コピーされない。

| 観点 | `ImageResource.metadata` | `ImageFetchRequest.plugin_data` |
| --- | --- | --- |
| 設定する hook | `inspect()` | `create_image_request()` / `recover_image_request()` |
| 適した値 | `variant`、page 番号、stable ID に付随する非秘密属性 | 復号用の一時値、nonce、request 時に取得した派生値 |
| secret | 入れてはならない | raw access は transform に限定されるが、必要最小限にし永続化・出力しない |
| recovery | manifest の値として不変 | bare `RequestSpec` を返すと前の data を継承し、`ImageFetchRequest` を返すと置換 |

したがって、「画像が何であるか」を表す情報は `metadata`、「今回の画像取得で得られ transform にだけ必要な情報」は `plugin_data` に置く。locator、`image_id`、manifest metadata に鍵・cookie・token・署名 URL を置く代替手段として `plugin_data` を使う。[value objects](../reference/library-api.md#value-objects-results-and-protocols) を参照する。

### Q14. `TransformContext.image_metadata` と `TransformContext.transport_metadata` の役割は？

`image_metadata` は `ImageResource.metadata` の読み取り専用 view であり、`inspect()` 時点の非秘密情報を site transform と configured processor に伝える。`transport_metadata` は成功した画像 fetch の実行時 snapshot であり、AuthFlow 適用後 initial request、redirect 後 final request、response URL/header、実際に送信された Cookie、`plugin_data` を持つ。cookie jar 全体は渡さず、同名 response header の複数値は tuple のまま保持する。

| 観点 | `image_metadata` | `transport_metadata` |
| --- | --- | --- |
| 情報源 | `inspect()` の `ImageResource.metadata` | 実画像 fetch と `ImageFetchRequest.plugin_data` |
| 値の性質 | 非秘密・安定 | request/response に依存する一時 snapshot |
| site transform | raw 値 | raw 値 |
| processor transform | raw 値（非秘密契約） | 明示許可時だけ raw、未許可時は `is_redacted=True` の同型 snapshot |
| public result への自動伝播 | manifest の画像 metadata として残る | 残らない |

processor へ raw transport data を渡す組は、`image_processors.transport_metadata_access` で永続設定する。processor が transport 情報を必要としないなら、この allow-list を追加せず site transform 内で処理を完結させる。[transport metadata access](../reference/configuration.md#config-plugin-settings) と [capability contexts](../reference/plugin-hooks.md#plugin-contexts) を参照する。

## CLI 利用者向け Q&A

### 1. 実際にどの plugin が選ばれるか確認するには？

`image-downloader doctor --host https://example.test/gallery --json` を使う。`selection` と `loaded_plugins` を確認し、URL に対する候補、設定、verification 状態を読む。これは registry-only の read-only 操作である。[doctor command](../reference/cli.md#cli-io) を参照する。

### 2. 一回だけ plugin 設定を変えるには？

`--plugin-config ID=JSON` を使う。複数指定と `--plugin-config-file` は deep merge される。一回限りの値は YAML を変更しないため、試験や automation に適する。対象 ID は選ばれた site plugin、または有効な processor でなければならない。[CLI options](../reference/cli.md#cli-options) を参照する。

### 3. profile や host ごとに設定を分けるには？

`config profile init NAME` で profile layer を作り、`sites/<host>.yaml` で host overlay を置く。適用順と最終値は `config explain --profile NAME --host HOST --json` で確認する。`storage`、`plugins`、`security` など site layer に置けない key もあるため、層の制限は [configuration layers](../reference/configuration.md#config-layers) を確認する。

### 4. サイトへ負荷をかけず順番に取得するには？

site overlay または main config の `plugin_settings.<id>.download_policy.preserve_image_start_order: true` を使う。鍵取得を含むその operation の HTTP transport も一件ずつにするなら `request_concurrency: 1` を追加する。全 plugin に適用したいときだけ global `download`／`network` concurrency を下げる。[configuration schema](../reference/configuration.md#config-schema) を参照する。

### 5. automation は成功・部分失敗をどう判定するか？

`--json` の stdout にある一つの JSON object と exit status を使う。通常の画像失敗は `failures` に入り、部分成功は exit code `5`、authentication/secret failure は `3`、plugin failure は `4` である。stderr は診断用として保存する。[JSON contract](../reference/cli.md#cli-json) と [exit status](../reference/cli.md#cli-exit-status) を参照する。

### 6. login が必要なサイトの cookie や credential はどう扱うか？

browser cookie は `cookie browser-import DOMAIN`、バックアップ・移行は `cookie export` と `cookie import` を使う。passphrase、cookie 値、raw credential を CLI option、YAML、ログへ書かない。plugin 用 secret は keyring または環境変数から logical reference で解決する。[secure operations](operate-securely.md) と [plugin secrets](../reference/configuration.md#config-secrets) を参照する。

### 7. plugin を安全に導入・診断するには？

`plugin install SOURCE --yes` は staged install、署名・tree・class の検証、catalog trust を行う。導入後は `plugin list` と `doctor --json` で確認する。`strict` verification を通常運用の既定とし、`warn` や bypass mode は復旧・開発用途だけにする。[plugin package and trust](../reference/plugin-package.md#plugin-catalog) を参照する。

### 8. `request_concurrency: 1` だけで短命の署名 URL を安全に扱えるか？

十分ではない。これは実 transport の同時数だけを制限し、`create_image_request()` の実行中には slot を保持しない。複数の image job が並列なら、複数の署名 URL が発行された後、最終画像 request が順番待ちになり TTL 切れになり得る。URL の発行から取得までを画像単位で順に進める必要がある場合は、`preserve_image_start_order: true`（または chapter/image concurrency をともに `1`）を使う。現行 API にこの二段階を原子的に予約する機構はない。[dynamic URL how-to](dynamic-urls-and-auth.md) を参照する。

### 9. 複数の CLI process を実行しても、サイトへの総並列数は抑えられるか？

抑えられない。`request_concurrency`、origin/domain concurrency、rate limit は一つの process 内の service/gateway ごとの制限であり、別 process 間で共有されない。複数 process を使う運用では、外部 job queue、process 数の制限、サイト側の rate-limit 応答を尊重する retry 設定で総量を制御する。単一 process 内での制限は [HTTP transport](../reference/runtime-behavior.md#http-transport) を参照する。

### 10. `--plugin`／`--force-plugin` を指定して、画像を保存せずに検証できるか？

できる。`image-downloader inspect URL --plugin ID` は通常 matcher が URL を受理することを確認したうえで、通常 download と同じ selection、plugin config、verification、AuthFlow 構築、cleanup を使う。`--force-plugin ID` は同じ検証をした後に matcher だけを bypass する。既定では manifest 順に `create_image_request()` も実行するが、`--manifest-only` を付ければ request hook を呼ばない。いずれも画像 body を fetch、transform、save しない。ただし site `inspect()` と request hook が補助 HTTP を行えばサーバー側のアクセス記録・token 発行などは起こり得るため、純粋な matcher 候補だけを読みたいときは `doctor --host URL --json` を使う。実行時の選択規則は [plugin selection](../reference/plugin-hooks.md#plugin-selection) を参照する。

### 11. image processor の `download_policy` は有効か？

有効ではない。core が scheduler と operation-local request cap に使うのは、選択された **site plugin** の `plugin_settings.<id>.download_policy` だけである。processor の private `config` に並列度に関する値を書いても core scheduler は変更されない。現行設定 parser は processor の `download_policy` を明示的に拒否しないため、設定を受理しても効果がない。この点は設定上の既知の制限であり、設定例では site plugin ID にだけ policy を置く。[plugin download policy](../reference/configuration.md#config-plugin-settings) を参照する。

### 12. 画像を取得せず、署名 URL を含む request の組立結果を確認できるか？

できる。`image-downloader inspect URL --json`（または `download URL --inspect-only --json`）は、manifest 順に `create_image_request()` の後で `AuthFlow.apply()` と HTTP request 組立てを行い、画像 body を送らず送信直前 URL を表示する。既定の `--inspection-data url` はその URL・位置・状態だけ、`--inspection-data http` は source `RequestSpec` と実 header/cookie/Base64 body、`--inspection-data all` はさらに locator、manifest/image metadata、`ImageFetchRequest.plugin_data` を raw で出す。`--manifest-only` は request hook を呼ばない。redirect 後 URL、response header、recovery 結果は response を必要とするため含まれない。署名 URL は表示した時点で失効し得るため後続 download に再利用しない。`url` でも署名 query は秘密値になり得、`http`／`all` は credential を含むので stdout/stderr を log、CI artifact、telemetry、共有端末、チケット、第三者サービスへ転送してはならない。[JSON contract](../reference/cli.md#cli-json) を参照する。

## ライブラリ利用者向け Q&A

### 1. `DownloadService` の resource lifetime は誰が管理するか？

`RuntimeComposer.compose()` が作った service は caller が所有し、必ず `async with` または `await service.close()` で閉じる。HTTP client、worker resource、log、state の cleanup を service に任せる。

```python
async with RuntimeComposer(
    config,
    config_root=config_root,
    plugin_root=plugin_root(config),
).compose() as service:
    result = await service.run("https://example.test/gallery")
```

import と設定ロードを含む完全な例は [embedded download tutorial](../tutorials/embedded-download.md) を参照する。

### 2. `DownloadResult` と例外はどう使い分けるか？

通常の fetch/process/save failure は、`continue_on_image_error=true` なら `ImageOutcome.failure` に入り、`DownloadResult` は返る。authentication、configuration、plugin、storage safety、lock、cancellation の失敗は部分 result を返さず例外になる。catch する例外と stable code/reason は [caller outcomes](../reference/library-api.md#api-call-outcomes) と [error catalog](../reference/library-api.md#api-errors) を参照する。

### 3. 一回だけ plugin 設定を上書きするには？

`service.run()` の `plugin_overrides` に plugin ID から object への mapping を渡す。設定は selected candidate に対して検証され、YAML には保存されない。

```python
await service.run(
    "https://example.test/gallery",
    plugin_overrides={"com.example.gallery": {"page_size": 50}},
)
```

[one-operation override](../tutorials/embedded-download.md#use-a-one-operation-plugin-override) を参照する。

### 4. 更新だけを確認して画像を保存しないには？

`await service.check_updates(url)` を使う。選ばれた plugin が `UpdateProvider` を実装している必要があり、結果は完全な `UpdateResult` または例外である。CLI の対応操作は `download URL --list-updated-urls` である。[update tutorial](../tutorials/embedded-download.md#check-a-complete-update-snapshot) を参照する。

### 5. 一つの service で複数 download を並列実行できるか？

できない。一つの `DownloadService` 内では `run()` と `check_updates()` は直列化される。独立した operation を並列化する必要があるなら、service を別に compose し、それぞれの resource lifetime を閉じる。画像と章の内部並列度は config で制御する。[execution lifecycle](../explanation/execution-lifecycle.md#lifecycle-concurrency) を参照する。

### 6. library API から plugin を強制選択できるか？

できる。`await service.run(url, plugin_id="com.example.gallery")` は通常 matcher が URL を受理するときだけ選択する。テストや復旧で matcher を bypass する必要があるときだけ `force_plugin=True` を併用する。`plugin_download_policy_overrides={"com.example.gallery": {"request_concurrency": 1}}` は一 operation の core policy をさらに制限する。[runtime facade](../reference/library-api.md#api-configuration) と [plugin selection](../reference/plugin-hooks.md#plugin-selection) を参照する。

### 7. event や log を成功判定に使ってよいか？

使わない。成功・失敗の主な判定は `DownloadResult`、`ImageOutcome.failure`、公開例外で行う。event、notification、log は観測用であり、cleanup failure も主 operation の結果を置き換えない。[observability explanation](../explanation/observability.md) を参照する。

### 8. 複数の `DownloadService` で HTTP 上限・rate limit は共有されるか？

共有されない。HTTP pool、cookie jar、origin/domain limiter、rate limiter は service 単位で所有される。同じ service の `run()`／`check_updates()` は直列化されるため、並列 operation のために複数 service を compose すると、その service 間で request 上限は合算されない。アプリケーション全体の上限が必要なら、呼出側で semaphore／queue を置くか、一つの process に operation coordinator を設ける。[runtime facade](../reference/library-api.md#runtime-facade) を参照する。

### 9. cancellation 時に partial `DownloadResult` を受け取れるか？

受け取れない。`asyncio.CancelledError` は `DownloadResult` に変換されず再送出される。途中まで保存されたファイルがある可能性を結果 DTO から復元する契約もないため、呼出側は cancellation を例外として扱い、出力 directory や event/log を必要に応じて運用上確認する。完了済み結果が必要な job system では、operation を小さな独立 job に分割する。[caller outcomes, ownership, and cancellation](../reference/library-api.md#caller-outcomes-ownership-and-cancellation) を参照する。

### 10. `ImageArtifact` から最終 response URL や response header を読めるか？

`ImageArtifact`、`DownloadResult`、`--json` からは読めない。`source_url` は manifest の locator であり、HTTP response URL ではない。ただし plugin の site transform は `TransformContext.transport_metadata` から、実送信された initial/final request、最終 response URL/header を読める。image processor は `image_processors.transport_metadata_access` でその site plugin を明示許可されている場合だけ raw 値を読み、それ以外は redacted snapshot を受ける。raw 値を artifact、結果、log へコピーしてはならない。[value objects](../reference/library-api.md#value-objects-results-and-protocols) を参照する。

### 11. 大きな manifest を逐次処理または逐次通知できるか？

できない。`inspect()` の完全な `DownloadManifest` が返って初めて画像 job が始まり、public API に manifest item の stream や progress callback はない。大量の pagination を扱う plugin は `inspect()` 内で有限な集合へ収束させる必要がある。規模上これが成立しないサイトは、サイト側の page/chapter 単位 URL を別 operation として呼出側で分割する。[dynamic discovery](../explanation/execution-lifecycle.md#dynamic-discovery-and-recovery) を参照する。

### 12. `plugin_download_policy_overrides` は自動選択 plugin にどう指定するか？

候補ではなく、選択される site plugin の ID を key に渡す。自動選択で ID が事前に分からない場合は `PluginRuntime` の選択診断または CLI `doctor` で通常 matcher の結果を先に確認し、その ID を渡す。別 plugin の ID を含む override は設定エラーであり、候補すべての policy を同時に渡すことはできない。確実に対象を固定したい場合は `plugin_id`（必要なら `force_plugin=True`）を併用する。[runtime facade](../reference/library-api.md#runtime-facade) を参照する。

### 13. 保存せずに manifest と request 組立結果を取得するには？

`await service.inspect(url)` は `ManifestInspectionResult` を返す。既定では manifest 順に `create_image_request()` を一回ずつ呼び、各成功は raw `RequestSpec`、`plugin_data`、bare `RequestSpec` か `ImageFetchRequest` かを持つ。ある画像の解決失敗は `ImageRequestResolution.failure` に入り、後続画像を続ける。一方、selection、manifest `inspect()`、AuthFlow 構築、cleanup、cancellation の失敗は result を返さず例外となる。`resolve_image_requests=False` なら image request hook を呼ばず空の resolution 一覧を返す。画像 body、recovery、transform、processor、save、event、notification、update state は実行しないが、plugin hook の補助 HTTP は起こり得る。結果の request material は credential を含み得るため、保存・log・外部公開をしない。[caller outcomes](../reference/library-api.md#api-call-outcomes) を参照する。

## plugin 開発者向け Q&A

### 1. config によって URL matcher を変えられるか？

`matches_with_config(url, config, app_settings)` を任意で実装できる。これは同期・副作用なしで、private config だけに依存し、network、secret、filesystem を使ってはならない。通常の `matches(url)` も bool を同期で返す。設定値は `validate_config()` で検証する。[selection contract](../reference/plugin-hooks.md#plugin-selection) を参照する。

### 2. `inspect()` はどこまで仕事をするべきか？

URL を検査して一つの完全・有限・deterministic な `DownloadManifest` を返す。cursor/page API の反復、停止条件、重複排除、上限はここで完結させる。JavaScript、browser automation、infinite scroll の後追いは core が行わない。`context.requests` で HTTP を実行する。[dynamic discovery](../explanation/execution-lifecycle.md#dynamic-discovery-and-recovery) を参照する。

### 3. 署名 URL と失効後の再発行はどの hook に置くか？

manifest には解決可能な locator と必要なら識別子を置く。canonical URL、stable ID、placeholder を使えるが、秘密値を置いてはならない。画像 fetch 直前の短命 URL 発行は `create_image_request()` が locator を解釈して actual absolute HTTP(S) `RequestSpec.url` を返す。auth refresh 後も HTTP `status >=400` が残る場合の一回限りの再発行は `recover_image_request()` で行う。transport failure に recovery hook は呼ばれない。[dynamic URL how-to](dynamic-urls-and-auth.md) を参照する。

### 4. 認証はどのように実装するか？

`auth_flow()` は `AuthFlow` を構築する。`apply()` で request に認証を適用し、401/403 または独自失敗応答で `refresh()` が呼ばれて更新・再試行を行う。missing secret は `SecretNotFound`、ログイン・refresh の拒否は `AuthenticationError` にする。認証 endpoint を呼ぶ場合も `context.requests` を使い、再帰認証を避ける必要がある request は `auth_required=False` にする。[hook contract](../reference/plugin-hooks.md#plugin-hooks) を参照する。

### 5. plugin が利用できる network と secret の境界は？

`PluginExecutionContext` だけが `requests` と `secrets` を持つ。`inspect`、image request/recovery、`auth_flow` とその flow でのみ、`RequestSpec` を通じて通信し、secret を logical name で読む。site transform と processor transform が受ける `TransformContext` には network/secret capability がない。raw client、YAML の raw credential、secret の永続書込みは許可されない。[capability contexts](../reference/plugin-hooks.md#plugin-contexts) を参照する。

### 6. `create_image_request()` と `transform_image()` の共有 state はどう設計するか？

選択された site instance は同 operation 内の auth、inspection、request、transform hook で共有され、画像 hook は並列に呼ばれる。inspection 時に確定した画像単位の非秘密情報は `ImageResource.metadata`／`TransformContext.image_metadata` を優先する。遅延取得する共有値は lock/Future で coalesce し、画像ごとの値は `image_id` を key に分離する。unselected temporary instance の state に依存せず、他画像の完了順にも依存しない。[concurrency](../explanation/execution-lifecycle.md#lifecycle-concurrency) を参照する。

### 7. 鍵を使う変換と package 配布で注意する点は？

変換に必要な鍵や response 由来の値を request-capable hook で取得したら、画像単位の request-time data は `ImageFetchRequest.plugin_data` で site transform に渡せる。これは transform-only で artifact や log、例外、manifest、YAML には自動コピーされない。processor に raw 値を渡すには利用者が processor/site の組を明示許可する必要があるため、processor が本当に必要としない限り site transform 内で完結させる。複数画像で共有する遅延取得値は operation 内で lock/Future により並列安全に管理する。secret は外部 reference とし、長期保存しない。配布時は author-default YAML、entry source、helper を含む source tree を署名し、変更後は file-tree hash と signature を再生成する。`manifest.json` を手編集しない。[plugin settings and secrets](../reference/configuration.md#config-plugin-settings) と [signing workflow](../reference/plugin-package.md#plugin-signing-workflow) を参照する。

### 8. `create_image_request()` の補助 API 呼出と画像取得を一連で直列化するには？

`context.requests.execute()` で補助 API を呼び、返した `RequestSpec` を core に画像取得させる。`request_concurrency: 1` は補助 API と画像取得という各 transport を直列にするが、hook の実行から最終取得まで同じ lock を保持しない。短命 URL で画像ごとの発行・取得順まで保証するには利用者に `preserve_image_start_order: true` を設定してもらう。hook 側で core の semaphore を再実装したり raw client を使ったりしてはならない。現在は atomic な request reservation API がない。[lifecycle concurrency](../explanation/execution-lifecycle.md#lifecycle-concurrency) を参照する。

### 9. response header や最終 URL が transform に必要な場合は？

site `transform_image()` は `context.transport_metadata` から成功した最後の physical response の URL/header、AuthFlow 後 initial request、redirect 後 final request を読む。`ImageArtifact.source_url` は locator のままである。processor の raw access は default deny であり、利用者が `image_processors.transport_metadata_access` に processor ID と selected site ID の組を明示した場合だけ許可される。未許可 processor は query、header/cookie 値、plugin data が mask された同型 DTO を受ける。raw URL/header を artifact、manifest、result、event、log にコピーしてはならない。[capability contexts](../reference/plugin-hooks.md#capability-contexts) を参照する。

### 10. `metadata` に構造化データや秘密値を置けるか？

`ImageResource.metadata` には置けない。これは `inspect()` 時点で確定する immutable `Mapping[str, str]` の**非秘密**情報だけである。構造化データは stable ID と小さな文字列属性に正規化する。request 時にだけ transform へ渡す必要がある値は `create_image_request()` / recovery が `ImageFetchRequest(request, plugin_data)` を返して渡せる。`plugin_data` は transform-only で raw access は site と明示許可 processor に限られ、artifact、result、event、log へ自動コピーされない。それでも無制限の payload や長期保存先ではない。token、cookie、鍵、署名 URL、raw response header を `ImageResource.metadata`、manifest metadata、artifact に置かない。[capability contexts](../reference/plugin-hooks.md#capability-contexts) を参照する。

### 11. plugin の runtime 利用ごとの cache／resource はいつ破棄されるか？

selected site plugin instance は一回の `run()` または `check_updates()` の hook 群で共有され、configured image processor instance は一回の download で共有される。いずれも runtime が使い終えたとき、任意の `cleanup_after_use()` を一回だけ呼ぶ。通常の `def` と `async def` のどちらでも実装でき、async の戻り値は await される。完了後、runtime は同じ instance の plugin hook を再び呼ばないが、object の破棄や外部コードからの利用禁止を意味しない。success、inspect/auth/image failure、cancellation、update check のいずれでも対象で、processor cleanup が先、site cleanup が後である。temporary matcher instance には cleanup は呼ばれない。`aclose()`、`close()`、`aclose_operation()`、`close_operation()` は cleanup hook として呼ばれないため、既存 plugin は移行する。正常終了時の cleanup failure は `PluginError`、すでに失敗/cancellation があるなら主原因を維持して diagnostic に残る。[plugin hooks](../reference/plugin-hooks.md#cleanup-after-runtime-use) を参照する。

### 12. plugin 自身が scheduler や origin ごとの rate limit を指定できるか？

できない。plugin private `config` は plugin の動作を設定するもので、core scheduler を変更しない。選択 site plugin の `download_policy` は request/chapter/image の上限を global/host 設定以下へ下げられるが、origin/domain concurrency、rate limit、retry、timeout は application の `network` policy が一元管理する。plugin が raw HTTP client や独自 limiter でこれを迂回してはならない。[configuration schema](../reference/configuration.md#config-schema) と [HTTP transport](../reference/runtime-behavior.md#http-transport) を参照する。

### 13. display-only inspection で `create_image_request()` はどう呼ばれるか？

`DownloadService.inspect(resolve_image_requests=True)` と CLI `inspect` は、manifest の chapter/images 配列順に一画像ずつ `create_image_request()` を呼び、`auth_required=True` の request には `AuthFlow.apply()` も適用して送信直前 `EffectiveRequestPreview` を組み立てる。core は request を画像 transport に送らず、redirect、response 判定、refresh、recovery、transform、save を実行しない。source request は得られても auth／組立てが失敗した画像は `partially_resolved` となり、後続画像の inspection は続く。`create_image_request()` や `apply()` が `context.requests.execute()` を使えば token 発行などのサーバー側副作用は起こり得る。plugin は画像 URL を raw client で直接 fetch して inspection を迂回してはならない。CLI の公開範囲は `--inspection-data` で選び、library result 自体は完全な raw diagnostic data を保持する。[dynamic URL how-to](dynamic-urls-and-auth.md) を参照する。
