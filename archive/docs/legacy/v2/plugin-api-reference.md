# image-downloader プラグインAPIリファレンス（v2・履歴資料）

> この文書の v2 entry point / descriptor / sidecar は現行実装では使用できません。v3 は [plugin-api-v3.md](../../../../docs/plugin-api-v3.md) を参照してください。

この文書は、サイトpluginと画像processorが実装するv2契約の完全な参照資料です。アプリケーション組込み用の`RuntimeComposer`、`DownloadService`、結果DTOは[公開APIリファレンス](api-reference.md)を参照してください。

## 1. 基本契約

pluginは`image_downloader`ルートからだけ公開型をimportします。

```python
from image_downloader import (
    AuthFlow,
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageProcessor,
    ImageResource,
    ImageSaveOptions,
    PluginDescriptor,
    PluginExecutionContext,
    RequestResponse,
    RequestSpec,
    SitePlugin,
    TransformContext,
    UpdateCandidate,
    UpdateProvider,
    UpdateSnapshot,
)
```

公開DTOはfrozen/slots dataclass、Protocolは`runtime_checkable`です。Protocolを明示継承する必要はありませんが、型検査を有効にするため継承を推奨します。

entry pointは引数なしで生成できるclassを指さなければなりません。instance、function、factory、コンストラクタ引数が必要なclassは契約外です。

```toml
[project.entry-points."image_downloader.plugins"]
example-site = "example_site.plugin:ExampleSitePlugin"

[project.entry-points."image_downloader.image_processors"]
example-watermark = "example_site.processor:WatermarkProcessor"
```

site plugin instanceは`DownloadService.run()`または`check_updates()`のoperationごとにURL照合時に新規生成され、選択されたinstanceをそのoperationで使用します。画像processor classはcompose時のname検査でも一度生成され、その後processor chainの各画像job・各processorごとに新規生成されます。いずれのconstructorも軽量かつ副作用なしにし、instance fieldをoperation間または画像間の永続stateとして使用しないでください。

## 2. `SitePlugin`

```python
class SitePlugin(Protocol):
    descriptor: PluginDescriptor

    def matches(self, url: str) -> bool: ...

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest: ...

    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec: ...

    async def recover_image_request(
        self,
        image: ImageResource,
        failed: RequestSpec,
        response: RequestResponse,
        context: PluginExecutionContext,
    ) -> RequestSpec | None: ...

    def auth_flow(self, context: PluginExecutionContext) -> AuthFlow | None: ...

    async def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact: ...
```

### `descriptor: PluginDescriptor`

```text
PluginDescriptor(id: str, priority: int = 0)
```

`id`はplugin設定、秘密値、更新state、署名manifestの識別子です。外部配布pluginではsidecar manifestの`id`と完全一致させます。安定したpublisher namespaceを含むID（例: `com.example.gallery`）を推奨します。

`priority`はURL競合の第1選択keyで、大きい値が優先されます。coreは値域や一意性を検証しません。同じURLに一致した外部pluginのうち最大priorityが複数ある場合はcatalogの`selection_priority`を比較し、それも同じなら`PluginError`です。

内蔵`core.generic-html`はpriorityが`-2147483648`ですが、数値比較の前に外部一致pluginが1件でもあれば候補から除外されます。外部pluginが一致しない場合だけfallbackになります。

### `matches(url) -> bool`

entry point解決時に同期呼出しされます。

- URLのscheme、host、pathなどだけで判定してください。
- ネットワーク、秘密値、filesystem、時間依存stateへアクセスしないでください。
- 対応URLだけに`True`を返し、広すぎるwildcard判定を避けてください。
- 例外は`PluginError("plugin URL matching failed")`に変換され、選択処理を停止します。
- `descriptor`不正、`matches`がcallableでない場合も`PluginError`です。

### `await inspect(url, context) -> DownloadManifest`

作品・章・画像の完全なdownload manifestを返します。HTMLやAPIの取得には必ず`context.requests.execute()`を使用します。

戻り値が`DownloadManifest`でなければoperation全体が`PluginError`になります。coreは戻り値の次を意味検証しません。

- content/chapter/image IDの書式と一意性
- 章番号・画像indexの正負、連番、重複
- URLのサイト固有妥当性
- title、subtitleの空文字

これらはpluginの責任で固定fixtureを用いて検証してください。`metadata`だけは`Mapping[str, str]`が必須です。

coreは戻されたmetadataへ`source_url`を追加し、plugin値があれば呼出しURLで上書きします。

### `await create_image_request(image, context) -> RequestSpec`

画像jobごとに、最初の画像requestを構築します。

- `image.url`をそのまま使う義務はありません。APIから得た画像IDを別endpointへ変換できます。
- `ImageResource.headers`と`referer`は自動転送されません。必要ならこのメソッドで`RequestSpec`へコピーします。
- site header、query署名、request単位Cookie、POST form/JSONもここで指定します。
- 戻り値の型が違う場合は継続可能なfetch失敗ではなく、operation全体の`PluginError`です。

### `await recover_image_request(...) -> RequestSpec | None`

認証失敗として処理されなかったHTTP 4xx/5xx画像responseに対して、1画像につき最大1回呼ばれます。短命署名URL、期限切れCDN token、別mirrorへの切替に使います。

| 引数 | 内容 |
|---|---|
| `image` | manifestの元`ImageResource` |
| `failed` | 実際に失敗したrequest。AuthFlowで変更済みの場合がある |
| `response` | status、headers、bodyを持つ失敗response |
| `context` | operationのplugin context |

新しい`RequestSpec`を返すと、AuthFlowの`apply()`後にtransport処理を再実行します。`None`なら元responseを通常のHTTP失敗として扱います。置換requestも失敗した場合、このメソッドは再度呼ばれません。

契約上は「非認証4xx/5xx」向けですが、runtimeの判定は`response.status >= 400`です。したがって429や5xxはまずtransport retryを使い切った後、回復hookの対象になります。

### `auth_flow(context) -> AuthFlow | None`

operation開始時に同期で1回呼ばれます。認証不要なら`None`を返します。返したflowはそのoperation内のinspection、追加API request、画像requestで共有されます。

認証に必要な初期設定や秘密値の参照は行えますが、ネットワーク処理そのものは非同期の`apply()`または`refresh()`から`context.requests`を再帰利用するとgateway再入の原因になるため避けてください。通常はflowがRequestSpecへheader/cookieを適用し、refresh用のHTTP処理は設計上の循環を起こさない方式に限定します。

### `await transform_image(artifact, context) -> ImageArtifact`

取得した画像bytesに対するサイト固有の変換です。難読化解除、tile並べ替えなど、一般processorより前に必要な処理を実装します。変換不要でも必ず実装し、同じartifactを返します。

```python
async def transform_image(self, artifact, context):
    return artifact
```

戻り値が`ImageArtifact`でない場合は`PluginError`です。このメソッドが予期しない例外を送出した場合も`PluginError("site image transform failed")`に変換されるため、`continue_on_error=true`でもoperation全体を停止します。変換結果の画像data不正は、その後のcore検証で継続可能な`PROCESS`失敗になります。

## 3. `PluginExecutionContext`

```python
class PluginExecutionContext:
    @property
    def config(self) -> Mapping[str, object]: ...

    @property
    def secrets(self) -> SecretProvider: ...

    @property
    def requests(self) -> RequestPort: ...
```

公開属性はこの3つだけです。containerは再帰的に読み取り専用化されます。

| property | 能力 |
|---|---|
| `config` | `AppConfig.plugins[descriptor.id].config`。秘密値を含めない |
| `secrets` | plugin設定で宣言した論理名を`get(name)`で解決する |
| `requests` | `await execute(RequestSpec)`だけを公開するHTTP port |

HTTP client、Cookie jar、scheduler、filesystem、output path、logger、通知、EventBusは公開されません。pluginがPython標準機能等を直接使用してこれらへアクセスする実装はv2契約外であり、coreのretry、masking、保存安全性、テストabilityを失います。

### `SecretProvider.get(name: str) -> str`

`name`は設定mappingのkeyです。たとえば次の設定では`context.secrets.get("api_token")`を呼びます。

```yaml
plugins:
  com.example.gallery:
    secrets:
      api_token: production-token
```

runtimeは次の順に解決します。

1. 環境変数`IMAGE_DOWNLOADER_PLUGIN_<PLUGIN_ID>_<REFERENCE>`。英数字以外は`_`へ置換し大文字化する。
2. OS keyring。serviceは`image-downloader.plugin.<plugin_id>`、usernameはreference。

上の例なら環境変数は`IMAGE_DOWNLOADER_PLUGIN_COM_EXAMPLE_GALLERY_PRODUCTION_TOKEN`です。設定に論理名がない場合は`SecretNotFound("required plugin secret is not configured")`、値を取得できない場合は`SecretNotFound("required plugin secret is unavailable")`です。例外messageへsecret値を含めないでください。

### `RequestPort.execute(spec) -> RequestResponse`

```python
async def load_items(context):
    return await context.requests.execute(
        RequestSpec(
            "https://api.example.test/items",
            headers={"Accept": "application/json"},
            query={"cursor": "next"},
        )
    )
```

このportを通るrequestにはglobal/host/site同時実行制限、request interval、timeout、transport retry、Cookie jar、AuthFlowが適用されます。4xx/5xxは成功responseとしてpluginへ返らず、最終的に`HttpStatusError`になります。画像requestだけに存在する`recover_image_request`は、直接`execute()`したrequestには適用されません。

## 4. `RequestSpec`と`RequestResponse`

### `RequestSpec`

```text
RequestSpec(
    url: str,
    method: str = "GET",
    headers: Mapping[str, str] = {},
    cookies: Mapping[str, str] = {},
    referer: str | None = None,
    query: Mapping[str, str] = {},
    form: Mapping[str, str] = {},
    json: object | None = None,
    auth_required: bool = True,
)
```

| field | HTTPへの対応 |
|---|---|
| `url` | request URL。`ws:`/`wss:`は`UnsupportedSiteFeature` |
| `method` | HTTP method文字列。coreは大文字化しない |
| `headers` | request固有header。client既定headerと統合される |
| `cookies` | request固有Cookie mapping。`site-downloader`能力の範囲で利用可能 |
| `referer` | headersに大文字小文字を無視した`Referer`がなければ追加される |
| `query` | query parameter mapping |
| `form` | URL-encoded form data |
| `json` | JSON-compatible値。nested mapping/listはfreezeされ、送信時に通常containerへ戻す |
| `auth_required` | falseならAuthFlowのapply、認証失敗判定、refreshを行わない |

`form`が非空かつ`json is not None`ならコンストラクタで`ValueError`です。`files`フィールドはありません。multipart upload、WebSocket、SSEは非対応です。`Accept: text/event-stream`またはresponseのContent-Typeが`text/event-stream`なら`UnsupportedSiteFeature`です。

mappingはshallowまたはJSON値について再帰的に読み取り専用化されます。request生成後に元dictを書き換えてもRequestSpecへは反映されません。

### `RequestResponse`

```text
RequestResponse(
    url: str,
    status: int,
    headers: Mapping[str, str],
    body: bytes,
)
```

`url`はredirect後の最終URLです。`headers`は読み取り専用mapping、`body`はbuffer済みbytesです。streaming response APIはありません。

## 5. transport・認証・画像回復の順序

requestは次の順で処理されます。

```text
AuthFlow.apply
  └─ transport retry（HTTP送信）
       ├─ 認証失敗ならrefreshを集約し、apply後に再送
       └─ 非認証HTTPエラーなら画像request回復を最大1回
            └─ 置換requestへapplyし、transport retry
```

transport retryの対象は`httpx.TransportError`とHTTP `429, 500, 502, 503, 504`です。`network.max_retries`は1回目を含む総試行回数です。待機時間は`min(max_retry_wait_seconds, 0.25 * 2**attempt_index)`秒です。それ以外のstatusはtransport段階では即時に次の判定へ進みます。

### `AuthFlow`

```python
class AuthFlow(Protocol):
    def is_auth_failure(self, request: RequestSpec, response: RequestResponse) -> bool: ...

    async def apply(self, request: RequestSpec) -> RequestSpec: ...

    async def refresh(self, failed: RequestSpec, response: RequestResponse) -> RequestSpec | None: ...
```

`is_auth_failure()`はHTTP 200のlogin HTMLや認証エラーJSONを検出するための同期判定です。HTTP 401/403はこの戻り値に関係なく認証失敗です。

`apply()`は初回送信、refresh後、別jobがrefresh済みだった後、回復requestの送信前に呼ばれます。同じRequestSpecへ複数回適用されても破綻しないよう、新しい認証情報で置換する実装にしてください。戻り値型違反は`PluginError`です。

`refresh()`は認証失敗requestとresponseを受け、新しい送信元となるRequestSpecを返します。認証回復を行わない場合は`None`です。`None`は`AuthenticationError`になります。

同じoperationで複数requestが同時に認証失敗した場合、gatewayはlockと世代番号でrefreshを1件へ集約します。待機側はrefreshを再実行せず、元requestへ新しい状態の`apply()`を実行します。

`network.max_auth_retries`は各requestで許すrefresh/re-login回数です。上限後は`AuthenticationError`です。`max_retries`とは別枠です。

## 6. manifestと画像DTO

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

`chapters=()`の受理はrootの`allow_empty_manifest`に従います。`metadata`は文字列key/valueのみです。`content_id`や`revision`はdownload結果として保持されますが、更新判定は`UpdateCandidate`の値を使用します。

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

manifest tupleの順で結果へ入ります。処理は`network.max_chapter_concurrency`まで並行します。画像0件の章も有効で、章directoryと`log.log`を生成します。

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

画像は`network.max_concurrency`とhost/site limit内で並行します。outcomeはtuple宣言順、章ログのdownload/save行は`index`昇順で、同じindexなら宣言順です。

### `ImageArtifact`

```text
ImageArtifact(
    data: bytes,
    content_type: str,
    source_url: str,
    image_id: str | None = None,
    extension: str | None = None,
    history: tuple[str, ...] = (),
)
```

画像responseからcoreが最初のartifactを作ります。`content_type`はresponse header、`source_url`は`ImageResource.url`、`image_id`はresource値です。plugin/processorは`dataclasses.replace()`で変更すると、元のimmutable DTOを安全に保持できます。

`history`は処理履歴です。coreは正規化時に`core.decode-normalize`と`core.final-validate`を追加します。plugin/processorが独自の履歴を加える場合は既存tupleを保持してください。

## 7. `TransformContext`

```python
class TransformContext:
    @property
    def image_id(self) -> str | None: ...
    @property
    def index(self) -> int: ...
    @property
    def config(self) -> Mapping[str, object]: ...
    @property
    def manifest(self) -> DownloadManifest: ...
    @property
    def chapter(self) -> Chapter: ...
```

サイト変換と外部processorへ渡される読み取り専用contextです。ネットワーク・秘密値能力はありません。`config`は選択中site pluginの設定です。外部processor固有の別設定namespaceではない点に注意してください。

## 8. artifact pipelineと保存形式

実行順は固定です。

1. `SitePlugin.transform_image()`
2. input artifact検証
3. core decode/normalize
4. `image_processors.chain`の設定順に外部processor
5. core最終decode/normalizeと検証

`ImageSaveOptions.format`があればrootの`output.image_format`より優先し、なければroot形式を使用します。この同じoptionsが最初と最後のcore正規化に適用されるため、外部processorが別形式のbytesを返しても最終保存形式はoptionsへ戻ります。

`media.input_validation`の動作は次のとおりです。

| mode | Content-Type拒否 | 事前decode検証 |
|---|---|---|
| `content_type` | `text/*`, `application/json`, `application/xml` | 原則なし。ただしmismatch=`error`かつ宣言が`image/*`ならdecode |
| `decode` | なし | Pillowでdecode/verify |
| `both` | 上記の明白な非画像を拒否 | Pillowでdecode/verify |

`media.content_type_mismatch=error`で、明示的な`image/*`とPillow検出MIMEが一致しない場合はprocess失敗です。`accept`では宣言不一致だけでは拒否しません。どのmodeでもcore正規化時には最終的にPillowでdecodeされるため、壊れた画像bytesは保存されません。

`ImageSaveOptions`のformat/extension/quality等の詳細と0.3.0の`exif`予約扱いは[公開APIリファレンス](api-reference.md)を参照してください。

## 9. `ImageProcessor`

```python
class ImageProcessor(Protocol):
    name: str

    async def transform(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact: ...
```

`name`は`AppConfig.image_processors.chain`に書くIDです。空文字、重複name、非callable transformはregistry diagnosticの失敗になります。同一nameを複数distributionが登録した場合、後から検出された側は失敗diagnosticになります。

processorのentry pointにも`image-processor` capabilityを含むv2 sidecarが必要です。processor例外は`ImageProcessingError("image processor failed: <name>")`へ変換され、`continue_on_error=true`なら画像単位の`PROCESS` outcomeになります。戻り値型違反も同様です。

processorはfilesystem、HTTP、secretを受け取りません。CPU負荷の高い同期処理をevent loop上で長時間行わないでください。

## 10. `UpdateProvider`

```python
class UpdateProvider(Protocol):
    async def check_updates(self, url: str, context: PluginExecutionContext) -> UpdateSnapshot: ...
```

`SitePlugin`と同じclassへこのメソッドを追加します。別entry pointは不要です。

```text
UpdateCandidate(
    url: str,
    content_id: str | None = None,
    revision: str | None = None,
)

UpdateSnapshot(
    source_url: str,
    candidates: tuple[UpdateCandidate, ...],
    checked_at: datetime,
)
```

毎回、現在存在するcandidateの完全一覧を返してください。前回との差分や「新着だけ」を返すと、欠けた項目が`REMOVED`としてstateから削除されます。

安定した`content_id`を必ず付けることを推奨します。`content_id=None`ではURLがkeyになるため、署名queryなどが変化するURLは不要なadded/removedを発生させます。`revision`は内容の変更を表す安定した値にしてください。

## 11. エラー境界

pluginは次の高水準例外を意図に応じて送出できます。

| 例外 | 用途 | 継続 |
|---|---|---|
| `ConfigurationError` | 必須plugin設定がない・不正 | operation停止 |
| `AuthenticationError` / `SecretNotFound` | 認証不能・秘密値不足 | operation停止 |
| `UnsupportedSiteFeature` | contract外機構が必須 | operation停止 |
| `PluginError` | site応答がplugin契約上解釈不能、実装契約違反 | operation停止 |
| `ImageDownloaderError` | 一般の取得・処理失敗 | stageにより画像failureへ変換可能 |

token、Cookie、Authorization header、署名付きURLのquery、response bodyを例外messageへ含めないでください。coreは一般的なcredential-like文字列とURL parameterをmaskしますが、pluginが秘密値を別表現へ加工した文字列まで完全に推定することはできません。

## 12. 非対応範囲

- multipart upload（`RequestSpec.files`は存在しない）
- WebSocket
- Server-Sent Events
- streaming response
- browser/JavaScript実行
- CAPTCHA、アクセス制御回避
- 対話型MFAや手動承認をgateway内で待つflow
- pluginからの任意filesystem/logging/notification操作

配布sidecar、署名、catalog schemaは[plugin package・署名ガイド](plugin-package-template.md)、実装手順は[プラグイン開発ガイド](plugin-development-guide.md)を参照してください。
