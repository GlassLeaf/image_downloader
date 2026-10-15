# QS修正8件のmain統合・実装確認（2026-10-13）

対象はQS-001、QS-002、QS-010、QS-011、QS-012、QS-013、QS-016、QS-015。
**8件ともmainへの取り込みを確認した。修正コード・回帰テストの取り込み漏れや巻戻しは見つからず、今回の全体テストも成功した。**
QS-013本来の組立て失敗時の資源解放と、関連する初期化flush競合の修正を確認した。
Windowsのopen待機対策については、過去の調査で確認した残存停止を踏まえ、暫定策という評価を維持する。

## 確認した状態と方法

- ローカルのHEAD・main・origin/main、およびGitHubのmainはすべて
  `f78b95f115ffa3b8b309cd0fa0f0018df56cd8aa`。ブランチ切替や新しい本体修正は不要だった。
- 開始時の作業ツリーはclean。対象コミット、GitHubの修正ブランチ7本の最新tip、PRのbaseとmerge先を確認した。
  QS-012には独立した修正ブランチがなく、QS-016ブランチ上の修正として統合されている。
- 各修正コミットと各修正ブランチtipについて、`git merge-base --is-ancestor`でmainの祖先であることを確認した。
  履歴上の存在だけで判定せず、修正時とmainの本体・通常テスト・pyproject.tomlのGit blob IDも比較した。
- 差分が残るファイルは現行コードと照合した。QS-013の追加ロック修正、QS-010による有限値判定の置換、
  QS-015による例外名変更は意図された後続変更であり、先行修正の撤回ではない。
- 現行の読込み・検証・送信・組立て処理を精読し、通常の安全な期待値の回帰テストを含むv3全体を再実行した。
  過去の脆弱な挙動を期待する監査probeは変更・実行していない。
- 本体・通常テスト・設定・CI・package smokeの175ファイルのSHA-256は実行前後で一致した。
  この確認では新しい確認資料のみを追加し、過去の調査資料・本体・テストを変更していない。

## mainへの統合経路

| ID / issue | 実装修正コミット | mainへの経路 | 統合・現行実装の判定 |
|---|---|---|---|
| QS-001 / #1 | `1e5b47e` | 元PR #9 → 統合PR #13 (`583026b`) | 取り込み・修正維持を確認 |
| QS-002 / #2 | `7835102` | 元PR #10 → 統合PR #13 (`583026b`) | 取り込み・修正維持を確認 |
| QS-010 / #3 | `a668cb3` | PR #14 (`67ab96d`) → main | 取り込み・修正維持を確認 |
| QS-011 / #4 | `49e950a` | PR #15 (`c4138e0`) → main | 取り込み・修正維持を確認 |
| QS-012 / #5 | `1cb31d9`、関連テスト安定化 `2586ee6` | QS-016ブランチ → 統合PR #13 (`583026b`) | 取り込み・修正維持を確認 |
| QS-013 / #6 | 資源解放 `d7e6a1b`、関連ロック修正・記録 `3d40a0f`～`469f0c6` | PR #12 → QS-016ブランチ (`2994570`) → 統合PR #13 (`583026b`) | 資源解放・初期化修正を確認。open対策は暫定 |
| QS-016 / #7 | `aee66c9` | 元PR #11のブランチを更新 → 統合PR #13 (`583026b`) | 取り込み・修正維持を確認 |
| QS-015 / #8 | `9b820c1` | PR #17 (`f78b95f`) → main | 取り込み・意図した破壊的API変更を確認 |

元PR #9・#10・#11はclosedだが、個別の`merged_at`はnullである。
これらのPRが単独でmainへmergeされたという履歴ではない。
実際には[統合PR #13](https://github.com/GlassLeaf/image_downloader/pull/13)が、
各修正・調査記録を履歴ごとmainへ取り込んでいるため、修正の取り込み漏れではない。
PR #12の直接の統合先もmainではなくQS-016ブランチである。

後続の[PR #14](https://github.com/GlassLeaf/image_downloader/pull/14)、
[PR #15](https://github.com/GlassLeaf/image_downloader/pull/15)、
[PR #17](https://github.com/GlassLeaf/image_downloader/pull/17)はmainへ統合済み。
既存workflowテストのタイミング競合を修正するPR #16もmainに含まれている。
issue #1～#8は今回のAPI確認ですべてclosed / completedだった。今回、PRやissueの状態は変更していない。

## 現行コードで確認した保証と互換性

| ID | 確認した実装・挙動 | 維持する制限・意図された変更 |
|---|---|---|
| QS-001 | verifierが受理したtreeをPluginRecordのprivate slotへ保持。入口・相対importのソースbytesを読み、同じbytesのハッシュを照合してコンパイルする。未署名pycを使わず、変更・未宣言helperを拒否。installのclass検査と失敗・unload・close時のfinder/module解放も確認 | off / bypass-allの検証迂回、外部依存の通常importを維持。同梱のソースなしpyc・native helperは拒否。信頼済みコードのsandboxではない |
| QS-002 | GenericHtmlPlugin本体の自動Refererだけを変更。最終ページURLを基準にuserinfo・fragmentを除去し、同一originはパス・クエリを維持、別originはoriginだけ、HTTPS→HTTPはNone | 明示Referer・独自plugin・継承classの互換性境界を維持。完全Refererを要求するCDNでの失敗はあり得る。自動緩和なし |
| QS-010 | 共通の非公開有限値判定で巨大整数のOverflowErrorを捕捉。設定・workflow API・lock constructor・CLIの既存検証エラーへ正規化。拒否を通信・処理・取得開始前に行う | 型・bool・文字列・正数/非負数・Noneの入口ごとの規則を維持。新しい実用上限や設定項目は追加していない |
| QS-011 | 暗号容器・export情報・復号後recordを構造/型/長さ/base64/有限値で検証。想定内の不正データは固定文言のAuthenticationErrorに統一。全recordの検証後にmergeし、値なしCookieのNoneを保持 | 正常なformat=1・AES-GCM/scrypt/keyring・mergeを維持。不正型の暗黙変換は拒否。明示file importでの破損復旧は維持し、不正import元や保存失敗では既存ファイルを保持 |
| QS-012 | send_messageの非空拒否辞書を検出し、SMTP終了後に部分拒否を通知失敗として扱う。全拒否と分類を分け、拒否件数・正規化した宛先ドメイン・設定SMTPホストを通常/debugログへ記録。失敗イベントは1回、成功イベントなし | 完全アドレス・SMTP応答本文・認証情報は診断に出さない。他通知経路と主処理結果を維持。一部送信済みのため自動再送なし。全受理は最終配送の保証ではない |
| QS-013 | 3種のservice組立てとregistryの構築ガード、sink→loggerの所有権移譲、逆順解放、active loop内の一時threadでの非同期closeを確認。通常例外・中断・cleanup失敗でも元の例外を保持し残りの解放を継続。正常時はserviceへ所有権を移す | 未使用の本体所有資源に限定。失敗時のcookie保存・通知送信や生成ファイルの削除は行わない。別runtimeのmodule/finderを維持 |
| QS-016 | 恒久接頭辞IMAGE_DOWNLOADER_PLUGIN_の下でIDと参照名をUTF-8 surrogatepassの完全HEXで別々に符号化。新名の非空値→既存keyringの順で解決。旧名fallbackなし | 環境変数利用者の名前移行が必要。旧名は利用者が定義元から確認して削除する。keyringのservice/usernameは維持。QS-015により現在の例外名はSecretNotFoundError |
| QS-015 | 両例外をUnsupportedSiteFeatureError / SecretNotFoundErrorへ改名し、定義・export・raise・catalog・文書/API一覧を更新。旧公開名・aliasなし。Ruffの通常selectにN818を含む | 計画どおり破壊的API変更。継承・constructor・固定文言・error code・CLI終了コードは維持。旧名を使う外部pluginは修正・再署名が必要。同梱署名済みpluginソースは変更していない |

主要な照合先は、plugins/source_loader.py・lifecycle.py・management.py・builtin.py、
_numeric.pyとその各呼出し、storage/cookies.py、observability/notifications.py、
application/composer.py・construction.py、storage/interprocess_lock.py、
credentials/plugin_secrets.py、exceptions.py・__init__.py・pyproject.tomlである。
修正コミットとmainのファイルごとのblob ID、後続変更の有無は証拠JSONへ記録した。

## 今回のローカル検証

Windows 10 build 19045、CPython 3.11.4、HTTPX 0.28.1で実行。
pytest 9.1.1、Ruff 0.16.5、mypy 2.3.1。依存版・実行ファイル・コマンド全文は証拠JSONに記録する。

- `pytest tests/v3 --cov=src/image_downloader --cov-fail-under=75 ... -q`:
  **2,128 passed / 17 skipped / 0 failed / 0 errors**、690.75秒。
  coverage **88.71%**（分岐込みの既存設定）、75%基準を満たした。
  外部期限1,800秒のwatchdogは発動せず、pytest終了コード0だった。
- Ruff通常規約とN818単独（ignore/noqaを無効化）はともに違反0件。
- mypyは96 source files成功、pip checkは依存不整合なし。
- 17 skipはローカルのsymlink作成権限による5件とopt-in Mailpitの12件。
  skipを成功に含めず、個々の理由を証拠JSONに保存した。

次表は**今回の全体実行のJUnitから抽出した関連テストファイルの件数**であり、別の実行結果を合算していない。
既存テストも含むため、各修正で新規追加した件数を意味しない。

| ID | 通常回帰テストファイル | 今回の結果 |
|---|---|---|
| QS-001 | plugins/test_verified_source_loading.py | 81 passed / 1 skipped |
| QS-002 | application/test_generic_workflow.py | 76 passed |
| QS-010 | configuration/test_finite_numbers.py | 137 passed |
| QS-011 | storage_output/test_cookie_data_validation.py | 153 passed |
| QS-012 | observability/test_smtp_refusal.py | 52 passed |
| QS-013 | application/test_composition_cleanup.py、storage_output/test_interprocess_lock.py | 64 + 94 passed |
| QS-016 | credentials/test_plugin_secrets.py | 40 passed |
| QS-015 | distribution/test_exception_names.py | 12 passed |

全体には公開API・文書契約、同梱pluginのstrict load、Cookie・状態・履歴・出力・catalogの
複数プロセス/lock回帰テスト、既存workflowの中断・timeoutテストも含まれる。
今回の実行ではそれらに失敗はなかった。

## 対象mainコミットのCI

[quality run 38293432656](https://github.com/GlassLeaf/image_downloader/actions/runs/38293432656)
はhead_branch=main、head_sha=`f78b95f115ffa3b8b309cd0fa0f0018df56cd8aa`、
attempt 1、completed / successだった。
2026-10-13 03:48～03:55 JSTの結果を今回APIで再確認した。

ubuntu-latest / windows-latest / macos-latest × Python 3.11 / 3.12 / 3.13 / 3.14の
**12 test jobがすべて成功**し、3 OSの**3 package-smoke jobもすべて成功**している。
既存workflowでtest jobはpip check・Ruff・mypy・coverage付き全体pytestを、
package-smokeはwheel/sdistのbuildとtools/release_smoke.pyを実行する。
branch PRの成功だけでmainの成功を代用していない。今回はCIの再実行やローカルpackage smokeの再実行は行っていない。

## QS-013関連ロックの保証範囲

同期・非同期とも、open→排他取得→空なら1バイト書込み/flush→所有権確定の順序である。
非バッファstreamを使用し、所有権確定前の失敗では取得済みlockの解放とcloseを試みる。
これにより従来の排他外初期化を解消している。

Windowsの未作成対象は、同じ親の一時ファイルへ1バイト書いてcloseした後にrenameで公開する。
既存非空ファイルを置換・truncateせず、openのO_CREATを除去して消失時の空ファイル再作成を防ぐ。
既存空ファイルは同じfileを取得後に初期化する。確認された共有違反だけを0.1秒待って1回再試行し、
再試行時も親と対象のfile identityを検査する。これらの回帰94件は今回すべて成功した。

一方、[保護環境での反復比較](workflow-history-lock-stability-comparison-2026-10-12.md)には、
修正後にも既存空条件のnative openが滞留した記録がある。
今回の通常テスト成功は、その過去の失敗を取り消す根拠にはならない。
OS内部のopen・一時ファイルI/Oが復帰しない停止は、open後に設定するlock待機timeoutでは強制中断できない。
初回公開と返る共有違反への限定再試行は、コード内コメントどおり暫定措置として扱う。
今回、COMODO保護状態の切替や専用の長時間比較probeは実施しておらず、保護状態によらない無停止を新たに保証しない。

## 証拠と完了範囲

- [検証JSON](raw/qs-remediation-main-verification-2026-10-13.json): コミット祖先・blob ID、修正ブランチtip、
  PR統合先、issue状態、対象mainのCI全job、ツール版、JUnit集計、skip理由、175ファイルのSHA-256。
- [今回のv3生ログ](raw/qs-remediation-main-verification-2026-10-13-v3.stdout): 全体実行とcoverage結果。

確認した現行mainに対し、8件の修正実装とテストが取り込まれ、意図された修正が維持されていると判断する。
未実施の保護状態・Mailpit・ローカルsymlink条件を成功扱いにせず、暫定策の残存リスクも維持する。
追加の本体修正、PR/issueの変更、commit/pushは行っていない。
