<a id="cli-reference"></a>

# CLI参照

<a id="cli-forms"></a>

workflowは各周回の取得前にprofileの`logs/workflow`へ全取得予定を必須保存する。
workflow専用の`--workflow-progress-log enabled|disabled`はJSONL・テキストの逐次ログを制御する。
コマンド前後で受理し、明示値が設定に優先する。他コマンド・bare URL形式では拒否する。
disabledでも予定は必須。dry-runは値を受理するが専用ファイルを作成しない。

```text
image-downloader workflow URL --workflow-progress-log disabled
image-downloader --workflow-progress-log enabled workflow URL --json
```

予定保存失敗は終了コード1で、その周回の取得を開始しない。逐次ログ保存失敗は取得を継続し、
通常JSONの`workflow_log`に保存状況・警告を返す。
原文URLの記録と制限は[専用ログ](logging.md#workflow-execution-files)を参照する。

正規のコマンド形式は次のとおりです。

```text
image-downloader download URL [--force-image-format FORMAT] [options]
image-downloader workflow URL [--download-scope all|updated] [download options]
image-downloader state workflow [list|show|history|run|prune] [arguments] [options]
image-downloader verify logs ABSOLUTE_PATH [--json]
image-downloader verify workflow RUN_ID --output-dir ABSOLUTE_PATH [options]
image-downloader inspect URL [options]
image-downloader download URL --inspect-only [options] # inspect の alias
image-downloader URL [download options]                 # bare-URL compatibility form
image-downloader doctor [--host HOST_OR_URL] [options]
image-downloader config path|explain|init [arguments] [options]
image-downloader config profile init NAME [options]
image-downloader plugin list|install|trust|revoke|uninstall [arguments] [options]
image-downloader cookie export|import|browser-import VALUE [options]
image-downloader help [COMMAND [SUBCOMMAND ...]]
```

従来の`--export-cookies`、`--import-cookies`、`--import-browser-cookies`は、Cookie操作を一つ選ぶ指定であり、併用できません。`URL`を単独で指定する形式は`download`へ読み替えます。

<a id="cli-state-effects"></a>

config 作成・rewrite の command ごとの副作用は [設定リファレンス](configuration.md#config-layers) が正本である。`config path` と `config explain` は観測専用である。

<a id="option-acceptance-and-effect"></a>

## オプションの受理と効果

<a id="workflow"></a>

### ワークフロー

`workflow URL --dry-run`は、実際にフィードを一度確認し、初回周回のURL選択を事前表示します。`--download-scope all|updated`と、コマンド前の指定を含む既存のワークフローオプションを受理します。`--dry-run`は`state workflow prune`でも使えますが、他のコマンドとURL単独のダウンロード形式では拒否します。既存の値検証と排他条件は維持し、再試行オプションは検証するものの、待機や追加周回は実行しません。

事前表示には、選択・除外された候補と理由（`all`、`added`、`changed`、`unfinished`、完了済みで除外する`completed`）、削除候補、候補数・選択URL数を示します。選択時はフィード順を維持してURLを重複排除し、同じURLを持つ候補は選択結果と理由を共有します。対象manifestの検査、画像の取得・加工・保存、画像件数・出力名・ファイル競合の予測、取得成功の保証は行いません。出力・形式・既存ファイルのオプションは受理しますが、その動作はシミュレーションしません。

このコマンドは、`updates.json`・`workflow.json`の更新、設定の初期化・書き換え、Cookie・ファイルログの保存、通知送信を行いません。既存Cookieを独立したセッションへ読み込み、認証更新や通常のHTTP再試行は起こり得ますが、セッションの変更は破棄します。lockファイルと親ディレクトリは作成する場合があります。フィード・プラグインの認証、確認、後始末のコードは実行し、プラグインが起こす外部への副作用はサンドボックスで隔離しません。次の本実行ではフィードを再確認し、事前表示した一覧を予約するものではありません。

`--json`では、安全な一つの文書に`operation="workflow"`、`dry_run=true`、`source_url`、`download_scope`、`plugin_id`、`first_run`、`checked_at`、`candidates`、`changes`、`selected_urls`、`items`、`summary`、`stop_error`、`cancelled`を含めます。各項目は`candidate`・`selected`・`reasons`を持ち、集計は`candidates`・`selected_urls`・`selected_candidates`・`excluded_candidates`・`removed_candidates`を数えます。`first_run`は履歴比較が完了するまではnullで、その後は当該プラグイン・フィードの履歴がないことを示します。失敗時も確定済みの事前確認情報を保持します。このモードではプラグインの標準出力を標準エラー出力へ送ります。空の選択を含め、成功時は0、エラーは既存の例外別終了コード、利用者キャンセルは130です。ダウンロードの部分成功を表す5は返しません。

```sh
image-downloader workflow https://example.test/feed --dry-run
image-downloader --dry-run workflow https://example.test/feed --download-scope all --json
```

`workflow URL`は、選択した更新確認プラグインを呼び、その後で選択URLを順にダウンロードします。

画像・付属ファイルの保存先は通常downloadと同じ`<profile>/downloads`で、`--output-dir`で変更する。
workflow専用の画像ディレクトリは作らず、`output.directory_format`も通常どおり適用する。
取得予定・逐次ログは`<profile>/logs/workflow`に保存し、`--output-dir`では移動しない。
通常診断は`<profile>/logs/debug.log`、章ログは画像の各章ディレクトリの`log.log`、
実行履歴は`<profile>/state/workflow-history.json`に残る。
対象ページURLと実際の画像要求URLの記録先・伏字の違いは[ログの保存先とURL](logging.md#workflow-output-locations)を参照する。

`core.generic-html`もワークフローに対応します。候補は要求ページ自体だけで、リンク先ページは含めません。revisionは、重複を含む抽出画像のURL・index・image_idの順序付き一覧を比較し、タイトル・本文・保存設定は無視します。URLクエリや順序の変化は更新になりますが、URLが同じまま画像本体が変化しても検知できません。HTMLは確認時と、選択したページのmanifest取得時にそれぞれ取り直します。再試行で画像結果を保持する規則は従来どおりです。画像0件のページも候補となり、通常ダウンロードと同じく空の章一つで成功します。空manifestの設定が制御するのは章0件の場合です。更新非対応のプラグインは`update_check_unsupported`を報告し、終了コードは4です。

```text
image-downloader workflow https://example.test/gallery --fallback-generic enabled
image-downloader workflow https://example.test/gallery --fallback-generic enabled --dry-run --json
```
既定の`--download-scope updated`は、追加・変更された候補と、現在も存在する未完了候補を選びます。`--download-scope all`は現在のスナップショット全体を選びます。初回実行は全候補を選びます。削除候補は取得せず、再出現した候補は再度追加として扱います。比較には専用のワークフロー履歴を使い、`--list-updated-urls`の履歴から独立しています。

設定・プロファイル・ルート・セキュリティ、プラグイン設定・選択・フォールバック・取得方針、出力先、ディレクトリ形式、既存ファイル、画像形式・強制画像形式、JSON、コンソールの指定はワークフローにも適用します。既存の優先順位と排他条件、つまりimage-formatとforce-image-format、pluginとforce-plugin、明示的なプラグイン選択と明示的なフォールバックの排他を維持します。明示的なプラグイン選択はフィードと全対象へ適用します。サイト設定はフィードに対して解決し、Cookie・接続を含めて対象間で共有します。`--yes`は受理しますが、新たな確認は追加しません。`--download-scope`は`workflow`の前後で受理し、他のコマンドでは拒否します。更新一覧、検査、Cookie操作、`--host`、`--selection-priority`は、コマンド前に指定した場合も拒否します。

出力や設定を変えただけでは、完了済み候補を更新扱いにしません。新しい出力設定で再取得する場合は`all`を使ってください。`skip`でも取得・加工を先に行う点を含め、既存ファイル方針は維持します。再試行可能な失敗がある未完了URLは、初回周回後に再試行します。各再試行でフィードと対象manifestを取り直し、消えたURLを除外して、残った失敗と追加・変更URLを処理します。同じ呼び出し内では、`all`であっても変更のない成功・skip画像結果を保持します。次の呼び出しではURL単位の未完了状態と通常の既存ファイル方針を使います。

URL候補の更新判定と、対象URL内の画像の変更判定は別である。画像は`ImageResource`の全フィールドを
比較し、同じ`image_id`でも画像URLのクエリ、`referer`／`headers`、`save_options`、`original_filename`
などが変われば再取得する。画像本体や実際の要求URLは比較しない。
条件表と既知の制限は[実行時動作](runtime-behavior.md#workflow-retry-rounds)、
具体例は[workflowのFAQ](../how-to/faq.md#workflow-retry-faq)を参照する。

| ワークフロー専用オプション | 既定値 | 動作 |
| --- | --- | --- |
| `--workflow-retries` N | 1 | 追加の周回数。0以上の整数。0で再試行を無効化する。 |
| `--workflow-retry-delay` SECONDS | 600 | 各再試行前の固定待機時間。有限かつ0以上の数値。 |
| `--workflow-retry-timeout` SECONDS | 制限なし | 初回周回後の、待機・確認・取得を含む再試行期間。有限かつ正の数値。 |

これらのオプションは`workflow`の前後で指定でき、他のコマンドでは拒否します。設定ファイルのキーは追加しません。全件成功時は、その後の更新を待機・監視せず終了します。時間切れでは実行中の処理をキャンセルし、後始末と開始済み書き込みを確定するため、終了が指定時間を超える場合があります。同一フィードのプロセス間lockは、既定10分の待機中も保持します。変更画像にも既存ファイル方針を適用するため、`rename`では追加ファイルができ、`error`では既存出力との競合により失敗する場合があります。

`--workflow-retry-timeout`を省略した場合、周回数は有限ですが再試行全体の時間制限はありません。HTTPとlock取得には別の時間制限があります。プラグインフックと後始末には強制停止用の専用期限がなく、プラグインやOSのI/Oが停止すると無期限に待つ場合があります。再試行期限を指定しても、同期のブロッキング処理は強制中断できず、後始末や開始済み書き込みが期限を超えて待ち続ける場合の上限は保証しません。どちらの場合も初回周回には全体期限がありません。低速ストリーミング、lock、キャンセルの制限は[時間制限の境界](runtime-behavior.md#workflow-timeout-boundaries)を参照してください。

```sh
image-downloader workflow https://example.test/feed --workflow-retries 2 --workflow-retry-delay 30 --workflow-retry-timeout 300 --json
image-downloader workflow https://example.test/feed --workflow-retries 0
```

再試行の対象には、通信・認証・プラグイン・画像処理の失敗を含めます。秘密情報の未設定、非対応機能・形式、リダイレクト・サイズ・画素数の制限、終了済み画像処理、恒久的なHTTP 4xx（401・403・408・429を除く）、保存失敗、ファイル競合は対象外です。5xxは再試行できます。設定・状態・lockのエラーと予期しない内部例外はワークフローを停止します。HTTP単位の再試行も引き続き有効です。

JSONは`operation`、`source_url`、`download_scope`、`checked_at`、`candidates`、`changes`、`selected_urls`、`items`、`summary`、`stop_error`、`cancelled`を持つ一文書です。各項目は安全な`url`、`reasons`、`status`、`download`、`error`を持ち、downloadは`saved`・`skipped`・`failures`を再利用します。理由は`all`・`added`・`changed`・`unfinished`、状態は`success`・`partial`・`failed`・`unprocessed`・`removed`で、集計はこれらの状態を数えます。再試行設定、`rounds`、項目ごとの`attempts`、`timed_out`も含めます。全体の`outcome`は`success`・`incomplete`・`stopped`・`timed_out`・`cancelled`を区別し、キャンセル、時間切れ、停止、URL単位の未完了の順に優先します。通常表示では`URL results`の件数と全体結果を分けます。スナップショット確定前の停止は`update check result: not established`と表示し、失敗URLが0件でも成功とは限りません。

通常表示では、URL集計より前に周回0の対象選択も説明します。`Initial selection: candidates=N selected_urls=M`は、プラグインの候補数と重複を除いた選択URL数を分けて数えます。同じURLを複数候補が共有する場合があるためです。`Selection`は全件指定、追加・変更候補、以前の未完了候補のどれが理由かを説明し、候補の削除は別に報告します。後の再試行で初回選択の説明を上書きせず、再試行の詳細は周回別の出力に残します。

`updated`では、完了済み候補の比較対象フィールドが変わっていない場合、次のように表示することがあります。

```text
workflow (updated): success
Initial selection: candidates=1 selected_urls=0
Selection: current candidates are unchanged and previously completed; no downloads selected
First-run status is not recorded; added candidates do not necessarily indicate a first run.
URL results: success=0 partial=0 failed=0 unprocessed=0 removed=0
```

これは取得対象0件で正常終了したワークフロー実行であり、履歴記録が成功すれば実行履歴を一つ保存します。URL集計は対象の結果数であり、ワークフローの実行回数ではありません。空のスナップショットなら`plugin returned no candidates`、選択前の停止なら選択未確定を表示します。周回0の情報がなければ、選択詳細が記録されていないことを示します。初回実行かどうかは記録せず、追加候補から推測もしません。「変更なし」はプラグインのスナップショット比較を指し、画像のバイト列の検証ではありません。

周回別の結果は、スナップショット・差分・選択URL・削除URL・停止情報を公開します。最上位のスナップショットと差分は、準備が成功した最新周回のものです。試行結果は、統合したダウンロード結果、画像位置、安全なlocator・パス、状態、`attempted`、`retained`を持ち、周回0が初回です。途中の失敗はログ・イベント・JSONへ残し、ダウンロード通知は最終結果だけを含めます。停止時も完了済み結果と未処理対象を保持し、JSONモードの診断は標準エラー出力へ送ります。成功・選択対象なしは0、保存・skipを伴う画像・URL失敗は5、保存・skipのない失敗は1です。重大な停止は既存の例外別コード、キャンセルは130です。

更新一覧取得中の`PluginError`は全体停止（通常4）だが、個別URLの`PluginError`は失敗として記録し、
次のURLへ進む。通常の`plugin_error`は追加周回の対象となる。発生箇所ごとの規則は
[エラー境界](runtime-behavior.md#workflow-error-boundaries)を参照する。
通常表示は各URLの`状態: URL`をstdoutへ、全体結果・選択理由・`URL results`をstderrへ出す。
例外停止時は`error [コード]: 理由`、キャンセル時は`workflow cancelled`もstderrへ出す。
`--json`では途中までの結果・`stop_error`・`cancelled`を一文書に含め、診断出力はstderrへ送る。
実行開始後の例外・協調的キャンセルでは、有効で書込み可能な専用ログに終了記録も残す。
記録項目、`run_corrected`、開始前の失敗や強制終了時の制限は
[中断時の記録](logging.md#workflow-interruption-records)を参照する。

<a id="cli-options"></a>

`--json`は全コマンドで受理します。**受理される指定でも効果があるとは限りません**。適用できないオプションは、設定解決前に`ArgumentError`となります。オプションはコマンド前、サブコマンド間、位置引数の後に置くことができ、受理条件と診断の優先順位は同じです。完全なオプション名と文書化した別名だけを受理し、`--j`や`--list`のような省略形は拒否します。

単一値のオプションは最後の指定を使います。繰り返し可能なJSON・ファイル指定は順序を保ち、すべてのファイル上書きを先に、その後でインライン指定を合成します。既定値と同じ値の明示指定も指定済みとして扱うため、`plugin list --selection-priority 0`は不正です。`--`でオプション認識を終了し、それ以降の文字列を位置引数として扱います。

位置引数だけでdownloadを選ぶのは、ホストを持つ絶対HTTP(S) URLの場合だけです。明示的なdownload・inspect・workflowにも同じURL条件を適用します。その他の単語は未知のコマンドです。

`help`、`help plugin`、`help config profile`は対応するヘルプを表示し、0で終了します。未知の対象や過剰な引数は引数エラーです。`--help`と`-h`は解析を早期終了しますが、それより前にオプション値が欠けていればエラーです。`--json`でもヘルプは常に標準出力のテキストで、設定・プラグイン・Cookie保存先は初期化しません。

| オプション | 受理され効果があるコマンド | 受理しても効果がない場合・拒否する場合 |
| --- | --- | --- |
| `--config PATH` | download、inspect、doctor、config explain/profile init、plugin、cookie | config path/initでは拒否 |
| `--profile NAME` | download、inspect、doctor、config explain、plugin、cookie | config path/init/profile initでは拒否 |
| `--data-root PATH`、`--plugin-root PATH` | download、inspect、doctor、config explain/init、plugin、cookie | config pathとprofile initでは拒否 |
| `--yes` | plugin install/trust/revoke/uninstallの確認 | config init/profile initは受理するが確認には使わない。他の解析済み指定も状態変更の承認にはならない |
| `--plugin-verification-override MODE` | download、inspect、doctor、plugin | configは解析値を表示できるが検証を行わない。cookieでも検証への効果はない |
| `--plugin-config ID=JSON`、`--plugin-config-file PATH`、`--fallback-generic` | download、inspect、doctor | config、plugin、cookieでは拒否 |
| `--plugin ID`、`--force-plugin ID` | download（`--list-updated-urls`を含む）、inspect | doctor、config、plugin、cookieでは拒否 |
| `--plugin-download-policy ID=JSON`、`--plugin-download-policy-file PATH` | download（`--list-updated-urls`を含む） | 検査を含む他のコマンドでは拒否 |
| `--no-console-log`、`--existing-file`、`--image-format` | download、doctor、config explainの許可範囲 | 検査、cookie、その他のconfig・plugin操作では拒否 |
| `--force-image-format FORMAT` | 通常のdownloadとURL単独形式 | `--image-format`と排他的。inspect、doctor、config、plugin、cookie、更新一覧では拒否 |
| `--output-dir ABSOLUTE_PATH`、`--directory-format FORMAT` | 通常のdownloadとURL単独形式 | `download --inspect-only`と`--list-updated-urls`では受理するが効果なし。inspect、doctor、config、plugin、cookieでは拒否 |
| `--list-updated-urls` | download | その他では拒否 |
| `--inspect-only` | `download URL`の検査用の別形式 | `--list-updated-urls`と排他的 |
| `--manifest-only` | inspect、`download URL --inspect-only` | 通常downloadでは拒否。`create_image_request()`を呼ばない |
| `--inspection-data url\|http\|all` | inspect、`download URL --inspect-only` | 標準出力に含める検査情報の段階。既定値`url`。通常downloadでは拒否 |
| `--host` | doctor、config explain | その他では拒否 |
| `--selection-priority INT` | plugin install/trust | その他では拒否 |

`--plugin-config`は繰り返し指定できる`ID=<JSON-object>`です。`--plugin-config-file`は、絶対パスの既存の通常JSONファイルを繰り返し指定する形式で、リンクは不可です。オブジェクトは正確に`{"plugin_id": "…", "config": {…}}`の形にします。ファイルのマッピングをコマンドラインのファイル順に再帰的に合成し、その後でインラインの`--plugin-config`を出現順に再帰的に合成します。このオプションはYAMLを受け付けません。

`--plugin-download-policy`は繰り返し指定できる`ID=<JSON-object>`です。`--plugin-download-policy-file`は、絶対パスの既存の通常JSONファイルを繰り返し指定する形式で、リンクは不可です。オブジェクトは正確に`{"plugin_id": "…", "download_policy": {…}}`の形にします。選択したサイトプラグインだけを対象にフィールドごとに合成し、コアが所有する`download_policy`スキーマで検証します。永続化はしません。`request_concurrency`、`chapter_concurrency`、`image_concurrency_per_chapter`、`preserve_image_start_order`は[設定](configuration.md#config-plugin-settings)を参照してください。

`--plugin-verification-override`の値は`bypass-all`、`bypass-catalog`、`bypass-signature`です。一時的な上書きであり、永続設定`security.plugin_verification`の値ではありません。永続設定の選択肢は`strict`・`warn`・`off`です。`plugin list`でも、`diagnostics`のためのソース発見・検証には上書きを**適用します**。カタログの登録内容である`plugins`一覧は変更せず、永続設定・カタログも書き込みません。

`--plugin ID`では、有効かつ非組み込みのサイトプラグインが、通常のmatcherでURLを受理する必要があります。`--force-plugin ID`は、有効な非組み込みサイトプラグインであることを確認した後でだけmatcherを省略します。両者は排他的で、明示的な`--fallback-generic=enabled|disabled`とも併用できません。`--fallback-generic`は、パーサーの既定値`auto`、`enabled`、`disabled`を受理し、`auto`は解決済み設定を維持します。`--existing-file`の値は`overwrite`・`skip`・`rename`・`error`です。

`--image-format`は`ORIGINAL`・`JPEG`・`PNG`・`WEBP`を受理し、その操作だけ`output.image_format`を上書きします。プラグインが`ImageResource.save_options.format`を指定した画像は、プラグインの形式を保ちます。`--force-image-format`も同じ値を受理しますが、`--image-format`と排他的で、プラグイン指定より優先します。`--force-image-format ORIGINAL`は、プラグインが生成した最終バイト列をコアで再エンコードせず保存します。HTTP応答をそのまま保存するモードではありません。強制的なエンコード形式を指定すると、プラグインの拡張子指定を解除し、出力拡張子を形式に合わせます。出力トークンは明示的な`%CONTENT_TITLE%`・`%CHAPTER_TITLE%`・`%CHAPTER_SUBTITLE%`・`%CHAPTER_NUMBER%`・`%IMAGE_INDEX%`を使い、`%NUM%`・`%TITLE%`・`%SUBTITLE%`は拒否します。[設定](configuration.md#config-storage-layout)を参照してください。

`--output-dir`は、通常のダウンロード一回の画像出力ルートだけを置き換えます。安全な絶対ディレクトリパスが必要で、プロファイルのログ・Cookie・更新状態は移動しません。`--directory-format`は、同じトークン検証を使って`output.directory_format`をその操作だけ上書きします。併用もでき、`--output-dir C:\Base --directory-format destination_%CHAPTER_NUMBER%`なら`C:\Base\destination_0001`以下へ保存します。`output.isolate_by_plugin=true`なら、選択した出力ルートと章ディレクトリの間に`<normalized-host>/<selected-site-plugin-id>/`を挿入します。どちらもYAMLは書き換えません。

<a id="cli-exit-status"></a>

| 終了コード | 意味 |
| --- | --- |
| 0 | 成功 |
| 1 | 一般的な失敗、または全画像の失敗 |
| 2 | 引数・設定の検証失敗 |
| 3 | 認証・秘密情報の取得失敗 |
| 4 | プラグインの発見・検証・実行の失敗 |
| 5 | 画像の部分成功、またはinspectでの一部画像要求の解決失敗 |
| 130 | `KeyboardInterrupt`。JSONは出力しない |

<a id="cli-io"></a>

<a id="command-io-and-side-effects"></a>

## コマンドの入出力と副作用

この表は、argparseが解析できる形式だけでなく、コマンド処理の挙動を示します。「読み取り」は結果に影響する入力、「書き込み」は永続的なファイルシステムへの影響です。`--json`は成功時の標準出力の表現だけを変え、入力や書き込み対象は変えません。

| コマンド | 位置引数 | 読み取り | 書き込み・操作 | 標準出力と標準エラー出力 | 終了コード |
| --- | --- | --- | --- | --- | --- |
| `download URL` | 絶対HTTP(S) URL一つ | 解決済み設定・レイヤー、選択プラグインのソース・カタログ、Cookie jar、一覧表示時だけ更新状態 | 利用者設定の初回作成・書き換えを行う場合がある。通常は画像・レポート・ログ・Cookie差分を書き込み、一覧表示は更新状態などを保存するが画像は取得しない | 通常は章・結果、JSONは一つの結果オブジェクト。診断とJSONモードのプラグインの`print()`は標準エラー出力 | 0、1、3、4、5 |
| `download URL --list-updated-urls` | 同じURL | 選択サイトの`UpdateProvider`と更新状態 | 同じ初期作成・書き換え。更新スナップショットとlockを保存し、画像出力は割り当てない | 追加・変更候補のURLを標準出力、集計を標準エラー出力。JSONは`updated_urls`と`removed` | 0、または操作の失敗 |
| `inspect URL` / `download URL --inspect-only` | 絶対HTTP(S) URL一つ | 解決済み設定・レイヤー、選択プラグインのソース・カタログ、プロファイルのCookieスナップショット | 設定作成・書き換え、Cookie変更・debugログ・更新状態・出力・レポートの保存、イベント、通知は行わない。サイトの`inspect()`と、`--manifest-only`以外では各`create_image_request()`・`AuthFlow.apply()`を順に実行し、送信しない実効要求を構築する。画像本文の取得・回復・加工・保存・出力割当は行わない。補助HTTPによるサーバー側の副作用は起こり得る | 標準出力は一つの検査JSON（`--json`なしでは整形JSON）。プラグインの`print()`は標準エラー出力へ転送する。既定の`--inspection-data url`は最小限の投影で、`http`・`all`にはクエリ・ヘッダー・Cookie・本文・トークン・`plugin_data`が含まれ得る。どちらの出力もログ・CI成果物・遠隔計測・問い合わせ・第三者へ転送しない | 0、一つ以上の解決失敗は5、操作失敗は2・3・4・1 |
| `doctor [--host HOST_OR_URL]` | 位置引数なし。hostはホスト名または絶対HTTP(S) URL | 解決済み設定・レイヤー、プラグインソース・カタログ、必要なら選択候補 | なし。レジストリだけを構築し、設定・カタログ・Cookie・画像ファイルを作成・書き換えしない | 人向けのレポート、または一つのJSONオブジェクト | 正常0、不健全なプラグイン診断4、処理済みエラー2・4 |
| `verify logs ABSOLUTE_PATH` | フォルダーまたはlog.log/debug.logファイル | 保存済みログだけ | なし | 記録済みエラーと判定不能項目、または一つのJSON文書 | 0・1・2・5 |
| `verify workflow RUN_ID --output-dir ABSOLUTE_PATH` | 正規UUIDの実行IDと出力ルート | 設定、プロファイルの履歴、保存済み画像と章ログ | 履歴の読み取りlockと親ディレクトリだけ | URL別の取得確認と独立したログ判定、または一つのJSON文書 | 0・1・2・5 |
| `config path` | なし | OS標準パスと同梱の基準設定の場所 | なし | パスのオブジェクト、または人向けのパス表示 | 0・2 |
| `config explain [--host HOST_OR_URL]` | なし | 選択した設定レイヤーと実行時オプション | なし | 有効値と由来のレポート | 0・2 |
| `config init [ABSOLUTE_PATH]` | メイン設定の絶対パスを省略または一つ指定 | テンプレートと保存先の安全性情報 | 未作成のメインYAMLを指定位置へ一つ作成。上書きしない | 作成パスのオブジェクト、またはメッセージ | 0・2 |
| `config profile init NAME` | プロファイル名一つ | メイン設定の存在とパスの安全性 | メイン設定がなければ作成し、その後で未作成のプロファイルYAMLを一つ作成 | メイン・プロファイルのパスのオブジェクト、またはメッセージ | 0・2 |
| `plugin list` | なし | 解決済み設定、カタログ、発見したソース単位 | なし。検証の上書きは発見時の診断だけに作用 | カタログの登録内容と診断 | 0・2・4 |
| `plugin install SOURCE`、`trust SOURCE`、`revoke ID`、`uninstall ID` | 操作に応じたソースディレクトリまたはmanifest ID | 解決済み設定、ソース・カタログ、確認入力 | 設定の初期作成・書き換え、トランザクションによるプラグインディレクトリ・カタログ変更 | カタログの登録内容、または削除結果のオブジェクト | 0・2・4 |
| `cookie export PATH` | 出力パス | 解決済み設定とプロファイルのCookie保存先 | 暗号化した書き出しファイル | 操作・対象のオブジェクト、またはメッセージ | 0・2・3 |
| `cookie import PATH` | 既存の暗号化書き出しファイルのパス | 解決済み設定、取り込みファイル、プロファイルのCookie保存先 | プロファイルの暗号化Cookieを合成・保存 | 操作・対象のオブジェクト、またはメッセージ | 0・2・3 |
| `cookie browser-import DOMAIN` | ブラウザーCookieのドメイン | 解決済み設定、ブラウザーのCookieデータベース、プロファイルのCookie保存先 | プロファイルの暗号化Cookieを合成・保存 | 操作・対象のオブジェクト、またはメッセージ | 0・2・3 |

`config init`は`--config`・`--profile`を受理せず、任意のパスは位置引数で指定します。`config profile init`はメイン設定の場所として`--config`を受理し、`--profile`・`--data-root`・`--plugin-root`を拒否します。処理側が拒否する指定では、最後の指定を採用するのではなく、主操作の前に引数エラーで終了します。上のコマンド表と[オプション表](#cli-options)で、受理・効果あり・効果なし・拒否を区別してください。

<a id="cli-cookie-actions"></a>

<a id="cookie-actions-and-browser-support"></a>

### Cookie操作とブラウザー対応

`cookie export PATH`と`cookie import PATH`は、制御端末で`Passphrase:`と表示して入力を求めます。パスフレーズはオプションや環境変数で受け付けません。`cookie browser-import DOMAIN`は、任意のブラウザー読み取り依存を`pip install 'image-downloader[browser-cookies]'`で追加してから使ってください。一致するブラウザーCookieを読み取り、現在のプロファイルへ合成します。3操作とも、成功JSONにはCookie値・パスフレーズ・ブラウザーの記録・暗号鍵を含めません。暗号化・合成・lockの挙動は[実行時の挙動](runtime-behavior.md#runtime-cookies)を参照してください。

<a id="cli-examples"></a>

<a id="common-invocations"></a>

### よく使う呼び出し方

```powershell
# 名前付きプロファイルで取得し、既存の出力を上書きしない。
image-downloader download https://example.test/gallery --profile comics --existing-file skip

# 自動処理向けに変更・新規URLだけを出力する。画像は取得しない。
image-downloader download https://example.test/gallery --list-updated-urls --json

# 設定を変更せず、サイトの上書き設定と選択したプラグインを検査する。
image-downloader doctor --host https://example.test/gallery --json

# プロファイルのレイヤーを作り、各値を決めたYAMLレイヤーを確認する。
image-downloader config profile init comics
image-downloader config explain --profile comics --host example.test --json

# その操作だけのプラグイン設定を渡す。YAMLには保存しない。
image-downloader download https://example.test/gallery --plugin-config com.example.site='{"page_size": 50}'

# 画像本文を取得せず、各画像の要求を解決する。既定では
# 各実効URLだけを出力する。この署名付きURLの出力もローカルに保管する。
image-downloader inspect https://example.test/gallery --plugin com.example.site --json

# 認証やエンコーディングの診断では、生のHTTP要求情報を含める。
image-downloader inspect https://example.test/gallery --inspection-data http --json

# manifest・画像メタデータ・プラグインデータの生の値をすべて含める。保管・共有しない。
image-downloader inspect https://example.test/gallery --inspection-data all --json

# manifestだけを表示し、create_image_request()は呼ばない。
image-downloader inspect https://example.test/gallery --manifest-only --json

# 一致するプラグインを1つ選び、この操作だけ画像要求の開始を直列化する。
image-downloader download https://example.test/gallery --plugin com.example.site \
  --plugin-download-policy com.example.site='{"preserve_image_start_order": true}'

# このダウンロードだけ、プラグインが要求する変換形式を上書きする。
image-downloader download https://example.test/gallery --force-image-format ORIGINAL
```

プラグインを対話なしで変更する場合は`--yes`を追加します。`config init`では確認への効果はなく、この指定によって安全性が増すわけではありません。自動化する側では、成功JSONの入力先を標準出力だけとし、標準エラー出力は診断用として保持してください。

<a id="json-and-stream-contract"></a>

## JSONと出力ストリームの契約

<a id="cli-json"></a>
<a id="cli-success-json"></a>

download と workflow の download 結果に `additional_files` を追加する。要素はファイル ID、hook 段階、operation／invocation／試行 ID、章番号・画像 index、phase、status、安全な相対パスと error のみを持ち、raw bytes、HTTP response、request header、plugin 内部状態を含めない。取得・保存・読み込み・callback の結果は別々の項目である。追加ファイルだけの失敗は画像の件数、終了コード、workflow 再試行判断を変更しない。

成功した`--json`コマンドは、標準出力にJSONオブジェクトを1つだけ出力します。downloadの章ログは標準出力に出さず、人間向けのエラー診断は標準エラー出力へ送ります。JSON形式のdownloadでは、プラグインのPythonの`print()`も標準エラー出力へ転送します。OSの標準出力ファイル記述子へ直接書き込むネイティブコードは、この制御の対象外です。

| コマンド | 成功時のオブジェクト |
| --- | --- |
| download | `{"saved": string[], "skipped": string[], "failures": ImageFailureJson[], "additional_files": AdditionalFileOutcomeJson[]}` |
| download `--list-updated-urls` | `{"updated_urls": string[], "removed": integer}` |
| inspect / download `--inspect-only` | `source_url`, `plugin_id`, `inspection_data: "url"|"http"|"all"`, `request_resolution: "resolved"|"manifest_only"`, `manifest`, `image_requests` |
| config path | `user_config`, `user_config_exists`, `package_baseline`, `default_data_root`, `default_plugin_root` |
| config explain | `source`, `main_config_kind`, `main_config`, `config_root`, `selected_profile`, `target_host`, `layers`, `effective`, `origins`, `runtime_overrides` |
| config init | `{"created_config": string}` |
| config profile init | `{"main_config": string, "created_main_config": boolean, "profile_config": string}` |
| cookieの各操作 | `{"operation":"cookie", "action":"export"|"import"|"browser-import", "target":string}` |
| plugin list | `plugin_root`, `plugins`, `diagnostics`, `catalog_warning` |
| plugin install/trust/revoke | 通常のpinまたはcontent pinの `CatalogEntryJson` |
| plugin uninstall | `plugin_id`, `removed_installation`, `removed_catalog` |
| doctor | `healthy`, `application`, `library`, `plugin_root`, `verification`, `configuration`, `paths`, `loaded_plugins`, `plugins` |

`ImageFailureJson`は、`kind`（`fetch|process|save`）、`exception`、`message`、`code`、`reason`、`output_path`、`response_url`、`http_status`、`transport`を持ちます。画像単位で該当しないフィールドは`null`です。saved・skippedのパスは絶対パス文字列です。失敗の`output_path`が設定済みのダウンロードルート内に由来する場合は、安全な相対パスとして表示し、ホストの絶対パスは出しません。`null`の場合もあります。

通常の固定情報を持つ`CatalogEntryJson`のフィールドは`id, kind, publisher, version, public_key, key_id, manifest_digest, file_tree_sha256, selection_priority, revoked`です。内容固定の場合は`id, kind, manifest_digest, content_digest, selection_priority, revoked`だけです。`diagnostics[]`の各項目は`{name, source, loaded, detail, warning}`であり、`warning=true`はロード失敗を意味しません。

<a id="cli-doctor-json"></a>

`doctor`の最上位構造は固定で、`healthy: bool`、`application: {version: string, root_directory: string}`、`library: {version: string, root_directory: string}`、`plugin_root: string`、`verification: string`、`configuration: object`、`paths: {name: string}`、`loaded_plugins: object[]`、`plugins: PluginDiagnosticJson[]`を持ちます。

`configuration`は、`config_file: string|null`、`source: string`、`main_config_kind: string`、`config_root: string`、`selected_profile: string`、`target_host: string|null`、`target_has_url_path: bool`、`effective: object`、`runtime: object`、`selection: object[]`、`layers: object[]`、`origins: {dotted_key: source_label}`を正確に持ちます。`effective`は専用の固定doctorスキーマではなく、[設定スキーマ](configuration.md#config-schema)の動的なマッピングに従います。`runtime`は`application_overrides: object`、`plugin_overrides: object`、`fallback_generic: "auto"|"enabled"|"disabled"`、`plugin_verification_override: string|null`です。`layers[]`の各要素は`{role: string, path: string|null, status: string}`です。`selection[]`は`id`・`matcher`・`matched`などを含む実行時の選択診断であり、カタログ項目ではありません。

`loaded_plugins[]`の各要素は、`id`、`kind`、`publisher`、`version`、`api_version`、`source`、`enabled`、`status`、`match_priority`、`capabilities`、`entry`、`config_file`、`key_id`、`manifest_digest`、`file_tree_sha256`、`verification`、`catalog`、`author_defaults`、`effective_config`を持ちます。`entry`は`{file: string, class: string}`、`catalog`は`null`または`{selection_priority, revoked, key_id, manifest_digest, file_tree_sha256}`です。`author_defaults`と`effective_config`は秘匿化された動的なマッピングです。`plugins[]`は`{name: string, source: string, loaded: bool, detail: string, warning: bool}`で、受理前に失敗したソース単位も含みます。機密値は置換しますが、置換後の固定文字列は保証しません。`null`は明示的にnull許容としたフィールドだけに使い、処理済みエラーの未知フィールドは後述のとおり省略します。

検査JSONの`inspection_data`は`url`・`http`・`all`のいずれかです。既定の`url`はmanifestの件数概要と、各要求の1始まりの`chapter_position`・`image_position`・`status`・AuthFlowとクエリとCookie jarの合成後の`effective_url`だけを含めます。実効要求の構築に失敗した場合はURLを推測しません。`http`は、返された完全な`RequestSpec`（`url`、`method`、`headers`、`cookies`、`referer`、`query`、`form`、`json`、`auth_required`、`retry_non_idempotent`）と`effective_request`（method、完全URL、順序・重複を保つヘッダー、実送信Cookie、Base64本文とバイト数）を追加します。`all`は、locator、referer、画像ヘッダー、保存設定、`original_filename`、メタデータを含む完全なmanifest・画像情報と、秘匿化前の`plugin_data`を追加します。`failed`と`partially_resolved`は、安定した`failure.code`・`failure.reason`・`failure.exception`・`failure.phase`を持ちます。後者の元の要求データは`http`・`all`だけで表示します。

`url`にも署名付きURLのクエリが含まれる場合があります。`http`と`all`は通常の出力安全性の例外であり、秘匿化前の認証情報、Cookie、本文、署名付きURLなどの秘密情報を含み得ます。Base64は本文を保護しません。検査の標準出力・標準エラー出力を、ログ、CI成果物、遠隔計測、共有端末、問い合わせ、第三者へ送らないでください。通常のダウンロード・更新出力と処理済みのJSONエラーは無害化します。`error`には`operation`・`exception`・`code`・`reason`・`message`が必須で、`response_url`・`http_status`・`output_path`・`stage`・`transport`は判明している場合だけ含めます。画像失敗には`image_url`も含める場合がありますが、これは安全に表示したmanifestのlocatorで、必ずしもHTTP URLではありません。通常の成功・失敗出力には、URLクエリ・フラグメントの秘密値、Cookie内容、パスフレーズ、ブラウザー記録、認証情報の参照元の生の値を出しません。

`--inspection-data`が変更するのは**標準出力に表示する項目だけ**です。`--manifest-only`を除き、 `url`、`http`、`all` は同じ manifest、`create_image_request()`、`AuthFlow.apply()`、effective request build を実行し、library result には effective request を組み立てられた各画像の raw preview（encoded body を含む）を保持する。したがって `url` は表示の露出を減らすが、request 解決の時間、plugin hook の補助 HTTP、サーバー側副作用、process 内の一時メモリを減らさない。現在は request 解決する画像数、chapter/image 範囲、preview body サイズの CLI 上限を提供しない。大きな manifest または大きな form/JSON body では `--manifest-only` を使うか、site plugin が manifest を有限の operation に分割する。`http`／`all` の Base64 body は元の byte 列より約 33% 大きい stdout を生成し得る。

inspection は registry-only の `doctor` とは異なり、sinkless な runtime を構築して profile cookie snapshot と HTTP gateway を初期化する。通常の output/state/log/notification は実行しないが、設定・plugin verification・filesystem path・cookie store の読取り／lock といったローカル初期化には依存する。matcher 候補だけを副作用最小で確認したいときは `doctor --host URL --json` を使う。

<a id="cli-errors"></a>
## Workflow状態表示・履歴・整理

```text
image-downloader state workflow
image-downloader state workflow list
image-downloader state workflow show URL [--plugin ID]
image-downloader state workflow history [URL] [--plugin ID] [--limit N]
image-downloader state workflow run RUN_ID
image-downloader state workflow prune [--dry-run]
```

`--config`、`--profile`、`--data-root`、`--json`はコマンド前後で指定できる。`--limit`はhistory専用の正の整数で、既定20件。`--plugin`はlist/show/historyで保存済みplugin IDを絞り込み、プラグインのインストール・検証・選択をしない。同じURLが複数pluginに属すると、指定なしではそれぞれ表示する。ダウンロード・再試行・inspection・Cookie操作・出力設定などの実行オプションは拒否する。`--dry-run`は通常workflowと`state workflow prune`のみで受理する。

ネットワーク通信・プラグイン読込み・Cookie読込み・設定の初期保存/rewrite・ファイルログ・通知は行わない。ロックファイルと親ディレクトリの作成は許容する。一度だけ保存情報を読み取り、進捗の自動更新、実行中判定、成果物検証は行わない。

現在状態は候補のURL、content ID、revision、完了フラグ、チェック日時と集計を持つ。未完了を失敗・未処理・実行中と推測しない。過去結果は別欄で表示し、過去の成功は現在の完了を保証しない。feedはplugin IDとSHA-256キーで識別する。履歴があれば伏字済み更新元URLを表示するが、既存の完了状態だけからURLを復元・推測しない。

listはfeedの概要、showは現在候補と最新実行の詳細を別欄で表示する。現在状態が未作成なら`current state: not available`と表示し、存在する空一覧の`candidates=0`と区別する。historyは実行ごとの概要、runはURL別結果・試行・周回・停止原因を整形して表示する。通常表示では辞書やJSONをそのまま出力しない。古い履歴の`plugin_error`から原因を推測せず、保存されたコードと安全な理由を表示する。

`state workflow list`に表示する実行結果は各feedの最新1件のみであり、過去の履歴を削除したことを意味しない。通常表示にもこの制限と履歴確認コマンドを案内する。過去の保存履歴は`py -m image_downloader state workflow history`で全feed分、`py -m image_downloader state workflow history URL`で特定URL分を確認する。既定は新しい順に20件で、`--limit N`で表示件数を変更できる。

通常表示のlist/historyにも初回周回の候補数・選択URL数と取得／対象0件の理由を簡潔に表示し、show/runでは別行で詳しく表示する。説明は通常workflowと共通で、現在状態から過去の理由を推測せず、保存済みの初回周回・scope・差分・確定できる選択理由のみを使う。初回実行かどうかは記録されていない。再試行で選択理由が混ざった場合、復元できない初回理由は断定しない。既存履歴の書換えやJSON項目の追加は行わない。

JSONは`operation="state"`、`resource="workflow"`、`action`を持つ一文書。list/showは`workflows`、historyは`runs`、runは`run`、pruneは`prune`を返す。pruneは削除予定RUN_ID、期限切れ件数、UTF-8ファイルサイズの整理前後、上限超過の有無を返す。URL・エラー・パスは既存の伏字処理を適用する。

list/showの各feedには`state_available`を追加する。falseでも互換性のため`checked_at=null`、空の`items`、0件の`summary`は維持する。この0件は「保存済みの空一覧」を意味しない。

表示・整理の成功は0（過去の失敗を表示しても0）、明示したURL/RUN_IDが存在しない場合は1、設定不正は2、保存情報の破損・ロック・I/O失敗は1、キャンセルは130。空の一覧・履歴一覧は正常終了する。通常workflowのJSONには`run_id`、`history_saved`、`history_warning`を追加する。履歴保存失敗は警告し、元のworkflow終了コードを変更しない。


## 保存済みログとworkflow成果物の検証

```powershell
image-downloader verify logs "C:\Downloads"
image-downloader verify logs "C:\Data\logs\debug.log" --json
image-downloader state workflow history --json
image-downloader verify workflow RUN_ID --output-dir "C:\Downloads" --json
```

`verify logs` は絶対パスを必須とし、フォルダー以下の `log.log` を再帰検査する。
単一ファイル指定は `log.log` または `debug.log`。章ログの `error:` / `error=`、
debugログの失敗イベント・`error=`・Python ERROR/CRITICALを構文として検出し、
タイトル・URL・detail中の単語 error、通常の再試行、WARNINGはエラーとしない。
全追記履歴を対象とし、後の成功で過去のエラーを消さない。件数はログ記録数であり、
同じ失敗の複数記録は統合しない。章ログは各ヘッダー区間の終了行 `done` も確認する。
`done` 自体は取得成功を意味しない。`--json` とヘルプ以外のオプションは拒否する。

`verify workflow` は標準UUID形式のRUN_IDと `--output-dir` を必須とする。
指定した実行の全周回の選択URLの和集合を検証し、最後に選択された周回以降の
最終試行が成功し、期待画像件数が1以上で、全画像の保存・スキップ記録に対応する
通常ファイルが存在して非空なら合格とする。全件スキップは `existing_files`、
新規保存を含む場合は `acquired` と表示する。未試行・部分成功・失敗・期待画像0件・
欠損・空ファイルは `not_acquired`。途中で候補から削除されたURLも除外しない。
`--config`、`--profile`、`--data-root`、`--json` はコマンド前後で指定できる。
更新確認済みで選択0件なら「取得対象なし」と表示する。更新確認が成立していない場合や
中断・タイムアウトは問題として報告し、残ったファイル記録も検査する。

workflow検証は出力ルート以下の全章ログも検査する。URL別取得確認とログ結果は
別欄であり、別実行の過去エラーも総合判定に影響する。
ログ未検出・空・形式不明・未完了区間・不正UTF-8・読取り失敗、危険なパス・リンク・
Windows reparse point、検査中の変更は `indeterminate`。旧履歴、履歴未保存・削除済み、
照合記録不足も判定不能となる。旧履歴の状態と保存・スキップ件数は参考情報として表示する。

今後のworkflow履歴は任意の `verification` フィールド（内部version=1）に、
周回別選択URLキー、URL別最終試行・期待画像件数、保存/スキップ種別と安全な相対パス、
記録の取得可否を残す。URLキーは伏字前URLのSHA-256で、表示が同じになるURLも区別する。
ルート外・危険なパスや伏字処理で変わるパスは照合可能な記録として保存しない。
旧履歴の読取りと既存の保持期限・容量制限は維持し、自動移行しない。

JSONは一文書で `operation="verify"`、`resource="logs|workflow"`、`status`、`logs` を持つ。
workflow検証は `run_id` と `workflow` も持つ。`logs` はファイル別 `errors` / `indeterminate` と集計、
`workflow` はURL別結果・保存/スキップ件数・問題一覧と集計を返す。
総合statusは `passed|failed|indeterminate`。終了コードは0=全検査合格、1=エラー記録または未取得、
2=引数/設定不正、5=判定不能、130=中断。既知の問題と判定不能が併存する場合は5を優先し、両方を表示する。

検証は通信・プラグイン実行・再取得・成果物変更・設定生成/書換えを行わない。
workflow履歴の読取りに必要なロックとその親ディレクトリは作成され得る。
「ログにエラー記録なし」は成果物の完全性を保証しない。画像のデコード・内容の正常性・
保存後のバイト変更・配信元との一致は検査しない。ログ出力失敗で記録されなかったエラーも検出できない。
