# 共通ファイルロックの初期化修正とWindowsの公開処理

本書は保護停止時の検証記録である。後続の[保護有効時の追試](workflow-history-lock-security-enabled-recheck-2026-10-12.md)では
最終版にもopen滞留が再現し、保護有効時の暫定対策の完了条件は未達と判定した。

## 判定

本記録は初期化flush競合とWindowsのopen停止への暫定対策を区別する。
排他外の初期化は現行の修正前ソースで再現し、ロック取得後の初期化へ変更した。
Windowsでは書込み済みの一時ファイルを公開し、作成禁止で既存ファイルを開く方式を追加した。
比較中に別の「openから返る共有違反」が見つかったため、限定的な待機・再試行も加えた。
**COMODO保護機能有効時の最終版は未検証であり、過去のopen停止の根本原因を修正したとは判定しない。**

最終検証の集計は以下の検証結果とraw evidenceを参照する。

## ソースと作業ツリー

開始時HEADは`c4ef652bed1d3fa45813b1e2e790b327b9e82b68`。
開始時に既にあった2件の追跡済み文書変更と未追跡の調査資料は変更せず保持した。
当時の監査資料124ファイルのSHA-256を保存し、終了時の再照合でも変更がないことを確認した。
比較ソースは`src`全体をキャッシュ・egg-infoを除いて固定し、workerの読込み位置とlockソースのSHA-256を記録した。

| 段階 | コミット | 内容 |
| --- | --- | --- |
| 初期化修正のみ | `3d40a0f5af64a59dbe771c78ccf93f9434abb1fa` | ロック取得後の初期化、非バッファ化、失敗時の解放 |
| Windows公開方式の初版 | `3f77918a98064f41e808ec6498b778cbba3ae1ab` | 1バイト一時ファイル、非上書きrename、O_CREAT除去 |
| 共有違反の即時再試行版 | `81563932116d9c4c0d26482f2b80f42ef9cf134f` | 既に見えるwinnerへのrename省略、共有違反32を一度再試行 |
| 最終実装 | `e7e45e00cb2ec574b6d4bd71af9e80572c75a676` | 0.1秒を一度待つ、非同期はawait、最初の親・対象IDを保持 |

2つの実装変更を別コミットとし、公開競合試験で必要になった補強は追加コミットとして履歴を残した。
最終実装のWindows作業ツリー／固定ソースSHA-256は
`f3b9253ed7dd7178265818ee7728ebd3a5f274e60b3c021ebe190018fac0b260`。
これはファイルbytesのSHA-256であり、GitコミットIDではない。

## 環境と保護状態

- Windows 10 Home、10.0.19045、64bit Python 3.11.4。
- pytest 9.1.1、pytest-cov 7.1.0、coverage 7.16.0、Ruff 0.16.5、mypy 2.3.1。
- HTTPX 0.28.1、Pillow 12.3.0。試験用の既存psutilは7.2.2、buildは1.6.1。
- COMODOはユーザー申告で「ユーザーが停止できる機能をすべて停止したまま」。設定の変更は行っていない。
- 登録状態のraw値はCOMODO Antivirus 393216、Windows Defender 397584。機能停止の範囲をこの数値だけから推定しない。
- `guard64.dll` 12.4.0.8170は検証プロセスにもロードされていた。SHA-256は
  `537BA3170E09EFB65C5DD4AC708F805E6BAA608ADDDC45EAF8ECEC72513B7E8A`。
- 記録した最終起動時刻は2026-10-11 18:42:28.5 +09:00。

停止中でもDLLが存在・ロードする点を保護機能有効と同一視しない。
過去の停止時記録とDefenderのraw値が異なることも、COMODOだけへの原因帰属を避ける理由である。
CIの一般的なWindows環境は、この端末でCOMODOを有効にした比較の代替にはならない。

## 実装の保証と限界

同期・非同期とも、streamのopen後にdeadlineを設定し、OSロックを取得してから空の場合だけ初期化する。
Windowsは先頭1バイト、POSIXは従来のflockを使用する。WindowsではEOFの先の領域もロックできる。
通常の非空ファイルには書き込まず、内容・サイズ・ファイルID・更新時刻を保持する。
初期化は非バッファstreamへ1バイトを書き、戻り値が1であることを確認する。fsyncは追加しない。
[Microsoftのlocking仕様](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/locking?view=msvc-170)

Windowsの未作成ファイルは同じ親の`mkstemp`へ1バイトを書いてcloseし、`os.rename`で公開する。
既存宛先を上書きせず、既に検証可能なwinnerが見えれば不要なrenameを省く。
renameの競合として処理する例外は`FileExistsError`だけで、権限エラー等は伝播する。
公開後の`a+b`はO_CREATだけを除き、対象消失時に空ファイルを再作成しない。
既存空ファイルは置換せず、同じIDのファイルを取得したロック下で初期化する。
[Pythonのrename仕様](https://docs.python.org/3.11/library/os.html#os.rename)

公開方式の初版は単発回帰テストとCIを通過したが、未作成条件1,800回でopenの`errno=13`が8件返った。
既に見えるwinnerへのrename省略だけでは、追加の1,800回でも18件返った。
CRT照会をopen前に追加した診断版では0件、失敗後だけ照会する診断版では8件で、すべてDOSコード32だった。
診断処理の追加自体がタイミングを変えるため、失敗率の差だけから作用機序を断定しない。
失敗後の照会値にはstale値の限界もある。一方、最終実装では毎回CRT値をclearし、
実際の共有禁止handleで32、readonlyファイルで5を取得する対照テストも確認した。
[CRT拡張エラーの公式仕様](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/get-doserrno?view=msvc-170)

Pythonのerrno 13とWindowsのエラー番号13を混同しない。今回の診断値32は共有違反で、5はアクセス拒否である。
どのプロセス・driverのhandleが競合したかは未確定で、rename内部のhandleや保護製品の関与は候補にとどまる。
これは返ったエラーの調査であり、過去の「native openが戻らない」現象の原因特定ではない。
[Windowsのエラー番号](https://learn.microsoft.com/en-us/windows/win32/debug/system-error-codes--0-499-)

即時に一度再試行する版は単独の3反復を通過したが、交互比較では再発したため完了扱いにしなかった。
その比較は意図的に打ち切り、operator-stopの理由と完了済みcaseを別に保存した。
最終版は**EACCESかつCRT値32を確認したopenだけ**、既存poll間隔0.1秒を一度待って再試行する。
同期はsleep、非同期はawaitで待ち、待機中の中断を伝播する。
最初の親・対象のIDを捕捉したcallbackを使うため、待機後に別の親やファイルを受理しない。
2回目も失敗した場合は最初の例外・causeを保持する。アクセス拒否5、他のエラー、CRT値取得不能は再試行しない。
CRTは標準ライブラリctypesからSystem32に限定して取得し、依存・設定は追加していない。

所有権確定前の通常例外・KeyboardInterrupt・CancelledErrorでは取得済みロックの解放とcloseを試みる。
一つの後始末が失敗しても続行し、元の例外を置き換えない。追加警告は固定文言と資源区分だけである。
未公開の自分の一時ファイル・descriptorだけを後始末し、公開済み対象や他プロセスの一時ファイルは削除しない。
親や一時ファイルが差し替わった場合、別のファイルを消すことを避け、固定警告を残す場合がある。

## 比較条件

比較probeは[history_lock_remediation.py](probes/history_lock_remediation.py)。各caseを別processで実行し、
外部120秒deadlineと、独立した成功数・返った例外・worker欠落・停止時phaseを記録する。
定期faulthandler dumpは使わず、試験用ファイルだけを使う。通常テストとの複数process競合試験の並行実行は避けた。
保護状態はユーザー申告のラベルであり、probeは変更しない。

| 条件 | 1 case | 操作・準備 |
| --- | --- | --- |
| cold-missing | 3 worker × 200 round | roundごとに未作成の別名。full acquire、保護区間1ms、共有counterの排他も確認 |
| cold-empty | 3 worker × 200 round | parentが空ファイルを作ってcloseしてから開始。full acquire |
| stable-one | 3 worker × 2,000 round | 最初に1バイトを作ってclose。同じファイルのopen／closeのみを反復 |
| stable-many | 3 worker × 2,000 round | 最初に複数バイトを作ってclose。同じファイルのopen／closeのみを反復 |

各条件3回。source順を回転・反転して比較した。非空保持は過去のopen-only対照に合わせたもので、
2,000 roundのfull acquire／競合の検証ではない。排他と空ファイルへの初期化はcold条件と通常回帰テストで確認した。
parentは全workerのcloseを含むround終了barrier後に次のファイルを準備し、使用中の対象をunlink／truncateしない。
返らないopenにはerrno／winerrorがなく、後から読んだGetLastErrorをそのopenの最終コードとして扱わない。

## 検証結果

<!-- FINAL-RESULTS -->

最終版・B版・修正前の36 case交互比較が完了した。全caseでworker欠落・open滞留・外部watchdogは0。
最終版とB版は全操作成功、stable条件の内容・サイズ・ID・mtimeも全caseで保持された。

| 条件（3回合計） | 修正前の成功／試行 | 初期化修正のみ | 最終版 | 修正前の返った例外 |
| --- | --- | --- | --- | --- |
| 未作成・full acquire | 1,683 / 1,800 | 1,800 / 1,800 | 1,800 / 1,800 | initialization_flush 117件 |
| 既存空・full acquire | 1,597 / 1,800 | 1,800 / 1,800 | 1,800 / 1,800 | initialization_flush 203件 |
| 1バイト保持・open-only | 18,000 / 18,000 | 18,000 / 18,000 | 18,000 / 18,000 | 0件 |
| 複数バイト保持・open-only | 18,000 / 18,000 | 18,000 / 18,000 | 18,000 / 18,000 | 0件 |

修正前の320例外はInterProcessLockErrorのcauseがPermissionError、errno=13、winerrorはNone。
最終版には返った例外がないため、その実行のerrno／winerrorを0として捏造しない。
比較probeの終了値1は修正前の既知の失敗によるもので、最終版の失敗ではない。
即時再試行版の交互比較は意図的な打切りを記録し、外部watchdogによる停止やopen滞留に数えない。

通常ロック回帰テストは94件成功（13.67秒）。空ファイルへの実processロック、同時初期化、短いwrite、
write／flush／close／unlock／公開の失敗、同期／非同期の中断、共有違反の再試行、待機中の親・対象IDの保持を含む。
元の履歴テストは、修正前・B版・即時再試行版の各20回が成功した後、**最終版でも独立20回が成功**。
source pinを各subprocessで記録し、外部120秒watchdogは一度も発動していない。
履歴テストの修正前20回は今回は失敗しなかったため、このテスト単独を不具合不存在の根拠にはしない。

最終実装e7e45e0の[CI run 64](https://github.com/GlassLeaf/image_downloader/actions/runs/38165052166)は15／15成功。
Ubuntu・Windows・macOS × Python 3.11〜3.14の12ジョブでRuff・mypy・v3・75%カバレッジ基準を確認し、
3 OSのpackage smokeも成功した。結果の[記録](raw/workflow-history-lock-remediation-ci-2026-10-12.json)を保存した。
初版3f77918のCIも通っていたが、その後の競合比較で失敗を発見したため、CIだけでは低頻度の競合の保証にならない。

最終実装の最初のローカルv3全体実行は1,773成功・17スキップ・1失敗、613.94秒、カバレッジ88.25%だった。
失敗は既存の`test_cancellation_during_final_log_write_preserves_success_and_revises_history`で、
最終ログ処理の開始を知らせる`started.wait(5)`がFalseになった。ロック例外やログ訂正結果の不一致ではない。
該当テストは単独で成功（call 2.30秒、全体3.35秒）、関連モジュール23件もカバレッジ付きで成功（42.23秒）した。
元の5秒待機と実装は変更せず、同じソースで全体を再実行した。
**最終再実行は1,774成功・17スキップ・失敗0、593.45秒、カバレッジ88.29%**だった。
17スキップはsymbolic link作成不能の5件と、Mailpitのopt-in未構成の12件で、各理由もraw evidenceへ記録した。
Ruffはリポジトリ全体で成功、mypyは95 source filesで成功、`git diff --check`も成功した。

最初の実行には失敗前のphase時間記録がないため、worker起動、IO、スケジューリングの遅延や実装上の問題を
切り分けることはできない。単独実行・関連モジュール・CIの成功だけで、この待機失敗の根本原因を確定しない。
最初の失敗traceと再実行の結果を[raw evidence](raw/workflow-history-lock-remediation-evidence-2026-10-12.json)へ保存し、
失敗実行を成功へ置き換えない。比較36 caseの個別集計は[CSV](workflow-history-lock-remediation-runs-2026-10-12.csv)を参照する。

<!-- /FINAL-RESULTS -->

## 副作用・残存リスク・移行

- 初回作成に一時ファイル作成・書込み・rename・cleanupが増え、親への作成・rename・削除権限が必要になる。
  既存非空ファイルに一時処理は追加しないが、Windowsの同一性検査とCRT処理によるIO／呼出しは増える。
- 返る共有違反の場合だけ、取得前に0.1秒の待機と1回の追加openが増える。deadlineの計算はopen完了後であり、
  この待機はロック競合deadlineの内側ではない。timeout=0でもbackendの取得を一度試す挙動は維持する。
- 既存空ファイルの初回open、一時ファイルを含むOS内部のIO停止は残る。追加openも強制中断できない。
  既定30秒timeoutはnative open／write等の終了期限ではない。電源断時のmarker永続化や保護製品内部の修正も保証しない。
- 対応は本体管理の履歴・Cookie・状態・出力・catalogに共通して適用される。NAS・外部アプリの協調や、
  同じ権限の悪意あるprocessに対する完全なファイルシステムsandboxは保証しない。
- 更新は関連processを終了してから切り替える。古いprocessは排他前の初期化を続け得る。
  移行コマンドやlockファイルの一括削除は不要で、既存空ファイルはロック下で初期化される。
- COMODO有効時の最終版の効果判定は未実施。未導入環境との差や停止時の改善を踏まえた暫定策であり、
  今回の成功数から有効時の成功率・根本原因を推測しない。

## 再実行

固定ソースは、各コミットを作業ツリーへcheckoutして上書きする代わりに、別ディレクトリへ展開した`src`を使う。
実行はリポジトリrootと、既存のpytest・psutilが利用できる同じPython環境から行う。

```powershell
python docs/investigations/quality-security-review-2026-10-06/probes/history_lock_remediation.py NEW_OUTPUT --source before=BEFORE_SRC --source initialization=INIT_SRC --source final=FINAL_SRC --protection USER_REPORTED_STATE
python docs/investigations/quality-security-review-2026-10-06/probes/history_lock_history_recheck.py NEW_HISTORY_OUTPUT --source final=FINAL_SRC --protection USER_REPORTED_STATE --repeats 20
```

出力先は毎回新しいディレクトリにする。修正前を含む比較は既知の失敗により終了値1になり得るため、
終了値だけで修正後の成功を判断せず、source別の成功数・failure_count・protocol_completedを確認する。
[診断対照](probes/history_lock_open_diagnostics.py)はopenerを単発に置き換えて失敗後のCRT値を観測するもので、
通常回帰テストや最終実装の効果判定には使わない。

## 保護機能有効後の追試（2026-10-12）

上記は保護停止時点の記録として保存する。ユーザーが保護機能を有効にした後の
[追試記録](workflow-history-lock-security-enabled-recheck-2026-10-12.md)を追加した。
最終版でも未作成条件3回中2回、既存空条件3回中3回でopen滞留が再現し、
**保護有効時のopen対策の完了条件は未達**だった。同じ非空ファイルのopen-only、ロック回帰94件、
通常の履歴テスト20回は成功した。初期化修正後の新たなwrite／flush例外は観測しなかった。

再試行だけを除いた比較用コピーでは、追加診断で返る共有違反が計3件再現した。
実際の共有禁止handleを使った機能対照で、最終版の同期・非同期の1回再試行が取得を回復することも確認した。
この結果は戻らないopenへの効果保証とは区別する。本体ソースと当時のraw evidenceは変更していない。
