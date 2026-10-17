# リファクタリングの進捗と検証記録

照合日: 2026-10-17。今回合意した全体リファクタリング、終了処理・テスト・storage境界の追加計画、残課題3点の進捗は、この文書を正本とします。
実装済み、ローカル検証済み、多環境CI確認済みを区別します。テストの存在やCI設定の存在だけでは、検証完了としません。

## 当初の7段階

| 段階 | 実装状況 | 確認できる構造 | 検証状況 |
| --- | --- | --- | --- |
| 検証基盤 | 実装済み | supportの共通helper、開始待ちと業務期限の分離、タスク所有権 | ローカル・多環境CIで成功。結果は下記 |
| 内部の受け渡し・失敗処理 | 実装済み | 不変の操作別リクエスト、SiteOperation、型付き失敗情報、確定待ち | 同上 |
| ダウンロード処理の分割 | 実装済み | Download・Chapter・Image実行器、既存ArtifactPipelineの利用 | 同上 |
| workflowの独立 | 実装済み | WorkflowRuntime、実行・計画・確定処理、画像保持判定 | 同上 |
| CLIの暗黙的状態の除去 | 実装済み | PreparedCommandとコマンド別入力型、cache属性の除去、循環依存の検査 | 同上 |
| 周辺サブシステムの整理 | 実装済み | HTTP認証、plugin検証・catalog、設定、通知、storageの境界 | 同上 |
| 公開API・文書の移行 | 実装済み | リクエストAPI、専用workflow例外、移行例、API・文書契約 | 同上 |

構造の正本は[アーキテクチャ](architecture.md)、公開契約は[ライブラリAPI](../reference/library-api.md)、移行方法は[アプリケーションAPIの移行](application-api-migration.md)です。

## 追加計画の4項目

| 項目 | 実装状況 | 維持する条件 |
| --- | --- | --- |
| サービス終了処理 | 実装済み | 単一の終了タスク、固定の終了順序、キャンセル中の終了回収、主原因・部分結果の保持 |
| テストのタスク所有権 | 実装済み | managed_taskとwait_until_started、早期失敗時の回収、スレッドからのEvent通知 |
| storageの読み取り境界 | 実装済み | 読み取り専用validate、不変なread_feeds、既存キー・ロック順序、公開の名前長調整 |
| 現行ドキュメント | 実装済み | 新APIへの統一、終了時の優先順位、旧API再混入検査、移行例・履歴資料の保持 |

重大例外を子タスクから直接漏らさず、開始済み処理を回収して元の例外を再送出する処理も導入済みです。保存では処理の失敗、履歴では待機側キャンセルを優先する既存の規則を維持します。

## 今回の残課題3点

| 項目 | 実装・整備状況 | 残作業 |
| --- | --- | --- |
| 多環境CIの結果確認 | 12組合せの品質検査と3 OSの配布smoke、JUnit・coverage XML、checkout識別情報を整備 | 検証済みheadの全15ジョブ成功。記録追記後の最新headも確認する |
| 進捗文書の更新 | この文書を正本として追加。旧計画・開発タスクを日付付き基準記録として区別 | ローカル・CI結果を記録済み |
| ログ終了例外の方針 | ログ固有の通常例外抑制を非公開helperへ分離。仕様と回帰テストに明記 | ローカル・多環境CIで確認済み |

### 終了例外の合意事項

ログの通常終了例外は、**最初の通常終了失敗を返す規則の対象外**です。固定の診断警告に留め、成功結果、CLI終了コード、履歴を変更しません。例外本文は警告へ含めません。

`CancelledError`、`KeyboardInterrupt`、`SystemExit`は、この通常例外の抑制対象ではありません。終了タスク内で失敗情報として保持し、終了完了後に元の例外を伝えます。既に主原因がある場合は、その主原因とworkflowの部分結果・原因チェーンを保持します。

## 検証記録

### 既存リファクタリングの基点

既存差分をコミット`67f742f`へ保存しました。以下は保存前の同内容の作業ツリーに対する、2026-10-17の記録です。

- 環境: Windows、Python 3.11.4。
- 全体テスト: 2,284件成功、17件スキップ。カバレッジ89.59%、下限75%を達成。
- Ruff、mypy（127ソースファイル）、build、オフライン配布smoke: 成功。
- この記録は、今回の追加修正や他OS・Python版の成功を意味しません。オフラインsmokeは依存解決を検証しません。

### 今回のローカル検証

2026-10-17、Windows / Python 3.11.4で、終了処理コミット`a8a04cf`、CIコミット`34493f5`と今回の文書を含む作業ツリーを検証しました。

- 全体pytest: **2,308件成功、17件スキップ**。実行時間799.54秒。
- カバレッジ: **89.61%**。下限75%を達成し、JUnit・coverage XMLも生成。
- スキップ: Mailpit任意実行12件、シンボリックリンク作成が許可されない環境による5件。
- 終了処理・重大例外・診断・CLIの関連85件、文書・API契約22件、CI設定等7件が成功。Python準備順を補強後のCI契約5件も成功。
- Ruff、mypy（127ソースファイル）、pip check、wheel・sdistのbuild、オフライン配布smoke: 成功。
- タスク未回収・未取得例外・RuntimeWarningは記録されていません。

オフラインsmokeは依存解決を検証しません。通常モードの配布smokeと他OS・Python版の確認は、以下のCI結果で区別します。

### 多環境CI

2026-10-17、[Draft PR #19](https://github.com/GlassLeaf/image_downloader/pull/19)の[quality実行 #93](https://github.com/GlassLeaf/image_downloader/actions/runs/39029189845)は、**全15ジョブ成功**で完了しました。

- 検証対象head SHA: `9cb6641d39c47a7f83bc23562459c88c6041c94c`。
- checkout SHA（PR用merge commit）: `48bc3bc6e9db6221740c4ff719e0ab867f53ecfc`。全15ジョブの記録を照合しました。
- run ID: `39029189845`、attempt: `1`。
- 品質検査12環境のJUnit・coverage XML成果物を確認済み。いずれも対象headに一致し、保存期間は14日です。
- この表は上記SHAの確定記録です。記録追記のpush後も最新headのCIを確認し、その最終SHAとrun URLはPR本文に記録します。

| ジョブ | OS | Python | 結果・ジョブへのリンク | 成功 / スキップ | coverage |
| --- | --- | --- | --- | --- | --- |
| 品質検査 | Ubuntu | 3.11 | [success](https://github.com/GlassLeaf/image_downloader/actions/runs/39029189845/job/117386045495) | 2,308 / 17 | 89.34% |
| 品質検査 | Ubuntu | 3.12 | [success](https://github.com/GlassLeaf/image_downloader/actions/runs/39029189845/job/117386045374) | 2,308 / 17 | 89.34% |
| 品質検査 | Ubuntu | 3.13 | [success](https://github.com/GlassLeaf/image_downloader/actions/runs/39029189845/job/117386045364) | 2,308 / 17 | 89.34% |
| 品質検査 | Ubuntu | 3.14 | [success](https://github.com/GlassLeaf/image_downloader/actions/runs/39029189845/job/117386045523) | 2,308 / 17 | 89.27% |
| 品質検査 | Windows | 3.11 | [success](https://github.com/GlassLeaf/image_downloader/actions/runs/39029189845/job/117386045442) | 2,313 / 12 | 89.68% |
| 品質検査 | Windows | 3.12 | [success](https://github.com/GlassLeaf/image_downloader/actions/runs/39029189845/job/117386045353) | 2,313 / 12 | 89.68% |
| 品質検査 | Windows | 3.13 | [success](https://github.com/GlassLeaf/image_downloader/actions/runs/39029189845/job/117386045479) | 2,313 / 12 | 89.68% |
| 品質検査 | Windows | 3.14 | [success](https://github.com/GlassLeaf/image_downloader/actions/runs/39029189845/job/117386045550) | 2,313 / 12 | 89.62% |
| 品質検査 | macOS | 3.11 | [success](https://github.com/GlassLeaf/image_downloader/actions/runs/39029189845/job/117386045468) | 2,308 / 17 | 89.34% |
| 品質検査 | macOS | 3.12 | [success](https://github.com/GlassLeaf/image_downloader/actions/runs/39029189845/job/117386045274) | 2,308 / 17 | 89.34% |
| 品質検査 | macOS | 3.13 | [success](https://github.com/GlassLeaf/image_downloader/actions/runs/39029189845/job/117386045414) | 2,308 / 17 | 89.34% |
| 品質検査 | macOS | 3.14 | [success](https://github.com/GlassLeaf/image_downloader/actions/runs/39029189845/job/117386045435) | 2,308 / 17 | 89.27% |
| 配布smoke | Ubuntu | 3.11 | [success](https://github.com/GlassLeaf/image_downloader/actions/runs/39029189845/job/117386045154) | — | — |
| 配布smoke | Windows | 3.11 | [success](https://github.com/GlassLeaf/image_downloader/actions/runs/39029189845/job/117386045309) | — | — |
| 配布smoke | macOS | 3.11 | [success](https://github.com/GlassLeaf/image_downloader/actions/runs/39029189845/job/117386045328) | — | — |

Windows CIではシンボリックリンク作成が可能なため、ローカルでスキップした5件も成功しました。Windows CIのスキップ12件はMailpit任意実行です。Ubuntu・macOSの17件はMailpitの12件とWindows固有の5件です。全環境でcoverage下限75%を満たしています。

### 多環境で確認した非推奨警告

UbuntuのPython 3.12・3.13では、既存のProcessPoolExecutorが使用する標準のfork起動に関する警告が187件ありました。Python 3.14では依存ライブラリpublicsuffix2のcodecs.openに関する警告が1件あり、ActionsランタイムにもNode.js由来の非推奨警告があります。いずれもテスト失敗とは区別します。

今回の範囲ではプロセス起動方式や依存バージョンを変更せず、後続の互換性保守候補として残します。これらはタスク未回収や未取得例外の警告ではありません。

各ジョブの未実行・キャンセル・スキップを成功として数えません。pytest内のOS条件によるスキップと、任意のMailpit未実行は理由を記録します。環境間で同じテスト件数になることは要求しません。

検証手順とCI成果物は[テストと文書の契約](testing.md#多環境ciの確認と検証記録)、配布smokeの内容は[リリース前の確認](release.md)を参照してください。

## 別計画・過去資料との境界

[実装計画](implementation-plan.md)と[開発タスク](development-tasks.md)の表・検証記録は、2026-10-03時点の基準を保持します。「設計待ち」「候補あり、未評価」は当時の状態であり、今回のリファクタリングの現在地ではありません。
SMTP方針、将来の成果物管理、外部サービス等の別課題の完了を、このリファクタリングから推測しません。今回の残課題3点の対応では、公開API、CLIのJSON・終了コード、保存形式、再試行条件、安全性アルゴリズムを維持します。基点リファクタリングで導入したリクエストAPI等の変更は、上記の移行資料で説明します。

この作業の成果物は専用ブランチ、Draft PR、検証記録です。マージと公開配布は対象に含めません。
