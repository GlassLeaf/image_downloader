# COMODO新版再導入後のopen停止再調査

調査日：2026-10-11（JST）。HEAD：`c4ef652bed1d3fa45813b1e2e790b327b9e82b68`。
ブランチ：`codex/fix-qs-013-construction-cleanup`。
以前からある未コミットの調査資料を保持し、本体・既存テスト・設定を変更せず再観測した。

## 結論

**COMODO Internet Security 2027 Premium 12.4.0.8170でも、以前と同じopen停止を再現した。**
停止時のnative stackは旧版と同じ`_wopen → CreateFileW → guard64.dll → NtCreateFile`の経路だった。
製品の初期化を除いた場合、初期化・byte lockを行わない場合、さらに本体moduleを一切importしない場合にも停止した。
初期化flush失敗・製品のbyte lock・本体のmodule読込みは、今回のopen停止の必要条件ではない。

COMODO不在時に完了したopen-only対照が、再導入後には毎ラウンドファイルを準備し直す条件で停止した。
同じファイルを再利用する対照は今回も完了した。
**ファイルの削除・新規作成やtruncate・書き直し、競合openとCOMODOを含む保護環境との相互作用**が、
より具体的な原因候補となる。
ただしkernel stack、I/O要求、COMODOの内部状態は採れておらず、特定のDLL・driver・機能の欠陥を確定したとはしない。

元の履歴テストの独立実行10回はすべて成功した。専用試験の停止を、この成功で取り消さない。
今回の停止はworkerが生存したままopenに滞留するもので、以前に観測した定期stack出力中のアクセス違反とは区別する。

## 再導入時の条件

ユーザーから「以前の設定を復元」と回答を得た。
新規導入の既定設定との比較ではない。旧設定の復元により設定差の可能性は小さくなるが、
版更新時の設定変換・内部動作を含む実効的な保護状態の一致は独立に確認していない。
調査側で保護設定、除外、サービス、driverを変更していない。

| 項目 | 旧版有効・再起動後 | アンインストール後 | 今回の再導入後 |
| --- | --- | --- | --- |
| COMODO | guard64.dll 12.2.3.8026 | 登録・確認対象driver・DLLロードなし | 登録名 COMODO Internet Security 2027 Premium 12.4.0.8170 |
| guard64.dll | 新規Pythonにロード | 新規Pythonにロードなし | 12.4.0.8170を新規Pythonにロード |
| 起動時刻（JST） | 09:46:31.500 | 13:39:35.500 | 18:42:28.500 |
| COMODO productState | 397312（0x61000） | 登録なし | 397312（0x61000） |
| Defender productState | 393472（0x60100） | 397568（0x61100）、後続調査397584（0x61110） | 393488（0x60110） |
| Windows | 19045.7727、22H2、Core | 同じ | 同じ |
| Python | 3.11.4、MSC v.1934、AMD64 | 同じ | 同じ |
| 本体・既存テスト・設定 | 168ファイルのSHA-256 | 同じ | 前後・以前との一致を確認 |

旧版のDLL版は再起動後に採取した記録に基づく。採取していない時点の版を推測しない。
Security Centerの値はraw値として記録し、個別機能の状態をdecodeしていない。
再導入では起動セッションとDefenderの登録状態も変わり、版番号だけを変えた比較にはならない。

今回の`guard64.dll`、`cis.exe`、`cmdagent.exe`はいずれも12.4.0.8170だった。
新規PythonがロードしたDLLの実パスは`C:/Windows/system32/guard64.dll`で、SHA-256は
`537ba3170e09efb65c5dd4ac708f805e6baa608adddc45eaf8ecec72513b7e8a`。
`cmderd`、`cmdGuard`、`cmdhlp`、`inspect`の4 driverはRunningだった。
調査対象名は以前と同じ7つに限定しており、全componentの網羅的な棚卸しではない。

pytest 9.1.1、pytest-cov 7.1.0、pytest-asyncio 1.4.0、pytest-mock 3.15.1、HTTPX 0.28.1、
AnyIO 4.14.2、Ruff 0.16.5、mypy 2.3.1、psutil 7.2.2と、4つのpytest entry pointは以前と一致した。

## 既存の公開probeによる再比較

[history_lock_contention.py](probes/history_lock_contention.py)を変更せず、
3 worker、200ラウンド、保持1ms、lock timeout 5秒、barrier 20秒で逐次実行した。
以前のprobeとSHA-256は同じである。今回は条件ごとに別parentを起動し、120秒の外部watchdogを付けた。
8秒ごとのstack出力は以前と同じ。この観測機構の影響は後述のdumpなし対照で分離した。

| 条件 | 完了ラウンド／予定200 | 共有counter | 秒数 | 停止・中断の分類 |
| --- | ---: | ---: | ---: | --- |
| 未作成 | 0 | 0 | 25.063 | 1 workerが最初のbarrierに到達せず。stackがなく停止箇所は未確定 |
| 空ファイル | 0 | 2 | 26.656 | worker 1が製品のPath.open内で停止 |
| 1バイト事前作成 | 6 | 20 | 27.438 | worker 0が製品のPath.open内で停止 |
| 初期化なし | 0 | 2 | 27.578 | worker 2が隔離ソースのPath.open内で停止 |

中断した条件のworker結果は欠落し、probeが表示するerrors 0／successes 0は全取得の実測値ではない。
最初の起動中断をopen停止には分類しない。
停止するラウンドが旧版の結果と一致する必要はなく、今回も実行間の揺らぎがある。

## 停止中のnative確認

以前の観測wrapperをそのまま用い、workerのPIDと所有親子関係を確認したうえで追加採取した。
採取threadは短時間だけ停止し、finallyで再開する。ローカルのmodule／export symbolだけを使う。

| 条件 | ラウンド／counter | 対象PID／TID | native経路 | 停止中の別プロセスによる同じbyte lock |
| --- | --- | --- | --- | --- |
| 現行・未作成 | 1／5 | 8896／12364 | ucrtbaseのwopen → CreateFileW → guard64.dll → ZwCreateFile | r+bで取得・解放できた |
| 初期化なし・未作成 | 8／26 | 14476／3880 | 同じ | r+bで取得・解放できた。ファイルは0バイト |

`ZwCreateFile`は採取したexport名で、NtCreateFile系統の呼出しとして記載する。
COMODO内の`Exported+0x18b3a`は近傍exportに基づく表示であり、内部関数名や機能名の特定ではない。
先頭frameの重複やPython内部の近傍export名も、そのまま生の証拠に残した。

旧版と同じWindowsのファイル作成・open経路で待っていることを確認した。
別プロセスのbyte lock取得成功は「byte lockが残っているだけ」という説明には合わないが、
kernelの待機対象や保護製品の内部lockを特定するものではない。

## 定期dumpなしの3回比較

[history_lock_supervisor.py](probes/history_lock_supervisor.py)の既定動作を用い、
定期dumpを無効にした。fatal handlerは有効で、各条件を別parent・120秒の外部watchdog付きで実行する。
shortは30ラウンド・3 worker・保持1ms・barrier 20秒。
open-onlyは2,000ラウンド・3 worker・barrier 60秒で、workerはwrite・flush・byte lockを実行しない。
初期化なしは隔離コピーから呼出し2か所だけを削除し、本体ソースを変更していない。

| 条件 | 1／2／3回目の完了ラウンド | 完了した条件数 | 確認した未完了openの最終観測時間（秒） |
| --- | --- | ---: | --- |
| short・現行 | 3／10／17（予定30） | 0/3 | 25.079／24.859／25.109 |
| short・初期化なし | 30／30／15（予定30） | 2/3 | 3回目24.969 |
| a+b・毎回未作成 | 23／35／0（予定2,000） | 0/3 | 65.015／65.063／64.937 |
| a+b・同じ1バイトファイル | 2,000／2,000／2,000 | 3/3 | 2秒以上の滞留なし |
| r+b・毎回1バイトへ書き直し | 39／44／3（予定2,000） | 0/3 | 65.079／64.922／65.110 |
| r+b・同じ1バイトファイル | 2,000／2,000／2,000 | 3/3 | 2秒以上の滞留なし |

18条件中8条件が完了し、10条件でopen phaseへの滞留を確認した。
外部watchdogによる打切りはなく、barrierの期限後にprobeが所有workerを終了して結果を保存した。
欠落したworkerの結果を成功やエラー0に数えない。
保存された36,578回のopenには、完了条件36,180回と中断条件の残ったworker398回が含まれる。
戻ったopenの最大は184.200msだったが、戻らないopenの所要時間をこの最大値へ混ぜない。

short初期化なしの2回成功は、200ラウンド条件と3回目shortでの停止を取り消さない。
初期化除去だけでopen停止が直る保証に広げない。
今回の保存された取得結果には初期化flushエラーがなかったが、停止条件の結果欠落が多く、
以前に確認したflush競合の解消を意味しない。本体の該当処理は未変更である。

2回目のa+b・再作成条件では、定期dumpなしのまま所有workerのnative stackを追加採取した。
PID 11180は`wopen → CreateFileW → guard64.dll → ZwCreateFile`、
他の2 workerはmultiprocessingの待機経路だった。
定期dumpがなくても同じopen停止があることを、shared phaseとnativeの両方で確認した。
この条件だけはnative採取による短時間のthread停止がある。停止は採取前から観測されていた。

## 本体をimportしない標準ライブラリ対照

前のopen-only workerは本体をimportした後にopenのみを実行していた。
module読込みの副作用の関与も分離するため、
[history_open_only_stdlib.py](probes/history_open_only_stdlib.py)を追加した。
既存probeから標準ライブラリの試験進行・ファイル準備・phase採取・統計だけを再利用し、
workerは直接`Path.open()`とcloseだけを実行する。本体moduleをimportせず、定期dumpも使わない。

| 条件 | 完了ラウンド／予定2,000 | 結果 | 未完了openの最終観測時間（秒） |
| --- | ---: | --- | ---: |
| a+b・毎回未作成 | 21 | worker 2がopenに滞留 | 65.047 |
| a+b・同じ1バイトファイル | 2,000 | 全6,000回完了、エラー0 | なし |
| r+b・毎回1バイトへ書き直し | 11 | worker 2がopenに滞留 | 64.985 |
| r+b・同じ1バイトファイル | 2,000 | 全6,000回完了、エラー0 | なし |

parentと結果を保存できた全workerの`sys.modules`には、本体moduleがなかった。
終了したworkerの最終結果は欠落しているため、全workerの最終module一覧を採れたとは表現しない。
そのworkerが実行したソースにも本体をimportする経路はなく、製品のlock・initializerは構築していない。
この追加対照は各条件1回であり、発生率の統計比較には使わない。

open-onlyではparentが全workerのclose後に次のラウンドのファイルを準備する。
a+b・未作成はparentが削除し、workerのopenが作成する。
r+b・書き直しはparentが`Path.write_bytes(b"\0")`で初回に作成し、以降もwbでtruncateして1バイトを書き込む。
この条件でparentはunlinkを実行せず、workerのr+bのopen自体にもO_CREATはない。
再利用条件では同じファイルを最初から最後まで保持する。
parentのmetadata操作・事前書込みまで除いた対照ではない。

機械可読な名前`readwrite-created`と以前の文書の「1バイト再作成」は同じ処理を指していたが、
各ラウンドの削除・新規作成という説明は不正確だった。今回、元probeの`reset_lock()`と照合して訂正した。
過去の観測値・probeの処理・条件IDは変更していない。

## 以前との比較と原因評価

[COMODO不在時の対照](workflow-history-open-variability-2026-10-11.md)では、
同じ4つのopen-only条件が各3回・計72,000回を完了し、長時間open停止は未観測だった。
今回の本体importあり対照は、毎回準備するa+b・r+bの各3回がすべて停止し、
同じファイルの再利用は各3回・計36,000回を完了した。
比較時の定期dump設定とparentの起動方法が異なるため、細かな時間や発生率を完全に同条件とは扱わない。
一方、定期dumpを除いた今回にも生存workerのopen停止があり、観測機構だけの説明は成立しない。

| 仮説 | 今回の評価 |
| --- | --- |
| 旧版だけの問題で新版なら解消 | 12.4.0.8170でも同じnative経路の停止があり、今回の条件では否定された |
| 初期化flush失敗がopen停止の必要条件 | 初期化なし、write／flushなし、本体importなしでも停止し、否定された |
| 製品のbyte lockが必須 | byte lockをしないopen-onlyでも停止し、否定された |
| 本体module読込みが必須 | 標準ライブラリ対照でも停止し、否定された |
| a+bのappendまたはO_CREATだけが必須 | r+bでも停止し、それだけでは説明できない |
| 定期faulthandler出力によるアクセス違反と同じ障害 | dumpなしでも生存workerのopen停止を確認。今回の停止とは区別する |
| ファイルの毎回の準備・競合openと保護環境の相互作用 | 削除・新規作成／truncate・書き直しでの停止、再利用での完了、COMODO不在時との違い、native stackが支持する主要候補 |
| 特定のCOMODO componentが単独で故障 | kernel待機対象や内部処理は未取得で、まだ確定できない |

ラウンドごとのファイル準備は今回の発生条件を絞る手掛かりであり、
すべての過去の元テスト失敗に必要だった、再利用すれば将来も必ず安全、とまでは判定しない。
少数の相関した実行から独立な停止確率や信頼区間を算出しない。

## 通常テストと検証記録

元の`test_independent_processes_merge_under_shared_history_lock`を通常のpytest plugin自動読込みで
独立に10回逐次実行し、すべて成功した。pytest所要時間は2.68〜4.47秒だった。
従来と同じ8秒ごとの診断stack出力を追加したが、今回の10回は8秒未満で完了した。

v3全体は**1685 passed／17 skipped／1 failed、coverage 88.19%**、523.29秒だった。
元の履歴テストは全体実行でも成功した。
失敗は`test_signed_plugins_survive_windows_line_ending_conversion`の`git checkout-index --all --force`で、
同じ専用checkoutでstderrを再採取すると、監査の既存証拠ファイルに対する`Filename too long`だった。
問題の絶対パスは262文字で、今回指定した長い`--basetemp`が原因だった。
clone・read-tree・addは通過しており、以前のGit所有者チェック失敗とは別である。

短い専用`--basetemp`へ変えた以外は同じ既存テストを3回実行し、すべて成功した。
本体・テスト、Gitのglobal設定、Windows設定は変更していない。
全体実行には以前と同じ、当該primary `.git`パスだけのプロセス限定safe.directoryを適用した。
この失敗をCOMODOやopen停止に帰属させない。
全体の失敗を単独成功で置換せず、全体1685成功＋1失敗と再確認3成功を別に保存する。
全体を再実行して1686成功を確認したという表現はしない。

Ruffは`All checks passed!`、mypyは`Success: no issues found in 95 source files`だった。
今回の専用試験と全体実行後も、本体・既存テスト・設定168ファイルのSHA-256は開始前・旧版時・削除後と一致した。
過去の調査ファイルも、この追記前に全てhash一致を確認した。
本体の修正、commit・push、保護設定の変更は行っていない。

## 修正検討と追加で必要な情報

初期化flushについては、以前の[全ブランチ・全52コミットの調査](workflow-history-git-history-2026-10-11.md)で初期コミットから存在した
排他前の初期化経路の修正検討を維持する。
今回のopen-only再現により、その修正とopen停止の対策を別に検証する必要性がさらに明確になった。
openより前にdeadlineを移すだけでは、戻らない同期openを中断できないという設計上の制限も残る。

open停止をさらに特定するには、今回追加した本体import不要の小さな対照を使い、
停止しているNtCreateFileのkernel stack、I/O・filterの待機情報、COMODO側の診断情報を時刻・PIDと対応付ける。
今回の毎回の準備／再利用の差を利用すれば、本体の設定・履歴保存・byte lockを含めずに比較できる。
製品の個別機能や他のfilterとの組合せを比較する場合は、同じprobe・同じfile lifecycle・同じ実効設定を記録する。
この調査では保護機能の停止・除外・driver変更を行っていない。

## 証拠と再実行

[証拠JSON](raw/workflow-history-post-reinstall-evidence-2026-10-11.json)に環境の前後、
各条件の結果、欠落worker、phase、Python／native stack、通常テスト、hash、実行driverを保存した。
[3回比較の実行別CSV](workflow-history-post-reinstall-runs-2026-10-11.csv)は、
欠落を含む集計を`statistics_scope`で区別する。
元ログ・隔離コピーはignore対象の`.runtime/history-lock-reinstalled-20261011-1913/`に保存する。
初期環境採取でWindows PowerShellの子プロセスがGet-FileHashを解決できなかったため、
Pythonのhashlibに切り替えてから試験を開始した。この採取失敗は製品のテスト失敗には数えない。

標準ライブラリ対照は試験終了後、未使用のjson importだけをRuffに合わせて削除した。
実行したソースとSHA-256を証拠JSONに保存し、配布版との違いを記録した。
workerの操作と試験の制御は変えていない。

```powershell
.runtime/quality-venv/Scripts/python.exe `
  docs/investigations/quality-security-review-2026-10-06/probes/history_lock_supervisor.py `
  .runtime/history-reinstalled-rerun --profiles short controls --repeats 3 `
  --control-rounds 2000 --case-timeout 120 --dump-period 0

.runtime/quality-venv/Scripts/python.exe `
  docs/investigations/quality-security-review-2026-10-06/probes/history_open_only_stdlib.py `
  .runtime/history-stdlib-rerun --rounds 2000
```

新しい出力先を指定する。標準ライブラリ対照にも外部の所有プロセス限定watchdogを付けることを推奨する。
今回の実行では全4条件に対する480秒の外部期限を設け、中断せず結果保存まで到達した。
当時の監査・過去の中断・別環境・全履歴の記録は保存した。


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
