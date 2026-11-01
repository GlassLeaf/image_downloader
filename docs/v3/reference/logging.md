# ログリファレンス

<a id="logging-reference"></a>

## 章ログの作品・章情報ヘッダー

<a id="chapter-publication-header"></a>

通常downloadの各章の `log.log` は、次の順のヘッダーを記録します。

```text
---
url: https://example.com/work
content_title: 作品A
author: 作者名
title: 第1話
subtitle: 出会い
content_id: work-123
chapter_number: 1
chapter_id: chapter-1
content_published_at: 2026年10月
content_publication_date: 2026年11月
chapter_published_at: 2026-11-01 09:00 JST
chapter_publication_date: 2026-11-01
content.publisher: 出版社名
chapter.edition: 改訂版
---
```

先頭は `url` → `content_title` → `author` → `title` → `subtitle` の固定順です。
`url` は操作に渡された元URL、`content_title` と `author` は `DownloadManifest`、
`title` と `subtitle` は `Chapter` の情報です。`url`・`content_title`・`title`・`chapter_number`
は常に出力し、それ以外の標準項目がNoneまたは空文字列なら省略します。
日付・日時の表記は変換しません。

独自項目は `DownloadManifest.log_fields` を `content.<key>`、`Chapter.log_fields` を
`chapter.<key>` として標準項目の後に出力します。作品側、章側の順で、各側はキー順です。
空文字列の独自項目は省略します。表示設定はなく、`metadata` は自動転記しません。
値は秘密値マスク、制御文字除去による単一行化、1024文字の制限（超過時は末尾に `…`）を受けます。
フォルダ名の書式とは独立しています。

`DownloadLogger.chapter_header()` の既存引数に、省略可能なkeyword引数 `content_title`、`author`、
`extra_fields: Mapping[str, str] | None` を追加しています。`extra_fields` は先頭5項目以外の
上記標準項目と `content.<key>` / `chapter.<key>` を受け付け、値は文字列に限ります。
無効なキー・先頭5項目の上書きはValueError、不正な値の型はTypeErrorです。
既存の引数だけの呼出しは旧形式を出力できます。

`verify logs` は旧形式・新形式・追記による両者の混在を検証します。既存ログは書き換えず、
新しいdownloadは新形式を追記します。独自項目の値にある `error:` などは失敗記録として解釈しません。

## workflowの実行記録ファイル

<a id="workflow-execution-files"></a>

<a id="workflow-output-locations"></a>

### 保存先とURLの確認

`<profile>`は`<data_root>/profiles/<profile名>`を表す。画像・付属ファイルは通常downloadと同じ
`<profile>/downloads`に保存し、`--output-dir`で変更できる。章ディレクトリ名は通常の
`output.directory_format`に従い、workflow専用の画像ディレクトリは作らない。

| 内容 | 保存先 | URLの扱い |
| --- | --- | --- |
| 各周回の取得予定 | `<profile>/logs/workflow/<UTC開始日時>_<RUN_ID>_runN.plan.json` | `selected_urls`に対象ページの原文URL・選択理由を保存 |
| workflowの逐次記録 | 同じ開始日時・RUN_IDの`.jsonl`／`.log` | `url_started`、`url_finished`、`url_interrupted`に対象ページの原文URLを保存 |
| 通常の診断ログ | `<profile>/logs/debug.log` | gatewayの`event=request_started module=http`に要求URL、画像取得の`download_finished`に応答URLを伏字処理して記録 |
| 章ログ | 画像の各章ディレクトリ内の`log.log` | `download:`に画像locator、`save:`に保存先。画像失敗は`image_url:`などを伏字処理して記録 |
| 実行履歴 | `<profile>/state/workflow-history.json` | URL単位の結果・周回・停止原因を伏字処理して保存 |

予定ファイルは取得成功の証拠ではない。対象ページの処理開始は`url_started`で確認するが、
manifest取得前でも出るため、画像のHTTP要求や保存成功を意味しない。予定に載っていても、
中断までに着手しなかったURLには`url_started`がない。
章ログの画像locatorはプラグインによって実際のHTTP要求URLと異なる場合がある。
`debug.log`も伏字・ログ書込み失敗・プラグイン独自の通信等により、原文URLを完全に復元できる通信台帳ではない。

### 予定・逐次ログの保存規則

通常workflowのCLI・APIはprofileの`logs/workflow`に各周回の全取得予定を必須保存する。
UTC開始日時と既存RUN_IDから`20261005T120000123456Z_<RUN_ID>_run1.plan.json`などを作る。
run1はAPIの周回番号0、run2は1に対応し、過去の予定は更新しない。
形式バージョン1、開始・確定日時、更新元URL・plugin ID、scope、比較履歴の有無、
候補数、返却順の重複排除した選択URL全体・理由、追加／変更／削除の差分を記録する。
理由は`all`・`added`・`changed`・`unfinished`で、追加周回の再試行対象も示す。
未完了だけでは未取得か失敗かを断定できない。

一時ファイルをflush・fsyncしてから同名ファイルを置き換えない原子的な公開を行う。
公開には同じファイルシステム内のハードリンクを使用し、未対応の保存先では保存失敗となる。
保存成功後にその周回のmanifest・画像取得を開始する。対象0件でも空の予定を保存する。
更新確認・対象選択が未確定なら予定は作らない。予定保存失敗は`workflow_plan_log_error`、終了コード1で停止する。
更新確認・認証通信、比較状態保存は先に行われるため、予定保存失敗時も最新一覧・未完了状態が残る場合がある。
以前の予定・成果物は保持する。

同じ日時・RUN_IDの`.jsonl`と`.log`には同じイベント番号で逐次追記する。
実行開始、周回開始・予定保存、URL開始・終了・中断、再試行待機、対象削除、実行終了を記録する。
開始しなかったURLは終了記録の未処理と試行回数で、中断したURLは`url_interrupted`で区別する。
中断原因が未確定なら終了記録でキャンセル／期限終了を示す。
CLIのservice closeで結果が変われば同じファイルへ`run_corrected`を追記する。
各形式の作成・追記失敗は固定の安全な警告を出し、その形式への追記を停止する。
取得・他方の記録・既存履歴保存は続行する。APIと通常JSONの`workflow_log`で各形式の欠落を確認できる。
falseの保存状況は途中までの記録が存在する場合も含む。無効な形式のパス・保存状況はnullとなる。

**専用ファイルは原文URLを含む。URL内の認証情報・署名付きクエリも残り得るため共有・保管先に注意する。**
Cookie、認証ヘッダー、任意の例外文字列、画像データは記録しない。改行はJSONエスケープし形式を保つ。
履歴・コンソール・通常`--json`には従来の伏字を適用する。
`--output-dir`で記録先は変わらず、`--json`・`--no-console-log`も専用記録を無効化しない。
`workflow_logging.progress_enabled: false`または`--workflow-progress-log disabled`は逐次ログのみを無効化する。
予定ファイルは必須のままで、dry-runは予定・逐次ログを一切作らない。一括結果は既存`--json`を使用する。

自動削除・ローテーションはなく、`workflow_history`の保持期間・容量や`state workflow prune`は適用しない。
ログは増え続けるため手動で整理する。開始済み書込みはキャンセル時も確定まで待つ。
OS I/O停止によって記録処理も無期限に待つ可能性があり、期限を指定してもプロセスの終了時刻は保証されない。
強制終了・電源断では最後の記録やJSONL・テキストの一致を保証しない。
終了記録がないことを実行中の証拠として扱わない。

<a id="workflow-interruption-records"></a>

### 例外・キャンセル時の記録

通常実行の開始後は、例外・協調的なユーザーキャンセルでも、書込み可能な有効な逐次ログへ
`run_finished`を記録する。URL処理中なら`url_interrupted`も記録し、終了レコードには
`outcome`、`exit_code`、`cancelled`、`timed_out`、`stop_error`、各URLの状態・保存／スキップ／失敗数・
試行回数を含める。未着手の対象は`unprocessed`・`attempt_count: 0`で区別する。
個別URLの通常の`PluginError`は全体中断ではなく`url_finished`に失敗を記録する。
発生箇所ごとの継続・停止条件は[workflowのエラー境界](runtime-behavior.md#workflow-error-boundaries)を参照する。

以下は終了レコードの抜粋であり、完全なJSONLレコードには日時・RUN_ID・イベント番号なども含まれる。

```json
{"event":"run_finished","outcome":"cancelled","exit_code":130,"cancelled":true,"timed_out":false,"stop_error":null}
{"event":"run_finished","outcome":"stopped","exit_code":4,"cancelled":false,"timed_out":false,"stop_error":{"code":"plugin_error","reason":"plugin operation failed","exception":"PluginError","http_status":null}}
```

任意の例外メッセージは専用ログに保存せず、`stop_error`はコード・固定の理由・例外クラス・HTTP statusに限定する。
追加周回の期限到達は`outcome: "timed_out"`、`timed_out: true`で、ユーザーキャンセルと区別する。
終了処理中のキャンセルやCLIのservice closeで結果が変わった場合は`run_corrected`を追記する。
実行履歴も保存を試みるが、専用ログとは独立しており、履歴保存失敗は警告と`history_saved`で確認する。

引数・設定検証やservice構築など、workflow実行開始前の失敗では専用の終了記録は作らない。
逐次ログを無効化した場合、dry-run、ログ書込み失敗、強制終了・電源断にも上記の記録保証はない。
通常終了・例外停止・キャンセル時の画面表示は[CLIリファレンス](cli.md#workflow)を参照する。

<a id="diagnostic-logging-api"></a>

## 診断ログAPI

`image_downloader.observability.logging.__all__`は安定した公開APIです。ログは診断用です。結果の判定には`DownloadResult`、例外、CLIの終了コードを使ってください。

各`DownloadLogger`メンバーを含む正確なコンストラクター・呼出しシグネチャは、[公開シグネチャ一覧](api-signatures.md#logging-api)に1行ずつ掲載しています。このページでは配送と失敗時の扱いを定義します。

| API | シグネチャ・動作 |
| --- | --- |
| `LogRecord(message, chapter_id=None, metadata={}, trusted=False)` | `formatted() -> str`は安全な表示用テキストを返します。 |
| `ChapterFailureRecord(url, response_url, path, stage, image_index, http_status, code, reason, exception_type, message, transport=None)` | 章の失敗グループを表す不変の入力です。 |
| `LogSink` | `async write(record) -> None`; `async close() -> None`。独自の出力先では並行する書込みを安全に扱います。 |
| `ChapterFileSink(path=None, *, filesystem=None, relative_path=None)` | path、またはfilesystemと相対パスの組合せが必要です。`async write/close`はファイル操作を直列化します。 |
| `DebugFileSink` | `ChapterFileSink`にUTC日時・章・モジュールを追加します。 |
| `ConsoleSink(stream=None)` | `async write(record) -> None`。 |
| `DownloadLogger(sinks=None)` | 安全化・収集・章の登録と、下記の記録・終了メソッドを管理します。 |

`DownloadLogger.configure_safety(logging, output_root=None)`、`safe_url(value)`、`safe_locator(value)`、`set_chapter_summary_console(enabled)`、`begin_python_log_capture(namespaces)`、`end_python_log_capture()` は同期の設定メソッドです。`flush_python_log_capture()`、`core(...)`、`log(...)`、`chapter_header(...)`、`chapter_download(...)`、`chapter_save(...)`、`chapter_error_group(...)`、`chapter_done(...)`、`close_chapter(...)`、`error_detail(...)`、`debug(...)`、`close()` は**非同期**で、awaitが必要です。

`core(event, *, module, chapter_id=None, url=None, path=None, method=None, status=None, bytes_count=None, count=None, plugin_id=None, attempt=None, action=None, error=None, url_is_locator=False, debug=False, include_chapter=False)` はevent・moduleの許可一覧に限定し、任意の生メッセージは受け取りません。`url_is_locator=True` は、画像のlocatorをURLとして再解析せず安全に表示する、実行時の内部指定です。章の重複登録と収集の重複開始は`RuntimeError`です。呼出し元は、終了と書込みを競合させないでください。

`safe_log_text(value, *, maximum=4096)`、`mask_log_text(value, *, safe_query_parameters=None, safe_fragment_parameters=None)`、`safe_url(value, *, safe_query_parameters=None, safe_fragment_parameters=None)`、`safe_locator(value, *, safe_query_parameters=None, safe_fragment_parameters=None)`、`safe_relative_path(value, output_root)`、`safe_exception_name(value)` は秘密情報・URL・locator・パス・例外名を安全に整形します。`safe_locator` はHTTP(S) URLを `safe_url` と同じ規則で処理し、それ以外を `locator:` 付きの伏字テキストとして表示します。これらは、生の秘密情報を保存してよい値へ変換するものではありません。

画像のイベント・ログの `url` は `ImageResource.url` のlocatorを元にするため、絶対HTTP(S) URLとは限りません。受信側へ渡す値は安全な表示用に整形済みで、実際の取得URLや再利用可能なlocatorを表すとは限りません。プラグインはlocatorに秘密値を置かないでください。

ログ出力先・イベント受信処理・ログ収集の後始末の失敗は、安全な診断警告として抑制する場合があります。ダウンロードの主結果・例外・終了コードは変更しません。

基底クラスの`LogSink.write()`は`NotImplementedError`を送出します。独自の出力先では、両方の非同期メソッドを実装し、並行する書込みに対応してください。`ChapterFileSink`は、`path`も`filesystem`と`relative_path`の組合せも指定しない構築を`ValueError`で拒否します。出力先を直接操作すると、ファイルシステムやストリームの例外が発生する場合があります。失敗の分離は、`DownloadLogger`が管理下の出力先へ配送・終了する際に適用します。その場合は安全な診断警告を記録し、すでに確定した主結果を維持します。
