# v3 実装計画と現状照合

照合日: 2026-10-03。これは保守向けの計画であり、将来機能の公開仕様ではない。
現行の仕様は [Reference](../README.md)、作業単位・依存関係・完了基準は
[開発タスク](development-tasks.md) を参照する。v2 の計画・タスクは履歴資料として保存し、現行計画の正本にしない。
今回の変更は文書のみで、以下の将来タスクを実装・テスト追加したことを意味しない。

## 現状と進め方

「実装済み」はソースで確認した機能、「補強必要」は実装があり設計確認や検証追加が必要な領域、
「未実装」は対象機能がない領域、「設計待ち」は仕様判断が先行する領域を表す。
テストへの参照は存在と確認対象を示す。今回実行したテストと結果は末尾に分けて記録する。

| 領域 | 現状 | 根拠と差分 | タスク |
| --- | --- | --- | --- |
| SMTP 送信 | 実装済み／補強必要 | [notifications](../../../src/image_downloader/observability/notifications.py) に SMTP、TLS、認証、集約送信がある。[通知テスト](../../../tests/v3/observability/test_notifications_v3.py) は fake SMTP による TLS 分岐などを確認。ローカルサーバーとの統合・詳細な失敗条件は補強対象。 | SMTP-001～003 |
| イベントと通知 | 実装済み／補強必要 | [events](../../../src/image_downloader/observability/events.py) の全40定義を下表で照合。定義・core 発火・通知への対応は一致するとは限らない。 | EVT-001～003 |
| workflow 通知 | 実装済み／補強必要 | [workflow retry テスト](../../../tests/v3/application/test_workflow_retry.py) に最終失敗通知・秘匿化・通知中キャンセルの検証がある。各試行と最終通知を別に扱う。 | EVT-002、SMTP-001 |
| 状態・実行履歴管理 | 実装済み | [WorkflowStateService](../../../src/image_downloader/application/workflow_state.py) と [CLI](../reference/cli.md) の `state workflow list/show/history/run/prune` が対象。成果物検証コマンドとは別。 | 基盤として参照 |
| 成果物台帳・検証・一覧・詳細 | 未実装／設計待ち | [workflow reporting](../../../src/image_downloader/application/workflow_reporting.py) は URL 単位の集計・失敗群を記録し、成功画像すべての台帳ではない。[既知の制限](../reference/runtime-behavior.md) に保存成果物の破損・削除を検知しない境界がある。 | ART-001～004 |
| リファクタリング | 設計待ち | [Architecture](architecture.md) の責務境界と画像比較維持方針を基準に、下記候補を評価する。 | REF-001 |

順序は現状・イベント棚卸し → SMTP／イベントのテスト補強 → 成果物台帳設計 → 管理コマンド →
根拠のあるリファクタリングとする。SMTP とイベントの検証は棚卸し後に独立して進められる。

## SMTP 通知の現行設計と補強計画

現行の [設定モデル](../../../src/image_downloader/configuration/models.py) は `notification.enabled=false` が既定。
有効な `notify_on` の経路についてだけ送信要件を検証し、カテゴリ別 `routes` がなければ `methods` を使う。
email には host、from、to が必要。username がある場合、非空の `IMAGE_DOWNLOADER_SMTP_PASSWORD` を優先し、
なければ keyring の `credential_service` と username から取得する。パスワードがなければ送信を失敗させる。

`use_tls=true` かつ port 465 は implicit TLS、それ以外の TLS 有効時は SMTP 接続後に STARTTLS。
TLS 無効時は通常 SMTP。TLS context は `ssl.create_default_context()`、接続の timeout 引数は30秒。
送信は `asyncio.to_thread()` で行い、専用の総処理時間上限や自動再送は実装されていない。
async 呼び出しのキャンセルが SMTP スレッドを即時停止する保証もない。

本文は flush 時にカテゴリ・reason code ごとの件数を集約し、画像失敗の例を章・画像等で並べて最大5件、
例の URL は256文字、本文は4096文字までに制限する。カテゴリ固有経路は送信先 channel を選ぶために使われ、
各 channel には同じ集約本文を送る。カテゴリ別に本文を分離する実装ではない。
通知成功・失敗のイベント自体は通知収集対象にしない。

将来の設計・テスト補強では、接続拒否、TLS 証明書不正／STARTTLS 不可、認証失敗、資格情報欠落、
timeout、送信者／宛先拒否、DATA 拒否、送信中切断を扱う。
特に `send_message()` の一部宛先拒否の戻り値を現在は検査していないため、全成功／部分送信／失敗の
判定と表示を SMTP-001 で決める。送信後の応答喪失時は到達状況が不確定なので、再送を既定で追加しない。
再送・重複抑止を追加する場合の単位、上限、履歴、秘密情報非保存を同タスクで判断する。

基本は fake transport の単体テストと、外部ネットワークに依存しない loopback SMTP 統合テスト。
ローカル TLS 証明書、試験用資格情報、日本語件名・本文、複数宛先の受信内容を検証する。
実メールサービスは任意の手動検証とし、実パスワード・受信者情報をリポジトリやテスト出力に残さない。

## 全イベントの発火・通知・テスト対応

根拠は [service](../../../src/image_downloader/application/service.py)、
[workflow](../../../src/image_downloader/application/workflow.py)、[EventBus](../../../src/image_downloader/observability/events.py)、
[NotificationService](../../../src/image_downloader/observability/notifications.py)。
以下は core の自動発火であり、利用者が `EventBus.emit()` を呼ぶ場合は別。
「なし」は通知カテゴリへの対応がないこと、「未発火」は core の発火点がないことを意味する。
download の開始／終了は `DownloadService.run()` と workflow の `download()`、
結果分類は `_run_operation()`、operation 例外分類は `_record_operation_failure()` と workflow の `_report_workflow_failure()`、
画像段階は `_run_chapter()` 内の `run_one()`、更新系は `check_updates()`／`_check_updates_operation()` と
workflow の `check()`、通知送信結果は `NotificationService._deliver()` が発火箇所である。

payload の略号（値は存在時のみ）:

- U: `url`。画像系は opaque locator も可。安全化済みで、成功イベントは通常これだけを持つ。
- F: U と `response_url/path/stage/chapter_id/image_index/http_status/error_code/error_reason/error_class/transport`。画像段階の失敗情報。
- O: U と `response_url/path/http_status/error_code/error_reason/error_class/operation`。download operation 失敗。
- W: U と `error_code/error_reason/error_class/operation=workflow`。workflow 自体の失敗。
- E: U と `response_url/path/http_status/error_code/error_reason/error_class/operation=update`。更新チェック失敗。
- C: `channel`、例外時は `error/error_class`。error は EventBus で秘匿化する。

通知内容の略号:

- I: 画像失敗カテゴリ件数・reason code 件数と最大5件の安全な画像段階例。
- G: カテゴリ件数・reason code 件数、先頭例の operation/reason/exception（存在時）。成功カテゴリも G の形式で、画像ごとの保存一覧は送らない。
- —: 自動通知なし。カテゴリ付きでも未発火のものは、外部 emit 時のみ I/G の集約対象になり得る。

テスト対応（識別子は以下の実ファイル内の関数名）:

- T1: [event notification contract](../../../tests/v3/observability/test_event_notification_contract.py)。正常順序、段階失敗一度、混在結果、transport context、保存衝突、画像認証失敗、キャンセル、経路と false sender。
- T2: [notifications v3](../../../tests/v3/observability/test_notifications_v3.py)。手動 emit の収集・秘匿化、経路、集約上限、runtime 表示、auth opt-in、observer 隔離、SMTP TLS。
- T3: [runtime hardening](../../../tests/v3/application/test_runtime_hardening.py)。`test_update_snapshots_report_added_changed_and_removed_entries`。
- T4: [workflow](../../../tests/v3/application/test_workflow.py)。plugin 通知重複防止と `test_workflow_state_failure_emits_storage_event_once`。
- T5: [workflow retry](../../../tests/v3/application/test_workflow_retry.py)。最終通知・再試行・秘匿化・キャンセル。

「不足」はイベント固有の直接的な発火／非発火の受入テストを棚卸し・追加する対象。
既存の機能テストがあることを、payload・全発火経路の網羅とみなさない。

| EventName / 値 | core の箇所・条件、順序・回数 | payload | 通知カテゴリ／内容 | 既存テストと不足 |
| --- | --- | --- | --- | --- |
| BEFORE_AUTH / `on_before_auth` | 未発火 | — | なし／— | 非発火保証が不足 |
| AUTH_FAILED / `on_auth_failed` | service の operation 分類、workflow の失敗分類。画像段階で報告済みなら download の分類発火を抑止。該当失敗に1回。 | O/W | auth_error／G | T4/T5 は関連経路。直接分類が不足 |
| AUTH_SUCCESS / `on_auth_success` | 未発火。HTTP 応答成功を認証成功とみなさない。 | — | なし／— | 非発火保証が不足 |
| AFTER_AUTH / `on_after_auth` | 未発火 | — | なし／— | 非発火保証が不足 |
| COOKIE_STORE_ACCESS / `on_cookie_store_access` | 未発火 | — | auth_cookie_store_access／G | 手動収集・core 非発火が不足 |
| CREDENTIAL_STORE_ACCESS / `on_credential_store_access` | 未発火 | — | auth_credential_store_access／G | 手動収集・core 非発火が不足 |
| LOGIN_SUCCESS / `on_login_success` | 未発火 | — | auth_login_success／G | T2 の auth opt-in。core 非発火が不足 |
| SESSION_REFRESH_SUCCESS / `on_session_refresh_success` | 未発火 | — | auth_session_refresh_success／G | 手動収集・core 非発火が不足 |
| BEFORE_DOWNLOAD / `on_before_download` | service.run または workflow の各対象試行の開始時に1回 | U | なし／— | T1 正常順序、T5。試行別回数は補強 |
| AFTER_DOWNLOAD / `on_after_download` | 開始した download の finally で1回。失敗・cancel も対象。通知 flush より前。 | U | なし／— | T1 正常／段階失敗／cancel、T5 |
| DOWNLOAD_COMPLETE / `on_download_complete` | DownloadResult 作成後、結果分類イベントの後に1回。全失敗 result も対象。例外・cancel では発火しない。 | U | なし／— | T1 正常／段階失敗。空 result は補強 |
| DOWNLOAD_FAILED / `on_download_failed` | 保存・skip がなく失敗がある result、または operation 例外。段階／分類イベントの後に1回。cancel は対象外。 | U または O | なし／— | T1 段階失敗／cancel。operation 分類は補強 |
| DOWNLOAD_SUCCESS / `on_download_success` | result.failures が空。COMPLETE の前に1回。画像0件もこの条件に含む。 | U | download_success／G | T1 正常。空・skip の専用確認は補強 |
| DOWNLOAD_PARTIAL_SUCCESS / `on_download_partial_success` | failure と saved/skip が混在。COMPLETE の前に1回。 | U | download_partial_success／G | T1 混在結果、T5 最終通知 |
| BEFORE_FETCH / `on_before_fetch` | 未保持画像 job の取得前に1回。gateway 内の HTTP retry ごとではない。 | U | なし／— | T1 正常順序。保持・HTTP retry は補強 |
| FETCH_SUCCESS / `on_fetch_success` | gateway の取得成功後に1回。process 開始前。 | U | なし／— | T1 正常順序 |
| FETCH_FAILED / `on_fetch_failed` | 画像 fetch 例外を段階分類して1回。fail-fast でも二重分類しない。 | F | fetch_error／I | T1 段階失敗、HTTP context、画像認証、T5 |
| PARSE_STARTED / `on_parse_started` | 未発火 | — | なし／— | 非発火保証が不足 |
| PARSE_SUCCESS / `on_parse_success` | 未発火 | — | なし／— | 非発火保証が不足 |
| REQUEST_STARTED / `on_request_started` | 未発火。同名のログは EventBus 発火ではない。 | — | なし／— | 非発火保証が不足 |
| REQUEST_SUCCESS / `on_request_success` | 未発火 | — | なし／— | 非発火保証が不足 |
| REQUEST_FAILED / `on_request_failed` | 未発火。workflow 最終結果の通知内部分類に使うが observer へ再 emit しない。 | — | fetch_error／I | T5 最終通知は関連経路。手動収集・非発火保証は補強 |
| IMAGE_PROCESS / `on_image_process` | 未発火。STARTED/SUCCESS の別名として発火しない。 | — | なし／— | 非発火保証が不足 |
| IMAGE_PROCESS_STARTED / `on_image_process_started` | FETCH_SUCCESS 後、pipeline 開始前に1回 | U | なし／— | T1 正常順序／段階失敗 |
| IMAGE_PROCESS_SUCCESS / `on_image_process_success` | pipeline 成功後、BEFORE_SAVE 前に1回 | U | なし／— | T1 正常順序 |
| IMAGE_PROCESS_FAILED / `on_image_process_failed` | process 段階例外で1回。save 段階へ進まない。 | F | process_error／I | T1 段階失敗／decode context、T2 集約 |
| BEFORE_SAVE / `on_before_save` | process 成功後、allocation 前に1回。skip の場合も発火。 | U | なし／— | T1 正常。skip 専用確認は補強 |
| SAVE_SUCCESS / `on_save_success` | 書込み・commit 後に1回。skip、保持画像は発火しない。 | U と path | なし／— | T1 正常／混在、T5。保持非発火は補強 |
| SAVE_FAILED / `on_save_failed` | allocation／write／commit 段階例外で1回 | F | save_error／I | T1 段階失敗／保存衝突／fail-fast |
| PARSE_FAILED / `on_parse_failed` | 未発火。plugin の parse 失敗を自動的にこのイベントにしない。 | — | parse_error／G | 手動経路・非発火保証が不足 |
| PLUGIN_FAILED / `on_plugin_failed` | operation の PluginError 分類時に1回。画像段階報告済みなら抑止。 | O/W | plugin_error／G | T4 重複防止、T5。payload は補強 |
| CONFIG_FAILED / `on_config_failed` | 実行中 operation の ConfigurationError 分類時に1回。compose 前の設定エラーを包括するものではない。 | O/W | config_error／G | 直接分類・compose 前非発火が不足 |
| STORAGE_FAILED / `on_storage_failed` | operation の StorageError 分類時に1回。画像保存報告済みなら SAVE_FAILED に留める。 | O/W | storage_error／G | T4 state failure、T1 save 分類。payload は補強 |
| RUNTIME_FAILED / `on_runtime_failed` | operation の上記分類以外の例外で1回 | O/W | runtime_error／G | T2 手動 runtime 表示。core 分類は補強 |
| UPDATE_FAILED / `on_update_failed` | _check_updates_operation の非 cancel 例外に1回、FINISHED より前 | E | update_error／G | update 機能テストあり。直接順序・payload は不足 |
| UPDATE_CHECK_STARTED / `on_update_check_started` | check_updates、workflow の各 feed check の開始時に1回 | U | なし／— | T3。workflow 各周回・失敗は補強 |
| UPDATED_URL_FOUND / `on_updated_url_found` | state に適用した added/changed の各 change に1回。removed には発火しない。 | U | なし／— | T3。payload・同一 URL の複数 change は補強 |
| UPDATE_CHECK_FINISHED / `on_update_check_finished` | 開始した check の finally で1回。失敗・cancel も対象。 | U | なし／— | T3。失敗・cancel は補強 |
| NOTIFICATION_SENT / `on_notification_sent` | sender が truthy を返した channel の送信後に1回 | C | なし／— | T2。各 channel の直接回数は補強 |
| NOTIFICATION_FAILED / `on_notification_failed` | sender 欠落・例外・false 結果の channel に1回 | C | なし／— | T1 false sender、T2 例外。欠落・複数 channel は補強 |

順序は同じ画像 job 内・同じ operation 内の局所的なもの。章／画像並列実行に全体の固定順序を要求しない。
workflow が保持した画像 outcome は fetch/process/save を再実行せず、これらの画像イベントも再発火しない。
workflow は試行中の download 通知収集を defer し、各対象 URL の最終結果を `workflow_item()` で直接収集・flush する。
最終通知のために他 observer へイベントを再 emit しない。removed item は通知しない。
認証アクセス／成功の4カテゴリは defer の除外対象だが、core の自動発火点は現在ない。
dry-run は通知を送らない（[preview テスト](../../../tests/v3/application/test_workflow_plan.py)）。

EventBus は URL・locator・path・例外を安全化し、observer の通常例外を隔離する。
stage/code/operation 等の有効値を制限し、raw exception の本文は渡さない。
observer の CancelledError と、通常の通知送信失敗は同じ扱いではない。
通知・診断 cleanup が主処理の result/exception を変えない境界は
[observability](../explanation/observability.md) と [診断失敗テスト](../../../tests/v3/observability/test_logging_failure_consistency.py) を参照する。

## 成果物の検証・管理コマンド（将来計画）

第1段階は検証・一覧・詳細の読み取り中心の機能とする。現行 CLI に新しいコマンドがあると記載しない。
実装着手前に ART-001 でコマンド名・引数・選択単位・text/JSON・終了コードを確定する。
一覧／詳細は記録と実ファイルを区別し、検証は欠損・空ファイル・decode 不能・記録との不一致を報告する。
期待 hash がない成果物は厳密な同一性を証明できない。decode 成功だけで完全性を保証しない。

ART-002 では plugin／対象／章／画像と保存先の対応、保存時点のサイズ・形式・hash 等の必要性を判断する。
saved/skip/rename/overwrite、processor 後の最終 bytes、キャンセル・commit 失敗時の記録を定義する。
台帳の atomic 更新・lock・version・移行・保持・保存失敗の扱いを設計し、既存 workflow 履歴を台帳に見立てない。
旧成果物には「未記録／検証根拠なし」の扱いを設け、ファイル名や安全化 URL から識別を推測しない。

ART-003 では root 外参照、symlink／Windows junction・reparse point、検証中の並行保存、読み取り不能、
巨大／悪意ある画像の resource limit を扱う。記録・実ファイルの変更を検出した場合に正常と報告しない。
検証・一覧・詳細からネットワーク取得、通知、成果物／台帳／workflow 状態の変更を行わない設計を基本とする。
lock 等の副作用が必要なら設計で明記する。修復・再取得・削除は別の後続提案であり、第1段階に含めない。

## リファクタリング候補と追加提案

| 候補・根拠 | 期待する効果 | 互換性リスク・必要な検証 |
| --- | --- | --- |
| service の operation／画像段階／cleanup の責務整理 | 発火点と result 確定点を追いやすくする | facade・DTO、例外分類、保存 commit、cancel を維持。T1/T5 と診断失敗テスト。 |
| service/workflow/NotificationService の失敗分類の重複評価 | 分類のずれを検知・削減する | 画像段階・operation・最終通知は意図的に異なる。単一化を先に決めず、T4/T5 と全イベント対応表で比較。 |
| workflow の比較・報告・履歴間の責務整理 | 比較根拠と確定済み結果の保持理由を明確にする | ImageResource 全フィールド比較、曖昧な識別、再試行可否を維持。[Architecture](architecture.md) と retry/history テスト。 |

REF-001 は採用・見送りを含む評価書が成果物で、コード整理を無条件に実施するタスクではない。
hash/revision 比較の追加や再取得条件変更は挙動維持の整理に混ぜない。
推奨追加項目は SMTP-001 の到達不確定／重複方針、ART-002 の台帳ライフサイクル、ART-003 の並行保存・path 安全性、
QA-001 のローカル SMTP／画像 fixture とする。外部サービス・運用方針の未決事項は
[既存の政策 backlog](open-documentation-todos.md) を参照し、重複登録しない。

## 文書整備の検証記録

2026-10-03、既存の `.runtime/quality-venv/Scripts/python.exe` で以下を実行した。

```powershell
.runtime/quality-venv/Scripts/python.exe -m pytest -q tests/v3/distribution/test_public_api_and_docs.py tests/v3/distribution/test_documentation_reference_contracts.py
git diff --check
git status --short
```

- 文書リンク・公開 API／参照契約テスト: **21 passed**。初回に発見した新規文書の相対リンク誤りを修正して再実行した。
- `events.py` の AST と一覧の enum 名／値を照合: **40定義・40行、欠落／余分なし**。
- whitespace 確認: エラーなし。最終変更は新規2文書と保守インデックスの Markdown のみ。
- SMTP／イベント／workflow の機能テストは今回は実行していない。参照は既存テストの存在・assert 内容の確認に基づく。

機能テストの不足は [開発タスク](development-tasks.md) に残し、
文書契約テストの成功を SMTP 統合や成果物コマンドの実装完了とみなさない。
