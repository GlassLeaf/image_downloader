<a id="runtime-behavior-reference"></a>

# 実行時動作リファレンス

通常workflowは各周回の更新確認・対象選択・比較状態保存後、取得前に全予定を
profileの`logs/workflow`へ非上書きで保存する。予定保存に失敗した周回は取得を開始しない。
状態・履歴のschema、取得条件、画像照合は変わらない。既存RUN_IDで履歴と対応し、履歴の保持・pruneから独立する。
詳細は[workflow専用記録](logging.md#workflow-execution-files)を参照する。

この文書は public API の型ではなく、transport、persistence、output safety の実行契約を定義する。callable signature は [ライブラリAPIリファレンス](library-api.md) を参照する。

<a id="runtime組立て時の所有権"></a>

## 実行環境の構築時の所有権

<a id="runtime-construction"></a>

`RuntimeComposer`は、通常download・CLI inspection・workflow planの組立て中に作成した
registry、log sink／logger、HTTP gateway、画像processorを所有する。serviceの構築が
成功したときだけ、完成した`DownloadService`へ終了処理の所有権を移す。
`compose_registry()`だけを成功させた場合は、返されたruntimeをcallerが閉じる。

組立てに失敗した場合は、作成済み資源の解放を試み、元の例外・中断を再送出する。
serviceを返さないため、callerによる部分的なserviceのcloseは不要である。
途中の資源はまだ通信・画像処理に使われておらず、cookie保存や通知送信も行わない。
正常に完成したserviceの`close()`によるcookie delta保存と通常の終了処理は維持する。

`compose()`の同期APIは維持する。呼出元に動作中のasyncio event loopがあるときは、
失敗時の非同期cleanupだけを一時スレッドの独立loopで実行し、完了を待つ。
後始末の一つが失敗しても残りを試み、元のエラーを置き換えず、固定文言と固定の資源区分で警告する。
後始末自体が失敗した資源の完全解放は保証できない。

本体が構築した未使用資源だけが対象であり、利用済みclientや独自依存の汎用的なloop移動機能ではない。
当該runtime以外のplugin moduleやfinder登録は保持する。作成済みディレクトリ・log・lockファイルは削除しない。

## HTTP通信

<a id="runtime-transport"></a>
<a id="http-transport"></a>

plugin は raw HTTP client を作らず `await context.requests.execute(RequestSpec(...))` を使う。core が timeout、pool、cookie、redirect、authentication、origin/domain concurrency、rate limit を管理する。`RequestSpec`、`RequestResponse`、`ImageResource` は immutable DTO であり、header/query を変える場合は新しい value を返す。

`ImageResource.url` は opaque locator であり transport に直接渡されない。実際の network destination は `create_image_request` または recovery hook が返す absolute HTTP(S) `RequestSpec.url` だけである。

成功した画像 fetch の AuthFlow 後 initial request、redirect 後 final request、response URL/header、request-time `plugin_data` は `TransformContext.transport_metadata` だけに記録される。site transform は raw snapshot を受ける。processor は `image_processors.transport_metadata_access` で selected site を明示許可した場合だけ raw、それ以外は値をマスクした `is_redacted=True` snapshot を受ける。cookie jar 全体を公開せず、各 request snapshot には実際に送信された Cookie header だけを含める。raw metadata は `ImageArtifact`、result、event、CLI JSON、chapter report、log へ自動コピーされない。ただし `filename_format` の `%ORIGINAL_*%` token を使うときだけ、core は `Content-Disposition` または final response URL から抽出した一つの path-less filename を出力名候補に使う。query/fragment、header 全体、URL 全体は保存・出力しない。

GET、HEAD、OPTIONS、TRACE は retry 対象である。429、500、502、503、504 は `Retry-After` を尊重し、`network.retry_max_delay_seconds` 以下の jitter を加える。POST 等の non-idempotent request は `retry_non_idempotent=True` を plugin が安全性を保証するときだけ retry する。

redirect は hop ごとに policy を検査する。anonymous cross-origin redirect には Cookie、Referer、plugin-specific header、application header を引き継がない。response body は streaming で読み、宣言済みサイズと受信済みサイズのいずれも `network.max_response_bytes` を超えると `ResponseSizeLimitError` で止める。

### 組込み汎用HTMLの自動Referer

`core.generic-html`のクラス本体は、HTML解析と画像URL解決後に、最終ページURLから画像ごとの
自動Refererを生成する。`base href`は画像URLの解決に使い、Refererの参照元には使わない。

| 最終ページと画像取得先の関係 | 自動Referer |
| --- | --- |
| 同一オリジン | userinfo・fragmentを除いたページURL。パス・クエリは保持 |
| 別オリジン、HTTPS→HTTP以外 | ページの`scheme://host[:port]/`のみ |
| HTTPS→HTTP | 省略（`None`） |
| HTTP(S)以外・解析不能・不正ポート | 省略。画像URL自体の既存検証・失敗処理は維持 |

オリジンはscheme・正規化したhost・実効portで比較する。サブドメインや異なるportは別オリジンである。
manifestの`ImageResource.referer`、組込みの`RequestSpec.referer`、明示ヘッダーがない場合の
effective previewと実送信に同じ値を使う。HTMLの`Referrer-Policy`／meta／`referrerpolicy`でこの方針を
緩和せず、403でも完全URLへ自動的に戻さない。完全なページRefererが必要なサイトは、独自プラグインで明示する。

独自プラグイン、`GenericHtmlPlugin`の継承クラス、共有HTML parser、明示されたRefererは従来の扱いを維持する。
ヘッダー名の大小文字によらず`RequestSpec.headers`のRefererが`RequestSpec.referer`より優先され、
どちらもない場合は`network.headers`の明示Refererが使われる。自動値が`None`でもこの設定値は送信される。
明示値に含まれる秘密情報と、同一オリジンへ保持するクエリは、この自動値の制限では除去されない。
認証・cookie・リダイレクトの既存方針、公開DTO・設定schema、詳細inspection出力の契約は維持する。

<a id="cookies"></a>

## Cookie

<a id="runtime-cookies"></a>

<!-- claim: TAX-RUNTIME-COOKIES -->
profile cookie は `<storage.data_root>/profiles/<profile>/cookie/cookies.enc` に AES-256-GCM で保存する。暗号鍵は keyring service `image-downloader.cookie-key` に保持し、cookie 本体と鍵を app configuration に書かない。

cookie export/import は passphrase container を使い、passphrase を対話入力する。browser import は `browser-cookie3` を含む `.[browser-cookies]` extra が必要で、既存 jar を差分 merge する。破損した encrypted file は上書きせず error にし、明示 import を復旧手段にする。

明示的な`chrome_user_data_dir`はChromeの`Default/Network/Cookies`と`Local State`に限定する。
両方の通常ファイルを安全に開き、変更を検知するスナップショットから読み取る。SQLiteとLocal Stateの最低限の構造を検査し、
Local StateのJSON読取りは4MiBを上限にする。
依存ローダーのsubstringドメインフィルターに加えて、取得後にドメイン完全一致で絞る。
依存の生例外・標準出力は公開せず、固定文言の認証エラーにする。未対応の復号方式を回避する権限昇格や保護機能変更は行わない。
実機確認では一時領域も隔離端末の専用領域内に置く。手順は[実Chrome試験](../../../manual-tests/browser-cookies/README.md)を参照する。

容器はJSON object、`format`はboolを除く整数の1、`cipher`は`AES-256-GCM`を必須とする。
nonceはbase64文字列から復元した12バイト、ciphertextは認証tagを含む16バイト以上で、
exportでは既存のkind・kdfと16バイトのsaltも検証する。復号後はrecordの配列とし、
name・domainは必須文字列、valueは必須の文字列またはnull、pathは文字列（既定`/`）、
secure・domain_specified・domain_initial_dotはbool（既定false）、expiresはboolを除く
float変換可能な有限範囲の整数またはnull（既定null）、restは文字列キーとJSON値の辞書（既定`{}`）
として復元前に検証する。未知のフィールドは無視し、ドメイン・パスを追加正規化しない。
期限0・過去の期限は期限切れCookieとして扱う。値なしCookieのnullは`None`のまま保持する。

不正な型を文字列・boolへ暗黙変換しない。UTF-8へ符号化できない文字列、
不正なJSON・base64・復号・recordは、入力値を含まない固定メッセージの`AuthenticationError`
とする。file importは元データ全体の検証を終えてから既存データのlock・merge・保存を行い、
不正recordを部分的に取り込まない。loadとbrowser importは破損容器を上書きしない。
file importによる明示的な復旧と、失敗時の既存ファイル保持を維持する。

同一端末上の本 application process は `cookie/cookies.lock` で read/merge/write を直列化する。異なる cookie は保持し、同一 `(domain, path, name)` は後に確定した更新、同値なら長い expiry、session cookie は persistent cookie より短期として扱う。lock file は解放後も残る。NAS と外部 application の同時変更は保証対象外である。`CookieStore.save()` は jar 全体を置換する library API であり、runtime の delta merge とは異なる。

## 共通ファイルロックの初期化と所有権

履歴・Cookie・状態・出力・plugin catalogが使う`InterProcessFileLock`は、非バッファstreamを開き、
OSロックを取得してから、空の場合だけ1バイトを書き込む。Windowsは先頭1バイト、POSIXは`flock`で
排他する。既存の非空ファイルには書き込まず、内容・サイズ・ファイルIDを維持する。
ロックファイルは解放後も残り、存在すること自体はロック保持を意味しない。

Windowsで未作成の場合は、同じ親ディレクトリに一時ファイルを作り、1バイトを書いてhandleを閉じ、
既存宛先を上書きしない`rename`で公開する。公開競合では先に作られた通常ファイルを使う。
その後の`a+b`のopenは`O_CREAT`を除き、対象の消失を取得失敗として扱う。
既存空ファイルを置換・truncateせず、同じファイルをロック下で初期化する。
一時ファイルは初回作成時だけ必要で、親ディレクトリへの作成・rename・削除権限を要する。
親・対象の検査時の同一性を確認し、link・reparse point・複数hardlinkを拒否する。

公開時の競合試験ではopenから共有違反が返るケースも確認されたため、WindowsのopenerはCRTの
拡張エラー32を確認できた場合だけ、既存のpoll間隔0.1秒を一度待ち、作成禁止の同じopenを一度再試行する。
同期APIは`time.sleep`、非同期APIは`await asyncio.sleep`で待つ。この待機中の中断も伝播する。
再試行は最初に確認した親・対象のファイルIDを引き継ぎ、待機中の差替え・消失を拒否する。
アクセス拒否5や他のエラーは再試行せず、再試行も失敗した場合は最初の例外とcauseを維持する。
CRTの拡張エラーを取得できない場合も従来の取得失敗を伝播させる。
Python標準の`ctypes`を使い、CRTはSystem32からのみ取得する。追加の依存や設定は不要である。

ロック待機期限はopenが戻ってから開始し、既定30秒・poll間隔0.1秒を維持する。
待機時間0でも取得を一度試す。同期・非同期の所有権確定前の失敗や中断では、取得済みロックの解放と
streamのcloseを試みる。一つの後始末が失敗しても続行し、元の例外を保持する。
公開前の一時ファイル・descriptorも所有者が後始末する。公開済み対象や他プロセスの一時ファイルは削除しない。
追加の後始末診断は固定文言と資源区分だけを含む。

Windowsの公開処理は、調査で効果が見られた「書込み済みの同じファイルを保持する」条件に基づく暫定策である。
既存空ファイルの初回open、一時ファイルの作成を含むOS内部のI/O停止は残る。
ロック競合timeoutはnative openやwrite等を強制中断せず、終了時間の上限、電源断時のマーカー永続化、
保護製品内部の動作は保証しない。更新時は関連プロセスを終了してから切り替える。
移行コマンドやロックファイルの一括削除は不要である。

<a id="output-allocation-and-locks"></a>

## 出力先の割当てとロック

output path は trusted root の配下だけに作る。managed directory/file/lock に symlink または Windows reparse point があれば `StorageSafetyError` を送出する。`OutputAllocator.allocate()` が reservation を返した `should_write=True` allocation は `commit()` または `abort()` の一度だけで完了する。`existing_file=skip` による `should_write=False` allocation は reservation を持たず、`commit()`/`abort()` は何度呼んでも no-op である。`existing_file=error` は controlled `ExistingFileConflictError`、output lock timeout は operation failure であり image retry ではない。

出力名のWindows禁止文字は、`"→”`、`*→＊`、`/→／`、`:→：`、`<→＜`、`>→＞`、`?→？`、`\→＼`、`|→｜`へ置換する。タイトル・subtitle・元ファイル名のstem等、名前部分の`.`は`．`へ置換し、画像ファイルの最後の拡張子区切りは`.`を維持する（`archive.tar.webp`の出力名は`archive．tar.webp`）。制御文字の`_`置換とWindows予約名の回避は維持する。汎用`safe_name()`は通常のファイル名を検査する用途も持つため、禁止記号を上記の文字へ置換するが、通常のドットは維持する。名前部分のドット変換は出力formatterが行う。元のmanifestタイトル、画像URL、revisionは変更しない。これまで禁止記号を`_`へ置換していた名前と、名前部分にドットを含む出力名は変わり得る。既存ファイル・状態の自動移行は行わない。

コア生成の章名・画像名・重複時の候補名・出力用host/plugin IDは、安全化と`output.max_component_length`による短縮の後、保存先ファイルシステムの要素長上限だけ追加で検査する。上限内なら安全化後の名前を維持する。WindowsはUTF-16単位数、POSIXは`os.fsencode()`のバイト数で測り、上限を取得できない場合は255とする。超過時だけ先頭を短縮し、名前全体のSHA-256先頭8桁を付ける。追加短縮では最後の拡張子と重複時の連番を保持する。既存の明示設定による短縮で既に拡張子が落ちる挙動は変更しない。

`max_component_length: null`は利用者指定の短縮を行わない意味であり、OSの要素上限対策は有効である。OS間で上限と計測単位が異なるため、超過する名前だけ保存名が異なる場合がある。追加ファイルのplugin指定相対パス、保存root、profile名、stateファイル名を自動改名せず、既存ファイルと状態を自動移行しない。

原子的保存は同じ親ディレクトリに最終名を含まない短い`.id-`接頭辞の一時ファイルを作り、`fsync`と安全性検査後に置換する。パス全体を260文字などで事前拒否しない。親が深すぎて短い一時名も作れない場合を含め、実際の長さ超過は`StorageError`の定型診断で通知する。長いWindowsパスで親が存在するのに作成が`FileNotFoundError`となる場合は、長さ制約の可能性として案内する。通常の不存在判定・無関係なエラーは維持する。保存rootやWindows設定を自動変更せず、別ディレクトリへの一時保存・非原子的保存へ切り替えない。

通常downloadの成果物には短い絶対パスの`--output-dir`を指定できる。profileのCookie・state・logも深い場合は、既存データを退避して必要なprofileツリーを短い`storage.data_root`へ移し、`config explain`で解決先を確認する。`--output-dir`だけではprofileデータの場所は変わらない。Windowsでは実行PythonとOSの長いパス対応も確認する。実測結果と再実行コマンドは[R15検証記録](../../investigations/implementation-review-2026-10-04/r15-details.md)を参照する。

<a id="empty-download-results"></a>

## 空のダウンロード結果

章一覧0件・各章の画像0件の許可判定は、各download試行で一度だけ共通検査を行う。
章一覧0件には`download.allow_empty_chapter_manifest`の拒否を優先し、設定がtrueの場合も
`DownloadManifest.allow_empty_chapters=False`なら`PluginError("plugin returned an empty manifest")`とする。
章がある場合、画像0件かつ`Chapter.allow_empty_images=False`の章が一つでもあれば
`PluginError("plugin returned an empty image list")`とし、全章の通常画像ダウンロードを開始しない。
画像0件の章で指定がNoneまたはTrueなら許可する。画像のある章のFalse指定は取得を妨げない。

共通検査は既存の章0件検査と同じ位置で、inspect・ledger更新・BEFORE_MANIFESTの保留保存・
全章のAFTER_MANIFEST処理の後、章の画像ジョブ開始前に行う。ページ取得・追加ファイル処理を
取り消さず、後始末と既存のPluginError失敗・再試行経路を使う。拒否結果は成功通知・完了登録しない。
許可された空結果、既存ファイルのスキップ、更新対象URL0件の従来挙動は維持する。

inspectionは許可指定の型を検証するが、空という理由では拒否しない。
`verify workflow`は取得検証を続け、許可された空結果も`no_images` / `not_acquired`とする。
履歴・状態schemaと既存の完了状態は変更せず、未指定の旧プラグイン・組込み汎用HTMLの画像0件成功は残る。

`allow_empty_chapter_manifest`は互換性のため当面維持する将来の整理対象であり、現在はfalseの拒否を
優先する。今回は削除・無効化・自動移行を行わず、整理時期と移行方法は別途決定する。

<a id="update-state"></a>

## 更新状態

<a id="runtime-update-state"></a>

<!-- claim: TAX-RUNTIME-UPDATE-STATE -->
profile state の `state/updates.json` は schema version 2 である。snapshot は plugin ID と source URL の SHA-256 key ごとに保持し、raw source URL を namespace key として保存しない。URL 表記が変われば別 feed である。

v1（version field のない旧形を含む）は次の update check で `legacy_records` に保存して移行する。旧 record と candidate の URL と content ID が完全一致するときだけ初回比較に使い、legacy record だけを根拠に `REMOVED` を出さない。新 snapshot を保存した次回から通常の removal 判定を行う。未来 schema または破損 state は上書きせず `UpdateStateError` を送出する。

同 profile の本 application process は `state/updates.lock` を共有し、read/compare/atomic write 全体を直列化する。default lock timeout は 30 秒で、timeout は update operation 全体を失敗させる。異なる feed の変更は両方保持する。lock file は残り、NAS と外部 application の変更は保証しない。

<a id="workflow-state"></a>

## workflowの状態

`workflow`は`updates.json`とは独立して、profileの`state/workflow.json`（スキーマバージョン1）にも書き込みます。プラグインIDとハッシュ化したfeed URLごとに、最新の完全なスナップショットと候補ごとの`completed`を保持します。時間経過による失効はありません。状態がなければ初回実行として扱い、不正なスキーマや将来のスキーマの場合は上書きせず失敗します。既存の更新一覧履歴も確認時に更新しますが、それをworkflowの完了状態の初期値には使いません。

ダウンロード前に、選択した候補を未完了として永続保存します。画像の失敗がなく、プラグインの後始末も成功したURLを完了として確定します。スキップや、許可された空のmanifestも完了に含めます。失敗・部分完了・未処理の候補は未完了のままです。一覧から消えた候補は、ローカルの成果物を削除せずworkflowのスナップショットから取り除き、再登場すれば新規として扱います。両方のscopeで完了状態を共有します。同じ対象URLは一度だけ処理し、一致するすべての識別子の状態を更新します。

サービスはworkflow全体を直列化します。`state/workflow-locks/<hash>.lock`は、同じプラグイン・feedの確認前から完了までをプロセス間で直列化します。`state/workflow.lock`は、全feedで共有する短い読込み・マージ・原子的書込みのトランザクションを直列化します。既定のタイムアウトは30秒で、ロックファイルは残ります。開始済みのトランザクションは、キャンセルを伝播する前に確定させます。完了したURLは中断後も保持します。認証・要求・プラグイン・既存ファイル衝突のエラーは当該URLを中止し、他のURLへ進みます。設定、保存・ロック・安全性のエラー、想定外の失敗はworkflow全体を中止します。

<a id="workflow-error-boundaries"></a>

### Workflowの処理順序とエラー境界

通常実行は、逐次ログ開始、更新元plugin選択・feedロック取得、更新一覧取得、履歴比較・対象選択と
未完了状態保存、当該周回の予定保存、対象URLごとのmanifest・画像取得、成功URLの完了状態保存の順に進む。
再試行対象が残れば待機して追加周回を行い、最後に実行履歴・終了ログを保存する。
CLIはservice closeと必要な履歴訂正の完了後に結果を表示します。本処理の結果と終了処理の結果を独立して保持し、JSONは[CLI共通形式](cli.md#cli-json)で1文書にまとめます。終了失敗を本処理の結果へ書き込まず、履歴と終了ログだけを別の結果で補正します。本処理の例外・キャンセルが優先し、終了失敗と待機側キャンセルでは終了失敗が優先です。logger終了の通常例外は固定警告に留めます。

| エラー発生箇所・種類 | 挙動 |
| --- | --- |
| 更新元plugin選択・更新一覧取得中の`PluginError` | 全体停止。対象未確定ならその周回の予定は作らず、通常の`PluginError`の終了コードは4 |
| 個別URLのmanifest取得・cleanup等の`PluginError` | 当該URLを`failed`として結果・試行を保持し、次のURLへ進む。通常の`plugin_error`は追加周回の再試行対象 |
| 画像単位で結果に収められた失敗 | URLを`partial`などとして保持し、再試行可能な失敗・未処理画像を追加周回で処理 |
| 設定・状態・保存／ロックのエラー、想定外の内部例外 | 全体停止。既に確定した成果物・結果と未処理対象を保持 |
| 予定ファイル保存失敗 | `workflow_plan_log_error`、終了コード1。その周回のmanifest・画像取得を開始しない |
| 逐次ログの作成・追記失敗 | 安全な警告を出し、失敗した形式への追記を止め、取得・他方のログ・履歴保存を続行 |

追加周回は既定で1回、待機600秒。更新一覧を再取得し、成功画像の保持と対象選択には
[再試行規則](#workflow-retry-rounds)を適用する。`PluginError`の派生エラーすべてが再試行可能なわけではなく、
未対応機能などは対象外となる。再試行が終わっても個別URL・画像の失敗が残る場合、保存／スキップ済み画像が
あれば終了コード5、なければ1となる。個別URLのplugin失敗だけを根拠に全体の終了コード4にはしない。

全体停止・キャンセルでも開始済みの保存を確定し、終了記録を試みる。
逐次ログの項目・欠落条件は[中断時の記録](logging.md#workflow-interruption-records)、
コンソールとJSONは[CLIリファレンス](cli.md#workflow)を参照する。

<a id="workflow-dry-run"></a>

### workflowの試行実行

CLIの`workflow --dry-run`とライブラリの`plan_workflow()`は、副作用のない共通の初回周回の選択処理を使います。プレビューはfeedを一度確認し、既存の共有ファイルロック下でworkflowの完了履歴を読みます。更新履歴とworkflow履歴をともに検証しますが、書込み・移行はしません。workflowのfeed履歴がなければ、どちらのscopeでも現在の全候補を選択します。操作ロックと同一feedのロックは、確認・選択・後始末まで保持します。他のfeedについては、通信での確認中にロックしません。

対象のmanifest、画像要求・加工・出力先の割当て、待機、再試行の追加周回は実行しません。複製したCookieJarと独立したgatewayを使い、認証の変更を通常のサービスセッションへ反映しません。CLIの計画ではさらに、初期設定の保存・書換え、ファイルログ、通知、終了時のCookie保存を抑止します。作成するのは必要なロックファイルとディレクトリだけです。ただし、プラグインコードや外部の認証処理を隔離する仕組みではありません。プレビューは観測結果であり予約ではなく、本実行ではfeedを再取得します。計画専用の結果の契約は[CLI出力](cli.md#workflow)と[ライブラリAPI](library-api.md)を参照してください。

<a id="workflow-retry-rounds"></a>

### workflowの再試行周回

再試行可能な未完了処理がある場合だけ、既定で600秒待って追加周回を1回行います。各周回では完全なfeedを再取得し、最新のスナップショットを確定します。削除された候補を除き、新規・変更候補と再試行可能な未完了URLを含めます。`all`はその呼出しの初回選択だけに適用します。待機中もfeedとサービス操作のロックは保持します。短い共有ファイルロックは待機中に保持しません。

同じ呼出し内では、画像結果が確定するたびに台帳へ記録します。失敗時の即時停止やキャンセルより前に並行処理で成功した結果も含めます。章は`chapter_id`を優先し、なければ番号で識別します。章内の画像は`image_id`を優先し、なければlocatorで識別します。識別が一意で、`ImageResource`の全フィールドが等しい場合だけ結果を再利用します。曖昧な識別子のグループは再取得し、プラグインが変われば画像結果の再利用をリセットします。manifestの画像情報が変わらない画像本体の変更は検出できず、成果物の検証も追加しません。変更された画像には設定した既存ファイル方針を適用するため、renameではファイルが増え、errorでは衝突を拒否する場合があります。画像が成功しても後始末が失敗すればURLは未完了のままにし、再試行では画像を再利用して後始末を繰り返します。

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
pluginでの組立方法と秘密情報の扱いは[動的URLの実装手順](../how-to/dynamic-urls-and-auth.md)を参照する。

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

永続化するのはURLの完了状態だけで、スキーマは1のままです。任意の再試行期限は初回周回の後から計測し、待機・更新確認・ダウンロードを含めます。期限に達すると実行中の処理をキャンセルし、後始末と開始済みのトランザクションを待ちます。ライブラリでは`WorkflowRetryTimeoutError`を原因に持つ`WorkflowExecutionError`となり、利用者によるキャンセルと区別できます。CLIは、いずれかの試行で保存・スキップしたファイルがあれば5、なければ1を返します。回復に成功した場合、または失敗した対象がすべて削除された場合は0です。重大なエラー本来の終了コードと、利用者キャンセルの終了コード130は維持します。

<a id="workflow-timeout-boundaries"></a>

### workflowの期限と無期限待機の制限

`--workflow-retry-timeout`未指定（APIでは`workflow_retry_timeout=None`）なら、追加周回全体の時間上限を設けず、完了またはエラーまで待つ。追加周回数は`--workflow-retries`で制限されるが、各周回の所要時間を保証するものではない。初回周回には、このオプションを指定した場合も全体期限を設けない。

通常の通信・ロック競合には別の制限がある。

- HTTPの接続・読み取り・書込み・接続プール待ちには個別タイムアウトがある。組込み既定は各30秒、要求単位の試行は最大3回。読み取り期限は次のデータを受信するまでの待機に適用され、要求全体の時間上限ではない。少量ずつ受信し続けるサーバーでは、応答サイズ上限があっても非常に長時間かかり得る。
- coreの同一feed・状態・履歴・Cookieのプロセス間ロック取得は既定30秒で失敗する。出力ロックも`output.lock_timeout_seconds`（既定30秒）で制限する。他アプリにロックされた成果物を保存できるまで無限に再試行する処理はなく、通常はOSのファイル操作失敗をエラーとして扱う。

ただし、pluginの更新確認・認証・加工hookやcleanupには専用の強制打切り期限がない。終了しないplugin処理・待機は、workflow期限未指定なら無期限に待ち得る。ファイルのopen・write・flush・fsync・replaceなどOSのI/O自体が停止する場合も、ロック取得の競合タイムアウトとは別であり、終了時間の上限を保証しない。

workflow期限を指定しても、キャンセルは協調的である。イベントループを塞ぐ同期処理や、キャンセルに応答しない処理を直ちに強制終了する保証はない。cleanupと開始済みの保存・状態確定は終了まで待ち、その処理自体が停止すれば期限到達後も待機し続ける可能性がある。現在、別プロセスからのwatchdogによる強制終了は実装していない。期限指定は追加周回の通常の待機・通信等を制限する手段であり、プロセスの終了時刻を厳密に保証するものではない。

<a id="observability-and-notification"></a>

## ログ・イベント・通知


chapter log は image URL、response URL、status、stage、transport、reason code、safe exception detail を記録する。`completed`、`response_received`、`response_limit_exceeded`、`redirect_rejected`、`failed` は取得段階を区別する。image-level fetch/process/save failure は対応する notification category を一度だけ送る。operation-level authentication/configuration/plugin/update/storage/unknown failure は対応する category に送る。

observer、notification sender、sink、Python log capture の cleanup failure は safe diagnostic warning に留め、主 operation の result/exception を変更しない。互換性のため定義される event でも、core に明確な判定点がなければ自動発火しない。設計上の理由は [ログ・イベント・通知の解説](../explanation/observability.md) を参照する。

SMTP 通知は全宛先がサーバーに受理された場合に成功とする。`send_message()` の拒否辞書が非空なら
部分拒否、`SMTPRecipientsRefused` なら全拒否として扱い、`NOTIFICATION_FAILED` を1回発火する。
`error_code` はそれぞれ `smtp_partial_recipients_refused`、`smtp_all_recipients_refused` とする。
部分拒否では一部宛先へ送信済みであり、自動再送は行わない。受理は受信箱への最終到達の保証ではなく、
通知失敗は download/workflow の結果や CLI 終了コードを変更しない。

拒否時の有効な通常ログ・console・debug.log には、固定の分類、拒否辞書の件数、`smtp_host`、
`refused_domains` を記録する。`smtp_host` は組込み email sender の送信設定にあるホスト、
`refused_domains` は拒否されたアドレスの `@` 以降を安全に抽出した、重複なし・ソート済みの一覧である。
DNS 名は小文字の IDNA ASCII 表記、IP は正規化済み表記にする。ポートはホスト表示から除く。
不正・解析不能な値は `unknown` とし、元の文字列を表示しない。独自 sender の SMTP ホストが不明な場合も
`unknown` とする。DNS/MX 検索は行わず、接続先 IP や配送先 MX ホストを特定する機能ではない。
宛先ドメインと SMTP ホストは診断として開示するが、ローカル部分・表示名・SMTP 応答本文・認証情報は
表示しない。イベントにはホストを追加せず、既存の `error`/`error_class` の秘匿を維持する。

## Workflow履歴の保存安全性

`state/workflow-history.json`はprofile内全feedの実行概要をschema 1で保持する。専用の短い共有ファイルロック下でread/merge/保持制御/atomic writeを行い、別feed・別プロセスの記録消失を防ぐ。開始済み保存はキャンセル時も確定させ、その後でキャンセルを伝播する。未知schema・破損を上書きしない。表示・整理ではエラー、workflow記録時には安全な警告と保存失敗フラグにする。

現在の`workflow.json`と過去結果は別の情報で、二つのファイルをまとめて更新するトランザクションではない。表示は保存時点の情報であり、実行中判定や成果物検証を行わない。開始中レコードやクラッシュ検出はないため、強制終了・プロセスクラッシュでは最終記録が残らない場合がある。既存の完了状態から過去の失敗や履歴を復元せず、導入後の実行から記録する。
