# image-downloader 0.3.0 公開APIリファレンス（v2・履歴資料）

> 現行の compose/service contract は [v3 ライブラリ API](../../../../docs/v3/library-api.md) を参照してください。この文書の v2 constructor・plugin 前提は新規実装に使用できません。

この文書は、アプリケーションへ組み込む利用者向けの公開APIを、0.3.0の実装に合わせて説明します。プラグイン作者が実装するProtocolとDTOの詳細は[プラグインAPIリファレンス](plugin-api-reference.md)も参照してください。

## 1. 互換性とimport

公開シンボルはすべて`image_downloader`ルートからimportします。`image_downloader.runtime`、`image_downloader.models`などの内部モジュールからのimportは互換性保証の対象外です。

```python
from image_downloader import AppConfig, DownloadService, RuntimeComposer
```

0.3.0はv2専用です。v1の`BaseDownloader`、adapter、旧plugin context、旧entry pointを公開しません。

安定した実行インターフェースは次の3メソッドです。

| クラス | メソッド | 用途 |
|---|---|---|
| `DownloadService` | `await run(url)` | manifestを取得し、画像を処理・保存する |
| `DownloadService` | `await check_updates(url)` | pluginの完全snapshotと保存済みstateを比較する |
| `DownloadService` | `await close()` | HTTP client、Cookie、loggerを終了する |

`DownloadService`のコンストラクタは依存注入用の内部境界です。通常は`RuntimeComposer`を使ってください。

## 2. 最小使用例

```python
import asyncio
from pathlib import Path

from image_downloader import AppConfig, RuntimeComposer


async def main() -> None:
    config = AppConfig.model_validate(
        {
            "security": {"plugin_verification": "off"},
            "logging": {"console": {"enabled": False}},
        }
    )
    composer = RuntimeComposer(config, Path.cwd())
    async with composer.compose() as service:
        result = await service.run("https://example.test/gallery")
        print(result.saved_files)
        print(result.skipped_files)
        for failure in result.failures:
            print(failure.kind, failure.exception_type, failure.message)


asyncio.run(main())
```

本番では`plugin_verification="strict"`を使用し、署名済みpluginとcatalogを用意してください。上の`off`はAPI利用例を短くするための設定です。

## 3. `AppConfig`

```python
class AppConfig(BaseModel):
    profile: Profile
    output: Output
    media: Media
    logging: Logging
    network: Network
    notification: Notification
    security: Security
    image_processors: ImageProcessors
    plugins: Mapping[str, PluginSettings]
    continue_on_error: bool = True
    allow_empty_manifest: bool = False
```

Pydantic v2のfrozen modelです。未知のフィールドは拒否されます。`plugins`、plugin固有`config`、headers、通知routeなどの可変containerも再帰的に読み取り専用化されます。モデル生成後に値を変更せず、変更が必要な場合は新しいモデルを検証してください。

```python
config = AppConfig.model_validate({"output": {"image_format": "PNG"}})
updated = config.model_copy(update={"continue_on_error": False})
```

ただし、ネストしたdictを`model_copy(update=...)`で直接渡すとネストモデルの再検証を迂回し得ます。設定ファイルと同じ検証を行う場合は`AppConfig.model_validate(...)`、CLI内部と同じdeep mergeを行う場合は非公開実装に依存せず、完全な入力mappingを再構築してください。

主要な既定値は次のとおりです。全設定とレイヤー順は[利用ガイド](user-guide.md)を参照してください。

| 設定 | 既定値 | 意味 |
|---|---:|---|
| `continue_on_error` | `true` | 通常の画像取得・処理・個別保存失敗をoutcomeとして継続する |
| `allow_empty_manifest` | `false` | `chapters=()`を受理するか |
| `output.existing_file` | `overwrite` | 画像単位の既存ファイル処理 |
| `output.image_format` | `JPEG` | 画像側で形式指定がない場合の最終形式 |
| `media.input_validation` | `content_type` | 明白な非画像Content-Typeを拒否する |
| `media.content_type_mismatch` | `accept` | 宣言された画像MIMEとdecode結果の不一致を許可する |
| `network.max_retries` | `3` | transportの総試行回数。初回を含む |
| `security.plugin_verification` | `strict` | 外部pluginをcatalogと署名で検証する |

## 4. `RuntimeComposer`

### コンストラクタ

```text
RuntimeComposer(
    config: AppConfig,
    config_base_dir: pathlib.Path,
    *,
    debug_allow_unverified_plugins: bool = False,
)
```

| 引数 | 仕様 |
|---|---|
| `config` | 検証済みの`AppConfig` |
| `config_base_dir` | profile root、plugin catalogなどの相対パスを解決する基準ディレクトリ |
| `debug_allow_unverified_plugins` | `profile.default == "development"`かつstrict時だけ、そのcomposeをoff相当にする。その他のprofileでtrueにすると`ConfigurationError` |

### `compose() -> DownloadService`

次の処理を同期的に行い、serviceを返します。

1. catalog pathとverification modeを解決する。
2. 内蔵generic HTML pluginを登録する。
3. `image_downloader.plugins`を検出し、sidecarをimport前に確認する。
4. `image_downloader.image_processors`を同様に検出する。
5. profile配下のdownloads、logs、state、cookie境界を構成する。
6. 暗号化Cookieを読み込み、HTTP clientを生成する。

strictでcatalogが欠落・不正・安全でない場合は`ConfigurationError`です。個別の外部pluginが不正な場合はregistryのdiagnosticへ失敗として記録され、そのpluginを読み込みません。`doctor`で診断できます。

## 5. `DownloadService`

### ライフサイクル

serviceはasync context managerです。

```python
async def download(config, base_dir, url):
    async with RuntimeComposer(config, base_dir).compose() as service:
        return await service.run(url)
```

`__aexit__`は`close()`を呼びます。`close()`は複数回呼んでも安全です。close後の`run()`または`check_updates()`は`RuntimeError("DownloadService is closed")`になります。

同じserviceに対する公開operationは内部lockで直列化されます。したがって、同じserviceで`run()`を複数同時に呼び出してもoperation同士は並行しません。1 operation内では章と画像が設定上限まで並行します。

### `await run(url: str) -> DownloadResult`

処理順は次のとおりです。

1. URLに一致するplugin classを選び、新しいinstanceを生成する。
2. operation専用の`PluginExecutionContext`と`AuthFlow`を作る。
3. `plugin.inspect(url, context)`を呼び、`DownloadManifest`であることを確認する。
4. manifestの`metadata["source_url"]`を呼出しURLで設定する。pluginが同じkeyを返した場合はcoreの値で上書きする。
5. 各章、各画像を取得・変換・保存する。
6. `DownloadResult`、章ログ、debug log、設定された通知を確定する。

`chapters=()`は空manifestです。`allow_empty_manifest=false`なら`PluginError`、trueなら成功し、`Chapter(number=0, title=manifest.title, subtitle="")`から計算したdirectoryに`log.log`を作り、`DownloadResult.chapters`は空tupleになります。

章が1件以上あり、その章の`images=()`が空であるケースは空manifestとは扱いません。通常の章directoryと`log.log`を作り、空outcomesの`ChapterResult`を返します。

`continue_on_error=true`でoutcomeに変換されるのは、通常の画像取得、core/processor画像処理、個別ファイル保存の失敗です。次はoperation全体を停止します。

- `AuthenticationError`
- `ConfigurationError`
- `PluginError`（戻り値の型違反、plugin transformの例外を含む）
- `StorageSafetyError`
- 呼出しtaskのcancel

`continue_on_error=false`では最初の通常画像エラーも例外として送出します。未開始jobは開始せず、すでに開始したjobの終了を回収してから例外を返します。外部cancel時は実行中jobをcancelし、atomic writeの一時ファイルは保存境界で掃除されます。

### `await check_updates(url: str) -> UpdateResult`

選択されたpluginにcallableな`check_updates(url, context)`が必要です。未実装なら`PluginError`です。

pluginは差分ではなく現在の完全な`UpdateSnapshot`を返します。coreはprofileの`state/updates.json`と比較します。

- keyは`plugin.descriptor.id + ":" + (content_id or url)`。
- 初出keyは`ADDED`。
- 同じkeyでURLまたはrevisionが変化した場合は`CHANGED`。
- 前回存在し今回のsnapshotから消えた同一pluginのkeyは`REMOVED`。
- 他pluginのstateは保持する。
- 比較後のstateはatomic replaceで保存する。

`checked_at`はpluginのsnapshot値を結果へ引き継ぎます。壊れたstate JSONは現実装では空stateとして扱います。

### `await close() -> None`

未expired CookieがHTTP clientのjarに1件以上ある場合は、profileの`cookie/cookies.enc`へAES-256-GCMで保存します。暗号鍵はOS keyringに置きます。その後HTTP clientとloggerを閉じます。Cookie保存またはkeyringアクセスに失敗した場合は`AuthenticationError`になり得ます。

## 6. download結果DTO

公開DTOはすべて`@dataclass(frozen=True, slots=True)`です。tuple fieldへlist等を渡した場合はコンストラクタでtuple化されます。ID、章番号、画像indexの業務上の意味、重複、正負、連番性をcoreは検証しません。

### `DownloadManifest`

```text
DownloadManifest(
    title: str,
    chapters: tuple[Chapter, ...],
    content_id: str | None = None,
    author: str | None = None,
    access: str | None = None,
    revision: str | None = None,
    metadata: Mapping[str, str] = {},
)
```

`metadata`はkey/valueとも実際に`str`でなければ`TypeError`です。metadataは読み取り専用mappingになります。

### `Chapter`

```text
Chapter(
    number: int,
    title: str,
    subtitle: str = "",
    images: tuple[ImageResource, ...] = (),
    chapter_id: str | None = None,
)
```

`number`、`title`、`subtitle`は出力templateに使われ、安全でないファイル名文字はcoreが置換します。同じ値から同じ章directoryが得られた場合はdirectoryを再利用します。

### `ImageResource`

```text
ImageResource(
    url: str,
    index: int = 1,
    referer: str | None = None,
    headers: Mapping[str, str] = {},
    save_options: ImageSaveOptions = ImageSaveOptions(),
    image_id: str | None = None,
)
```

`index`はファイル名の`%NUM%`と章ログの表示順に使います。結果の`outcomes`自体はmanifest内の宣言順です。`headers`と`referer`は`create_image_request()`が利用するための情報で、coreが自動的にHTTP requestへコピーするわけではありません。

### `ImageSaveOptions`

```text
ImageSaveOptions(
    format: str | None = None,
    extension: str | None = None,
    quality: int | None = None,
    optimize: bool | None = None,
    progressive: bool | None = None,
    lossless: bool | None = None,
    compress_level: int | None = None,
    exif: bool = False,
)
```

| field | 適用条件・検証 |
|---|---|
| `format` | 大文字小文字を無視。`JPEG`、`PNG`、`WEBP`、`GIF`、`BMP`、`TIFF`を処理系が認識する。指定時はrootの`output.image_format`より優先 |
| `extension` | 先頭`.`が必須。`.jpg`、`.jpeg`、`.png`、`.webp`、`.gif`、`.bmp`、`.tif`、`.tiff`。選択形式との不一致は`ConfigurationError` |
| `quality` | 0～100。JPEG/WEBPだけへ渡す |
| `optimize` | JPEG/PNGだけへ渡す |
| `progressive` | JPEGだけへ渡す |
| `lossless` | WEBPだけへ渡す |
| `compress_level` | 0～9。PNGだけへ渡す |
| `exif` | 公開予約フィールド。0.3.0のcore保存処理ではEXIFコピーに使用しない |

`format=None`の場合、runtimeは`output.image_format`を設定して正規化するため、通常のdownloadでは最終形式がroot設定になります。pluginが`format`を明示するとそちらが優先です。

### `DownloadResult`

```text
DownloadResult(
    source_url: str,
    manifest: DownloadManifest,
    chapters: tuple[ChapterResult, ...],
)
```

| property | 戻り値 |
|---|---|
| `saved_files` | `SAVED` outcomeの非null pathを章・manifest宣言順で集めた`tuple[str, ...]` |
| `skipped_files` | `SKIPPED` outcomeの非null pathを同順で集めたtuple |
| `failures` | failureがあるoutcomeから集めた`tuple[ImageFailure, ...]` |

`ChapterResult(chapter, outcomes)`のoutcomesは`Chapter.images`の宣言順です。

`ImageOutcome`は`image`、`kind`、任意の`path`、任意の`failure`を持ちます。kindは次のいずれかです。

| `ImageOutcomeKind` | `path` | `failure` | 意味 |
|---|---|---|---|
| `SAVED` | 保存した絶対path | `None` | 保存成功 |
| `SKIPPED` | 既存対象の絶対path | `None` | `existing_file=skip`による正常skip |
| `FAILED` | `None` | `ImageFailure` | 継続可能な画像失敗 |

`ImageFailure`は`kind: FailureKind`、`exception_type: str`、`message: str`を持ちます。messageはmask処理済みです。body、traceback、例外objectは公開結果へ含めません。

| `FailureKind` | stage |
|---|---|
| `FETCH` | request作成後のHTTP/transport失敗 |
| `PROCESS` | artifact検証、decode、core変換、外部processor |
| `SAVE` | allocate、既存file policy、atomic保存 |

## 7. update DTO

### `UpdateCandidate`

```text
UpdateCandidate(url: str, content_id: str | None = None, revision: str | None = None)
```

`content_id`が安定している場合、URL変更を同一contentの`CHANGED`として検出できます。省略時はURL自体がkeyになるため、URL変更は旧URLの`REMOVED`と新URLの`ADDED`になります。

### `UpdateSnapshot`

```text
UpdateSnapshot(
    source_url: str,
    candidates: tuple[UpdateCandidate, ...],
    checked_at: datetime,
)
```

pluginが観測した現在の完全一覧です。差分だけを入れてはいけません。

### `UpdateResult`

```text
UpdateResult(
    source_url: str,
    plugin_id: str,
    changes: tuple[UpdateChange, ...],
    checked_at: datetime,
)
```

`UpdateChange.kind`は`ADDED`、`CHANGED`、`REMOVED`です。各値は`str` enumなのでJSON化時は`"added"`等として扱えます。

## 8. 例外

```text
DownloaderError
├── ConfigurationError (ValueErrorでもある)
├── PluginError
│   └── UnsupportedSiteFeature
├── AuthenticationError
│   └── SecretNotFound
└── StorageSafetyError
```

| 例外 | 代表条件 |
|---|---|
| `ConfigurationError` | AppConfig不正、catalog path不正、processor欠落、画像format/extension不整合 |
| `PluginError` | plugin contract違反、戻り値型違反、選択競合、unsupported/invalid sidecar |
| `AuthenticationError` | refresh上限、Cookie復号、keyring、認証情報不足 |
| `SecretNotFound` | pluginが宣言名を設定していない、または参照値がenv/keyringにない |
| `UnsupportedSiteFeature` | WebSocket、SSEなどv2 request contract外の機能 |
| `StorageSafetyError` | root外path、symlink/reparse point、保存中のpath差替え |
| `DownloaderError` | transport上限、HTTP error、decode/processor等の一般実行失敗 |

継続可能な画像失敗は`run()`から例外として出ない場合があります。`continue_on_error=true`では必ず`DownloadResult.failures`も確認してください。

## 9. 公開シンボル一覧

0.3.0がルートからexportするシンボルは次のとおりです。

- 構成・実行: `AppConfig`, `RuntimeComposer`, `DownloadService`
- plugin protocol: `SitePlugin`, `UpdateProvider`, `ImageProcessor`, `AuthFlow`, `RequestPort`, `SecretProvider`
- context: `PluginExecutionContext`, `TransformContext`
- download DTO: `PluginDescriptor`, `DownloadManifest`, `Chapter`, `ImageResource`, `ImageSaveOptions`, `ImageArtifact`, `RequestSpec`, `RequestResponse`
- result DTO: `DownloadResult`, `ChapterResult`, `ImageOutcome`, `ImageOutcomeKind`, `ImageFailure`, `FailureKind`
- update DTO: `UpdateCandidate`, `UpdateSnapshot`, `UpdateChange`, `UpdateChangeKind`, `UpdateResult`
- 例外: `DownloaderError`, `ConfigurationError`, `PluginError`, `AuthenticationError`, `UnsupportedSiteFeature`, `SecretNotFound`, `StorageSafetyError`

各plugin protocolとrequest/artifact DTOの詳細は[プラグインAPIリファレンス](plugin-api-reference.md)に続きます。
