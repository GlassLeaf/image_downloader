# v3 開発タスクと完了基準

基準日: 2026-10-03。[実装計画](implementation-plan.md) の現状照合とイベント表を根拠にする。
この backlog は将来の開発作業であり、今回の文書整備でコード・設定・テストを変更しない。
優先度は P1（設計・検証の前提）、P2（機能拡張／追加評価）、P3（後続候補）。
各 ID は追跡用で、採用する公開 API・CLI・設定名ではない。

## 棚卸しと通知・イベント

| ID / 優先度 | 現状 | 依存関係 | 成果物 | 完了基準 | 検証方法 |
| --- | --- | --- | --- | --- | --- |
| BASE-001 / P1 | 文書照合済み | なし | 実装・テストの根拠付き基準表 | 実装済み／補強必要／未実装／設計待ちを区別し、テスト存在と実行結果を分離。変更時に照合日を更新。 | implementation-plan の各根拠リンクと文書契約テスト |
| EVT-001 / P1 | 全40定義の文書棚卸し済み、継続保守 | BASE-001 | 全イベントと通知対応表 | 全 EventName の発火点・条件・局所順序・回数・payload・カテゴリ・内容・テストを追跡。外部 emit、core 未発火、最終通知内部収集を区別。 | events.py の全 enum と対応表を照合、service/workflow/notifications の emit・収集点を確認 |
| SMTP-001 / P1 | 実装済み、追加設計待ち | EVT-001 | SMTP 動作・失敗判定・到達不確定／重複方針の設計書 | TLS／認証／timeout の現行仕様を保持したうえで、全宛先拒否・一部拒否・応答喪失・キャンセルを定義。再送の採否、通知失敗と主処理の境界、総時間上限の必要性を決定。 | _send_mail/EmailNotificationSender/_deliver/diagnostic scope を照合し、SMTP 例外・戻り値別の期待表をレビュー |
| SMTP-002 / P1 | fake SMTP TLS テスト等あり、補強必要 | SMTP-001 | SMTP 単体テストの不足分 | 接続・TLS・認証・送信の例外、timeout、資格情報欠落、環境変数優先／keyring fallback、宛先の一部／全拒否、日本語 MIME、複数宛先、secret 非露出を確認。既存 TLS テストを重複作成しない。 | notifications v3 の既存ケースを維持し、sender 注入・fake SMTP・keyring/env の隔離で実行 |
| SMTP-003 / P2 | ローカルサーバー統合は未整備 | SMTP-002、QA-001 | loopback SMTP 統合テストと任意の手動確認手順 | implicit TLS／STARTTLS の実通信、試験用認証、受信した envelope／件名／本文、拒否・切断・timeout を検証。外部メールサービスを必須にしない。 | ローカル専用サーバー・証明書で再現。プロセス／port／thread を後始末。実サービス確認は明示的な opt-in |
| EVT-002 / P1 | 主要 download/workflow テストあり、補強必要 | EVT-001 | 自動発火・非発火・payload の受入テスト不足分 | operation の auth/plugin/config/storage/runtime 分類、update 失敗、空 manifest／空章、skip、保持画像、HTTP retry、workflow 各試行、cancel の回数・局所順序・秘匿化を検証。未発火定義を実装せず、その境界を検証。 | T1/T3/T4/T5 と preview テストを基準に、failure injection と observer 記録で確認。並列画像間の固定順序は要求しない |
| EVT-003 / P1 | 手動収集・経路・observer 隔離テストあり、補強必要 | EVT-001、SMTP-001 | 通知経路・抑止・診断失敗の不足テスト | 全通知カテゴリの opt-in/default/routes を確認。sender 欠落／false／例外、複数 channel、通知イベント非再収集、本文上限、ログ失敗、最終結果の一度だけの収集を検証。通常例外と cancel を分離。 | T2 と診断失敗テスト。通知による結果／終了コード変更、重複・秘密情報漏洩がないことを照合 |

T1～T5 の実ファイルと既存ケースは [イベント対応表](implementation-plan.md#全イベントの発火通知テスト対応) を参照する。
未発火イベントを追加実装する場合は別の仕様変更タスクを起票し、成功判定点と互換性を先に決める。

## 成果物管理の第1段階（将来計画）

| ID / 優先度 | 現状 | 依存関係 | 成果物 | 完了基準 | 検証方法 |
| --- | --- | --- | --- | --- | --- |
| ART-001 / P1 | 未実装、インターフェース設計待ち | BASE-001 | 検証・一覧・詳細コマンドの仕様書 | コマンド名・引数・対象選択・text/JSON・終了コード、検証レベル、欠損／空／破損／不一致／未記録／読取不能／検証中変更を決定。decode 検査と hash 同一性の限界を説明。修復・再取得・削除を含めない。 | CLI 定義保守手順と照合し、入力・出力・失敗例のレビューで判断事項を閉じる |
| ART-002 / P1 | 成功画像の台帳は未実装、設計待ち | ART-001 | 台帳 schema・保存／移行／保持設計 | 画像識別・root 相対保存先・必要なサイズ／形式／hash・最終 bytes と記録の関係を確定。saved/skip/rename/overwrite、atomic 更新、lock、version、旧成果物、台帳破損・記録失敗・cancel を定義。既存 workflow 集計とは分離。 | 保存 transaction・workflow history と整合レビュー。正常／失敗／移行／台帳なしの fixture 仕様を作成 |
| ART-003 / P1 | 既存 path/lock 基盤あり、新機能への適用設計待ち | ART-001、ART-002 | 安全な読み取り・並行保存・resource limit の仕様とテスト計画 | root 外、symlink/junction/reparse point、検証中 rename/overwrite、アクセス拒否、巨大画像を扱う。検証中変更を成功とみなさない。lock 等の副作用を明記し、検証で成果物・台帳・workflow 状態を修正しない。 | storage の既存安全性テストを参照し、Windows を含む path／並行処理／資源上限の受入表をレビュー |
| ART-004 / P2 | コマンド・台帳とも未実装 | ART-001～003、QA-001 | 合意済み台帳と読み取りコマンドの実装・文書・テスト | 一覧・詳細・検証と保存時の台帳記録を合意済み仕様通り実装。旧成果物を推測で登録しない。公開 API 追加は必要性を判断して仕様に反映。 | text/JSON、help、引数配置・無効入力・終了コード・ネットワーク／通知非実行、保存方針別台帳、破損／欠損／並行書込みの受入テスト |

ART-004 は将来のコード変更を伴うタスク。今回は着手しない。
CLI 設計は [定義の拡張手順](cli-definition-extensions.md)、保存安全性は
[storage](../../../src/image_downloader/storage/filesystem.py) と
[path safety](../../../src/image_downloader/storage/path_safety.py) を参照する。
各設計タスクは未確定事項をすべて決定し、具体的な受入例を残してから実装へ渡す。

## 評価・推奨追加項目

| ID / 優先度 | 現状 | 依存関係 | 成果物 | 完了基準 | 検証方法 |
| --- | --- | --- | --- | --- | --- |
| QA-001 / P1 | 画像生成 helper と fake transport は既存、統合 fixture は補強必要 | BASE-001 | 再現可能なローカル SMTP／画像 fixture 方針と実装 | 最小限の正常／空／破損／形式不一致／台帳なし／検証中変更 fixture、TLS 証明書と試験資格情報、OS 条件、資源上限、cleanup を定義。既存 helper を再利用し、外部 URL に依存しない。 | fixture 単独実行と隔離・後始末確認。受信者・資格情報・URL 秘密値の出力を確認 |
| REF-001 / P2 | 候補あり、未評価 | EVT-002、EVT-003、ART-001～003 | service／失敗分類／workflow 責務の採否評価書 | 各候補の根拠、効果、互換性リスク、必要テスト、採用／見送りを記録。挙動維持の整理と比較条件・通知方針等の仕様変更を分離。採用時のみ別の実装タスクを起票。 | Architecture、イベント表、既存 retry/history/diagnostic テストに照合。公開 DTO/facade と result/cancel/commit の差分レビュー |
| ART-NEXT-001 / P3 | 後続候補、今回の第1段階対象外 | ART-004 | 修復・再取得・削除の必要性評価 | 必要性が確認できた場合だけ再取得条件、状態更新、保持・削除対象、dry-run、承認／復旧方針を別計画にする。 | 第1段階の検証結果と利用場面を評価。現行コマンドとして公開しない |

推奨項目の対応: 通知失敗と再送・重複は SMTP-001、並行書込み・ルート外・リンクは ART-003、
台帳 version・移行・保持は ART-002、ローカル受入検証は QA-001／SMTP-003。
外部サービスや運用者判断の既存 TODO は [open-documentation-todos](open-documentation-todos.md) を維持する。

## 変更時の検証と完了報告

今回の文書整備は、新規2文書・保守インデックスの Markdown 差分だけを対象とする。
全 EventName と対応表の一致、参照ファイル・関数の存在、現行文書の再帰リンク・契約テスト、
`git diff --check` と最終変更一覧を確認する。実行結果は [計画の検証記録](implementation-plan.md#文書整備の検証記録) に記録する。

将来コードを変更する際は、対象の受入テストと [Testing](testing.md)、[Release](release.md) の必要な確認を実施する。
「設計書を作成」「テストを追加」「テストが成功」「任意の実サービス確認」を別々に報告し、
ソース内のテスト存在だけでタスクを完了にしない。
