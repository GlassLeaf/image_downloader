<a id="library-api-reference"></a>

# ライブラリAPI参照

<a id="application-request-api"></a>

## アプリケーションのリクエストAPI

アプリケーションの操作は、不変のリクエストオブジェクトを一つ受け取ります。既存のプラグインAPI v3のフック、CLIコマンド、設定キー、保存データ形式の契約は維持します。次の呼び出し形式が、従来のURLとキーワード引数による形式を置き換えます。互換用のオーバーロードはありません。

| 操作 | 戻り値 |
| --- | --- |
| `await service.download(DownloadRequest(url=...))` | `DownloadResult` |
| `await service.inspect(InspectRequest(url=...))` | `ManifestInspectionResult` |
| `await service.check_updates(UpdateCheckRequest(url=...))` | `UpdateResult` |
| `await service.plan_workflow(WorkflowPlanRequest(url=...))` | `WorkflowPlanResult` |
| `await service.workflow(WorkflowRequest(url=...))` | `WorkflowResult` |

従来のキーワード引数は、対応するリクエストのフィールドへ移り、名前と既定値は変わりません。リクエストの構築時に検証し、公開サービスのメソッドは異なるリクエスト型を拒否します。通常の画像単位の失敗は`DownloadResult.failures`に残します。ワークフローの例外は、部分結果・履歴・進捗記録の確定後に公開します。`error_info_for()`は元の原因を取り出すため、CLIのコードと安全な診断は維持されます。[移行手順](../maintenance/application-api-migration.md)を参照してください。

<a id="workflow-api"></a>

## ワークフローAPI

`ArgumentError`は`ConfigurationError`の公開サブクラスです。CLIの入力エラーは`argument_error`（終了コード2）、設定ファイルのエラーは従来どおり`configuration_error`を使います。既存の`ConfigurationError`の捕捉処理は、引き続き両方を捕捉できます。共通の`ErrorInfo`、ログ、通知のメッセージは安全な分類文を維持し、コアが生成した構造化診断の表示はCLIが担当します。

`await service.workflow(WorkflowRequest(url=url, download_scope="updated", **download_overrides))`は不変の`WorkflowResult`を返します。リクエストは`DownloadRequest`と同じ上書きフィールドを受け取ります。完全なシグネチャは[APIシグネチャ](api-signatures.md)を参照してください。`all`は現在の完全なスナップショットを選び、`updated`は専用のワークフロー状態を使って追加・変更・未完了の候補を選びます。初回は全件を選択します。サービス設定は共有し、自動的なプラグイン選択は対象URLごとに評価します。

組み込みの`core.generic-html`は`check_updates()`、`workflow()`、`plan_workflow()`に対応します。完全なスナップショットには、要求したページURLをちょうど一つと、処理完了時のUTC日時を含め、`content_id`は付けません。他のページへのリンクはたどりません。候補のrevisionは`generic-html-image-list-v1:`に続けて、重複を含む全抽出画像の`url`・`index`・`image_id`を順序どおりに並べたUTF-8 JSONのSHA-256を付けた値です。JSONは`sort_keys=True`、`ensure_ascii=False`、空白なしの区切り、非有限値なしで生成します。タイトル、本文、ヘッダー、保存設定は含めません。URL（署名付きクエリを含む）、順序、ID、追加・削除に変化があれば`updated`でページを選び、完了済みで変化がなければ選びません。未完了ページは引き続き選択し、`all`は初回の周回で必ずページを選びます。再試行時の画像比較と成功結果の保持条件は維持します。

フィードの確認と選択ページの検査は、それぞれ別にHTMLを取得し、manifestをキャッシュしません。このrevisionは画像内容のハッシュではないため、画像URLが同じまま画像本体だけが変わっても検知できません。画像が0件でも章とページ候補を一つ作るため、通常のダウンロードとワークフローは成功します。`allow_empty_chapter_manifest`が制御するのは章が0件の場合であり、画像が0件の場合ではありません。任意の`DownloadManifest.allow_empty_chapters`と章単位の`Chapter.allow_empty_images`は[空結果の許可](plugin-hooks.md#empty-result-permissions)を参照してください。指定を省略すれば組み込みの従来動作を保ち、検査処理では空結果を理由に拒否しません。更新確認に対応しないプラグインでは`UpdateCheckUnsupportedError`となります。ワークフローと計画処理では専用例外の`cause`にこれを保持し、安全なコードは`update_check_unsupported`（CLI終了コード4）です。

コアの`GenericHtmlPlugin`は、BOM、非ASCIIバイトを含む厳密に妥当なUTF-8、HTTPのcharset、先頭1,024バイト内の最初の有効なmeta宣言、UTF-8のフォールバックの順にHTMLを復号します。非ASCIIバイトを条件にすることで、ASCIIバイトだけからなるISO-2022-JPは自身の宣言を使えます。このUTF-8優先規則は、ブラウザーの文字コード選択とは異なります。未知の宣言は飛ばし、復号エラーは置換します。metaのUTF-16ラベルはUTF-8、`x-user-defined`はWindows-1252として扱い、統計的な推測は行いません。この方針はコアの当該クラス自体にだけ適用し、サブクラスを含む独立したプラグインは自身の復号動作を保ちます。HTTPのバイト列・ヘッダーDTOとプラグイン選択は変わりません。`webencodings`は全利用者に必須のインストール依存ですが、復号用の参照処理を使うのはこのフォールバックだけです。タイトルの修正で出力ディレクトリが、画像URL・IDの修正で更新revisionが変わることがあります。タイトルだけの修正ではrevisionは変わりません。既存ファイルとワークフロー状態は移行しないため、先にmanifestを検査し、必要に応じて明示的に再ダウンロードするか、ワークフローの`all`を使ってください。

`WorkflowResult`は、`source_url`、`download_scope`、`snapshot`、ワークフロー履歴との差分`changes`、順序付きの`items`、任意の`stop_error`、`cancelled`を持ちます。`selected_urls`は項目から導出します。`WorkflowItemResult`は、`url`、理由のタプル`reasons`、`status`、任意の`download: DownloadResult`と`error: ErrorInfo`を持ちます。理由は`all`・`added`・`changed`・`unfinished`、状態は`success`・`partial`・`failed`・`unprocessed`・`removed`です。同じURLを共有する複数の識別子は、周回ごとに一つの試行を共有します。削除されたURLは`removed`となり、過去の試行を保持しますが失敗対象には残しません。ライブラリの結果は正確なURLを保持し、CLIのシリアライズ時に安全なURL表示へ変換します。

再試行は`workflow_retries=1`、`workflow_retry_delay=600.0`、`workflow_retry_timeout=None`で制御します。再試行回数は0以上の整数、待機時間は有限かつ0以上、任意の時間制限は有限かつ正の数である必要があります。不正な値は確認・ダウンロードの開始前に`ValueError`となり、永続設定は追加しません。待機時間・時間制限の整数が浮動小数点数へ変換できる範囲を超える場合も、開始前に`ValueError`となります。APIの再試行回数は任意の0以上の整数を受理します。再試行ではフィードと選択対象のmanifestを取り直し、失敗・未処理・追加・変更された画像を取得します。`ImageResource`の全フィールドが同じ成功・skip結果は再利用し、manifestのrevisionが変わっただけでは無効化しません。この保持は一回の呼び出し内に限定し、次の呼び出しはURL単位で再開します。

`await service.plan_workflow(WorkflowPlanRequest(url=feed_url, download_scope="updated"))`は、実行時と同じワークフロー履歴・選択計算を使い、初回周回のURL選択だけを事前確認します。プラグイン設定・選択・フォールバック・ダウンロード方針の上書きを受け付けますが、画像形式や再試行の引数はありません。フィードを一度確認し、対象manifestの検査や画像取得は行いません。サービスの操作lockと同一フィードのプロセス間lockは、確認・選択・プラグインの後始末が終わるまで保持します。

不変の`WorkflowPlanResult`は、`source_url`、`download_scope`、`plugin_id`、`snapshot`、`changes`、`first_run`、`items`、`stop_error`、`cancelled`、重複を除いた`selected_urls`を公開します。不変の各`WorkflowPlanItem`は、`candidate: UpdateCandidate`、`selected: bool`、理由のタプル`reasons`を持ちます。理由は`all`・`added`・`changed`・`unfinished`、完了済みで除外する場合は`completed`です。同じ対象URLは選択結果と理由を共有します。`first_run`は比較前にはnullで、プラグインとフィードのワークフロースナップショットが存在しない場合だけtrueになります。計画失敗は`WorkflowPlanError`、キャンセルは`WorkflowPlanCancelledError`です。`result`は確定済みの情報を持ち、通常の失敗は元の例外を`cause`と`__cause__`に保持します。ライブラリでは元の候補URLを参照でき、CLI出力では既存の秘匿化を適用します。

計画処理は更新履歴とワークフロー履歴を検証しますが、保存・移行は行いません。独立したCookieゲートウェイを使い、認証・セッションの変更を破棄します。計画処理によるファイルログ、イベント、通知も生成しません。通常のサービス構築や別の操作による永続化、および通常のサービス終了時のセッション変更の保存は無効化しません。CLIはログ出力先を持たずCookie永続化もしない専用の構築処理を使います。必要なlockファイル・ディレクトリは作成する場合があります。プラグインコードと認証処理は実行し、その外部への副作用をサンドボックスで隔離するものではありません。後からワークフローを実行すると、フィードは再度確認します。

`WorkflowItemResult.attempts`は、周回番号、状態、統合済みのダウンロード結果、安全なエラー、`WorkflowImageResult`を持つ不変の`WorkflowAttemptResult`一覧です。画像結果は1始まりの位置、画像、状態、結果、`attempted`、`retained`を保持します。`WorkflowResult.rounds`は、不変の`WorkflowRoundResult`としてスナップショット、差分、選択・削除URL、停止した周回のエラーを保持します。周回0が初回です。最上位の`snapshot`と`changes`は、直近の確定済み準備処理を表します。後のmanifestで画像が削除されても過去の成功結果を保持します。各結果は、有効な再試行設定と`timed_out`も公開します。

```python

from image_downloader import WorkflowRequest
result = await service.workflow(WorkflowRequest(url=feed_url, workflow_retries=2, workflow_retry_delay=30, workflow_retry_timeout=300))
```

時間切れでは、`WorkflowRetryTimeoutError`を`cause`に持つ`WorkflowExecutionError`を送出し、`result`は`timed_out=True`かつ`cancelled=False`になります。期限の計測は初回の周回後に始まります。実行中の処理はキャンセルしますが、後始末と開始済みの書き込みの確定を待ちます。全件成功なら待機せず終了します。利用者のキャンセルは引き続き`asyncio.CancelledError`です。ダウンロード通知は最終結果だけを報告し、ライフサイクルイベントとログには各試行を残します。

重大なエラーは`WorkflowExecutionError`、キャンセルは`asyncio.CancelledError`を継承する`WorkflowCancelledError`として送出します。`result`属性には完了済み結果、停止情報、未処理対象を保持し、呼び出し側はキャンセルを引き続き伝播させてください。対象ごとの通常の認証・要求・プラグイン・既存ファイル競合の失敗は記録し、残りのURLを続けます。操作はdownload・check_updates・inspect・closeと直列に実行します。専用の状態とlockは[実行時の挙動](runtime-behavior.md)を参照してください。

この文書、[設定リファレンス](configuration.md)、[CLIリファレンス](cli.md)、[ログリファレンス](logging.md)、[公開シグネチャ一覧](api-signatures.md)、[API一覧](api-contract-inventory.md) が stable Python API の参照仕様である。inventory にある `__all__` 以外の import は stable ではない。async method は await する。`asyncio.CancelledError` は result に変換せず再送出する。

<a id="api-common"></a>
<a id="api-download-result"></a>

<a id="value-objects-results-and-protocols"></a>

## 値オブジェクト・結果・プロトコル

| API | シグネチャ・意味 |
| --- | --- |
| `RequestSpec` | `(url: str, method="GET", headers=<fresh frozen mapping>, cookies=<fresh frozen mapping>, referer=None, query=<fresh frozen mapping>, form=<fresh frozen mapping>, json=None, auth_required=True, retry_non_idempotent=False)`。`form` と `json` の同時指定は `ValueError`。 |
| `RequestResponse` | `(url, status, headers, body)` 不変の応答。 |
| `ImageFetchRequest` | `(request: RequestSpec, plugin_data=<fresh frozen string mapping>)`。`create_image_request()`・`recover_image_request()`が従来の`RequestSpec`の代わりに返せる、画像取得専用のDTOです。`plugin_data`は要求時点のプラグイン独自の文字列データで、成功した画像の`TransformContext.transport_metadata`だけに渡します。回復フックが`RequestSpec`だけを返した場合は直前のデータを維持し、このDTOを返した場合はデータを置き換えます。 |
| `ImageRequestResolutionFailure` | `(code, reason, exception, phase="create_image_request")`。表示専用の検査で使う安定した失敗情報です。`phase`は`create_image_request`または`effective_request`です。生の例外メッセージは公開契約に含めません。 |
| `ImageRequestResolutionStatus` | enum value は `resolved`、`partially_resolved`、`failed`。 |
| `EffectiveRequestPreview` | `(method, url, headers: tuple[TransportHeader, ...], cookies=(), body=b"")`。`client.send()`の直前に得る、送信しない要求のスナップショットです。ヘッダーは順序・重複を保ち、Cookieは実際の`Cookie`ヘッダーの値だけ、本文はエンコード済みのバイト列です。 |
| `ImageRequestResolution` | `(chapter_position, image_position, status, request=None, plugin_data=<fresh frozen string mapping>, uses_image_fetch_request=None, failure=None, effective_request=None)`。1画像の検査結果です。positionはプラグインの章・画像番号ではなく、manifestの配列内の1始まりの位置です。`resolved`は元の要求と送信直前のプレビューを持ちます。元の要求を得た後で認証・要求の組立てに失敗した`partially_resolved`は、元の要求と失敗情報を持ちます。`failed`は失敗情報だけを持ちます。 |
| `ManifestInspectionResult` | `(source_url, plugin_id, manifest, request_resolution_performed, image_requests=())`。画像本文のダウンロードを行わない、manifestと画像要求の解決結果です。`failures`プロパティは部分解決・失敗した解決結果のタプルです。`request_resolution_performed=False`なら`image_requests`は空です。 |
| `ImageResource` | `(url, index=1, referer=None, headers=<fresh frozen mapping>, save_options=<fresh ImageSaveOptions>, image_id=None, metadata=<fresh frozen mapping>, original_filename=None)`。`url`は既存のフィールド名を維持した空でない文字列のlocatorで、正規URL・ID・仮の識別子を指定できます。`metadata`は`inspect()`時点で確定した非秘密の文字列マッピングで、要求フックと変換コンテキストへ渡します。`original_filename`はプラグインが明示する非秘密の単一ファイル名で、保存テンプレートの`%ORIGINAL_*%`トークンの最優先候補です。コンストラクター自体はURLを検査しない不変の値オブジェクトです。フック呼出し側は、返された画像のlocatorが空白だけでないことと、`original_filename`の型・空値・パス区切りを検証します。実際の絶対HTTP(S) URLは、`create_image_request()`の`RequestSpec.url`に必要です。locator・`image_id`・metadata・`original_filename`に秘密値を置かず、locatorから要求を組み立てられることは、プラグイン作者の責務です。 |
| `ImageArtifact` | `(data, content_type, source_url, image_id=None, extension=None, history=())`。画像変換の入出力です。`source_url`はHTTP URLとは限らない空でない文字列のlocatorであり、サービスはmanifestの`ImageResource.url`を保持します。最終応答URLは`DownloadResult`・`ImageArtifact`の公開DTOでは提供しません。`%ORIGINAL_*%`出力トークンの候補解決時だけ、パス中のファイル名を内部的に参照します。 |
| `TransportCookie` / `TransportHeader` / `TransportRequestMetadata` | `(name, value)`、`(name, value)`、`(url, headers: Mapping[str, tuple[str, ...]], cookies=())`。後者は実送信された一つの request hop の immutable snapshot であり、cookie jar 全体ではない。headers は同名 header の複数値を失わない。 |
| `ImageTransportMetadata` | `(initial_request, final_request, response_url, response_headers, plugin_data=<fresh frozen string mapping>, is_redacted=False)`。画像取得成功時の、変換専用のスナップショットです。`initial_request`はAuthFlow適用後の最初の実送信、`final_request`はリダイレクト後の最終要求です。応答ヘッダーは、複数の`Set-Cookie`も含め、同名の値を保持します。成果物・結果・イベント・CLIのJSON・ログへは自動コピーしません。 |
| `ImageSaveOptions` | `format, extension, quality, optimize, progressive, lossless, compress_level, exif=False`。`None`は設定・既定値を使います。`format="ORIGINAL"`は本体の再エンコードを止めるプラグイン指定であり、拡張子・エンコーダーのフィールドと組み合わせられません。 |
| `Chapter` / `DownloadManifest` | immutable chapter/images。`Chapter(number, title, subtitle="", images=(), chapter_id=None, *, allow_empty_images=None)` と `DownloadManifest(title, chapters, content_id=None, author=None, access=None, revision=None, metadata=<fresh frozen mapping>, *, allow_empty_chapters=None)`。新しい許可指定はboolまたはNoneで、keyword-only・repr対象外・比較対象。既存の位置引数と位置パターンは維持する。plugin は複数 chapter を返す manifest 内で `Chapter.number` を重複させず、1 始まりの連番を使うことが推奨される。各 `Chapter.images` でも `ImageResource.index` を重複させず、1 始まりの連番を使うことが推奨される。core は採番・重複を修正しない。 |
| `ImageFailure` | `kind, exception_type, message, code, reason, response_url, http_status, output_path, transport`。URL・章・indexは持ちません。 |
| `ImageOutcome` | `(image, kind, path=None, failure=None)`。失敗と画像の対応関係はここから確認します。 |
| `DownloadResult` | `(source_url, manifest, chapters)`。`saved_files`・`skipped_files`は絶対パス文字列のタプルです。`failures`は平坦化したタプルで、画像との対応関係は失われます。 |
| 結果の列挙型 | `FailureKind` の member/value は `FETCH="fetch"`、`PROCESS="process"`、`SAVE="save"`、`ImageOutcomeKind` は `SAVED="saved"`、`SKIPPED="skipped"`、`FAILED="failed"`、`UpdateChangeKind` は `ADDED="added"`、`CHANGED="changed"`、`REMOVED="removed"`。member name と serialization の `.value` を混同しない。 |
| 更新のDTO | `UpdateCandidate(url, content_id=None, revision=None)`、`UpdateSnapshot(source_url, candidates, checked_at)`、`UpdateChange(kind, url, content_id=None, revision=None)`、`UpdateResult(source_url, plugin_id, changes, checked_at)`。kind は ADDED/CHANGED/REMOVED。 |
| `RequestPort` | `async execute(spec: RequestSpec) -> RequestResponse`。`RequestError` family を送出し得る。 |
| `SecretProvider` | `get(name: str) -> str`。未設定/取得不能は `SecretNotFoundError`。 |
| `AuthFlow` | `is_auth_failure(request, response) -> bool`、`async apply(request) -> RequestSpec`、`async refresh(failed, response) -> RequestSpec | None`。 |
| `SitePlugin` / `ImageProcessor` / `UpdateProvider` | signature と hook failure は [プラグインフックリファレンス](plugin-hooks.md) が正本。 |

DTO は frozen で、JSON-like mapping は read-only mapping、sequence は tuple に freeze される。`<fresh …>` の default は dataclass default factory であり、mutable `{}` を共有する Python default ではない。変更は mutation でなく `dataclasses.replace()` または新しい DTO を使う。core は internal `freeze_json`/`thaw_json` で input と serialization payload を分離するが、それらの internal import は stable API ではない。serialization boundary は独立した mutable collection を生成し、caller collection と共有しない。

`ImageSaveOptions.exif` は core の再エンコード時に適用する。`True` はJPEG・PNG・WEBP・TIFFへ
入力のEXIF（Orientationを含む）を保持し、それ以外の出力形式では`ConfigurationError`となる。
`False` はOrientationを画素へ反映してからEXIFを削除する。TIFFの画像構造を表す必須タグは残る。
`ORIGINAL`および`ImageProcessor.process()`の同形式・無変換のバイト保持経路では、元のEXIFもそのまま保持する。

`ImageProcessor.inspect()`は構造検査後に全フレームの画素を読み込み、各フレームの画素数上限を
検査する。読込み不能・切り詰めは`ImageDecodeError`、画素数上限超過は`ImageDimensionLimitError`になる。
再エンコードは行わない。`ImageProcessor.process()`も変換／バイト保持の前に同じ全フレーム検査を行う。
`DownloadService`の既定はMIMEと実体の両方を検査する`both`であり、
ORIGINAL保存も対象になる。設定の許可範囲と既存設定の変更手順は
[保存前の画像検査](configuration.md#image-input-validation)を参照する。

<a id="api-public-methods"></a>
<a id="api-configuration"></a>

<a id="runtime-facade"></a>

## 実行環境のファサード

| API | 契約の説明 |
| --- | --- |
| `RuntimeComposer(config, *, config_root, plugin_root, output_root=None, plugin_verification_override=None)` | `output_root` は absolute safe path の任意 image-output root であり、指定すると profile `downloads` だけを置換し、logs/state/cookies は config 由来の root に残る。`compose() -> DownloadService` は log file、filesystem/state/cookie store、HTTP gateway、worker/image processor を作成してそれらを所有するため、compose 自体が filesystem/resource side effect を持つ。`compose_registry() -> PluginRuntime` は registry/module discovery だけを作り、gateway/logger/worker/file handle を作らず、caller が `close()` する。CLI `inspect` は内部の sinkless composition を使うため debug log / console sink を作らない。 |
| `DownloadService(config, registry, dependencies)` | `async download(request: DownloadRequest) -> DownloadResult`、`async check_updates(request: UpdateCheckRequest) -> UpdateResult`、`async inspect(request: InspectRequest) -> ManifestInspectionResult`、`async plan_workflow(request: WorkflowPlanRequest) -> WorkflowPlanResult`、`async workflow(request: WorkflowRequest) -> WorkflowResult`、`async close() -> None`、`async with service`。selection や format の指定は対応する request に渡す。`plugin_id` は matcher 必須、`force_plugin=True` は明示的に matcher を bypass する。`force_image_format` は `ORIGINAL|JPEG|PNG|WEBP|None` で、plugin の保存形式を含め最優先する。operation は同一 service 内で直列、close は idempotent、close 後の全操作は `RuntimeError`。 |
| `RequestGateway(config, cookie_jar=None, *, logger=None)` | `operation(*, plugin_id, operation_url, auth_flow_factory=None, invoker=None, request_concurrency=None) -> OperationRequestGateway`、`async execute(spec) -> RequestResponse`、`async close() -> None`。operation-local cap は physical transport/retry にだけ適用され、hook の実行中には保持されない。transport policy は [実行時動作](runtime-behavior.md#runtime-transport)。 |
| `ArtifactPipeline(config, registry, site_record, overrides, image_processor, logger, invoker, processors, *, force_image_format=None)` | `async process(plugin, artifact, image, manifest, chapter, *, transport_metadata=None) -> ImageArtifact`。site transform、validation、configured processor chain を実行する。`ORIGINAL` は最終 artifact を core 非変換で返し、processor 後にも selected input validation を行う。渡した metadata は site には raw、processor には configuration allow-list に応じ raw または redacted で渡る。 |
| `ChapterReporter(filesystem, chapter_directory, manifest, chapter, logger, reporter_id)` | `async start()`、`async record(position, outcome)`、`async finish(outcomes=None)`。chapter report/log を書く。 |
| `OutputAllocator(filesystem, config)` | `async refresh_directory(directory)`、`chapter_directory(context) -> Path`、`async allocate(chapter_directory, context) -> OutputAllocation`。`context` は `OutputFormatContext`。 |
| `OutputAllocation(relative_path, ...)` | `should_write` プロパティ、`async commit()`、`async abort()`。reservation を持つ `should_write=True` allocation は commit/abort のいずれか一回が必要で、二回目は `RuntimeError`。既存ファイルによるスキップ用の割当て (`should_write=False`) は reservation を持たず、`commit()`/`abort()` は完了操作不要の反復 no-op。 |
| `RuntimeSecrets(plugin_id, references, *, store)` | `get(name) -> str`。プロファイル別保管庫を注入する。[資格情報保管庫](credential-vault.md)。 |
| `UpdateState(filesystem, *, lock_timeout_seconds=30.0)` | `records() -> dict`、`save(records) -> None`、`apply_snapshot(plugin_id, source_url, snapshot) -> tuple[UpdateChange, ...]`、`async apply_snapshot_async(...)`。state contract は [実行時動作](runtime-behavior.md#runtime-update-state)。 |
| `_RuntimeDependencies(outputs, logs, state, events, logger, notifications, cookie_store, cookie_baseline, gateway, image_processor, output_locks)` | stable re-export された advanced composition DTO。すべて compatible かつ open な dependency が必要で、通常は composer を使う。 |

service snapshot 後の plugin filesystem 変更は既存 service に再発見されない。HTTP pool、cookie jar、host/site limiter は service 単位で共有する一方、plugin ID、AuthFlow、allowed origin、refresh state は operation 単位で隔離する。selected site plugin は各 `download()` / `check_updates()` / `inspect()` の runtime 利用終了時に、任意の `cleanup_after_use()` を一回だけ受ける。configured processor も download 終了時に同じ hook を受け、processor が先、site が後である。hook は同期または awaitable を返せ、runtime は完了後に同じ instance の plugin hook を呼ばない。normal completion の cleanup failure は `PluginError`、既存の failure/cancellation がある場合は主原因を維持し cleanup failure は診断に留める。`close()` は operation 完了を待ち、gateway/processor/logger を閉じ、cookie delta を保存する。

<a id="api-call-outcomes"></a>

<a id="caller-outcomes-ownership-and-cancellation"></a>

## 呼び出し結果・所有権・キャンセル

依存の構築と、シグネチャ一覧で同期と明記した補助処理を除き、実行時の操作はすべて非同期です。ライブラリの呼び出し側が扱う境界を次に示します。Pythonの詳細なシグネチャは[APIシグネチャ](api-signatures.md)を参照してください。

| API | 戻り値と所有権 | 副作用・並行性 | 例外と呼び出し側の対応 |
| --- | --- | --- | --- |
| `RuntimeComposer.compose()` | リソースを所有する`DownloadService`を返す。`async with`か`await service.close()`で閉じる | ファイルに保存するログ・状態・Cookie、HTTPゲートウェイ、ワーカー、ログ処理を作成 | 設定・通知・プラグインの初期化は、サービスを返す前に失敗し得る。構築済みリソースを巻き戻し、Cookie保存や通知送信を行わず、元の例外・中断を維持する。入力を修正するか新しく構築し直す。構築途中のサービスは利用できない。 |
| `RuntimeComposer.compose_registry()` | `PluginRuntime`を返す。同期の`close()`は**呼び出し側**で実行する | プラグインの発見・検証・分離モジュールのロードだけを行い、実行時データ保存先・ログ処理・ゲートウェイ・ワーカーは開かない | 不正なルート・設定は`ConfigurationError`、ソース・検証・クラスの失敗は`PluginError`。構築失敗時は自身のruntimeを閉じてから、元の例外・中断を伝播する。 |
| `DownloadService.download()` | `DownloadResult`。通常の画像失敗と画像の対応は`chapter.outcomes`から得る | 同一サービスでは一度に一操作だけを実行し、プラグイン・設定のスナップショットを固定する | `AuthenticationError`、`ConfigurationError`、`PluginError`、`StorageSafetyError`、`InterProcessLockError`、即時停止対象の画像失敗、キャンセルは部分結果を返さず中断する。`ImageDownloaderError`の捕捉はアプリケーション境界で行い、回復には具体的な型・コードを使う。 |
| `VaultError` | `vault_error` | credential vault operation failed |
| `DownloadService.check_updates()` | 完全な`UpdateResult`。部分結果は返さない | `download`と直列に実行し、lock内で更新状態を読み取り・比較・保存する | サイトが`UpdateProvider`を持たなければ`PluginError`のサブクラス`UpdateCheckUnsupportedError`。`UpdateStateError`、認証・設定・要求・保存の失敗、キャンセルも操作を中断する。 |
| `DownloadService.inspect()` | `ManifestInspectionResult`。`create_image_request()`の失敗は`failed`、元の要求の生成後に認証・組み立てが失敗すると`partially_resolved`。後続画像をmanifest順に続ける | download・updateと直列に実行し、Cookie jarを複製した独立の一時ゲートウェイを使う。`inspect()`、必要なら`create_image_request()`、`AuthFlow.apply()`を呼び、`EffectiveRequestPreview`を作る。画像本文の取得、回復、加工、保存、出力割当、レポート、状態更新、イベント、通知は行わない。フック自身による許可された補助HTTP通信は起こり得る。 | 選択・設定・manifest・認証フロー構築・後始末の失敗とキャンセルは、結果を返さず中断する。`request_resolution_performed=False`なら画像要求フックは未実行。元の要求、プラグインデータ、実効URL・ヘッダー・Cookie・本文は秘匿化前の診断情報であり、保存・ログ出力・公開してはならない。組み立てられた各画像要求のエンコード済み本文を含むプレビューを保持し、件数・本文サイズのAPI上限やストリーミング結果はない。大規模なmanifestは操作を分けるか`resolve_image_requests=False`を使う。CLIの`--inspection-data`は出力への投影だけで、ライブラリの結果は秘匿化しない。 |
| `DownloadService.close()` / 非同期コンテキストの終了 | `None`。繰り返し呼び出し可能 | 現在の操作を待ち、Cookie差分を保存し、ゲートウェイ・画像処理・ログ処理を閉じる | 閉じた後の5つのアプリケーション操作は`RuntimeError`。[実行時の挙動](runtime-behavior.md#observability-and-notification)で規定する範囲では、確定済みの主結果から後始末・観測処理の失敗を分離する。 |
| `RequestGateway.execute()` | `RequestResponse` | 設定済みの接続プール・再試行・リダイレクト・サイズ制限の方針を使用 | 通信・状態コード・方針・サイズの失敗は`RequestError`系、認証が必要な場合は`AuthenticationError`。プラグイン認証を伴う処理には、ルートの`execute()`ではなく操作用ゲートウェイを使う。 |
| `OutputAllocator.allocate()` | `OutputAllocation` | 割当器内部の予約で競合パスを直列化し、外側の出力lockでプロセス間を調整 | `existing_file=error`は`ExistingFileConflictError`、改名候補の枯渇は`OutputAllocationError`、実現不能な名前長制限は`ConfigurationError`。書き込み予約は一度だけ確定または中止する。 |
| `UpdateState`のメソッド | 記録、または完全な`UpdateChange`のタプル | 同期の`apply_snapshot`はメモリー・ファイルの処理。非同期版はプロセス間の更新lockを使用 | `UpdateStateError`、`StorageSafetyError`、`InterProcessLockError`なら信頼できる更新結果はない。壊れた状態ファイルを黙って作り直さない。 |

`_RuntimeDependencies`は高度な依存構築向けの安定DTOです。`outputs`、`logs`、`state`、`events`、`logger`、`notifications`、`cookie_store`、`cookie_baseline`、`gateway`、`image_processor`、`output_locks`には、互いに互換性があり利用可能な依存を渡す必要があります。`DownloadService`へ渡すと、サービスが終了処理を所有します。正確なコンストラクターフィールドはシグネチャの契約で固定し、サービスの構築に失敗した場合は、直接呼び出した側が所有権を保ちます。通常は`RuntimeComposer`を使用してください。

`close()`は、操作の終了を待ってからCookie保存、HTTPゲートウェイ、画像ワーカー、プラグイン登録、ログ処理の順に後始末します。同時に呼ぶと同じ終了タスクを待ち、終了後の再呼び出しは何もしません。待機側をキャンセルしても、開始済みの終了処理は中断せず、ワーカースレッドも含めて完了してからキャンセルを伝えます。終了失敗も生じた場合はその失敗を優先し、通常の終了失敗が複数あれば最初のものを返します。重大例外は通常例外へ変換しません。ログの通常終了例外は、最初の通常終了失敗を返す規則の対象外です。固定の診断警告に留め、成功結果・CLI終了コード・履歴を変更しません。キャンセルや重大例外は、この通常例外の抑制対象ではありません。

`async with`の本体、またはdownload・inspect CLIが既に失敗・キャンセルしている場合は、その主原因を保持します。終了処理の失敗は秘密値を含まない固定の警告に留め、workflow専用例外の`result`と原因チェーンを置き換えません。workflow CLIは、既存どおり確定結果に基づいて終了状態と履歴を訂正します。

`UpdateState.validate()`は更新ロックの下で状態を読み取り、検証だけを行います。旧形式の移行や書き込みは行いません。読み取りの失敗は既存の`UpdateStateError`系の診断を維持します。

同期のcomposerは、実行中のasyncioイベントループ内でも利用できます。構築に失敗した場合に限り、未使用リソースの非同期の後始末を、独自のループを持つ一時スレッドで実行し、完了まで待ちます。後始末の失敗は構築失敗を置き換えず、固定の警告だけを出します。作成済みのディレクトリ・ログ・lockファイルは削除しません。境界は[構築時の所有権](runtime-behavior.md#runtime-construction)を参照してください。

公開の実行時APIは、`asyncio.CancelledError`を`DownloadResult`、`UpdateResult`、画像失敗へ変換しません。呼び出し側で必要な`finally`の後始末を行ってから伝播させてください。リソースの後始末には`close()`を使います。

<a id="security-facade"></a>

## セキュリティのファサード

| API | シグネチャ・戻り値 |
| --- | --- |
| `CatalogEntry(...)` | `content_pinned` プロパティ と `as_json() -> dict`。normal/content-pin JSON shape は [プラグインパッケージリファレンス](plugin-package.md#plugin-catalog)。 |
| `PluginCatalog(entries)` | `load(path) -> PluginCatalog`、`find(plugin_id) -> CatalogEntry|None`、`replace(entry) -> PluginCatalog`、`remove(plugin_id) -> PluginCatalog`。新しい不変の値へ置換。 |
| `CatalogEntry` | `(id, kind, manifest_digest, selection_priority, revoked, publisher=None, version=None, public_key=None, key_id=None, file_tree_sha256=None, content_digest=None)`。`PluginKind` は `site_plugin|image_processor_plugin`。 |
| `PluginManifest(value, signature, digest, directory)` | verified manifest value object。value は frozen JSON mapping、signature は `str|None`、digest は manifest digest、directory は verified source root。 |
| discovery/verification DTO | `PluginDiagnostic(name, source, loaded, detail="", warning=False)`、`PluginDiscoveryResult(candidates, diagnostics)`、`PluginRecord(manifest, author_defaults, catalog, builtin=False)`、`PluginVerificationResult(records, catalog, diagnostics)`。`warning=True` は failed load と同義ではない。 |
| `PluginDiscovery(plugin_root, *, mode="strict")` | `discover() -> PluginDiscoveryResult`。 |
| `PluginManifestVerifier(plugin_root, *, mode)` | `verify(discovery) -> PluginVerificationResult`。 |
| `PluginClassLoader()` | `register_class`、`load_class`、`module_namespace`、`unload`、`close`、`construct`、`site_instance`、`processor_instance`、`typed_instance`。isolated module namespace を所有し、invalid source/type は `PluginError`。 |
| `PluginRegistry(records=(), *, catalog=None)` | `records` プロパティ、`get(plugin_id) -> PluginRecord|None`。不変のレコードのスナップショット。 |
| `PluginRuntime(config, plugin_root, *, mode)` | `register_builtin`、`validate_settings`、`enabled`、`effective_config`、`effective_download_policy`、override validation、instance/module access、`prepare`、`doctor_validate`、`validate_all`、`resolve`、`processor`、`close`。invalid config は `ConfigurationError`、source/type は `PluginError`。 |
| `PluginSelector(loader)` | `select(records, url, *, fallback_enabled, overrides, enabled, effective_config, app_settings)`。priority/tie behavior は [plugin hooks](plugin-hooks.md)。 |
| `catalog_entry_for` / `catalog_path` / `canonical_jcs` / `plugin_content_digest` | 副作用のないカタログ・署名の補助関数。 |
| `read_manifest` / `verify_signed_plugin_source` / `verify_manifest` | filesystem/signature/trust validation。invalid schema/root は `ConfigurationError`、manifest/tree/signature は `PluginError`。 |
| `author_config` / `safe_app_settings` | 作者既定値・プラグインに公開する秘匿化済みのアプリ設定マッピング。 |
| `write_catalog` / `trust_plugin` / `revoke_plugin` / `install_plugin` / `uninstall_plugin` | catalog/filesystem mutation。transaction and confirmation policy は [プラグインパッケージリファレンス](plugin-package.md)。 |
| `PluginUninstallResult(id, removed_directory, removed_catalog_entry)` | `as_json()` は `plugin_id`、`removed_installation`、`removed_catalog` を返す。 |

`PluginConfigOverrides` は `Mapping[str, Mapping[str, Any]]`。outer key は plugin ID、inner value は mapping だけで scalar/list は不可。`PluginVerificationMode` は `strict|warn|off|bypass-all|bypass-catalog|bypass-signature`、persisted security value は最初の三つ、ephemeral override は三つの bypass 値だけを受ける。

<a id="api-errors"></a>

<a id="exceptions-and-caller-handling"></a>

## 例外と呼び出し側の扱い

`code`と`reason`は、実際の出力と照合するため英語の固定値を掲載しています。

<!-- error-catalog:start -->
| 例外 | code | reason（固定の出力値） | 公開属性 |
| --- | --- | --- | --- |
| `ImageDownloaderError` | `image_downloader_error` | image downloader operation failed | — |
| `WorkflowExecutionError` | `workflow_execution_error` | workflow execution stopped | `result`, `cause` |
| `WorkflowPlanError` | `workflow_plan_error` | workflow planning stopped | `result`, `cause` |
| `WorkflowRetryTimeoutError` | `workflow_retry_timeout` | workflow retry time limit was reached | — |
| `ConfigurationError` | `configuration_error` | configuration is invalid | — |
| `ArgumentError` | `argument_error` | command-line arguments are invalid | — |
| `PluginError` | `plugin_error` | plugin operation failed | — |
| `UpdateCheckUnsupportedError` | `update_check_unsupported` | selected plugin does not support update checks | — |
| `UnsupportedSiteFeatureError` | `unsupported_site_feature` | site requires an unsupported feature | — |
| `AuthenticationError` | `authentication_error` | authentication failed | — |
| `SecretNotFoundError` | `secret_not_found` | required secret was not found | — |
| `RequestError` | `request_error` | HTTP request failed | — |
| `HttpTransportError` | `http_transport_error` | HTTP transport failed | — |
| `HttpStatusError` | `http_status_error` | HTTP server returned an error response | `status`, `response_url` |
| `RedirectPolicyError` | `redirect_policy_error` | HTTP redirect violates the configured policy | `request_url`, `redirect_url`, `response_url`, `http_status` |
| `ResponseSizeLimitError` | `response_size_limit_error` | HTTP response exceeds the configured byte limit | `response_url`, `http_status`, `limit_bytes` |
| `ImageProcessingError` | `image_processing_error` | image processing failed | — |
| `ImageDecodeError` | `image_decode_error` | image data cannot be decoded | — |
| `UnsupportedImageFormatError` | `unsupported_image_format` | image format is unsupported | `image_format` |
| `ImageContentTypeError` | `image_content_type_error` | image response has a non-image content type | — |
| `ImageMimeMismatchError` | `image_mime_mismatch` | declared image MIME does not match image data | — |
| `ImageDimensionLimitError` | `image_dimension_limit_error` | image dimensions exceed the configured pixel limit | — |
| `ImageWorkerError` | `image_worker_error` | image worker process failed | — |
| `ImageProcessorClosedError` | `image_processor_closed` | image processor is closed | — |
| `StorageError` | `storage_error` | storage operation failed | — |
| `WorkflowPlanLogError` | `workflow_plan_log_error` | workflow execution plan could not be saved | — |
| `OutputAllocationError` | `output_allocation_error` | could not allocate a unique output filename | — |
| `ExistingFileConflictError` | `existing_file_conflict` | output file already exists and existing-file=error prevents overwrite | `relative_path`, `policy` |
| `UpdateStateError` | `update_state_error` | update state is invalid or cannot be read | — |
| `StorageSafetyError` | `storage_safety_error` | storage operation would escape its trusted root | — |
| `InterProcessLockError` | `interprocess_lock_error` | inter-process lock operation failed | — |

<a id="constructors-and-diagnostic-attributes"></a>

### コンストラクターと診断用属性

一覧の多くの例外は、継承した`Exception(message)`コンストラクターと、安定したクラス属性`code`・`reason`を使います。以下の例外には、追加の公開コンストラクター引数や属性があります。属性は診断データであり、常に非nullであることを保証しません。

| 例外 | コンストラクター | 属性 |
| --- | --- | --- |
| `HttpStatusError` | `HttpStatusError(status: int, *, response_url: str | None = None)` | `status`, `response_url` |
| `RedirectPolicyError` | `RedirectPolicyError(message: str | None = None, *, request_url: str | None = None, redirect_url: str | None = None, http_status: int | None = None)` | `request_url`, `redirect_url`, `response_url` （要求URL）, `http_status` |
| `ResponseSizeLimitError` | `ResponseSizeLimitError(message: str | None = None, *, response_url: str | None = None, http_status: int | None = None, limit_bytes: int | None = None)` | `response_url`, `http_status`, `limit_bytes` |
| `UnsupportedImageFormatError` | `UnsupportedImageFormatError(image_format: str)` | `image_format` |
| `ExistingFileConflictError` | `ExistingFileConflictError(relative_path: str | Path)` | `relative_path: Path`, `policy == "error"` |

`continue_on_image_error=true` の通常 fetch/process/save failure は `ImageOutcome.failure` に残る。authentication、configuration、plugin、storage safety、inter-process lock、fail-fast は result を返さず operation を中断する。`check_updates()` は complete `UpdateResult` または exception のいずれかで partial result を返さない。caller は具体例外を扱い、機械利用には stable `code`/`reason` を使い、raw exception message を契約として扱わない。

<a id="api-logging"></a>

ログの契約は[ログリファレンス](logging.md)を正本とします。
<a id="additional-file-results"></a>

## 追加ファイルの結果

`DownloadResult.additional_files` は既定値が空 tuple の追加ファイル結果である。`AdditionalFileOutcome` はファイル ID、段階、operation／invocation／試行 ID、章番号・画像 index、処理 phase、status、相対パス、安全な error を保持する。取得・保存・読み込み・callback の結果を別々の項目として記録する。追加ファイルは `saved_files`、`skipped_files`、画像 `failures` に含めず、元の画像処理結果に影響しない。raw bytes と HTTP response は選択 site の callback 専用 DTO にだけ渡す。任意の `AdditionalFileProvider`、`AdditionalFileReceiver`、`AdditionalFileSaveObserver` と DTO は package root から import できる。契約とサンプルは [plugin hooks](plugin-hooks.md) を参照する。

workflow 履歴は URL 別最終結果と各試行に安全な `additional_files` を記録する。旧履歴でこの項目がない場合は空一覧として読む。

<a id="offline-workflow状態履歴api"></a>

## オフラインのワークフロー状態・履歴API

```python
from image_downloader import AppConfig, WorkflowStateService

state = WorkflowStateService(AppConfig())
feeds = state.list_workflows()
current = state.get_workflow("https://example.com/feed", plugin_id="example.site")
runs = state.list_runs(limit=20)
run = state.get_run(runs[0].run_id) if runs else None
preview = state.prune_history(dry_run=True)
```

同期APIでruntimeを構築せず、ネットワーク・プラグイン・Cookie・通知を使用しない。設定解決は呼出し側で行う。`list_workflows()`と`get_workflow()`は不変の`WorkflowStateView`のtuple、`list_runs()`は`WorkflowRunRecord`のtuple、`get_run()`は記録またはNone、`prune_history()`は`WorkflowPruneResult`を返す。詳細シグネチャは[API signatures](api-signatures.md)を参照。

`WorkflowResult`の末尾に省略可能な`run_id`、`history_saved`、`history_warning`を追加した。引数検証とサービスのopen検査を通過して開始したworkflowを記録し、失敗時は例外に付随する`result`にも保存状況を含める。CLIのservice closeで全体の終了状態が変われば同じRUN_IDを更新する。CLIが表示する本処理の結果は保持し、訂正後の履歴保存情報は[共通JSON](cli.md#cli-json)の`shutdown.result`で公開する。APIはworkflow終了時の結果を記録し、呼出し側による後日のclose操作は別の操作である。

履歴はURL別最終結果・選択理由・保存/skip/失敗件数・安全なエラー・回復前の試行結果と周回の対象/削除URL・差分・件数を持つ。全画像の試行結果、manifest、画像データは保存しない。履歴保存失敗は固定の安全な警告を出し、元の例外、成果物、完了状態、終了コードを変更しない。期限終了とユーザーキャンセルは別のフラグで記録する。

<a id="workflow-execution-plan-and-progress-files"></a>

## ワークフローの実行計画と進捗ファイル

通常のCLI・APIのワークフロー呼び出しは、プロファイルの`logs/workflow`へ必須の周回別計画を書き込みます。`workflow_progress_log: bool | None = None`で`workflow_logging.progress_enabled`を上書きでき、JSONL・テキストの進捗記録だけを制御します。`WorkflowResult.workflow_log`は任意の不変な`WorkflowLogResult`で、`plan_files`、`progress_enabled`、`jsonl_path`、`text_path`、独立したnull許容の`jsonl_saved`・`text_saved`、固定の安全な`warnings`、`event_sequence`を持ちます。APIのパスは絶対パスで、CLIのJSONには既存のパス秘匿化を適用します。保存フラグがfalseなら記録が停止しており、ファイルには途中までしか残っていない可能性があります。無効な出力先はパスと状態がnullです。計画の失敗は`WorkflowPlanLogError`（`StorageError`、終了コード1）で、確定済みの部分`result`を保持します。その周回の完全な計画を保存する前には、対象manifestや画像を取得しません。進捗記録の失敗はワークフローの結果や履歴保存を変更しません。これらのファイルには元の取得元・対象URLを記録しますが、ヘッダー、Cookie、画像データ、任意の例外文字列は含めません。`plan_workflow()`はこれらを作りません。永続性、保持、キャンセル時の制限は[ログ](logging.md#workflow-execution-files)を参照してください。後日のAPIの`close()`は別操作であり、CLIの終了処理で失敗した場合は同じ実行の進捗ファイルへ訂正を追記します。
