# Logging reference

<a id="logging-reference"></a>

## Workflow execution files

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
通常終了・例外停止・キャンセル時の画面表示は[CLI reference](cli.md#workflow)を参照する。

## Diagnostic logging API

`image_downloader.observability.logging.__all__` is stable. Logging is diagnostic only; use `DownloadResult`, exceptions, and CLI exit status as the result source of truth.

Exact constructor and callable signatures, including each `DownloadLogger` member,
are listed one per row in the [public signature index](api-signatures.md#logging-api).
This page defines their delivery and failure semantics.

| API | signature / behavior |
| --- | --- |
| `LogRecord(message, chapter_id=None, metadata={}, trusted=False)` | `formatted() -> str` returns safe display text. |
| `ChapterFailureRecord(url, response_url, path, stage, image_index, http_status, code, reason, exception_type, message, transport=None)` | immutable input for a chapter failure group. |
| `LogSink` | `async write(record) -> None`; `async close() -> None`。custom sink は concurrent write を安全に扱う。 |
| `ChapterFileSink(path=None, *, filesystem=None, relative_path=None)` | path または filesystem+relative path が必要。`async write/close` は file を直列化する。 |
| `DebugFileSink` | `ChapterFileSink` に UTC timestamp/chapter/module を追加する。 |
| `ConsoleSink(stream=None)` | `async write(record) -> None`。 |
| `DownloadLogger(sinks=None)` | safety/capture/chapter registration と、下記 record/close method を所有する。 |

`DownloadLogger.configure_safety(logging, output_root=None)`、`safe_url(value)`、`safe_locator(value)`、`set_chapter_summary_console(enabled)`、`begin_python_log_capture(namespaces)`、`end_python_log_capture()` は synchronous configuration method である。`flush_python_log_capture()`、`core(...)`、`log(...)`、`chapter_header(...)`、`chapter_download(...)`、`chapter_save(...)`、`chapter_error_group(...)`、`chapter_done(...)`、`close_chapter(...)`、`error_detail(...)`、`debug(...)`、`close()` は **async** で await が必要である。

`core(event, *, module, chapter_id=None, url=None, path=None, method=None, status=None, bytes_count=None, count=None, plugin_id=None, attempt=None, action=None, error=None, url_is_locator=False, debug=False, include_chapter=False)` は event/module allowlist に限定し、任意 raw message は受け取らない。`url_is_locator=True` は画像 locator を URL として再構文解析せず安全に表示する内部 runtime 指定である。duplicate chapter registration と duplicate capture start は `RuntimeError`。caller は close と write を race させてはならない。

`safe_log_text(value, *, maximum=4096)`、`mask_log_text(value, *, safe_query_parameters=None, safe_fragment_parameters=None)`、`safe_url(value, *, safe_query_parameters=None, safe_fragment_parameters=None)`、`safe_locator(value, *, safe_query_parameters=None, safe_fragment_parameters=None)`、`safe_relative_path(value, output_root)`、`safe_exception_name(value)` は secret、URL、locator、path、exception name を安全に整形する。`safe_locator` は HTTP(S) URL を `safe_url` と同じ規則で処理し、それ以外を `locator:` 付きの masked text として表示する。これらは raw secret を保存してよい値に変換するものではない。

画像 event/log の `url` は `ImageResource.url` の locator を元にするため、absolute HTTP(S) URL とは限らない。observer が受ける値は safety renderer 済みで、実際の fetch URL や再利用可能な locator を表すとは限らない。plugin は locator に秘密値を置かない。

sink/observer/capture cleanup の failure は safe diagnostic warning として抑制され得る。主 download result、exception、exit status は変更しない。

`LogSink.write()` on the base class raises `NotImplementedError`; an application
providing a custom sink must implement both async methods and tolerate concurrent
writes. `ChapterFileSink` rejects construction without either `path` or both
`filesystem` and `relative_path` with `ValueError`. Direct sink I/O can still
raise its own filesystem/stream exception; failure isolation applies when
`DownloadLogger` delivers or closes managed sinks, where it records a safe
diagnostic warning and keeps the already determined primary result unchanged.
