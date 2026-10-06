# R03: 画像0件の成功仕様と変更の影響

調査日: 2026-10-05。製品コードは変更していない。
対象は画像manifestの章が存在するものの、全章の画像数の合計が0になるケース。
章そのものが0件のケース、更新候補0件、全画像skip、新規保存0件とは区別する。

## 現行の根拠

1. [GenericHtmlPlugin._load_manifest](../../../src/image_downloader/plugins/builtin.py#L94)は、
   imgが見つからなくても1章・画像0件のmanifestを返す。
2. [DownloadService._run_with_additional](../../../src/image_downloader/application/service.py#L543)は、
   `manifest.chapters`が空の場合だけ`allow_empty_chapter_manifest`を確認する。
   章が存在すれば画像数0でも章処理へ進む。
3. [DownloadResult.failures](../../../src/image_downloader/models.py#L461)は画像outcomeに含まれる失敗を集める。
   対象画像がなければ失敗も空になる。
4. [通常downloadのイベント](../../../src/image_downloader/application/service.py#L509)と
   [CLI終了コード](../../../src/image_downloader/commands/download.py#L130)は、失敗が空なら成功となる。
5. [workflowのURL試行](../../../src/image_downloader/application/workflow.py#L316)は初期statusをsuccessとし、
   例外や画像失敗がなければそのまま返す。
   [成功URLの完了記録](../../../src/image_downloader/application/workflow.py#L300)によってcompletedになる。
6. [候補の次回選択](../../../src/image_downloader/storage/workflow.py#L151)は、URL・revisionが同じで
   completedならupdatedで選択しない。
7. 一方、[verify workflow](../../../src/image_downloader/application/workflow_verification.py#L122)は
   期待画像数0を`no_images`として取得失敗と判定する。

したがって、現在の「成功」は画像を取得した証明ではなく、対象の処理中に例外・画像失敗がなかったことを表す。
これは[既存テスト](../../../tests/v3/application/test_generic_workflow.py#L220)と
[FAQ](../../v3/how-to/faq.md#L444)にも明記された契約である。

## 仕様を維持した場合の対比

| 利用場面・観点 | 維持するメリット | 発生しうる問題・デメリット |
| --- | --- | --- |
| 正当な空ページ・未公開章 | 空であることが正しいページをエラーにせず、バッチが完了する。 | 正当な空とログイン画面・HTML解析失敗を区別できない。 |
| 追加ファイルだけの取得 | 画像0件でも章があれば、JSON等の追加ファイルを保存しhookを完了できる。実際に再現した。 | 画像取得の成功表示・verifyの画像取得判定とは意味が一致しない。追加ファイルの成功を独立して扱う必要がある。 |
| 既存プラグイン・API・CLI | 空結果を正常とするプラグイン、例外処理、終了コード0を前提とするスクリプトを維持する。 | 外部自動化が終了コード0だけを見て後続処理を実行すると、必要な画像がないまま進む。 |
| workflow完了・再試行 | 正当な空に対して毎回取得したり、追加周回で待機したりせずに済む。 | 誤った空結果も完了になる。候補のrevisionが変わらなければ次回updatedで取得しない。 |
| ログ・通知 | 現行の成功イベント、章ログ、成功通知の意味を変えずに済む。 | 成功通知を有効にした利用者が取得できたと誤認する。画像0枚でも出力ディレクトリ・章ログは作られる。 |
| 検証との関係 | 実行成功と成果物の検証を別の段階にできる。 | 同じ初回実行がworkflowではsuccess、verifyではfailedになり、成功の意味がわかりにくい。 |

具体例:

- HTTP 200で返るログイン画面・アクセス確認画面にimgがなければ成功になる。
  0件だけから認証失敗だと断定することはできない。
- JS描画や未対応のsrcset/lazy属性によって、画像が存在するページから0件を抽出しても成功になる。
- PNGそのもののURLを汎用HTMLへ渡しても0件・成功となる。
- 独自プラグインのセレクターがサイト変更で合わなくなり、空章を返した場合も同様。
- 取得対象が正当に0件のページでも全く同じ判定になる。

汎用HTMLでは、後日画像一覧が0件から非0件へ変わればrevisionも変わり、updatedで選択される。
したがって必ず永久に省略されるわけではない。ただしタイトルや説明文だけの変更はrevisionに含まれない。
独自プラグインの場合は、そのプラグインが候補の変更を伝えるかに依存する。

## 変更方式と影響の差

| 方式 | 改善できること | 制限・互換性 |
| --- | --- | --- |
| 警告だけ追加し成功・completedを維持 | 人が0件に気づきやすくなる。終了コード・結果型への影響が小さい。 | updatedで省略される問題や終了コードによる誤認は解消しない。 |
| 汎用HTMLのdownloadだけ拒否 | ログイン画面等の代表的な問題を、他プラグインの契約を変えずに改善できる。 | 独自プラグインが空章を返す問題は残る。共通のinspect解析で拒否するとinspectまで影響する。 |
| コアのdownload境界で「章あり・全体画像0件」を原則拒否し、明示許可を用意 | 全プラグインで一貫した取得成功判定にでき、workflow完了も防げる。 | 空ページ・追加ファイルのみのプラグインは許可設定が必要。推奨候補だが既定変更になる。 |
| 各章で必ず1画像以上を要求 | 一部章だけの抽出失敗も検出できる可能性がある。 | 画像あり章と正当な空章が混在する作品まで拒否する。R03の最小修正としては影響が大きい。 |

推奨候補は3番目。判定は新規保存枚数ではなく`sum(len(c.images) for c in manifest.chapters)`で行う。
1章でも画像があれば従来の画像処理・失敗判定を維持し、空章だけを新しい画像失敗として扱わない。
章0件は既存`allow_empty_chapter_manifest`の意味を維持し、別の仕様変更として無断で上書きしない。
空画像の明示許可を全体設定だけにするか、プラグイン単位で指定できるようにするかは実装前に決める。

この変更でJS対応・画像URLの直接保存・srcset抽出が実装されるわけではない。
またplaceholderやサイトロゴが1枚でも抽出されれば0件ではなくなるため、誤取得全般は防げない。

## 他の実装・現在の挙動への影響

以下は「コアのdownload境界で画像総数0を例外化する」候補に対する調査。
PluginErrorを利用した場合の後段の挙動は一時的な差込みで実行確認した。
専用例外・専用codeの追加はまだ設計判断であり、以下の数値はそのまま保証するものではない。

| 対象 | 現行 | 候補の変更後・必要な対応 |
| --- | --- | --- |
| 通常download・bare URL・ライブラリrun | 0件のDownloadResultを返しCLIは0。 | PluginErrorならライブラリは例外、通常CLIは既存分類の4。JSONも正常結果の空配列からエラー形式になる。画像失敗ではないので架空のImageFailureを追加しない。 |
| workflow all/updatedの取得試行 | 0件success、全件successなら終了0、completed=true。 | 該当URLはfailed・completed=false。全件失敗なら終了1、別URL等で保存・skipがあれば終了5。既存の成功URLは維持される。 |
| workflow追加周回 | 0件successは再試行しない。 | 既存PluginErrorはretryable。追加1周回・待機600秒の既定が適用され得る。回復可能な空と恒久的な空を区別できないので、再試行の採否を明示する必要がある。 |
| 既存完了状態 | revisionが同じならupdatedで省略。 | 将来のdownload判定だけ変えても過去のcompletedは変わらない。修正導入後のallまたは明示的な再取得が必要。過去状態の自動無効化は別の移行作業。 |
| inspect・download --inspect-only | 0画像のmanifestと画像数0を表示できる。 | download境界で検査すれば維持できる。GenericHtmlPlugin.inspectや共通_normalized_manifestで拒否すると、この診断用途にも影響するので避ける。 |
| 更新確認・更新URL一覧・workflow dry-run | 更新候補を比較し、対象を計画する。 | download境界だけなら取得試行をしないので維持。dry-runでの成功は実取得成功を保証しない。 |
| 更新候補0件・変更なし | 取得試行なしで正常終了。 | 維持する。候補0件を画像0件の取得失敗と混同しない。実行確認済み。 |
| 全画像skip・新規保存0件 | 画像outcomeはskipで正常。 | manifestに画像があるので維持する。実行確認済み。保存ファイル数を条件にすると誤って拒否する。 |
| 空章と画像あり章の混在 | 画像あり章を取得し、空章も正常処理。 | 全体0件だけの条件なら維持。実行確認済み。各章0件拒否では変更される。 |
| 章0件の明示許可 | allow_empty_chapter_manifest=trueで成功・完了。 | 現行の明示許可を維持するのが互換性面で最小。画像0件の許可設定との関係を文書化する。 |
| サイト・processorのlifecycle | 選択・認証・manifest取得前後のsetup/cleanupがある。 | download境界の判定でも既に開始済みのlifecycleは存在する。正常画像のtransform・検査・保存順序は維持し、エラー時cleanupも必ず維持する。 |
| 追加ファイルhook | BEFORE_MANIFESTの取得後、manifestを使って保存し、AFTER_MANIFEST・AFTER_DOWNLOADも実行。 | manifest取得後すぐ拒否すれば、pendingの最終保存や後続hookは行われなくなる。許可設定がない追加ファイル専用利用には破壊的変更。後段拒否では既に保存された追加ファイルが残り、失敗表示との不整合が増える。 |
| 章ディレクトリ・log.log | 空章でも作成する。 | reporter開始前の拒否なら新しい章ディレクトリ・章ログは作られなくなる。既存ディレクトリやファイルの削除は不要。コア・workflowの失敗記録は残す。 |
| イベント・通知 | DOWNLOAD_SUCCESSとDOWNLOAD_COMPLETE。成功通知を有効にしていれば収集。 | PluginErrorならPLUGIN_FAILEDとDOWNLOAD_FAILEDになり、DOWNLOAD_COMPLETEは発火しない。BEFORE/AFTER_DOWNLOADとcleanupは維持。workflow通知は試行ごとでなく最終結果から収集する現行方針を維持。 |
| verify workflow | 成功履歴でもexpected_images=0ならno_imagesでfailed。 | 拒否結果もfailed。ただし理由はdownload_failedとなる。空画像を明示許可してsuccessにする場合、verifyを従来の画像取得検証のままにするか、許可を証拠へ記録するか別途決める必要がある。 |
| 履歴・JSON・終了コード契約 | success・saved=0・skipped=0・failures=0が記録される。 | failedとURL単位errorを記録。status値・履歴schemaは既存値を再利用可能だが、利用者の集計・終了コード分岐は変わる。 |
| その他の管理コマンド | doctor/config/plugin/cookie/state/help等。 | download境界だけの変更なら新しい判定を通らない。新設定・例外を追加した場合は設定説明・公開API・エラー一覧・文書契約には整備が必要。 |

検査位置の推奨は、[現在のmanifest取得・ledger.begin直後](../../../src/image_downloader/application/service.py#L545)、
`additional.flush_pending()`、AFTER_MANIFEST、章reporterの開始より前。
BEFORE_MANIFESTの追加データ取得は既に発生するので、後始末と「保存しなかった」理由を記録する。
追加ファイルの受信・保存callbackの呼出し規則も受入条件に含める。
manifestのrevisionやプラグイン選択、画像検査、ファイル名、既存画像の保存bytesを変更する必要はない。

## 例外分類は単独変更で済まない

既存PluginErrorをそのまま使うと、通常CLIの終了4、workflowの再試行、plugin_error通知が既存処理で得られる。
ただし公開reasonは汎用的な`plugin operation failed`となり、空画像の理由が十分に伝わらない。

専用codeを作る場合は、[ERROR_CATALOG](../../../src/image_downloader/exceptions.py#L241)への登録、
[retryableのcode一覧](../../../src/image_downloader/application/workflow_retry.py#L22)、
[workflow最終通知のcode対応](../../../src/image_downloader/observability/notifications.py#L173)も整える必要がある。
未登録codeは安全な汎用エラーへ置き換わり、retryや通知分類が想定と異なる可能性がある。
章0件・全体画像0件・候補0件は公開診断でも区別する。

## 実行確認と限界

[r03-behavior-probe.py](r03-behavior-probe.py): **6 passed**（3.78秒）。実サイト通信なし。

```powershell
python -m pytest docs/investigations/implementation-review-2026-10-05/r03-behavior-probe.py -q
```

確認したこと:

- 現行は空画像の通常成功・workflow終了0・完了記録・次回省略・初回verify失敗。
- `allow_empty_chapter_manifest`のtrue/falseどちらでも、1章・画像0件の結果は同じ。
- 画像0件の章から追加JSONファイルを保存できる。
- 空画像をPluginError化するとworkflowは終了1、2試行ともfailed、完了しない。
- 既存completedは判定変更だけではupdatedで再実行されず、allを指定すると拒否される。
- downloadへの拒否差込みはinspectを変えず、混在空章・全skip・更新候補0件も維持できる。

差込みは`_run_with_additional`のreturn直後であり、推奨する早期検査位置を実装した試験ではない。
そのため追加ファイルやcallbackの早期停止、設定追加、専用codeの契約はソース上の影響分析であり、
修正実装後の受入試験が必要。
通常CLIの終了コード4と管理コマンドの影響範囲はコード経路による確認であり、
差込みプローブで全CLIを実行したという意味ではない。

既存の関連v3テスト: **181 passed, 1 skipped**（142.08秒）。
汎用HTML、workflow検証、追加ファイル、workflow再試行、イベント通知契約の5ファイルを実行した。
調査プローブのruffは成功。製品の修正、全v3テスト、実サイト・実通知の試験は実施していない。
