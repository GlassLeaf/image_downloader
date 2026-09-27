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

選択 plugin の image job を直列にするには Q7 の `preserve_image_start_order` を使う。画像 URL 解決、認証、鍵取得、`inspect()` を含むその operation の全 HTTP transport も一件ずつにしたい場合は、同じ `download_policy` に `request_concurrency: 1` を置く。これは global `network.request_concurrency` を緩めず、selected operation だけをさらに制限する。[configuration schema](../reference/configuration.md#config-schema) を参照する。

### Q9. 画像 URL の決定に鍵取得など別 URL へのアクセスが必要な場合は？

`create_image_request()` から `await context.requests.execute(RequestSpec(...))` を呼び、鍵、署名 URL、画像用 token を取得して最終 `RequestSpec` を返す。plugin は raw HTTP client を作らない。manifest locator は ID や placeholder に留め、取得した鍵・token・署名 URL を locator、`image_id`、image/manifest metadata へ保存してはならない。operation 内で共有する鍵を cache する場合、同 hook が並列に呼ばれるため lock または共有 `Future` で一回だけ取得する。`download_policy.request_concurrency: 1` は実 transport を直列にするが、hook 内の鍵取得を自己デッドロックさせない。認証更新は `AuthFlow.refresh()`、失効済み署名 URL の HTTP failure 後の再発行は `recover_image_request()` を使う。[runtime transport](../reference/runtime-behavior.md#runtime-transport) を参照する。

### Q10. `auth_flow()` は何をするべきで、何をするべきでないか？

`auth_flow()` は operation ごとに一度、`AuthFlow` または `None` を返す同期 factory である。返した flow の `apply()` は credential を request に適用し、`is_auth_failure()` は独自の認証失敗を判定し、`refresh()` は失敗後に token 更新・ログインを行い再試行 request を返す。credential を追加 origin に送る必要があるときだけ `allowed_origins` を明示する。

`auth_flow()` 自体で画像取得、manifest 生成、変換、raw HTTP、credential の永続書込みをしてはならない。ネットワークを伴う更新は `refresh()` から `context.requests` を通して行う。[authentication hook](../reference/plugin-hooks.md#plugin-hooks) を参照する。

### Q11. `transform_image()` は並列安全か？　他画像の結果に依存してよいか？

並列安全でなければならない。選択済み site plugin の同じ instance に対し、複数画像の request hook と site transform が並列に呼ばれ得る。他画像の完了順、変換結果、共有可変 state に依存してはならない。processor transform には instance ごとの runtime lock があるが、site `transform_image()` にはない。[execution lifecycle](../explanation/execution-lifecycle.md#lifecycle-concurrency) を参照する。

### Q12. 変換時に、鍵ファイルなど画像取得時の別情報が必要なら？

`TransformContext` には network と secret capability がないが、`inspect()` 時点で確定した非秘密な画像補助情報は `ImageResource.metadata` に置き、`TransformContext.image_metadata` から読める。鍵・token のような秘密または request 時に変わる値は metadata に置かない。静的鍵は `inspect()` または `auth_flow()` で取得して、選択済み plugin instance の operation 内・不変 state として保持する。画像ごとの鍵は `create_image_request()` で取得し、`image_id` で引ける並列安全な operation 内 cache に保存して同画像の transform で参照する。`ImageArtifact.source_url` は manifest locator を保持するため、最終 response URL を transform/result DTO から取得することはできない。鍵を header/cookie として使うだけなら、transform ではなく request hook または `AuthFlow` で完結させる。

複数画像の変換結果を集約する処理は現行 hook 契約の外であり、application 側に集約 hook を追加する設計が必要である。[capability contexts](../reference/plugin-hooks.md#plugin-contexts) を参照する。

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

変換に必要な鍵は request-capable な早い hook で取得し、operation 内の並列安全な state として transform へ参照させる。secret は外部 reference とし、artifact や log、例外、manifest、YAML に混ぜない。配布時は author-default YAML、entry source、helper を含む source tree を署名し、変更後は file-tree hash と signature を再生成する。`manifest.json` を手編集しない。[plugin settings and secrets](../reference/configuration.md#config-plugin-settings) と [signing workflow](../reference/plugin-package.md#plugin-signing-workflow) を参照する。
