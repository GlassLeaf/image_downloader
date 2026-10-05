# Runtime behavior reference

この文書は public API の型ではなく、transport、persistence、output safety の実行契約を定義する。callable signature は [library API reference](library-api.md) を参照する。

## HTTP transport

<a id="runtime-transport"></a>
<a id="http-transport"></a>

plugin は raw HTTP client を作らず `await context.requests.execute(RequestSpec(...))` を使う。core が timeout、pool、cookie、redirect、authentication、origin/domain concurrency、rate limit を管理する。`RequestSpec`、`RequestResponse`、`ImageResource` は immutable DTO であり、header/query を変える場合は新しい value を返す。

`ImageResource.url` は opaque locator であり transport に直接渡されない。実際の network destination は `create_image_request` または recovery hook が返す absolute HTTP(S) `RequestSpec.url` だけである。

成功した画像 fetch の AuthFlow 後 initial request、redirect 後 final request、response URL/header、request-time `plugin_data` は `TransformContext.transport_metadata` だけに記録される。site transform は raw snapshot を受ける。processor は `image_processors.transport_metadata_access` で selected site を明示許可した場合だけ raw、それ以外は値をマスクした `is_redacted=True` snapshot を受ける。cookie jar 全体を公開せず、各 request snapshot には実際に送信された Cookie header だけを含める。raw metadata は `ImageArtifact`、result、event、CLI JSON、chapter report、log へ自動コピーされない。ただし `filename_format` の `%ORIGINAL_*%` token を使うときだけ、core は `Content-Disposition` または final response URL から抽出した一つの path-less filename を出力名候補に使う。query/fragment、header 全体、URL 全体は保存・出力しない。

GET、HEAD、OPTIONS、TRACE は retry 対象である。429、500、502、503、504 は `Retry-After` を尊重し、`network.retry_max_delay_seconds` 以下の jitter を加える。POST 等の non-idempotent request は `retry_non_idempotent=True` を plugin が安全性を保証するときだけ retry する。

redirect は hop ごとに policy を検査する。anonymous cross-origin redirect には Cookie、Referer、plugin-specific header、application header を引き継がない。response body は streaming で読み、宣言済みサイズと受信済みサイズのいずれも `network.max_response_bytes` を超えると `ResponseSizeLimitError` で止める。

## Cookies

<a id="runtime-cookies"></a>

<!-- claim: TAX-RUNTIME-COOKIES -->
profile cookie は `<storage.data_root>/profiles/<profile>/cookie/cookies.enc` に AES-256-GCM で保存する。暗号鍵は keyring service `image-downloader.cookie-key` に保持し、cookie 本体と鍵を app configuration に書かない。

cookie export/import は passphrase container を使い、passphrase を対話入力する。browser import は `browser-cookie3` を含む `.[browser-cookies]` extra が必要で、既存 jar を差分 merge する。破損した encrypted file は上書きせず error にし、明示 import を復旧手段にする。

同一端末上の本 application process は `cookie/cookies.lock` で read/merge/write を直列化する。異なる cookie は保持し、同一 `(domain, path, name)` は後に確定した更新、同値なら長い expiry、session cookie は persistent cookie より短期として扱う。lock file は解放後も残る。NAS と外部 application の同時変更は保証対象外である。`CookieStore.save()` は jar 全体を置換する library API であり、runtime の delta merge とは異なる。

## Output allocation and locks

output path は trusted root の配下だけに作る。managed directory/file/lock に symlink または Windows reparse point があれば `StorageSafetyError` を送出する。`OutputAllocator.allocate()` が reservation を返した `should_write=True` allocation は `commit()` または `abort()` の一度だけで完了する。`existing_file=skip` による `should_write=False` allocation は reservation を持たず、`commit()`/`abort()` は何度呼んでも no-op である。`existing_file=error` は controlled `ExistingFileConflictError`、output lock timeout は operation failure であり image retry ではない。

出力名のWindows禁止文字は、`"→”`、`*→＊`、`/→／`、`:→：`、`<→＜`、`>→＞`、`?→？`、`\→＼`、`|→｜`へ置換する。タイトル・subtitle・元ファイル名のstem等、名前部分の`.`は`．`へ置換し、画像ファイルの最後の拡張子区切りは`.`を維持する（`archive.tar.webp`の出力名は`archive．tar.webp`）。制御文字の`_`置換とWindows予約名の回避は維持する。汎用`safe_name()`は通常のファイル名を検査する用途も持つため、禁止記号を上記の文字へ置換するが、通常のドットは維持する。名前部分のドット変換は出力formatterが行う。元のmanifestタイトル、画像URL、revisionは変更しない。これまで禁止記号を`_`へ置換していた名前と、名前部分にドットを含む出力名は変わり得る。既存ファイル・状態の自動移行は行わない。

コア生成の章名・画像名・重複時の候補名・出力用host/plugin IDは、安全化と`output.max_component_length`による短縮の後、保存先ファイルシステムの要素長上限だけ追加で検査する。上限内なら安全化後の名前を維持する。WindowsはUTF-16単位数、POSIXは`os.fsencode()`のバイト数で測り、上限を取得できない場合は255とする。超過時だけ先頭を短縮し、名前全体のSHA-256先頭8桁を付ける。追加短縮では最後の拡張子と重複時の連番を保持する。既存の明示設定による短縮で既に拡張子が落ちる挙動は変更しない。

`max_component_length: null`は利用者指定の短縮を行わない意味であり、OSの要素上限対策は有効である。OS間で上限と計測単位が異なるため、超過する名前だけ保存名が異なる場合がある。追加ファイルのplugin指定相対パス、保存root、profile名、stateファイル名を自動改名せず、既存ファイルと状態を自動移行しない。

原子的保存は同じ親ディレクトリに最終名を含まない短い`.id-`接頭辞の一時ファイルを作り、`fsync`と安全性検査後に置換する。パス全体を260文字などで事前拒否しない。親が深すぎて短い一時名も作れない場合を含め、実際の長さ超過は`StorageError`の定型診断で通知する。長いWindowsパスで親が存在するのに作成が`FileNotFoundError`となる場合は、長さ制約の可能性として案内する。通常の不存在判定・無関係なエラーは維持する。保存rootやWindows設定を自動変更せず、別ディレクトリへの一時保存・非原子的保存へ切り替えない。

通常downloadの成果物には短い絶対パスの`--output-dir`を指定できる。profileのCookie・state・logも深い場合は、既存データを退避して必要なprofileツリーを短い`storage.data_root`へ移し、`config explain`で解決先を確認する。`--output-dir`だけではprofileデータの場所は変わらない。Windowsでは実行PythonとOSの長いパス対応も確認する。実測結果と再実行コマンドは[R15検証記録](../../investigations/implementation-review-2026-10-04/r15-details.md)を参照する。

## Update state

<a id="runtime-update-state"></a>

<!-- claim: TAX-RUNTIME-UPDATE-STATE -->
profile state の `state/updates.json` は schema version 2 である。snapshot は plugin ID と source URL の SHA-256 key ごとに保持し、raw source URL を namespace key として保存しない。URL 表記が変われば別 feed である。

v1（version field のない旧形を含む）は次の update check で `legacy_records` に保存して移行する。旧 record と candidate の URL と content ID が完全一致するときだけ初回比較に使い、legacy record だけを根拠に `REMOVED` を出さない。新 snapshot を保存した次回から通常の removal 判定を行う。未来 schema または破損 state は上書きせず `UpdateStateError` を送出する。

同 profile の本 application process は `state/updates.lock` を共有し、read/compare/atomic write 全体を直列化する。default lock timeout は 30 秒で、timeout は update operation 全体を失敗させる。異なる feed の変更は両方保持する。lock file は残り、NAS と外部 application の変更は保証しない。

## Workflow state

`workflow` also writes profile `state/workflow.json` (schema version 1), independently of `updates.json`.
It retains the latest complete snapshot and per-candidate `completed` flag by plugin ID and hashed feed URL.
There is no time-based expiration. Missing state means first run; invalid/future schemas fail without overwrite.
Existing update-listing history is still updated at check time, but does not initialize workflow completion.

Before downloads, the selected candidates are durably marked unfinished. Each URL with no image failures
and successful plugin cleanup is committed complete, including skips and permitted empty manifests.
Failed, partial and unprocessed candidates remain unfinished. Disappeared candidates are removed from the
workflow snapshot without deleting local artifacts, and are treated as added if they reappear.
Both scopes share completion state; duplicate target URLs run once and update every matching identity.

The service serializes the entire workflow. `state/workflow-locks/<hash>.lock` serializes one plugin/feed
across processes from before its check through completion; `state/workflow.lock` serializes short
read/merge/atomic-write transactions for all feeds. Default timeout is 30 seconds. Lock files remain.
Started transactions finish before cancellation propagates. Completed URLs survive interrupted runs.
Authentication/request/plugin/existing-file-conflict errors abort that URL and continue with other URLs;
configuration, storage/lock/safety errors and unexpected failures abort the workflow.

### Workflow dry-run

CLI `workflow --dry-run` / library `plan_workflow()` use a shared pure initial-round selection calculation.
The preview checks the feed once and reads workflow completion history under the existing shared-file lock;
both update and workflow histories are validated, but neither is written or migrated. Missing workflow feed
history selects all current candidates in either scope. The operation and same-feed locks cover the check,
selection and cleanup; other feeds are not locked for the network check.

No target manifest, image request/processing/output allocation, wait or retry round is run. A cloned CookieJar
and detached gateway keep authentication changes out of the normal service session. CLI planning additionally
suppresses initial configuration persistence/rewrite, file logs, notifications and close-time cookie persistence.
Only required lock files/directories are created. This is not a sandbox for plugin code or remote authentication.
Preview results are observations, not a reservation; actual execution refreshes the feed again.
See [CLI output](cli.md#workflow) and [library API](library-api.md) for the separate plan result contract.

### Workflow retry rounds

The default is one additional round, after a fixed 600-second wait, only while retryable unfinished work exists.
Every round refreshes the complete feed, commits its latest snapshot, excludes deleted candidates, and includes
new/changed candidates plus retryable unfinished URLs. `all` applies to initial selection only during this call.
The feed lock and service operation lock remain held through waits. Short shared-file locks are not held while waiting.

Within a call, an image ledger records outcomes as they settle, including concurrent successes before fail-fast
or cancellation. Chapter identity prefers chapter_id, otherwise number; image identity within chapter prefers
image_id, otherwise locator. Only unique identities with equal ImageResource fields can reuse outcomes.
Ambiguous identity groups are fetched again. A changed plugin resets image reuse. Actual image-body changes
without changed manifest image information cannot be detected; no artifact validation is added.
Changed images use the configured file policy, so rename can add files and error can reject collisions.
Cleanup failure keeps the URL unfinished even when images succeeded; retries reuse those images and repeat cleanup.

#### Workflowで取得対象URLを選ぶ条件

「取得対象URL」は更新元が返す `UpdateCandidate.url`、「画像URL／locator」は各URLのmanifestが返す
`ImageResource.url` を指す。両者の変更判定は別である。

| 条件・情報 | 初回周回 | 追加周回 |
| --- | --- | --- |
| workflow専用履歴がない | `all`／`updated`とも全候補を選ぶ | 当該実行の状態を使う |
| `all` | 今回一覧の全候補を選ぶ | 全件取得を強制せず、以下の条件で選ぶ |
| 新規候補 | 選ぶ | 選ぶ |
| 同じ候補のURLまたは`revision`が変更 | 選ぶ | 選ぶ。成功済みURLも対象になる |
| 一覧に残る未完了候補 | 選ぶ | 再試行可能な失敗または未処理画像がある場合に選ぶ |
| 成功済みで候補情報に変更なし | `updated`では選ばない | 選ばない。manifestも再確認しない |
| 一覧から削除された候補 | 選ばない | 対象・専用状態から除外。再登場すれば新規扱い |

候補の識別には `content_id` を優先し、なければURLを使う。URLと`revision`を比較し、チェック日時や
出力先・加工設定の変更は更新判定に含めない。同じURLを指す複数候補は周回内で一度だけ処理する。
追加周回は再試行可能な未完了対象がある場合だけ開始する。全成功、または再試行対象外の失敗しか
残っていない場合は終了するため、新規・変更候補を監視するための周回は行わない。

#### Workflowで画像を照合・再取得する条件

選ばれたURLのmanifestを再取得してから、同じ実行中に確定した画像結果と照合する。
chapterは`chapter_id`、なければ章番号で識別し、そのchapter内の画像は`image_id`、なければ
`ImageResource.url`で識別する。画像配列の位置自体は識別子ではない。

| 情報 | 比較・照合での扱い |
| --- | --- |
| `ImageResource.url` | 全文字列を比較。クエリだけの変更でも再取得する。`image_id`が同じでも除外しない |
| `image_id` | 識別と比較に使用。変更・追加・削除で照合できなければ再取得する |
| `index` | 比較する。値が変われば再取得する |
| `referer`、`headers` | 比較する。値の変更で再取得する。mappingの並び順だけの変更は無視 |
| `save_options` | 全フィールドを比較。形式・拡張子・品質・optimize・progressive・lossless・compress_level・EXIFの変更で再取得 |
| `metadata` | キーと値を比較。変更で再取得する。mappingの並び順だけの変更は無視 |
| `original_filename` | 比較する。値が変われば再取得する |
| 対象URLのplugin ID | 変われば以前の画像結果を流用しない |
| chapter／画像の識別子の重複 | 一意に照合できない範囲の画像結果を流用せず再取得する |

同じplugin、一意なchapter／画像識別子、上記画像フィールドの一致が揃った成功・skip結果は保持し、
画像の要求生成・取得・加工・保存を再実行しない。再試行可能な失敗画像、未処理画像、新規・変更画像は取得する。
再試行対象外の画像失敗も、照合可能で比較フィールドが同じなら保持する。未処理画像があれば追加周回は
実行できるが、保持した対象外の失敗画像は再取得しない。画像単位の継続は`continue_on_image_error`に従う。

この判定は画像本体の同一性を証明するものではなく、pluginが提示した取得・加工・保存に関わる画像情報が
変わらなければ、確定済み結果を保持するための条件である。部分失敗の回復時に、変更のない成功画像の
通信・加工・保存を繰り返さず、`rename`での重複保存や`error`での既存ファイル衝突を抑える。
`metadata`、`headers`／`referer`、`index`は要求や加工結果へ影響し得る情報として比較し、
`save_options`と`original_filename`の変更も保存条件・名前への追随のために再取得対象とする。

当面はこの動作仕様を維持し、比較条件と結果を保持する理由を明確にする整理を優先する。
保存条件・名前の比較除外や、画像revision／hashの一致によるURL比較の省略は現行機能ではない。
内部では`ImageResource`全体のdataclass等価比較を使うため、比較に参加するフィールドを追加すると、
そのフィールドの変化も自動的に再取得条件になる。フィールド追加・比較処理の整理では、
[保守上の方針](../maintenance/architecture.md#workflow-image-comparison-maintenance)に従い、
挙動を維持する整理と仕様変更を区別する。

| 比較に使わない情報 | 制限・注意点 |
| --- | --- |
| 画像本体・ハッシュ、HTTPの`ETag`／`Last-Modified`／`Content-Length` | 実体が変わっても画像情報が同じなら検出できない。保存済み成果物の存在・破損も検証しない |
| `create_image_request()`が返す実際の要求URL・headers・cookiesなど | 保持した成功画像ではhookを呼ばない。要求情報だけの変化を検出しない |
| manifestの`revision`、タイトル、著者、`content_id`、access、metadata | 単独の変更では成功画像を再取得しない。更新元候補の`revision`とは異なる |
| chapterのタイトル・サブタイトル | 単独の変更では再取得しない |
| 安定した`chapter_id`があるchapterの章番号 | 番号だけの変更では再取得しない |
| chapter／画像の配列順 | 一意に照合でき、画像フィールドが同じなら保持。ただし`index`の変更は比較対象 |

例えば同じ`image_id`でも、manifestの画像URLが
`images/image.jpeg?ver=yymmdd&key-pair-id=aaaa`から
`images/image.jpeg?ver=yymmddhhmmss&key-pair-id=bbbb`へ変われば、内容が同じでも再取得する。
URLの正規化や認証用クエリの除外は行わない。一方、manifestには安定した非秘密locatorを返し、
短命の署名URLを`create_image_request()`で生成する場合、署名URLだけの更新では保持済み画像を再取得しない。
pluginでの組立方法と秘密情報の扱いは[dynamic URL how-to](../how-to/dynamic-urls-and-auth.md)を参照する。

画像結果の保持は同じ実行中・同じ取得対象URL内に限定する。候補の取得対象URLが変われば新しい台帳になり、
画像情報が同じでも以前のURLの成功結果を流用しない。削除されたURLの台帳も破棄し、再登場時は流用しない。
次回起動には画像結果を永続化しないため、未完了URLを取得すると成功済み画像も通常処理の対象になる。
`skip`は通常処理では取得・加工後に保存を省略するが、同一実行中に保持したskip結果は取得自体を省略する。
変更画像の取得は保存成功を保証せず、`overwrite`／`rename`／`skip`／`error`の既存ファイル方針に従う。

通信・認証・plugin・画像加工の失敗は再試行対象。HTTPは401／403／408／429と5xxが対象で、
それ以外の恒久的4xx、秘密情報不足、未対応機能・形式、サイズ・画素数・リダイレクト制限、閉じた画像処理器、
保存失敗・既存ファイル衝突は対象外。設定・ロック・状態異常、予期しない内部例外では停止する。
更新チェック失敗、不正一覧、状態保存失敗でも以降の取得を開始しない。HTTP要求単位の既存再試行・
認証更新・画像URL再発行の後にworkflowの再試行判定を行う。

Only URL completion is durable; schema remains 1. An optional retry deadline starts after the initial round,
includes waiting/checking/downloading, cancels active work, and then waits for cleanup and started transactions.
It raises WorkflowRetryTimeoutError, distinct from user cancellation. CLI returns 5 if any attempt saved/skipped
files, otherwise 1; successful recovery or removal of all failed targets returns 0. Original fatal error codes
and user-cancellation code 130 remain unchanged.

<a id="workflow-timeout-boundaries"></a>

### workflowの期限と無期限待機の制限

`--workflow-retry-timeout`未指定（APIでは`workflow_retry_timeout=None`）なら、追加周回全体の時間上限を設けず、完了またはエラーまで待つ。追加周回数は`--workflow-retries`で制限されるが、各周回の所要時間を保証するものではない。初回周回には、このオプションを指定した場合も全体期限を設けない。

通常の通信・ロック競合には別の制限がある。

- HTTPの接続・読み取り・書込み・接続プール待ちには個別タイムアウトがある。組込み既定は各30秒、要求単位の試行は最大3回。読み取り期限は次のデータを受信するまでの待機に適用され、要求全体の時間上限ではない。少量ずつ受信し続けるサーバーでは、応答サイズ上限があっても非常に長時間かかり得る。
- coreの同一feed・状態・履歴・Cookieのプロセス間ロック取得は既定30秒で失敗する。出力ロックも`output.lock_timeout_seconds`（既定30秒）で制限する。他アプリにロックされた成果物を保存できるまで無限に再試行する処理はなく、通常はOSのファイル操作失敗をエラーとして扱う。

ただし、pluginの更新確認・認証・加工hookやcleanupには専用の強制打切り期限がない。終了しないplugin処理・待機は、workflow期限未指定なら無期限に待ち得る。ファイルのopen・write・flush・fsync・replaceなどOSのI/O自体が停止する場合も、ロック取得の競合タイムアウトとは別であり、終了時間の上限を保証しない。

workflow期限を指定しても、キャンセルは協調的である。イベントループを塞ぐ同期処理や、キャンセルに応答しない処理を直ちに強制終了する保証はない。cleanupと開始済みの保存・状態確定は終了まで待ち、その処理自体が停止すれば期限到達後も待機し続ける可能性がある。現在、別プロセスからのwatchdogによる強制終了は実装していない。期限指定は追加周回の通常の待機・通信等を制限する手段であり、プロセスの終了時刻を厳密に保証するものではない。

## Observability and notification


chapter log は image URL、response URL、status、stage、transport、reason code、safe exception detail を記録する。`completed`、`response_received`、`response_limit_exceeded`、`redirect_rejected`、`failed` は取得段階を区別する。image-level fetch/process/save failure は対応する notification category を一度だけ送る。operation-level authentication/configuration/plugin/update/storage/unknown failure は対応する category に送る。

observer、notification sender、sink、Python log capture の cleanup failure は safe diagnostic warning に留め、主 operation の result/exception を変更しない。互換性のため定義される event でも、core に明確な判定点がなければ自動発火しない。設計上の理由は [observability explanation](../explanation/observability.md) を参照する。
## Workflow履歴の保存安全性

`state/workflow-history.json`はprofile内全feedの実行概要をschema 1で保持する。専用の短い共有ファイルロック下でread/merge/保持制御/atomic writeを行い、別feed・別プロセスの記録消失を防ぐ。開始済み保存はキャンセル時も確定させ、その後でキャンセルを伝播する。未知schema・破損を上書きしない。表示・整理ではエラー、workflow記録時には安全な警告と保存失敗フラグにする。

現在の`workflow.json`と過去結果は別の情報で、二つのファイルをまとめて更新するトランザクションではない。表示は保存時点の情報であり、実行中判定や成果物検証を行わない。開始中レコードやクラッシュ検出はないため、強制終了・プロセスクラッシュでは最終記録が残らない場合がある。既存の完了状態から過去の失敗や履歴を復元せず、導入後の実行から記録する。
