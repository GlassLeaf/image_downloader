# ライブラリ API

## 公開 import

利用者・plugin 作者は package root から import する。`image_downloader.runtime`、
`security`、`models` 等は内部 module であり、直接 import は互換性保証の対象外である。

```python
from image_downloader import (
    AppConfig,
    RuntimeComposer,
    DownloadService,
    DownloadResult,
    UpdateResult,
    ConfigurationError,
    PluginError,
)
```

公開 DTO は `Chapter`、`DownloadManifest`、`ImageResource`、`ImageArtifact`、
`RequestSpec`、`RequestResponse`、`ImageSaveOptions`、download/update result と関連 enum/例外
である。value object は immutable。mapping/list の変更ではなく、新しい DTO を作成するか
`dataclasses.replace()` を使う。

設定、DTOのJSON payload、plugin contextに含まれるJSON形式の値は、同じ再帰変換規則で
読み取り専用化する。mappingは文字列keyの読み取り専用mapping、list/tupleはtuple、
set/frozensetはfrozensetになる。HTTPやJSON serializationの境界では、これらを新しい
dict/listへ再帰的に戻すため、呼び出し元のcollectionとも内部のimmutable値とも共有しない。

## config を compose する

`RuntimeComposer` は app config、config root、plugin root を必要とする。`load_application_config()` と
`resolve_application_config()` が返す有効 config では、`storage.data_root` / `plugins.root` の `null` を
platformdirs の絶対 path に解決済みである。明示する場合は absolute path を使う。
`load_application_config()` は package 同梱 baseline も常に統合する。

```python
from pathlib import Path

from image_downloader import RuntimeComposer, load_application_config

config_root = Path(r"C:\work\downloader-config").resolve()
config = load_application_config(
    config_root / "app.yaml",
    profile="work",  # optional: main app profile.default より優先
    site="gallery.example.test",  # optional: host overlay を含める
    require_config=True,
)
plugin_root = Path(config.plugins.root).resolve()

service = RuntimeComposer(
    config,
    config_root=config_root,
    plugin_root=plugin_root,
).compose()
```

`compose()` は configuration、plugin root/catalog discovery、manifest/tree verification、
enabled site class と enabled chain processor class の contract を検査し、service snapshot を
作る。compose 後に plugin directory を変更しても既存 service は再発見しない。新しい service
を compose すること。

診断専用には`compose_registry()`を使用できる。これは同じmanifest/catalog/class discoveryを
行うが、`DownloadService`、HTTP client、cookie storeを生成しない。CLIの`doctor`はこの経路を
使うため、壊れたまたは復号不能な既存cookieによってplugin/config診断が妨げられない。通常の
download/updateには必ず`compose()`で得た`DownloadService`を使う。`compose_registry()`を直接
利用した場合は、診断後に`PluginRuntime.close()`を呼び出す。

内部ではdirectory discovery、manifest/catalog verification、class loading、URL selectionを
独立したcomponentとして構成する。`PluginRegistry`自体は検証済みrecordの読み取り専用snapshotと
ID検索だけを担当し、設定合成、doctor検証、instance生成、selectionはplugin runtime facadeが
調整する。これによりregistry snapshotはserviceの生存期間中に再discoveryされない。
`PluginRecord`はimmutableで、import済みclassやmodule namespaceを保持しない。これらの実行時状態は
service単位のclass loader cacheが所有する。各loaderは固有namespaceへpluginをimportし、load失敗、
`PluginRuntime.close()`、`DownloadService.close()`の各経路でnamespace配下を`sys.modules`から解放する。
このため、一方のserviceを閉じても別serviceが所有するplugin moduleには影響しない。

設定 object 自体をライブラリ側で変更する場合は、compose 前に
`apply_overrides(config, mapping)` を使う。operation ごとの generic app override はない。

## download と update

`DownloadService` は `async with` で使うか、必ず `close()` する。`close()` は HTTP client、
logger を閉じ、必要なら profile-scoped Cookie を保存する。

```python
async with RuntimeComposer(
    config,
    config_root=config_root,
    plugin_root=plugin_root,
).compose() as service:
    result = await service.run("https://gallery.example.test/item/42")
    print(result.saved_files)
    print(result.failures)

    updates = await service.check_updates("https://gallery.example.test/feed")
    for change in updates.changes:
        print(change.kind, change.url)
```

`run()` は selected site plugin の manifest を inspection し、chapter/image を処理・保存して
`DownloadResult` を返す。recoverable image failure は `download.continue_on_image_error` により `ImageOutcome`
として残り得る。plugin/config/auth/storage contract failure は image failure へ変換せず、
operation を raise する。

`check_updates()` は plugin が optional `check_updates()` を実装する場合にだけ使える。snapshot
を selected profile の state と比較し、`ADDED`、`CHANGED`、`REMOVED` の `UpdateResult` を
返す。対応しない plugin は `PluginError`。
更新履歴はplugin IDと呼び出し時のfeed URLの組ごとに保持する。同じpluginで複数feedを
交互に確認しても、一方の項目を他方のsnapshotから削除扱いにしない。feed URLの表記を
変えると別の履歴として扱うため、継続監視では同じURLを使用する。

同じ`DownloadService`に対する複数の`run()`／`check_updates()`呼び出しは安全に受け付けるが、
service単位で直列実行する。HTTP connection pool、Cookie jar、host/site rate limiterはservice内で
共有し、plugin ID、AuthFlow、許可origin、認証refresh状態はoperationごとのsessionに隔離する。
したがって前後または同時に要求されたoperation間で認証情報が混在しない。top-level operationを
実際に並行実行する必要がある場合は、operationごとに別の`DownloadService`をcomposeする。

## operation plugin override

両 operation は plugin private config と builtin fallback を一時上書きできる。

```python
result = await service.run(
    "https://gallery.example.test/item/42",
    plugin_overrides={
        "com.example.gallery": {"page_size": 50},
        "com.example.scaler": {"scale": 2},
    },
    fallback_override=None,  # None は YAML fallback.generic_html.enabled を使う
)
```

`plugin_overrides` の value は mapping で、author default → persistent
`plugin_settings.<id>.config` → operation override の順に deep merge される。指定できる ID は
selected site plugin と enabled processor chain のみ。選択されない ID、disabled processor、
plugin ID に対して mapping ではない value は `ConfigurationError`。mapping 内の scalar/list
は通常どおり後勝ちで置換する。`fallback_override=True/False` は YAML をその operation
だけ上書きする。

`check_updates()` の引数と validation 規則は同じである。

## 例外

| 例外 | 意味 |
|---|---|
| `ConfigurationError` | 設定 schema、path、profile、unknown plugin setting、runtime override が不正 |
| `PluginError` | manifest/signature/tree/class contract、selection tie、plugin hook contract、未対応 update |
| `AuthenticationError` / `SecretNotFound` | auth flow または external secret の失敗 |
| `StorageSafetyError` | output/state path が trusted root を外れる操作 |
| `RequestError` | HTTP transport、status、redirect policy、response size の失敗 |
| `ImageProcessingError` | HTTP応答取得後の画像検証、decode、format、MIME、pixel limit、worker の失敗 |
| `StorageError` | output allocation、update state、lock、storage safety の失敗 |

`ImageDownloaderError` は上記の予期済み例外群の共通基底である。通常は recovery に対応する具体例外を
捕捉し、広域のエラーハンドラだけがこの基底を捕捉する。旧一括例外は廃止され、互換名はない。
`FailureKind.PROCESS` は HTTP取得が成功した後の画像検証・decode・正規化失敗を表し、HTTP取得失敗ではない。

### 画像失敗の診断

章ごとの `log.log` は各失敗について `image_url`、`response_url`、`http_status`、`stage`、
`reason_code`、具体例外名を記録する。`stage: image_processing` と `transport: completed` は、
HTTP応答の取得後に画像処理で失敗したことを示す。

デスクトップ／メール通知は `image_fetch_failed`、`image_processing_failed`、`image_save_failed` を
件数として表示する。これはエラー番号ではない。理由コード別の件数と章・画像番号順の代表5件を表示し、
全件は章ログで確認する。URLと詳細は通常のログ安全化規則を通る。

`PluginError` が起きた時は「別 plugin へ自動 fallback」しない（ただし external match が
ゼロで generic fallback が有効な場合を除く）。`matches()` が例外を送出した時も同様に
plugin error である。

すべてのplugin operation hookは共通invokerを通る。予期しないcallback例外はplugin IDと
hook名を持つ`PluginError`へ変換し、元の例外を`__cause__`に保持する。`inspect()`、
`check_updates()`、request/recovery、AuthFlow、site/processor transformの戻り値は、外側のDTO
だけでなくchapter、image、candidate、URL、index、mappingの要素型まで利用前に検証する。

operation終了時は通知observerとPython log captureを共通diagnostics scopeでcleanupする。
flushが失敗してもhandlerの解除は必ず実行する。cleanup失敗は短い診断警告として扱い、
主処理の成功結果または例外を変更しない。

## test と dependency injection

`DownloadService` constructor、registry、request gateway は core の内部境界である。通常の
利用者は compose する。`RuntimeComposer` は filesystem、state、logger、event bus、notification、
Cookie、HTTP、画像処理backendを構築してserviceへ注入し、serviceは注入された依存の利用と
lifecycle管理だけを行う。repository の受入テストは HTTP transport を差し替えるが、外部
利用者がその内部属性に依存してはならない。

内部event busは `EventName` と immutableな `EventPayload` を境界に使用する。observerへ
渡す前にURL・path・例外情報を安全化し、observer自体が失敗しても本処理や他observerへは
伝播させず、`event_observer_failed` として安全な診断logへ記録する。通知設定は検証済みの
`Notification` modelをそのまま使用し、desktop/email配信は `NotificationSender` として
composition rootから注入する。

download operationは`BEFORE_DOWNLOAD`から始まり、戻り値がある場合は成功・部分成功・全件失敗の
いずれか一つと`DOWNLOAD_COMPLETE`を発火する。例外終了時は`DOWNLOAD_FAILED`、キャンセル時は
失敗扱いせず、いずれも`AFTER_DOWNLOAD`を発火する。画像単位の取得・処理・保存の失敗イベントは
発生時に一度だけ発火し、fail-fast時も欠落しない。update checkは開始・終了と、追加または変更された
URLのイベントを発火する。`EventName`に残る`LOGIN_SUCCESS`、`COOKIE_STORE_ACCESS`など、
coreに明確な判定点がないイベントは自動発火しない。既存の名前は、serviceのevent busから明示的に
発火する利用者との互換性のため維持する。認証適用だけでログイン成功とはみなさない。

plugin の unit test は context/DTO の fake を作り、integration test は temporary absolute
config/plugin root と署名済み fixture directory を用いる。詳しくは
[テストと移行](testing-and-migration.md) を参照する。

## 内部モジュール境界

互換性のため、package rootと`image_downloader.config`、`runtime`、`security`、
`cli`、`observability.logging`からの文書化済みimportは維持する。
これらの入口は再エクスポートのみを担い、内部実装から逆importしない。
文書化していない旧内部パス（例：`network`、`cli_parser`）は互換対象ではない。

| leaf module | 責務 |
|---|---|
| `configuration.models` | immutable設定モデルと既定値 |
| `configuration.layers` | YAMLレイヤー読込、merge、override |
| `configuration.hosts`／`configuration.paths` | host正規化／設定済みrootの解決 |
| `storage.path_safety` | 設定・plugin root等のパス安全性検証 |
| `privacy.sensitive_values`／`privacy.log_safety` | 機密値の判定、伏字化、安全な診断表示 |
| `credentials.keyring`／`credentials.plugin_secrets` | 鍵取得、plugin secret provider |
| `transport.gateway` | HTTP pool、retry、rate limit、operation単位の認証session |
| `output.output_allocator` | 出力pathの予約、衝突判定、commit／abort |
| `plugins.plugin_manifest` | manifest、署名、file tree、trust catalog |
| `plugins.lifecycle` | plugin discovery、immutable record、class cache、module namespaceのcleanup |
| `plugins.runtime`／`plugins.management` | plugin選択・実行時管理／install・trust・revoke |
| `plugins.builtin` | 組込みgeneric HTML plugin |
| `plugins.plugin_invoker` | plugin hookの例外変換、戻り値とnested DTOの境界検証 |
| `application.service`／`application.dependencies`／`application.composer` | 操作実行／依存型／既定依存の構築 |
| `media.artifact_pipeline` | 画像の変換・processor chain |
| `observability.chapter_reporter` | 章ごとのログと結果報告 |
| `commands.parser`／`commands.setup` | argparse・legacy URL構文／CLI設定準備 |
| `commands.download`／`commands.cookie`／`commands.doctor`／`commands.config`／`commands.plugin` | コマンド別の引数検証と処理 |
| `commands.validation`／`commands.dispatch` | 共通option検証／実行・終了コード |
| `observability.scope` | operation observerのflushとlog captureの例外安全なcleanup |
| `immutable` | JSON形式の値に対する共通freeze／thaw変換 |
