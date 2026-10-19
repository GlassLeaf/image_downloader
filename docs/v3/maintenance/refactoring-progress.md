# リファクタリングの進捗と検証記録

照合日: 2026-10-17。今回合意した全体リファクタリング、終了処理・テスト・storage境界の追加計画、残課題3点の進捗は、この文書を正本とします。

2026-10-19のCLI JSON形式・終了処理の変更は、末尾の[追補](#cli-outcome-followup)を参照してください。以下の従来計画・CI記録は、それぞれの検証対象に対する記録として保持します。
実装済み、ローカル検証済み、多環境CI確認済みを区別します。テストの存在やCI設定の存在だけでは、検証完了としません。

## 現在の達成状況

合意した当初の7段階、追加計画の4項目、残課題3点は、実装・検証ともに完了しています。記録追記後のhead `7d416ddaf42fd209da7d40f6c6453aa159bf0d26`でも、[quality実行 #95](https://github.com/GlassLeaf/image_downloader/actions/runs/39029847732)の全15ジョブ成功を確認しました。未対応のレビュー指摘はありません（2026-10-17の確認時点）。

この文書は確認済みのSHAに対する記録を保持します。以降の文書更新を含む最新headのSHA・CI結果は、[Draft PR #19の本文](https://github.com/GlassLeaf/image_downloader/pull/19)を参照してください。下記の非推奨警告と別計画の課題は、今回の完了項目と区別します。

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

| 項目 | 実装・整備状況 | 確認結果 |
| --- | --- | --- |
| 多環境CIの結果確認 | 12組合せの品質検査と3 OSの配布smoke、JUnit・coverage XML、checkout識別情報を整備 | 完了。初回検証と記録追記後のheadで、全15ジョブ成功を確認済み |
| 進捗文書の更新 | この文書を正本として追加。旧計画・開発タスクを日付付き基準記録として区別 | 完了。ローカル・CI結果と最新結果の参照先を記録済み |
| ログ終了例外の方針 | ログ固有の通常例外抑制を非公開helperへ分離。仕様と回帰テストに明記 | 完了。ローカル・多環境CIで確認済み |

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
- この表は上記SHAの確定記録です。記録追記後の検証結果は下記、最新headのSHAとrun URLはPR本文に記録します。

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

### 記録追記後の確認

head `7d416ddaf42fd209da7d40f6c6453aa159bf0d26`に対する[quality実行 #95](https://github.com/GlassLeaf/image_downloader/actions/runs/39029847732)（run ID `39029847732`、attempt `1`）も、全15ジョブ成功で完了しています。checkout SHAはPR用merge commit `92511796d46a7aa201dadaf470f0fcb463874773`で、全15ジョブのhead・checkout・run・attemptを照合済みです。

品質検査12環境のJUnit・coverage XML成果物を確認しました。成功・スキップ件数とcoverageは上表と同じで、配布smokeは3 OSとも通常モードで依存解決を含めて成功しています。これにより、進捗記録の追記後にも全環境の成功を確認する完了条件を満たしています。

### 多環境で確認した非推奨警告

UbuntuのPython 3.12・3.13では、既存のProcessPoolExecutorが使用する標準のfork起動に関する警告が187件ありました。Python 3.14では依存ライブラリpublicsuffix2のcodecs.openに関する警告が1件あり、ActionsランタイムにもNode.js由来の非推奨警告があります。いずれもテスト失敗とは区別します。

今回の範囲ではプロセス起動方式や依存バージョンを変更せず、後続の互換性保守候補として残します。これらはタスク未回収や未取得例外の警告ではありません。

各ジョブの未実行・キャンセル・スキップを成功として数えません。pytest内のOS条件によるスキップと、任意のMailpit未実行は理由を記録します。環境間で同じテスト件数になることは要求しません。

検証手順とCI成果物は[テストと文書の契約](testing.md#多環境ciの確認と検証記録)、配布smokeの内容は[リリース前の確認](release.md)を参照してください。

## 別計画・過去資料との境界

[実装計画](implementation-plan.md)と[開発タスク](development-tasks.md)の表・検証記録は、2026-10-03時点の基準を保持します。「設計待ち」「候補あり、未評価」は当時の状態であり、今回のリファクタリングの現在地ではありません。
SMTP方針、将来の成果物管理、外部サービス等の別課題の完了を、このリファクタリングから推測しません。今回の残課題3点の対応では、公開API、CLIのJSON・終了コード、保存形式、再試行条件、安全性アルゴリズムを維持します。基点リファクタリングで導入したリクエストAPI等の変更は、上記の移行資料で説明します。

この作業の成果物は専用ブランチ、Draft PR、検証記録です。マージと公開配布は対象に含めません。

<a id="cli-outcome-followup"></a>

## 2026-10-19追補：CLI結果表示・終了処理・JSON形式

本処理の結果と終了処理の結果を独立して保持し、終了と必要な履歴訂正を終えてから1つのJSON文書で表示する構造へ変更しました。全CLIのJSON出力と、`--json`なしのinspect・inspect-onlyを対象にしています。[CLI参照](../reference/cli.md#cli-json)に外枠、状態、終了コード、例外の優先順位、移行方法を記載しています。

本処理の例外・キャンセルと原因チェーンを保持し、終了側の失敗も記録します。workflowでは本処理の結果を書き換えず、別の結果で履歴・進捗ログを訂正します。終了後の保存情報は`shutdown.result`で公開します。logger終了の通常例外を固定警告に留める規則と、開始済みの終了処理を回収する規則は維持します。

CLI JSONの外枠は、以前の検証記録にある「維持」の対象から、今回の合意に基づいて変更しています。サービスの公開Python API、保存形式、再試行条件、安全性アルゴリズムは変更していません。過去の監査記録は保持し、閲覧ツールで旧形式と新形式の両方を読めるようにしています。

| 検証 | 今回の結果 |
| --- | --- |
| 終了処理・JSON・履歴訂正の関連検証 | 75件成功。download・workflow・計画の重大例外18条件も成功 |
| 監査記録の旧・新形式 | 4件成功 |
| Ruff・mypy・pip check・差分チェック | 成功 |
| sdist・wheel | `build --no-isolation`で成功 |
| 全体テスト・coverage | 2,355件成功・17件スキップ、coverage 89.85%（下限75%）。全体実行後の追加19件も、最終関連66件の実行で成功 |
| 配布smoke | 依存解決を含む通常モードで成功。最初の制限付き実行の通信失敗は、接続許可を伴う再実行で解消 |
| 多環境CI | 実装コミットに対する全15ジョブが成功。検証対象SHAと内訳は下記に記録 |
| Mailpit | 任意実行のため未実行。ローカルの17件のスキップはシンボリックリンク不可5件・Mailpit 12件 |

検証環境はWindows / Python 3.11.4です。全体テストのJUnit・coverage XMLは`.runtime/cli-final-junit.xml`・`.runtime/cli-final-coverage.xml`、最終関連検証のJUnitは`.runtime/cli-outcomes-final.xml`です。現行の収集件数2,391件について、全体実行と追加検証を合わせて2,374件成功・17件スキップを確認しました。

今回の変更は基点`707b51ae949d59a9fb51bb3664cde5fc87fa5ba1`から`codex/cli-outcome-finalization`ブランチへまとめ、実装コミット`c61b907`をpushしました。配布物の公開は行っていません。

<a id="cli-outcome-ci"></a>

### 実装コミットの多環境CI

実装コミット`c61b907f55bddb002141530da5b7b7da93a24221`に対する[quality実行 #99](https://github.com/GlassLeaf/image_downloader/actions/runs/39233367788)（run ID `39233367788`、attempt `1`、push起動）は、品質検査12ジョブと配布smoke 3ジョブの全15ジョブが成功しました。全ジョブの記録で、head SHAとcheckout SHAがこの実装コミットに一致することを確認しています。

| OS | Python | 成功 | スキップ | coverage |
| --- | --- | --- | --- | --- |
| Linux | 3.11 | 2374 | 17 | 89.60% |
| Linux | 3.12 | 2374 | 17 | 89.60% |
| Linux | 3.13 | 2374 | 17 | 89.60% |
| Linux | 3.14 | 2374 | 17 | 89.54% |
| Windows | 3.11 | 2379 | 12 | 89.94% |
| Windows | 3.12 | 2379 | 12 | 89.94% |
| Windows | 3.13 | 2379 | 12 | 89.94% |
| Windows | 3.14 | 2379 | 12 | 89.88% |
| macOS | 3.11 | 2374 | 17 | 89.60% |
| macOS | 3.12 | 2374 | 17 | 89.60% |
| macOS | 3.13 | 2374 | 17 | 89.60% |
| macOS | 3.14 | 2374 | 17 | 89.54% |

配布smokeは3 OSとも通常モードで依存解決を含めて成功しました。品質検査12環境のJUnit・coverage XML成果物は、OS・Python版・attemptで一意な12件として保存され、同じhead SHAを指しています。全環境でcoverage下限75%を満たし、CIの失敗に伴うコード修正は発生していません。

CIのWindowsはシンボリックリンクを作成できるため、スキップは任意のMailpit 12件だけです。Linux・macOSではMailpitに加えてWindows専用条件5件をスキップしています。ローカル検証のWindowsでシンボリックリンク不可だった5件とは、理由を区別します。

UbuntuのPython 3.12・3.13でのfork警告、Python 3.14でのpublicsuffix2のcodecs.open警告、ActionsのNode.js非推奨警告は既存の保守候補として残っています。新しいCI失敗や、タスク未回収・未取得例外の警告とは区別しています。

この記録を追加する文書更新コミットもpushし、最終headのCIを別途確認します。記録対象の実装SHAと、文書更新後の最終headを混同しないよう、最終headのSHAとrun URLは作業完了報告に記載します。
