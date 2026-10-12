# QS-013 修正確認：組立て失敗時の所有資源解放

対象issue：[QS-013 / #6](https://github.com/GlassLeaf/image_downloader/issues/6)。
当時の[監査報告書](report.md#qs-013)、指摘データ、問題挙動をassertする
[プローブ](behavior_probe.py)は変更していない。修正後の安全な期待値は通常回帰テストで検証する。

## 修正保証と所有権

通常のcompose、CLI inspection、workflow plan、compose_registryに構築ガードを追加した。
組立て中はcomposerが作成済み資源を所有し、完成したserviceまたはregistryを正常に返す段階でだけ
所有権を移譲する。内部の依存組立て失敗でも、すでに作成した資源を閉じる。

- 作成済みlog sinkはlogger構築成功後にloggerへ移譲し、二重closeを避ける。
- 完成した依存DTOを受け取った後にservice構築が失敗した場合も、画像processor、gateway、logger、registryを解放する。
- registryのbuiltin登録・prepare失敗では当該runtimeを閉じ、そのmoduleとfinder登録を解放する。
- 元の例外の同一性・causeを保持し、KeyboardInterrupt・CancelledErrorでも後始末を試みる。
- cleanupの例外・中断を固定診断に留め、後続資源の解放を続ける。例外本文・秘密値・パスを追加診断へ出さない。
- failure cleanupはservice.closeを呼ばず、cookie保存・通知送信を行わない。

公開API・同期compose・依存DTOのコンストラクター／フィールド／比較・設定schema・正常終了時のcloseは維持する。
各構築呼出しは従来の形を維持し、独自の依存構築結果をserviceへ渡す経路も保持する。
gateway、画像processor、file sinkは、ローカルのlock等の初期化を済ませてから資源を取得する。

## 非同期終了処理と制限

呼出元でasyncio event loopが動作していなければasyncio.runで非同期cleanupを完了する。
動作中の場合は一時スレッドの独立loopで実行し、終了を待つ。
待機中に再中断された場合も元の構築エラーを保持して終了を待つ。
同期資源のcloseは呼出元スレッドで行い、完成したserviceの通常closeは従来の経路を使う。

対象は本体が構築し、通信・画像処理をまだ開始していない資源である。
利用済みclient・独自のthread/loopに依存する資源を移動するための一般機能ではない。
HTTPXの非公開状態を操作せず、gateway.closeからclient.acloseを実行する。
他runtimeの所有module・finder登録は保持する。

ディレクトリ、log、lockファイルの作成自体は取り消さない。cookie/stateの移行は不要である。
失敗時の例外返却は終了処理の完了まで遅れる。後始末自体の失敗、プロセス強制終了、
回復不能なメモリ不足では完全解放を保証できない。独自プラグインのimportが作った外部資源や
外部依存moduleのすべてを巻き戻す保証は含めない。

## 検証環境・基準

- 基準コミット：`92a3d0cdd3f81eba553bd2920b9c768922947edb`。
- 実装修正コミット：`d7e6a1bec2b58a66e702356f16c8dc3d7a107cd9`。
- 着手時の作業ツリー：clean。修正ブランチ：`codex/fix-qs-013-construction-cleanup`。
- 実行日：2026-10-11（Asia/Tokyo）。Windows 10 build 19045、Python 3.11.4。
- Ruff 0.16.5、mypy 2.3.1、pytest 9.1.1、pytest-cov 7.1.0、HTTPX 0.28.1、httpcore 1.0.9。
- 最小対応版検証：HTTPX 0.27.0とsniffio 1.3.1を検証専用ディレクトリに導入。既存環境・依存宣言は変更していない。

## 検証結果

通常回帰テストは[test_composition_cleanup.py](../../../tests/v3/application/test_composition_cleanup.py)に追加した。
通信はMockTransport、keyring/cookieは模擬、workerは模擬executorを使用する。

| 検証 | 結果 |
| --- | --- |
| QS-013の新規回帰テスト / HTTPX 0.28.1 | 64 passed |
| 同じ回帰テスト / HTTPX 0.27.0 | 64 passed。検証launcherの事前importによるpytest assert-rewrite warningが1件あり、製品の警告ではない |
| Ruff | 成功 |
| mypy | 95 source filesで成功 |
| 公開API・文書契約 | 21 passed |
| ローカルv3全体・カバレッジ | 1 failed / 1685 passed / 17 skipped、679.33秒、88.19%。失敗の詳細は下記 |
| ローカル配布package smoke | wheel・sdistビルド成功、非editable installによるoffline smoke成功 |
| CI / 3 OS × Python 3.11〜3.14・3 OS package smoke | [run 38069118436](https://github.com/GlassLeaf/image_downloader/actions/runs/38069118436)、attempt 2で15/15 success。初回の失敗と再実行は下記 |

回帰テストではcookie読込み・snapshot・processor構築・output locks構築・service構築・logger構築・
registry組立てに失敗を注入する。同期／動作中loop、例外／中断、cleanup失敗、cleanup待機中の再中断、
反復失敗、別runtimeの遅延import、正常時の所有権移譲とidempotent closeを確認する。
module／finder登録の前後比較、実際のfile streamとclientのclosed状態、worker shutdown、
終了loop・スレッドの解放、cookie保存・通知の非実行をassertする。

再実行例：

```powershell
python -m pytest tests/v3/application/test_composition_cleanup.py -q
python -m ruff check .
python -m mypy src/image_downloader
python -m pytest tests/v3 -q --cov=image_downloader
python -m build --sdist --wheel
python tools/release_smoke.py
```

historical probeは修正前の問題をassertするため、修正後には失敗することがある。
修正後の合否判定には上記の通常回帰テストを使う。

## ローカル全体テストで残った既存の失敗

`test_workflow_history.py::test_independent_processes_merge_under_shared_history_lock`で、
子プロセスの`join(60)`後にも`exitcode`が`None`となり失敗した。
同じテストの単独再実行でも再現した（sandbox内64.83秒、sandbox外70.27秒）。
基準コミット`92a3d0c`を隔離したコピーで実行しても同じassertionが失敗した（67.09秒）。
そのためQS-013修正前にも存在する失敗と確認したが、子プロセスが終了しない根本原因は未確定である。
sandboxが原因と断定せず、ローカル全体が全件成功したとも判定しない。
該当テスト・履歴保存・interprocess lockの実装は今回変更していない。

2026-10-11の[追加調査](workflow-history-process-hang-2026-10-11.md)で、停止がlockファイルの
Windows open内部にあり、期限の設定前であると確認した。初期化用1バイトの排他外書込みを省く隔離試作では
10回成功し、同じコピーで復元すると6回中4回停止した。プログラム側の初期化競合と、
COMODOのmoduleを含むWindows I/O経路の未確定な内部要因を区別して記録した。

同日、ユーザーがCOMODO Internet Security Premiumの停止可能な機能をすべて一時停止した条件で
[再調査](workflow-history-process-hang-2026-10-11.md#comodo一時停止後の再調査)した。
未作成lockの38試行では従来のopen停止を観測しなかったが、初期化flushのPermissionErrorが3試行に残った。
関連テストは1 failed / 93 passedで、失敗は同じ履歴workerの初期化、QS-013の64件は成功した。
この調査時点ではCOMODOがopen停止の再現条件に影響した可能性を支持するが、再有効化との比較はなかった。
停止後もguard64.dllは新規Pythonに読み込まれていた。
本体側の排他外初期化の問題は残り、ローカル全体が全件成功したとの判定は変更しない。
状態のraw値と結果は[一時停止後の証拠](raw/workflow-history-security-paused-evidence.json)に保存した。

同日06:19 JSTからの[再有効化後の調査](workflow-history-process-hang-2026-10-11.md#comodo再有効化後の再調査)では、
元の60秒待機の単独テストで再びopen停止を観測した。未作成・空ファイルの比較はそれぞれ6回中3回停止し、
1バイトの事前作成は6回成功した。停止は初回と同じguard64.dll／NtCreateFileを通る経路で、
その間にも別プロセスが同じbyte lockを取得・解放できた。
初期化呼出しを省いた同じ隔離試作は、再有効化後も20回成功した（55.45秒）。
有効時・一時停止中・再有効化後の比較から、保護環境との相互作用がopen停止に影響する根拠が強まった。
Windows Defenderの登録値も同時に変化しており、COMODO単独の特定機能の欠陥までは確定していない。
詳細と出典は[再有効化後の証拠](raw/workflow-history-security-reenabled-evidence.json)へ保存した。

ローカル17 skipはMailpit opt-inの12件と、実行環境のsymlink作成権限に依存する5件である。
srcおよびtests/v3の175ファイルについて、全体テスト開始後のSHA-256比較で変更がないことを確認した。
元のログ・カバレッジJSON・基準コピーはignore対象の`.runtime/qs013-verification/`に保存した。

## CIの確認結果

修正コミット`d7e6a1b`のquality workflowで、3 OS × Python 3.11〜3.14のテスト12件と
3 OSのpackage smokeを確認した。テストジョブではpip check・Ruff・mypyも成功した。
機械可読な集計は[qs-013-verification.json](qs-013-verification.json)に保存した。

| OS | 各Python版の結果 | coverage |
| --- | --- | --- |
| Ubuntu | 1690 passed / 13 skipped | 87.97〜88.04% |
| macOS | 1690 passed / 13 skipped | 87.97〜88.04% |
| Windows | 1691 passed / 12 skipped | 88.20〜88.26% |

CIの12 skipはMailpit opt-inである。Ubuntu/macOSにはWindows専用テストの1 skipが加わる。
初回はWindows 3.14の既存`test_cancel_or_timeout_distinguishes_interrupted_and_not_started[True]`が
`started.wait()`の5秒制限でTimeoutErrorとなった。他の14ジョブは初回成功した。
全ジョブ終了後、該当Windows 3.14ジョブを同一コミットのまま再実行し、1691 passed / 12 skippedで成功した。
最終のworkflow conclusionはsuccessである。再実行の成功だけからタイムアウトの根本原因は断定しない。
この既存テストとworkflow本体は今回変更していない。

この確認記録と集計の追記では、本体コード・回帰テストを変更していない。

## 別環境の調査を取り込んだ再確認（2026-10-11）

調査資料だけの変更である`c4ef652`へfast-forwardし、
[別環境との比較](workflow-history-cross-environment-recheck-2026-10-11.md)を追記した。
最新v3全体は1685 passed / 17 skipped / 1 failed、coverage 88.19%、Ruff・mypyは成功した。
QS-013と切り分けていた履歴の複数プロセステストは、この全体実行では成功した。
1 failedは署名checkout試験のgit cloneで、sandbox由来の`.git`所有者と実行ユーザーの違いが原因だった。
対象パスのみのプロセス限定safe.directoryで既存テストは成功した。グローバル設定は変更していない。

別環境の3,000回の取得競合では排他外初期化flushの失敗が46件あり、初期化なしは0件だった。
この環境の強い競合試験では初期化なしでも従来と同じopen停止を確認したため、
「初期化除去だけで停止も解消できる」とは判定しない。flush競合とopen停止を別の経路として扱う。
過去の試験結果・CI記録は保存し、今回の結果を合算して全体成功扱いにはしていない。
本体コード・既存テストの追加変更はない。

## システム再起動後の再確認（2026-10-11）

ユーザーによる09:46 JSTのシステム再起動後に、同じHEAD・主要ツール版で
[再調査](workflow-history-post-reboot-2026-10-11.md)を行った。
v3全体は1686 passed / 17 skipped、失敗0、coverage 88.19%、Ruff・mypyも成功した。
元の履歴テストは独立実行3回と全体実行で成功した。署名checkout試験も全体実行で成功した。
前回のGit所有者チェックは再起動後も残るため、当該パスだけのプロセス限定safe.directoryを
全体テストで指定した。この改善を再起動の効果とは扱わない。

専用競合試験では、現行ソース・初期化なしの隔離コピーともopen停止が残った。
native stackは再起動前と同じguard64.dll／NtCreateFile経路で、
停止中にも別プロセスが同じbyte lockを取得・解放できた。
システム再起動で停止が解消したとは判定せず、初期化flush競合とopen停止を分けた評価を維持する。
本体・既存テスト・設定の168ファイルのSHA-256は一致し、保護設定やグローバルGit設定は変更していない。
詳細は[再起動後の証拠](raw/workflow-history-post-reboot-evidence-2026-10-11.json)に保存した。

## COMODOアンインストール後の再確認（2026-10-11）

ユーザーによるCOMODOアンインストール後、同じHEAD・本体ソース・Windows build・主要ツール版で
[再調査](workflow-history-post-uninstall-2026-10-11.md)を行った。
COMODOのSecurity Center登録・確認対象プロセス・7つのdriver名と、新規Pythonへのguard64.dllのロードは見つからなかった。
前回に停止した公開probeの4条件はすべて200ラウンドを完了し、従来のopen停止は今回未観測だった。
有効のままの再起動では停止した結果と合わせ、COMODOを含む保護環境との相互作用が関与した根拠が強まった。
Defenderの登録値と起動セッションも変わっているため、特定のCOMODO機能・DLLの単独の欠陥とは断定しない。

一方、排他取得前の初期化flushは、5組・3,000回の現行コードの取得で258件失敗した。
元の履歴テストはplugin自動読込み無効の20回中1件、通常条件の3回中1件が同じ経路で失敗した。
初期化なしの完了した比較条件はエラー0だったが、1条件は最初の開始barrierで中断し、workerの停止箇所は未確定である。
この中断は従来の製品open停止と分類せず、結果欠落を成功に数えない。追加の600回成功も元の中断を取り消さない。

v3全体は1686 passed / 17 skipped、失敗0、coverage 88.19%、Ruff・mypyも成功した。
全体の履歴テストと署名checkoutテストは成功した。当該`.git`パスだけのプロセス限定safe.directory条件を維持した。
独立実行で確認した初期化失敗は残るため、アンインストールで既存問題がすべて解消したとは判定しない。
本体・既存テスト・設定168ファイルは前回・今回の前後で一致し、保護設定やグローバルGit設定は変更していない。
詳細は[アンインストール後の証拠](raw/workflow-history-post-uninstall-evidence-2026-10-11.json)に保存した。

## 履歴ロックの回数・時間・競合条件を変えた比較（2026-10-11）

COMODOアンインストール後の同じHEAD・本体ソース・Pythonで、
[30〜800ラウンド、3／8 worker、保持0／1／25msの比較](workflow-history-open-variability-2026-10-11.md)を行った。
主試験48条件のうち45条件が観測を完了した。保存できた97,332回のopenの最大は51.305msで、
従来の長時間open停止は有効な観測で確認されなかった。中断した3条件と欠落結果を成功に数えない。
初期化flushの失敗は完了条件353件・中断条件の生存worker70件あり、元の履歴テストも10回中1回同じ経路で失敗した。
初期化なしの完了した13,110取得、open-onlyの72,000回はエラー0だった。

中断条件は定期stack出力途中のアクセス違反を伴い、残るworkerのprobe内barrier待機も確認した。
定期dumpあり／なし各2回の補助比較では、なしの2回は完了、ありの1回はアクセス違反・1回は完了した。
観測方法とPython実装の関与を支持するが、native障害位置が未取得であり原因確定とはしない。
この異常終了を、従来のguard64.dll／NtCreateFileを通る生存workerのopen停止と混同しない。

以前に全workerの初期化を除いてもopen停止した結果は残り、flush失敗をopen停止の必要条件とは判定しない。
初期化除去だけでopen停止も直るという保証を追加しない。今回はv3全体を再実行しておらず、
前回の全体・CI結果はその時点の記録として保持する。Ruff・mypyは再確認して成功した。
本体・既存テスト・設定168ファイルは前回・今回前後で一致し、QS-013の追加実装修正は行っていない。
詳細は[今回の証拠](raw/workflow-history-open-variability-evidence-2026-10-11.json)と
[実行別CSV](workflow-history-open-variability-runs-2026-10-11.csv)へ保存した。


## COMODO新版再導入後の再確認（2026-10-11）

以前の設定を復元したCOMODO Internet Security 2027 Premium 12.4.0.8170で、
[再調査](workflow-history-post-reinstall-2026-10-11.md)を行った。
旧版12.2.3.8026と同じguard64.dll／NtCreateFile経路のopen停止を現行・初期化なしで再現した。
定期dumpなしのopen-only対照、さらに本体moduleをimportしない標準ライブラリ対照でも停止した。
初期化flush失敗・製品のbyte lock・本体のmodule読込みは、このopen停止の必要条件ではない。
ファイルの削除・新規作成やtruncate・書き直しを伴う競合openと、保護環境との相互作用が主要候補となる。
特定のCOMODO componentの欠陥は、kernel待機情報がなく未確定である。

元の履歴テストの独立10回と全体実行は成功した。
v3全体は1685 passed／17 skipped／1 failed、coverage 88.19%だった。
1件は調査用basetempの長さによるGit checkout-indexのFilename too longで、短いパスで同じテストは3回成功した。
全体の失敗と単独再確認を別に保存し、全体1686成功を再確認したとは扱わない。
Ruff・mypyは成功。本体・既存テスト・設定168ファイルは以前・今回前後で一致した。
QS-013の追加実装修正や保護設定の変更は行っていない。
詳細は[再導入後の証拠](raw/workflow-history-post-reinstall-evidence-2026-10-11.json)に保存した。


## 保持ファイルのサイズ・openモードの追加比較（2026-10-11）

[0／1バイトと3モードの比較](workflow-history-open-size-modes-2026-10-11.md)を追加した。
3 worker・2,000ラウンド・3回、定期dumpなし、本体module・initializer・byte lockを使わない条件で、
同じ1バイトを保持するr+b／a+bは各3回完了した。
保持した0バイトはr+b／w+b／a+bの各3回が停止し、初期1バイトのw+bも0バイト化後に各3回停止した。
毎回削除・作成するa+b、毎回1バイトへ書き直すr+bの対照も各3回停止した。

0バイトの3モードで、以前と同じ_wopen→CreateFileW→guard64.dll→NtCreateFile系統のnative停止を確認した。
保持した空ファイルの競合openも発生条件に含まれ、毎回の削除・書き直しは必要条件ではない。
事前作成だけでの解消を保証せず、非空ファイルを更新せず保持する条件を対策候補として評価する。
サイズ・事前書込み・検査履歴を完全には分離できず、COMODO内部の原因は未確定である。
本体・既存テスト・設定168ファイルと過去資料を追記前に照合して保存した。
この追加調査では本体修正、別Python版の比較、v3全体・CIの再実行は行っていない。
詳細は[証拠JSON](raw/workflow-history-open-size-modes-evidence-2026-10-11.json)と
[実行別CSV](workflow-history-open-size-modes-runs-2026-10-11.csv)に保存した。


## open前後の待機とWindowsエラーの追加確認（2026-10-11）

[待機位置・時間とエラー捕捉の比較](workflow-history-open-delay-errors-2026-10-11.md)を追加した。
以前のopen-onlyには明示sleepがなく、今回open前・handle保持中・close後の10／100msを
待機なしと比較した（8条件・各100ラウンド・2回、計112試行）。34試行完了、78試行open滞留。
1バイトを保持するr+b/a+bは全設定で各2回完了した。
空ファイルではhandle保持中の待機で一部完了したが、同じ設定の別試行で停止した。
held-100を1,000ラウンドへ増やした追試もr+b 87、w+b 60、a+b 122ラウンドで停止し、
約65秒のopen滞留を確認した。固定sleepによる安定した解消を支持しない。

戻ったopen/closeの例外は主比較・追試・native-opener補助比較で0件。
未復帰openの最終GetLastErrorは取得不能とし、barrier失敗やprocess終了コードと区別した。
返る失敗の対照ではfile不在2、path不在3、access拒否5、sharing違反32を
doserrnoとCreateFileW復帰直後のGetLastErrorで照合した。
対照の5／32はどちらもPython errno 13となり、errnoだけで原因を区別しない。
これらの対照値を、滞留したopenの原因コードとしては扱わない。

本体・既存テスト・設定168ファイルと既存資料のhashを追記前に照合した。
本体修正、v3全体・mypy・CIの再実行は今回行っていない。
各試行・欠落worker・実測sleep・native stack・取得コードを
[CSV](workflow-history-open-delay-errors-runs-2026-10-11.csv)と
[証拠JSON](raw/workflow-history-open-delay-errors-evidence-2026-10-11.json)に保存した。
