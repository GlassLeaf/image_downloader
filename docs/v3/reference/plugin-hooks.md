<a id="plugin-hook-reference"></a>

# プラグインフックリファレンス

<a id="plugin-hooks"></a>

この文書はプラグインフックの入出力、権限、例外の正本です。呼出し順・回数・並行性は[実行ライフサイクル](../explanation/execution-lifecycle.md)を参照してください。

<a id="plugin-selection"></a>

選択処理では、有効なサイトプラグインの`matches_with_config()`（実装されている場合）と`matches()`を評価し、`selection_priority`が最も高い候補を選びます。最高優先順位の候補が複数ある場合は、曖昧な選択として`PluginError`にします。`matches_with_config`が依存できるのはプラグイン固有設定だけであり、通信・秘密情報・ファイルシステムへはアクセスしません。

| フック | シグネチャ | 契約 |
| --- | --- | --- |
| 検証 | `validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None` | 同期処理で副作用なし。不正なプラグイン固有設定は `ValueError`。 |
| 任意の選択処理 | `matches_with_config(self, url: str, config: Mapping[str, object], app_settings: Mapping[str, object]) -> bool` | 同期処理で副作用なし。不正なプラグイン固有設定にも対応する。要求・秘密情報・ファイルシステムを使わない。 |
| 選択 | `matches(self, url: str) -> bool` | 同期処理で真偽値を返す。例外や真偽値以外の返却値は、不一致扱いにはせず `PluginError`。 |
| 検査 | `async inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest` | operation ごとに一回、有限で完全な manifest。pagination はここで完結し、JS/DOM/browser execution はない。画像の論理的な元名が分かる site plugin は `ImageResource.original_filename` に非秘密な単一ファイル名を設定できる。 |
| 画像要求 | `async create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec | ImageFetchRequest` | image fetch 直前に一回。`image.url` の locator（canonical URL、ID、placeholder のいずれでもよい）と `image_id` から header/referer/直前 API 解決を行い、実際に送信する absolute HTTP(S) `RequestSpec.url` を返す。request-time の transform data が必要なら `ImageFetchRequest(request, plugin_data)` を返せる。locator 自体は transport に送られない。 |
| 回復 | `async recover_image_request(self, image: ImageResource, failed: RequestSpec, response: RequestResponse, context: PluginExecutionContext) -> RequestSpec | ImageFetchRequest | None` | AuthFlow refresh 後にも HTTP `status >=400` が残るときだけ最大一回。transport failure には呼ばれない。short-lived URL を再発行するか `None`。bare `RequestSpec` は直前の `plugin_data` を維持し、`ImageFetchRequest` はそれを置換する。 |
| 認証 | `auth_flow(self, context: PluginExecutionContext) -> AuthFlow | None` | `is_auth_failure(request: RequestSpec, response: RequestResponse) -> bool`、async `apply(request: RequestSpec) -> RequestSpec`、async `refresh(failed: RequestSpec, response: RequestResponse) -> RequestSpec | None` を実装する。secret 欠落は `SecretNotFoundError`、login failure は `AuthenticationError`。display-only inspection でも送信直前 request を組み立てるため `apply()` は呼ばれる。 |
| サイト側の画像変換 | `async transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact` | 通信・秘密情報へのアクセス権限なし。 |
| 更新確認 | `async check_updates(self, url: str, context: PluginExecutionContext) -> UpdateSnapshot` | 任意。部分的な差分ではなく、完全なスナップショットを返す。 |
| プロセッサーの画像変換 | `async transform(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact` | 設定済みの処理チェーンの順に実行する。 |
| 任意の出力書式値 | `output_format_values(self, context: PluginFormatContext) -> Mapping[str, str]` | selected site plugin と enabled processor に任意で実装できる同期 hook。operation 構成後に一度だけ呼ばれ、同一 operation 内では時点によらず同じ、大文字スネークケース key の非秘密 string mapping を返す。未実装は空 mapping。 |
| 実行時利用後の任意の後始末 | `cleanup_after_use() -> Awaitable[None] | None` | selected site instance と configured processor instance に任意で実装する。runtime がその instance の利用を終えると一回だけ呼び、awaitable は await する。最終戻り値は `None`。 |

更新確認hookがない場合は`UpdateCheckUnsupportedError(PluginError)`を返す。
codeは`update_check_unsupported`、安全な固定reasonは`selected plugin does not support update checks`。
hook内の通常の失敗や不正な返却値は引き続き`PluginError`として区別する。

## 作品・章情報とログへの明示出力

作品情報は `DownloadManifest`、章情報は `Chapter` で提供します。作品タイトル・作者は
`DownloadManifest.title` / `author`、章タイトル・サブタイトルは `Chapter.title` / `subtitle` です。
両DTOに keyword-only の `published_at: str | None`、`publication_date: str | None`、
`log_fields: Mapping[str, str]` を指定できます。既定値はそれぞれ `None`、`None`、空mappingです。
日時はサイトの表記を保持し、日付解析やタイムゾーン変換を行いません。
新フィールドは既存の `repr()` を維持するため文字列表現には含めませんが、等値比較と
`dataclasses.replace()` では保持されます。ログ・inspection JSON・開発支援ツールには明示的に渡されます。

`log_fields` はログへ記録する非秘密の独自項目です。キーは `[a-z][a-z0-9]*(?:_[a-z0-9]+)*`、
値は文字列に限り、コピーして不変化します。不正な型・キーはconstructorでTypeError/ValueError、
hook内の構築失敗や契約違反は `PluginError` になります。既存の `metadata` は自動出力しません。
ログでは `content.<key>` と `chapter.<key>` を区別します。詳しくは
[ログヘッダー](logging.md#chapter-publication-header)を参照してください。

```python
from image_downloader import Chapter, DownloadManifest


def example_manifest() -> DownloadManifest:
    return DownloadManifest(
        title="作品A",
        author="作者名",
        publication_date="2026年11月",
        log_fields={"publisher": "出版社名"},
        chapters=(
            Chapter(
                number=1,
                title="第1話",
                subtitle="出会い",
                published_at="2026-11-01 09:00 JST",
                log_fields={"edition": "改訂版"},
                images=(),
            ),
        ),
    )
```

`inspect()` が返した情報からコアが各章の `log.log` を生成します。追加の必須hookはありません。
既存の位置引数とAPI version 3は維持しますが、新フィールドを渡すには対応する本体版が必要です。
旧本体ではTypeErrorになるため、constructor signatureで対応を確認するか対応配布版を指定してください。
新しい `log_fields` は `inspect --inspection-data all` と開発支援ツールでも確認できます。

## 空の結果の許可

<a id="empty-result-permissions"></a>

プラグインは、返す`DownloadManifest`にkeyword-onlyの`allow_empty_chapters`、各`Chapter`に
keyword-onlyの`allow_empty_images`を任意に指定できる。両方ともboolまたはNone（既定値）のみ。
constructorでの不正型は値を含まない固定文言のTypeError、hook内の構築失敗や不正返却値は
既存のPluginError経路で扱う。metadataによる指定と必須hookの追加はない。

| 対象 | 許可・拒否 |
| --- | --- |
| 章一覧0件 | 設定`allow_empty_chapter_manifest=False`なら常に拒否。設定Trueの場合はmanifestのNone/Trueを許可、Falseを拒否 |
| ある章の画像0件 | そのChapterのNone/Trueを許可、Falseを拒否。禁止された空章が一つでもあればmanifest全体を拒否 |
| 画像のある章 | `allow_empty_images=False`でも通常処理を継続 |

作品全体の画像合計では判定せず、章一覧0件にはChapterの画像許可指定を適用しない。
例えば公開前の章だけ空を許可し、通常の章には画像を要求できる。

```python
manifest = DownloadManifest(
    "Book",
    (
        Chapter(1, "Published", images=published_images, allow_empty_images=False),
        Chapter(2, "Coming soon", images=(), allow_empty_images=True),
    ),
    allow_empty_chapters=False,
)
```

新フィールドはreprに出さず、比較には含める。既存位置引数・位置パターンを維持するが、
DTOのfields・signature・slotsは拡張される。新フィールドを渡すプラグインはこの拡張に対応する
本体版が必要で、旧本体ではTypeErrorになる。版番号を変更していないため、番号だけで対応を
判別しない。使用前にconstructor signatureの対応を確認するか、対応する配布版を運用で指定する。
変更したプラグインは通常どおり再署名・再インストールする。未変更プラグインの再署名は不要で、
署名manifest形式・API version 3は維持する。

inspectは空でも結果を返し、詳細JSON（all）は非Noneの許可指定だけを表示する。
許可された空結果のdownload/workflow成功は画像の取得を保証せず、verify workflowでは
no_images / not_acquiredになる。未指定の旧プラグイン・組込み汎用HTMLの空画像成功は残る。
既存の完了状態を自動解除しない。

<a id="optional-additional-files"></a>

## 任意の追加ファイル

選択されたsite pluginは任意に `AdditionalFileProvider` を実装できる。
必須 `SitePlugin` は変更しない。最初の2メソッドは対で実装し、callbackは個別に省略できる。

| メソッド | シグネチャ・戻り値 |
| --- | --- |
| 宣言 | `additional_file_hook_points(self, context: PluginExecutionContext) -> tuple[AdditionalFileHookPoint, ...]` |
| 提供処理 | `async additional_files(self, hook: AdditionalFileHookContext, context: PluginExecutionContext) -> tuple[AdditionalFileSpec, ...]` |
| 受信後のコールバック | `async additional_file_received(self, result: AdditionalFileReceiveResult, context: PluginExecutionContext) -> None` |
| 保存後のコールバック | `async additional_file_saved(self, result: AdditionalFileSaveResult, context: PluginExecutionContext) -> None` |

宣言は各download試行で一回、同期・副作用なし。結果を固定し、宣言順によらず以下の順で呼ぶ。
未知・重複段階、非tuple、片方だけの実装はdeclaration failureを記録し、その試行で無効化する。

| 段階 | 呼出し時点・利用可能なコンテキスト |
| --- | --- |
| `BEFORE_MANIFEST` | inspect前に一回。manifest/chapter/imageはNone |
| `AFTER_MANIFEST` | manifest検証・章保存先決定後、章ごと。manifest/chapterあり |
| `BEFORE_IMAGE_REQUEST` | create_image_request前、対象画像ごと。imageもあり |
| `AFTER_IMAGE_REQUEST` | auth/recoveryを含む画像取得の最終成功後、変換前。responseもあり |
| `BEFORE_IMAGE_SAVE` | 変換後、画像保存先割り当て前。artifactもあり |
| `AFTER_IMAGE_SAVE` | 保存またはスキップ後。image_outcomeに確定パス・状態あり |
| `AFTER_DOWNLOAD` | 画像処理通常完了後、章ごと。chapter_resultあり。plugin cleanupより前 |

主処理失敗・キャンセルで未到達の段階は呼ばない。画像失敗を結果として返す通常完了では
AFTER_DOWNLOADを呼ぶが、後続cleanupで全体が失敗し得る。inspection・plan/dry-run・更新確認だけでは
宣言も追加処理も実行しない。

仕様は `AdditionalFileSpec(file_id, relative_path, request=...)` または `data=bytes`。
IDは `[A-Za-z0-9_.-]{1,128}` の非秘密識別子。relative_pathは章別画像フォルダ基準で、例えば
`metadata/source.json` をその章内に保存する。絶対パス・drive/UNC・親参照・dot/空成分・Windows不正名/ADS・
symlink/reparse pointを拒否する。既存画像を含む衝突は `output.existing_file` に従う。

返却順に取得→received callback→保存/スキップ→saved callbackをawaitしてから次へ進む。
通信は既存gatewayの認証origin・concurrency・size制限を使う。bytesと既存ファイル読み込みにも
`network.max_response_bytes` を適用する。通信・callback中は保存先ロックを保持しない。
原子的書き込みはファイル単位で、画像と追加ファイルの一括transactionはない。

received callbackは取得成功・失敗で一回。bytesも成功として扱いresponse=None。
失敗時はdata=Noneで保存せず、saved callbackも呼ばない。成功時は生bytesとAPIのresponseを渡す。
saved callbackはsaved/skipped/failedで一回。savedは書いた内容、skippedは**既存ファイルの内容**を渡す。
読み込み失敗・サイズ超過時は保存状態を維持し、data=Noneとread_errorを渡す。
callbackの例外・不正な戻り値は別の失敗として記録し、保存取り消し・主処理失敗にはしない。
received callback失敗でも保存を続ける。成功戻り値はNone。CancelledErrorは伝播する。

BEFORE_MANIFESTの取得・received callbackはinspect前に実行する。内容は専用一時領域に保持し、
manifest確定後、同じ内容を各章へ保存する。saved callbackには元pointと対象manifest/chapterを渡す。
章0件はsave/no_targetを記録する。一時領域は成功・失敗・キャンセルで後始末する。

生データは選択site専用で、公開結果・event・log・CLI JSON・historyへコピーしない。
callbackのpathは実保存先、公開pathは相対・秘匿済み。保存内容自体を自動redactionはしないため、
秘密情報を保存するかはpluginが明示的に判断する。必須データはpluginが保持し、後続画像hookで不足を
明示的に失敗させる。画像request/transformへの受け渡しには既存 `ImageFetchRequest.plugin_data` を使える。

並行callbackはoperation_id/attempt_number/invocation_idとchapter/imageで区別する。
保持画像には画像hook/callbackを再実行せず、対応結果を保持する。新試行は必要なデータを再取得する。
追加ファイルだけのretryはない。外部副作用の冪等性はplugin側で扱う。
`DownloadResult.additional_files` はreceive/save/read/callbackの安全な結果を別々に記録し、画像の
saved_files/failures・CLI終了コード・retry判定を変更しない。CLI/workflow履歴にも別項目で投影し、
旧履歴の欠落項目は空として読む。
例は [additional-files sample](../../../examples/plugin-v3-additional-files/README.md) を参照する。

<a id="manifest-numbering"></a>

## manifest内の採番

`inspect()` を実装する site plugin は、manifest の採番にも責任を持つ。複数の
`Chapter` を含む `DownloadManifest` では、各 `Chapter.number` を manifest 内で
重複しない値にし、1 始まりの連番を使用することが推奨される。各
`Chapter.images` でも `ImageResource.index` を重複しない値にし、1 始まりの連番を
使用することが推奨される。

core は plugin が返した number/index を再採番、正規化、重複修正しない。出力
template は `%CHAPTER_NUMBER%` と `%IMAGE_INDEX%` にその値を四桁ゼロ埋めで展開する。
番号を重複させてテンプレートが同じ出力名を作る場合、最終的な扱いは
`output.existing_file` の collision policy に従う。採番の詳細は
[設定リファレンス](configuration.md#config-storage-layout) を参照する。

<a id="plugin-contexts"></a>

<a id="capability-contexts"></a>

## 権限を持つコンテキスト

次の表に示すマッピングは、すべて読取り専用で変更できないスナップショットです。プラグインはコンテキストを変更せず、新しいDTOを返してください。

| コンテキストのプロパティ | 型 | 利用可能な値・権限 |
| --- | --- | --- |
| `PluginExecutionContext.config` | `Mapping[str, object]` | 選択したサイトプラグインの実効的な固有設定 |
| `.app_settings` | `Mapping[str, object]` | 秘密情報を除いたアプリ設定。通信ヘッダー・プロキシ、セキュリティポリシー、通知、資格情報、他のプラグイン設定は含みません。 |
| `.manifest` | `Mapping[str, object]` | 選択したプラグインのmanifestのスナップショット |
| `.catalog` | `Mapping[str, object] | None` | 選択したプラグインのカタログのpin。pinなし・組込みの場合は`None`。 |
| `.secrets` | `SecretProvider` | `get(name: str) -> str`。参照の未構成・未登録は`SecretNotFoundError`、鍵欠損・破損などの保管庫障害は`VaultError`。読取り専用。[登録方法](credential-vault.md#登録一覧削除)。 |
| `.requests` | `RequestPort` | `await execute(RequestSpec) -> RequestResponse`。提供する通信権限はこれだけです。 |
| `.get_cookies(*, domain=None, path=None)` | `dict` | 共有Cookie jarの現在値を同期取得する。domain/pathは保存属性との完全一致。返却階層は下記を参照。 |
| `PluginFormatContext.plugin_id`, `.plugin_kind`, `.operation_url` | `str` | 出力書式値を提供しているプラグインの識別情報と操作URL。マッピングは画像・章・成果物ではなく、操作中に変わらないこのコンテキストだけに依存します。 |
| `.config`, `.app_settings`, `.manifest`, `.catalog` | 上記と同じマッピング型 | 出力書式値の提供元プラグインの設定、秘密情報を除いたアプリ設定、manifest・カタログのスナップショット。要求・秘密情報・ファイルシステムの権限はありません。 |
| `TransformContext.image_id` | `str | None` | 元の`ImageResource.image_id` |
| `.index` | `int` | 元の画像のindex |
| `.image_metadata` | `Mapping[str, str]` | 元の`ImageResource.metadata`にある、不変かつ非秘密の検査時メタデータ |
| `.transport_metadata` | `ImageTransportMetadata | None` | 画像取得成功時の、変換専用のスナップショット。サイト側の変換には生の値を渡します。プロセッサーには、`image_processors.transport_metadata_access`で選択サイトのIDを明示的に許可した場合だけ生の値を渡し、それ以外は`is_redacted=True`の同じ型のスナップショットを渡します。 |
| `.config`, `.app_settings`, `.plugin_manifest`, `.catalog` | 上記と同じマッピング型 | 現在のプラグインの、プロセッサー・サイト変換用メタデータ |
| `.site_manifest`, `.site_catalog` | `Mapping[str, object] | None` | 選択したサイトのメタデータ。別途サイトのメタデータがない場合はnullを許可します。 |
| `.manifest` | `DownloadManifest` | 検査済みの操作manifest |
| `.chapter` | `Chapter` | 対象画像を含む章 |

`TransformContext`には、要求の送信機能も秘密情報の提供元もありません。通信や資格情報を必要とする処理は、プロセッサーの契約の範囲外です。サイト側の要求段階で実装してください。`transport_metadata`は通信権限ではなく、画像取得成功時の限定的なスナップショットです。生のURLクエリ、ヘッダー・Cookieの値、`ImageFetchRequest.plugin_data`を、成果物・結果・イベント・ログ・CLIのJSONへ自動的にコピーすることはありません。プロセッサーに生の値を渡す場合は、利用者がプロセッサーとサイトの組合せを永続設定で明示的に許可します。

`output_format_values()`は任意の同期フックです。本体は、選択したサイトと有効なプロセッサーチェーンを構成した後で一度だけ呼びます。返されたマッピングを不変にし、その操作中のすべての出力名に使います。`PluginFormatContext`と、それが公開するすべてのマッピングも不変です。フックはI/Oを行わず、変化する時刻、画像、成果物、`DownloadManifest`、章の状態に依存しないでください。例えば`com.example.grayscale`が`{"FILTER_NAME": "_grayscale"}`を返すと、本体は`%PLUGIN[com.example.grayscale:FILTER_NAME]%`という名前空間付きのトークンとして扱います。値は秘密情報を含まない出力用テキストです。フック・選択プラグイン・キーの欠落はエラーではありません。対応するトークンは文字列のまま残し、その後にパス構成要素の安全化を適用します。例外、マッピング以外の戻り値、文字列以外の値、`[A-Z][A-Z0-9_]{0,63}`に合わないキーは`PluginError`です。将来のプラグイン種別では、その操作の構成処理が出力書式値の提供元として明示的に登録した場合だけ、この仕組みに参加できます。

`AuthFlow` は既定で operation URL の origin にだけ credential を送る。credentialed CDN は `OriginScopedAuthFlow.allowed_origins` に absolute origin を明示して opt-in する。プラグインから保管庫へrefreshed tokenを永続書込みするAPIはない。display-only inspection は `apply()` を呼ぶが image request を送らない。`apply()` が token 発行などの補助 HTTP や外部 state 変更を自ら行えば、そのサーバー側副作用は取り消せない。可能な限り `apply()` は request を決定するだけにし、必要な補助 HTTP は明示的に扱う。

HTTP 401と403は、独自の`AuthFlow.is_auth_failure()`判定より先に、常に認証失敗として扱います。それ以外のステータスは、判定によって認証処理の対象にできます。要求の順序は、`apply`、通信、必要なら認証更新（最大`network.auth_refresh_attempts`回）、再度`apply`、それでもHTTPステータスが`>=400`なら最大1回の`recover_image_request`です。認証更新が`None`を返す場合、認証失敗の応答に対応する認証フローがない場合、更新回数を使い切った場合は`AuthenticationError`を送出します。回復フックには通信の例外を渡しません。

invoker は manifest、chapter/image index、plugin-returned `ImageResource.url` と `ImageArtifact.source_url` の **空でない文字列のlocator**、`ImageResource.original_filename` の非空・path separator なし string、`ImageResource.metadata` と `ImageFetchRequest.plugin_data` の string mapping、`RequestSpec.url` と referer の absolute HTTP(S) **構文**、hook return class、nested DTO、cleanup return を検証する。`ImageResource` constructor 自体は URL を検査しない。locator は plugin が `create_image_request` で実 request を組み立てるのに十分に安定していなければならない。`image_id`、image metadata、manifest metadata、`original_filename` は inspection-time の非秘密値だけにし、token、cookie、署名 URL、鍵などを含めてはならない。`plugin_data` は request-time の transform 専用値であり、必要最小限にして artifact/result/event/log へ自分でコピーしてはならない。plugin は `ImageSaveOptions(format="ORIGINAL")` により core の再エンコードを止められる。この指定は extension/encoder field と併用できず、site transform/processor 後の artifact bytes を保存する。core は locator の意味を解釈せず、`RequestSpec(url=image.url)` が HTTP(S) でなければ fetch 前に失敗する。予期しない hook exception と contract violation は plugin ID と hook 名を持つ `PluginError` に変換し、`asyncio.CancelledError` は再送出する。core fetch/process/save failure を plugin 内で握りつぶしてはならない。

```python
async def create_image_request(self, image, context):
    signed = await context.requests.execute(RequestSpec(f"https://api.example.test/sign/{image.image_id}"))
    return ImageFetchRequest(
        RequestSpec(signed.headers["x-image-url"]),
        {"variant": image.metadata["variant"]},
    )
```

### 変換後の画像と保存形式

`transform_image(artifact, context)` が返す `ImageArtifact` に保存オプションはない。
`artifact.data` / `artifact.content_type` は変換後の入力であり、保存形式は
`--force-image-format` → `ImageResource.save_options.format` → `output.image_format`
（通常の `--image-format` による上書きを含む、既定は `ORIGINAL`）の順で決まる。
PNG bytesと`image/png`を返しても、保存指定がJPEGなら、入力検証後にJPEGへ再エンコードする。
宣言MIMEと実体が違う場合は`media.content_type_mismatch`に従い、非画像MIME等の検証エラーは
変換より先に発生する。MIMEを変更するだけで保存形式を指定することはできない。

`ORIGINAL` はサイト変換・プロセッサー後の最終bytesを維持する。元のHTTP本文へ戻す指定ではなく、
画像検証も省略しない。保存拡張子は最終artifactの `extension`、`content_type`、`source_url`の拡張子、
`.bin` の順で選び、bytesのデコード結果では自動補正しない。形式を変えるプラグインは
`data`・`content_type`・`extension` を一致させる。
形式の食い違いの具体例と、形式不明データの扱いは
[保存形式の正本](configuration.md#image-save-format)、[入力検証](configuration.md#image-input-validation)、
[FAQ](../how-to/faq.md#faq-image-save-format)を参照する。

### Cookieの同期取得

`PluginExecutionContext.get_cookies(*, domain: str | None = None, path: str | None = None)` は、
指定なしでは `{domain: {path: {name: value}}}`、domainのみでは `{path: {name: value}}`、
pathのみでは `{domain: {name: value}}`、両方では `{name: value}` を返す。overloadで各返却型を定義する。
一致条件は保存文字列との完全一致で、`example.com` と `.example.com`、`/` と `/images` は別扱い。
該当なしは空辞書。全階層がコピーされ、変更してもjarは変わらない。
有効期限切れは除き、Secure/HttpOnlyを含める。値がNoneの場合は空文字列になる。
送信先への適用判定・通信・永続化は行わず、jarにない `RequestSpec.cookies` や手動Cookieヘッダーは含めない。

呼出時点の共有jarを参照するため、受信したSet-Cookieの適用後の値を取得できる。
リダイレクト途中、再試行、認証更新、他の並行通信による更新も含み、直前の自分の要求専用の履歴ではない。
`TransformContext` と `PluginFormatContext` にはこの取得機能を追加しない。

手動で構築する `PluginExecutionContext` は従来の位置引数を維持し、任意のkeyword-only引数
`cookie_provider: CookieProvider | None = None` を追加する。
`CookieProvider.get_cookie_snapshot() -> dict[str, dict[str, dict[str, str]]]` は有効なCookieのコピーを返す。
未注入時の `get_cookies()` は `UnsupportedSiteFeatureError`。本体と開発ツールはproviderを注入する。

`RequestResponse.set_cookies() -> dict[str, str]` は最終応答自身のSet-Cookieだけを同期解析する。
途中の応答は含めず、各ヘッダーを個別にSimpleCookieで解析し、カンマで分割しない。
同名は後のヘッダーが優先し、属性は返さない。削除指定も記載値を返すのでjarの現在値とは異なり得る。
不正なヘッダーは無視し、該当なしは空辞書。jarは変更しない。
keyword-onlyの `set_cookie_headers: tuple[str, ...] | None = None` に個別のヘッダーを保持し、reprには出さない。
手動構築時に未指定なら既存headersのSet-Cookieを単一ヘッダーとして扱うが、結合済みの複数ヘッダーの復元は保証しない。
新APIを使うpluginは対応する本体版を必要とする。API version 3と永続化形式は維持する。
取得した秘密値をmanifest/画像metadata、成果物、公開結果、ログへコピーしないこと。

```python
response = await context.requests.execute(RequestSpec("https://example.test/login"))
received = response.set_cookies()
current = context.get_cookies(domain="example.test", path="/")
session = current.get("session")
all_cookies = context.get_cookies()  # domain -> path -> name -> value
```

<a id="plugin-hook-errors"></a>

<a id="hook-exception-and-result-matrix"></a>

## フックの例外と結果の対応表

フック呼出し側は、例外の種類に応じて扱いを分けます。以下の「公開アプリケーションエラー」は、プラグインが意図的に送出する`ImageDownloaderError`の派生クラスを指します。これは原則そのまま伝播します。ただし、`cleanup_after_use`の例外は常に`PluginError`として報告します。通常の`Exception`は、プラグインIDとフック名を持つ`PluginError`で包みます。`asyncio.CancelledError`は変換せず再送出し、呼出し元までキャンセルを伝えます。

| フックのグループ | 成功時に許可する戻り値 | 作者側の検証・エラー | 呼出し結果と操作への影響 |
| --- | --- | --- | --- |
| `validate_config`, `matches_with_config`, `matches` | 順に`None`, `bool`, `bool` | `validate_config`では不正な固有設定に`ValueError`を使います。選択フックは通信・秘密情報・ファイルシステムを使いません。 | 不正な戻り値・通常の例外は`PluginError`になります。検証・選択の失敗は、単なる不一致として無視せず、操作または診断の失敗として扱います。 |
| `inspect`, `check_updates` | 完全な`DownloadManifest`、完全な`UpdateSnapshot` | ページの一部や部分差分は返しません。画像・取得元のlocatorは空でない値、要求・更新URLはHTTP(S)にします。 | 不正な戻り値・通常の例外は`PluginError`になり、その操作の結果は返しません。 |
| `auth_flow`, `is_auth_failure`, `apply`, `refresh` | `AuthFlow|None`, `bool`, `RequestSpec`, `RequestSpec|None` | 秘密情報の欠落は`SecretNotFoundError`、ログイン・認証更新の拒否は`AuthenticationError`。 | 不正な戻り値・通常の例外は`PluginError`になります。明示した認証失敗は画像の結果には収めず、操作を終了します。 |
| `create_image_request`, `recover_image_request` | `RequestSpec|ImageFetchRequest`, `RequestSpec|ImageFetchRequest|None` | 有効な絶対HTTP(S) URLを持つ要求DTOを返します。プラグインデータは不変の文字列マッピングです。 | 不正な戻り値・通常の例外は`PluginError`になります。要求・認証・ステータスの失敗はライフサイクルの規則に従い、通常の継続が適用される場合だけ画像単位の取得結果になり得ます。 |
| `transform_image`, プロセッサーの`transform` | `ImageArtifact` | 有効なバイト列・コンテンツ種別・取得元locatorを維持します。プロセッサーには秘密情報・通信のコンテキストがありません。 | 不正な戻り値・通常の例外は`PluginError`になります。通常の画像加工失敗は`continue_on_image_error=true`の場合に画像結果へ記録できます。`PluginError`は操作全体の失敗です。 |
| `output_format_values` | `Mapping[str, str]` | 任意の同期フック。キーが`^[A-Z][A-Z0-9_]{0,63}$`に合う、安定した非秘密のマッピングを返します。未実装なら空のマッピングです。 | 不正な戻り値・通常の例外は`PluginError`になります。値は検査前に収集し、操作全体で再利用します。 |
| `cleanup_after_use` | `None`、またはawait後に`None` | 選択したサイト・設定済みプロセッサーを実行時に使い終えた後の、任意の後始末です。属性がある場合は呼出し可能でなければなりません。完了後は同じインスタンスのプラグインフックを再度呼びません。一時的な選択用インスタンス、`aclose`、`close`、`aclose_operation`、`close_operation`は対象外です。 | 通常終了時の例外・不正な戻り値は`PluginError`です。既存の操作失敗・キャンセルは置き換えず、診断に残します。プロセッサーの構築が途中で失敗した場合も実行します。 |

結果の境界は[実行ライフサイクル](../explanation/execution-lifecycle.md#result-boundary)で定義します。呼出し元は、画像単位の失敗を`ImageOutcome.failure`から確認します。すべてを一つにまとめたプラグイン例外を捕捉する形ではありません。

<a id="cleanup-after-runtime-use"></a>

## 実行時利用後の後始末

`cleanup_after_use()`は、実行時に一つのインスタンスを使い終えた後の後始末用フックです。オブジェクト自体のclose・破棄を行うフックではありません。サイトプラグインは選択したインスタンスにだけ`download()`・`inspect()`・`check_updates()`などの操作終了時、プロセッサーは設定したインスタンスにダウンロード終了時に呼びます。プロセッサーの後始末をサイトより先に行います。通常終了時の失敗は`PluginError`、すでに操作失敗・キャンセルがある場合は主原因を維持し、診断に残します。

```python
class GalleryPlugin:
    async def cleanup_after_use(self) -> None:
        await self._temporary_session.aclose()


class WatermarkProcessor:
    def cleanup_after_use(self) -> None:
        self._scratch_paths.clear()
```

この hook が終わった後、runtime はその instance の plugin hook を再び呼ばない。ただし、Python object の破棄や外部コードによる呼出しを禁止するものではない。selection 用だけの temporary matcher instance には hook を呼ばないため、constructor と `matches()` は cleanup が必要な外部 resource を確保してはならない。以前の `aclose()`、`close()`、`aclose_operation()`、`close_operation()` は cleanup hook として呼ばれない。既存 plugin は `cleanup_after_use()` へ移行する。

<a id="examples"></a>

## 実装例

<a id="plugin-examples"></a>

| 用途 | ソース |
| --- | --- |
| 相対画像URL・Referer | [汎用CSSセレクター](../../../plugin-sources/generic-css-selector/plugin.py) |
| HTMLのOrigin・Referer | [公開ギャラリー](../../../plugin-sources/public-gallery/public_gallery.py) |
| カーソルによるページ分割とトークン | [カーソル方式のAPIギャラリー](../../../plugin-sources/cursor-api-gallery/cursor_api_gallery.py) |
| CSRFログイン | [CSRFログイン対応ギャラリー](../../../plugin-sources/csrf-login-gallery/csrf_login_gallery.py) |
| OAuthの認証更新 | [OAuthメディアAPI](../../../plugin-sources/oauth-media-api/oauth_media_api.py) |
| ユーザー名・パスワードの秘密情報 | [CSRFログイン対応ギャラリー](../../../plugin-sources/csrf-login-gallery/csrf_login_gallery.py) |
| 署名付きURLの再発行 | [署名付きCDNギャラリー](../../../plugin-sources/signed-cdn-gallery/signed_cdn_gallery.py) |
| 更新一覧の提供 | [章別カタログ](../../../plugin-sources/chaptered-catalog/chaptered_catalog.py) |
| プロセッサーの契約 | [加工履歴プロセッサー](../../../plugin-sources/artifact-history-processor/artifact_history_processor.py) |
| Pillowでの縮小 | [リサイズプロセッサー](../../../plugin-sources/resize-processor/resize_processor.py) |
| 要求・サイト変換での画像メタデータと、許可したプロセッサーへの受渡し | [メタデータ連携の組合せ](../../../examples/plugin-v3-metadata-bridge/README.md) |
| 全7段階の追加ファイル・必須データ連携・cleanup | [追加ファイルパッケージ](../../../examples/plugin-v3-additional-files/README.md) |
| 保管庫認証・追加origin・保存指定・設定付き選択・短命URL・workflow | [ローカル機能ラボ](../../../examples/plugin-v3-feature-lab/README.md) |
| Cookie認証のプラグインと、実ブラウザ確認の分離 | [Cookie認証例](../../../examples/plugin-v3-feature-lab/cookie-gallery/plugin.py)、[隔離環境の手動試験](../../../manual-tests/browser-cookies/README.md) |

HTTP(S) 以外、SSE/WebSocket、JavaScript/DOM/headless browser、CAPTCHA、interactive MFA/WebAuthn、multipart/streaming body は contract 外である。必要な site は `UnsupportedSiteFeatureError` または明示的な plugin/authentication error で失敗させる。
