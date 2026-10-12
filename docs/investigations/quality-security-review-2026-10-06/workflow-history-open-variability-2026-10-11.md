# 履歴ロックのopen停止：回数・時間・競合条件の比較

調査日：2026-10-11（JST）。対象HEAD：`c4ef652bed1d3fa45813b1e2e790b327b9e82b68`。
ブランチ：`codex/fix-qs-013-construction-cleanup`。
前回までの未コミット調査資料を保存した。本体・既存テスト・設定は変更せず、
観測用probeと今回の調査資料だけを追加した。

**COMODOアンインストール後、条件を変えた主試験で保存できた97,332回のopenは最大51.305msだった。
従来の長時間open停止は、今回の有効な観測では確認されなかった。
一方、初期化flushの失敗は残り、以前に初期化なしでもopen停止した結果と合わせると、
flush失敗はopen停止の必要条件ではない。**

主試験48条件のうち3条件はアクセス違反を伴って中断しており、全条件成功とはしない。
その後の比較では定期stack出力を無効にした2回は完了し、有効な2回のうち1回はアクセス違反となった。
この新しい異常終了とbarrier待機は、以前にnative stackで確認したopen停止とは別に扱う。
保護環境との相互作用は従来のopen停止の主要候補だが、COMODOの特定機能やkernel内部の原因までは未確定である。

## 観測目的と判定

[アンインストール後の調査](workflow-history-post-uninstall-2026-10-11.md)に続き、
試行回数・worker数・保護区間の保持時間・ファイル再作成頻度を変えた。
実行ごとの取得エラー数・所要時間と、各openの所要時間を比較した。
現行コードと初期化なしを対にし、openだけを行うworkerも対照にした。

openの開始・終了を記録し、2秒以上の未完了を0.25秒間隔で観測する。
戻ったopenは10ms／100ms／1秒／2秒以上の件数と、workerごとの中央値・95／99 percentile・最大値を保存する。
長時間の未完了、戻った遅いopen、製品の初期化flushエラー、lock timeout、worker開始待ちを混同しない。
2秒は観測の閾値であり、製品の期限やテストの合否基準は変更しない。

## 環境

Windows build 19045.7727／22H2／Core、Python 3.11.4、quality-venvで実行した。
COMODOはユーザーがアンインストールした状態で、起動時刻は13:39:35.500 JSTのままである。
15:01:56 JSTに採取した修正済みの環境照会では、Security CenterはWindows Defenderだけで、
COMODOの確認対象プロセス・7 driver名・guard64.dllファイルはなく、新規PythonへのDLLロードもなかった。
Defenderの登録raw値は397584（0x61110）で、前回の397568（0x61100）とは異なる。
raw値から各保護機能の状態はdecodeしない。「保護製品がすべてない環境」とは扱わない。
15:49:06 JSTの終了時照会でも、同じ起動時刻・Defender登録値・確認対象のCOMODO不在を確認した。
pytest 9.1.1、HTTPX 0.28.1、Ruff 0.16.5、mypy 2.3.1など主要9 package版と
pytestの4 entrypointは、前回アンインストール後の試験と一致した。
本体・tests/v3・pyproject.tomlの168ファイルのSHA-256は、前回・今回開始・終了で一致した。

最初の環境照会ではdriver名のWhere-Object構文が失敗した。
その空の配列を不在の証拠に使わず、構文を修正し、読み取り専用の照会を別のファイルへ保存した。
製品の試験はそのエラーに依存せず開始しており、照会エラーも記録に残す。

## 試験条件

[history_lock_variability.py](probes/history_lock_variability.py)を用いた。
各条件を3回、新しいspawn worker・別の専用ディレクトリで逐次実行した。
現行／初期化なしの順序と、条件の実行順を入れ替えて、実行時刻と順序の偏りを減らす。
3回の順序は事前に決めたもので、ランダム化試験ではない。

| 条件 | ラウンド／worker数 | 保持時間 | ファイル | 開始・終了barrier期限 |
| --- | --- | --- | --- | --- |
| short | 30／3 | 1ms | 毎回削除し、未作成から開始 | 20秒 |
| long | 300／3 | 1ms | 同上 | 60秒 |
| wide | 80／8 | 1ms | 同上 | 60秒 |
| held | 80／3 | 25ms | 同上 | 60秒 |
| stable | 300／3 | 1ms | 最初だけ空ファイルを用意し、その後は再利用 | 60秒 |
| burst | 800／3 | 意図的なsleepなし | 毎回削除し、未作成から開始 | 60秒 |

ロック取得期限は全条件で5秒であり、製品のpoll間隔100msは変えない。
バリア期限を60秒へ延ばす条件は、起動や正当な処理の遅延を20秒の中断と取り違えないために設ける。
期限を延ばしただけで、戻らない同期openに製品のtimeoutが適用されるわけではない。

open-only対照は3 worker・各2,000ラウンドを3回ずつ実行する。
workerはデータを書かず、初期化・byte lockを実行せず、open・closeだけを行った。
`a+b`の未作成／同じ1バイトファイルの再利用と、`r+b`の1バイト再作成／同じファイルの再利用を比較する。
`r+b`用の1バイトファイルはparentが準備する。ファイル作成時のmetadata書込みまで除く対照ではない。

このPython 3.11.4の`a+b`は`O_APPEND | O_CREAT | O_RDWR`、`r+b`は`O_RDWR`となり、
Windowsではいずれも`_wopen`へ進む。append時はopen後のEOFへのseekもある。
したがって、この2モードの差が観測できても、単一のappend flagだけの効果とは断定できない。
[CPython 3.11.4の実装](https://github.com/python/cpython/blob/v3.11.4/Modules/_io/fileio.c#L285-L358)。

## 観測方法と適用範囲

子プロセス内だけで`Path.open`を元の実装へ委譲するwrapperに置き換え、
対象lockファイルのopenだけを計時する。初期化helperにも同様のwrapperを付ける。
openの引数、元のhelper本体、backend、製品のtimeout・sleepを変えない。
phaseは共有メモリに記録し、faulthandlerのstack採取は製品importの前に開始する。
通常のworker起動・import待ちも保存し、open停止として扱わない。

初期化なしは、隔離コピーで同期・非同期の初期化呼出し2か所だけを削除する。
実際にworkerが読んだmodule pathと、初期化呼出しの有無を記録する。
製品のソース・既存testは変更しない。専用ファイルは全workerが解放した後にだけparentが再作成する。
中断時はこのprobeが所有する子プロセスだけを終了する。

観測wrapper・shared memoryへの書込みは実行時間とスケジューリングに影響し得る。
そのため、以前の未加工の公開probeと失敗率・性能を直接同一視しない。
両variantで同じ観測を行い、観測wrapperなしの元の履歴テストも10回逐次実行した。
元の履歴テストには通常のpytest plugin自動読込みを適用し、診断用の8秒間隔stack出力だけを追加した。

初回のmatrixは15:01:14 JSTに開始した。3組目burst-currentでアクセス違反後にprobeのbarrierが止まったため、
15:35:48 JSTに所有プロセスを確認して終了した。保存済み38条件と中断条件を残し、
未実行の9条件を別parent・各240秒の外部watchdog付きで継続した。
したがって最後の9条件は当初とparentの起動方法・順序が異なる。同じworker処理と隔離ソースを使ったが、
中断した条件の成功への置換や、同一条件での再試行による取り消しはしていない。

## 測定結果

各表のエラーは`_ensure_lock_byte():165 / stream.flush()`でのPermissionErrorである。
「完了」は指定ラウンド数の観測を終えたという意味で、取得エラー0件を意味しない。
秒数はworker起動から終了までを含む。open最大値は戻って結果を保存できたworkerの値である。

| 条件 | 現行：1／2／3回目のflush失敗数 | 初期化なし：1／2／3回目 | 現行の所要時間（秒） | 初期化なしの所要時間（秒） | open最大：現行／初期化なし（ms） |
| --- | --- | --- | --- | --- | --- |
| short：各90取得 | 3／7／3 | 0／0／0 | 8.187／6.828／8.031 | 9.078／7.875／7.656 | 11.603／1.399 |
| long：各900取得 | 50／42／47 | 0／0／0 | 58.157／58.781／58.188 | 63.250／62.594／62.547 | 11.392／12.567 |
| wide：各640取得 | 19／20／14 | 0／0／0 | 56.250／55.313／57.297 | 58.734／56.672／58.625 | 35.697／28.113 |
| held：各240取得 | 9／20／11 | 0／0／0 | 19.078／17.735／18.671 | 20.563／19.765／19.859 | 3.242／6.029 |
| stable：各900取得 | 0／0／0 | 0／0／0 | 62.766／63.000／62.734 | 62.797／64.157／63.625 | 1.263／2.912 |
| burst：各2,400取得を予定 | 108／70以上・中断／欠落・中断 | 0／0／欠落・中断 | 58.031／116.594†／526.800† | 114.703／108.047／240.047† | 31.111／51.305 |

†中断までの経過時間であり、指定した800ラウンドが終わる処理時間ではない。
2回目burst-currentは生存した2 workerの1,512回のopenだけが保存され、
3回目burstの両variantは最終結果JSONと観測イベントが欠落した。
欠落をエラー0・open停止0とは扱わない。

| open-only対照 | 1／2／3回目の所要時間（秒） | 保存open数 | エラー | open最大（ms） |
| --- | --- | --- | --- | --- |
| a+b・毎回未作成 | 5.250／5.313／5.719 | 18,000 | 0 | 23.429 |
| a+b・同じ1バイトファイル | 3.656／3.703／3.812 | 18,000 | 0 | 3.776 |
| r+b・毎回1バイト再作成 | 4.750／4.813／5.984 | 18,000 | 0 | 37.532 |
| r+b・同じ1バイトファイル | 3.688／2.922／3.922 | 18,000 | 0 | 2.958 |

主試験は予定103,020回、48条件中45条件で観測を完了した。
完了条件で保存した95,820回と中断条件の生存workerの1,512回を合わせ、**97,332回のopenを保存した**。
そのうち10ms以上は55回、100ms・1秒・2秒以上はいずれも0回、最大は51.305msだった。
保存された長時間phase観測にも2秒以上の未完了openはなかったが、2条件のイベントは欠落している。
欠落workerのopen時間は不明であり、全103,020回で未観測と表現しない。

保存結果の初期化flush失敗は423件で、完了条件353件と中断条件の生存worker70件からなる。
初期化なしの完了条件13,110取得はエラー0、open-onlyの72,000回もすべて完了しエラー0だった。
byte lockを使った完了条件のcounterは、保存された成功取得数と一致した。

実行間の揺らぎは、例えばheldの9→20→11件、shortの3→7→3件で確認できる。
回数を増やしたlongでも42〜50件となり、失敗が1回だけの偶然ではない。
stableは初回だけ空ファイルを用意し、初期化後は再利用するため、初期化競合にさらされる回数が大幅に少ない。
その3回の0件から初期化処理自体が安全とは判定しない。

再作成するopen-onlyの最大値は、同じファイルを使う条件より大きかった。
ファイルの作成・再作成とスケジューリングは遅延の候補だが、最大値は数十msであり、
以前の60秒以上戻らないopenとは桁が異なる。a+b／r+bとも長時間停止は今回の対照で確認されなかった。
現行の所要時間が短い条件は、flush失敗でlock取得を待たずに終わるworkerを含む。
初期化除去による性能悪化や、flushがopenを高速化する効果とは判断しない。

元の`tests/v3/application/test_workflow_history.py::test_independent_processes_merge_under_shared_history_lock`を
独立に10回実行した結果は、**9 passed／1 failed**だった。
pytest内の各実行は2.90〜3.82秒で、失敗はhistory-09の初期化flush PermissionErrorだった。
workerはexitcode 1となり、従来のjoin後も生存するopen停止ではなかった。
この10回ではアクセス違反も確認していない。前回v3全体の1686 passed／17 skipped・coverage 88.19%は
前回の結果として保存し、今回の単独失敗を全体成功で取り消さない。今回はv3全体を再実行していない。

## 長時間停止とは別に観測したアクセス違反

`repeat-02-burst-current`は800ラウンド指定の755ラウンドを完了した後、
worker 1（PID 896）がexitcode 3221225477（`0xC0000005`）で異常終了した。
stackファイルには`Windows fatal exception: access violation`がある。
これは、期限後もworkerが生存し続けた従来のopen停止とは異なる。
他workerは最後の終了barrierで待ち、parentもBrokenBarrierErrorになった。
中断条件の116.594秒にはこの待機が含まれるため、正常な800ラウンドの所要時間と同一視しない。

共有phaseの最後は、iteration 755の`lock_wait`だった。これは観測wrapperが元のopenから戻った後に書く値である。
2秒以上の未完了openイベントはない。一方、fatal出力にはPath.openのframeがあり、
定期dumpとfatal出力が途中で混ざっている。Python stackと共有phaseだけでnativeの障害箇所は特定しない。
該当時刻のApplication log（ID 1000・1001、15:13〜15:17 JST）をsandbox外でも照会したが、
該当Python障害を特定できるイベントはなかった。

クラッシュしたworkerの最終結果JSONはなく、そのworkerのopen所要時間・取得エラー数は欠落している。
残る2 workerの755ラウンド以降の結果と、共有counter 2157を保存する。
中断条件の取得エラー70件は残ったworkerで確認した件数であり、条件全体のエラー総数ではない。
この条件を2,400回成功とは数えず、counterと保存結果の違いも単なるデータ破損とは判断しない。

3回目burst-currentではworker 1・2のstackにアクセス違反が残り、
残るworker 0は`multiprocessing.synchronize.Condition.notify():290`で待った。
Pythonのbarrier内部の通知完了待ちは、指定した60秒の待機期限でも戻らず、parentの結果保存まで到達しなかった。
採取stackは製品のPath.open停止を示しておらず、所有プロセスだけを手動終了した。
3回目burst-no-initializationでも定期dumpの途中にアクセス違反が記録され、
他workerはbarrier待機となり、240秒の外部watchdogで終了した。
この障害も初期化flushを必要とせず、ただし従来のguard64.dll経由の停止の再現とは扱わない。

調査用の定期stack出力の関与も候補とし、同じcurrent・800ラウンド・3 worker条件で
8秒ごとのdumpあり／なしを2組、8→0、0→8の順序で比較した。fatal handlerは両方で有効にした。
本体やopen wrapperは変えず、定期dumpだけを変えた。各条件に240秒の外部watchdogを付けた。

| 条件 | 結果 | flush失敗 | 所要時間（秒） | 保存open最大（ms） |
| --- | --- | --- | --- | --- |
| 1組目・8秒ごとのdump | 471ラウンド後アクセス違反、結果欠落worker 1件 | 24以上 | 92.500 | 5.321 |
| 1組目・定期dumpなし | 800ラウンド完了 | 119 | 62.391 | 4.046 |
| 2組目・定期dumpなし | 800ラウンド完了 | 77 | 51.953 | 12.489 |
| 2組目・8秒ごとのdump | 800ラウンド完了 | 116 | 61.344 | 3.396 |

定期dumpなしは2回とも完了し、dumpありは1回中断・1回完了だった。
主試験の障害出力にも、定期dumpの`File ...`途中とfatal報告が混在している。
これらは**観測機構／Python実装と今回のアクセス違反の関与を支持するが、2組だけでは原因確定にならない**。
native例外アドレス・faulting moduleは得られておらず、Python stack上のframeを障害命令と同一視しない。
定期dumpを無効にしても119／77件のflush失敗は残り、通常の初期化競合とは別である。

CPython側には3.11.4のLinux signal handlerで、stack出力中のframe読取りがクラッシュした報告がある。
今回のWindows・watchdogによる定期出力とは条件が異なり、同一原因とは断定できない。
観測方法の影響を調べる理由として参照する。
[CPython issue #116008](https://github.com/python/cpython/issues/116008)。

## open停止と初期化flushの関係

製品の処理順序は次のとおりである。

該当箇所は[interprocess_lock.py](../../../src/image_downloader/storage/interprocess_lock.py#L90)の
`acquire():96–101`、初期化helperの`160–166`であり、`acquire_async()`も同じ順序である。

```text
Path.open("a+b")
  → _ensure_lock_byte(): 空ならwrite／flush
  → deadline作成
  → 非blocking byte lock取得のpoll
  → 保護区間
  → byte lock解放・close
```

同じ呼出しのflushはopenが戻った後にしか実行されない。
したがって、その呼出し自身のflush失敗が先行してopenを止めるという説明は成立しない。
一方、別workerの初期化write／flushとopenが重なり、保護製品やOSのI/O処理へ影響する可能性は別である。
同一呼出しの順序だけで、プロセス間の間接的な関係まで否定しない。

[再起動後の調査](workflow-history-post-reboot-2026-10-11.md)では、
**すべてのworkerから初期化呼出しを除いた専用条件でもopen停止を再現した。**
native stackは`_wopen → CreateFileW → guard64.dll → NtCreateFile`（採取export名`ZwCreateFile`）で、
同じファイルのbyte lockは別プロセスが`r+b`で取得・解放できた。
初期化なしの停止を、初期化flushの失敗に分類していない。
これが「初期化flushエラーがopen停止の必要条件」という説明への反証になる。
初期化除去だけでopen停止も解消する保証には広げない。

Pythonの`msvcrt.locking`はEOFを越える範囲のlockを許し、Windowsの仕様も同様である。
初期化なしの空ファイル対照でbyte 0をlockすること自体は、この仕様と矛盾しない。
[Python仕様](https://docs.python.org/3.11/library/msvcrt.html#msvcrt.locking)、
[Windows LockFileEx仕様](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex)。

製品のdeadlineはopenと初期化の後に作成される。
CreateFileWの引数にも取得期限はなく、現行のlock pollの期限は同期openの内部待機を制限しない。
[Windows CreateFileW仕様](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)。
この期限の適用範囲の不足と、OS／保護環境内部で待機する理由も分けて扱う。

## 原因仮説の評価

| 仮説 | 評価根拠 |
| --- | --- |
| 初期化flush失敗がopen停止の必須原因 | 前回の初期化なしでの停止と矛盾する。同一呼出し内の順序からも直接原因とは考えにくい |
| 競合する作成・openと、COMODOを含む保護環境の相互作用 | 有効時・再有効化・有効のままの再起動で再現し、停止中はguard64.dllを通る。アンインストール後の有限試験では未観測。主要な候補だが個別機能の欠陥までは未確定 |
| byte lockが残っているだけ | 停止中に別のr+bプロセスが同じbyte lockを取得できたため、その説明だけでは不足 |
| 起動・importの遅さをopen停止と誤認 | 今回はphaseを分けて確認する。前回の最初のbarrier中断1件は未確定であり、既にnative stackで確認したopen停止とは別 |
| 今回のアクセス違反・barrier停止が従来のopen停止の再現 | 根拠不足。COMODO不在で異常終了し、残るworkerのstackはprobeのbarrier通知待ち。定期dumpとの関連を別に調べた |
| スケジューリング、共有処理、OS内部のI/O待機 | 関与を排除できない。kernel stack・COMODO内部処理・同じ新版probeの導入前後の比較は採れていない |

## 証拠と制限

環境・全条件の結果・計時分布・stack・例外例・元の履歴テストの結果・中断記録を
[証拠JSON](raw/workflow-history-open-variability-evidence-2026-10-11.json)へ保存した。
主試験48条件の一覧は[実行別CSV](workflow-history-open-variability-runs-2026-10-11.csv)にある。
中断条件の数値の範囲は`statistics_scope`、欠落worker数は`missing_worker_results`で示した。
元ログと隔離コピーはignore対象の`.runtime/history-open-variability-20261011-1500/`に保存する。
以前の調査の結果はそのまま保持する。

再実行には[history_lock_supervisor.py](probes/history_lock_supervisor.py)を入口にする。
観測時の元probeはhashを維持して保存し、barrier期限だけでは中断できない障害を受け、
条件ごとに別parent・所有子プロセスに限定した終了期限を設けるhelperを追加した。
このhelperの定期dumpは既定で無効である。主試験・補助比較には後から作成したhelperを使っておらず、
その検証は別枠として証拠へ記録した。

```powershell
# quality-venvにpsutilを用意し、新しい専用出力先を指定する
.runtime/quality-venv/Scripts/python.exe `
  docs/investigations/quality-security-review-2026-10-06/probes/history_lock_supervisor.py `
  .runtime/history-open-rerun --repeats 3 --case-timeout 240
```

当時の定期出力を比較する場合だけ`--dump-period 8`を指定する。
ソースへの観測wrapper、環境照会・実行driverの内容、SHA-256、コマンドも証拠JSONに保存した。
新helperはshortの両variantと小規模open-only 4条件の全6条件でラウンド完了を確認した。
別途、期限を意図的に0.5秒にして両variantを中断し、所有プロセスの終了と欠落扱いを確認した。
この期待された中断を製品のopen停止としては数えていない。
Ruffは`All checks passed!`、mypyは`Success: no issues found in 95 source files`だった。
本体・既存テスト・設定の差分はなく、コミット・push・保護設定変更は行っていない。

同じroundの取得は相関するため、件数から将来の停止率や独立な試行の信頼区間を計算しない。
openの計時はスケジューリングを含む壁時計時間であり、kernel内のI/Oだけの所要時間ではない。
workerごとのpercentileを平均して全試行のpercentileとは扱わない。
今回の有効な観測での未観測は有限回の結果であり、あらゆる環境での停止不能を保証しない。
新probeをCOMODO有効時に実行した比較はなく、以前の未加工probeとの発生率の直接比較はできない。
保護製品の個別機能・driver・kernel stackを分離した原因確定は今回の観測範囲を超える。

## 修正検討への含意

初期化flushは、排他前の空判定・write・flushという本体の競合経路を修正する必要がある。
COMODOアンインストールやテスト待機時間の延長では、この経路は解消しなかった。
一方、初期化除去だけをopen停止への修正保証に含めるべきではない。
本体のdeadlineがopenに適用されないことと、保護環境内でopenが戻らない根本原因は別に評価する。
open停止の原因をさらに絞るには、同じprobe・Python・OSでの保護環境比較と
停止中のkernel／native待機情報が必要であり、今回の結果から特定DLLの欠陥を断定する修正は提案しない。


## 新版再導入での対照とファイル準備手順の訂正

[COMODO 12.4.0.8170再導入後の調査](workflow-history-post-reinstall-2026-10-11.md)では、
以前完了したopen-only対照のうち、毎ラウンド準備するa+b・r+b条件が各3回ともopenで停止した。
再利用する同じファイルでは各3回完了した。定期dumpなし・本体importなしの対照も追加した。

元probeのreset_lock()と再照合すると、条件ID readwrite-created（本表の「r+b・毎回1バイト再作成」）は、
parentがPath.write_bytes()で初回に作成し、その後もwbでtruncateして1バイトを書き直す処理だった。
unlinkして毎回新規作成するという説明は不正確であり、ここで訂正する。
append-createdではparentが毎回unlinkし、workerのa+bが作成する。
以前の観測値・probe・条件IDは変更せず保存した。
