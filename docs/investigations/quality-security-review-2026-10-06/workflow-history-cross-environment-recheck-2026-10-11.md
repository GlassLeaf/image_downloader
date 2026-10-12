# 最新コードと別環境の結果を照合した履歴ロック再調査

調査日：2026-10-11（JST）。対象ブランチ：`codex/fix-qs-013-construction-cleanup`。
`0d63138`からリモートの`c4ef652bed1d3fa45813b1e2e790b327b9e82b68`へfast-forwardした。
更新時の作業ツリーはcleanで、取得した変更は調査資料・プローブだけである。
製品ソース・既存テスト・依存宣言は変更されていない。

ユーザーによる09:46 JSTの再起動後の追試は
[再起動後の調査](workflow-history-post-reboot-2026-10-11.md)に記録した。
初期化なしでも同じopen停止とnative経路が再現し、再起動だけでは解消していない。

## 原因の評価

**初期化flushの失敗と、Windowsのopenが戻らない停止を、別の失敗経路として扱う。**
両者とも同じlock取得処理で現れるが、現在の根拠では一つの原因にまとめられない。

| 失敗経路 | 確認できた原因・条件 | 未確定の範囲 |
| --- | --- | --- |
| `_ensure_lock_byte()`のflushでPermissionError | 空判定・write・flushがbyte lock取得前にあり、プロセス間で競合する。COMODOを使用しないとの申告がある別環境でも再現 | 失敗した各取得のWindows内部のI/O順序 |
| `Path.open("a+b")`が戻らない | この環境の同期ファイル取得が、期限を作る前で停止する。今回の強い競合条件では初期化なしのコピーでも観測 | COMODOの個別機能、Windows／filter driver／oplock等の具体的な待機原因 |

初期化を省く案は、初期化flush競合への対策として引き続き支持される。
ただし今回の結果から、**初期化の除去はopen停止を防ぐ十分条件ではない**。
前回の単独テスト20回成功を、強い競合条件やOSのあらゆるopenに対する保証へ広げない。

## 別環境から得られた根拠

取得した[別環境調査](workflow-history-alternate-environment-2026-10-11.md)と
[5組の再現性調査](workflow-history-repeatability-2026-10-11.md)を読み、証拠JSONとも照合した。
別環境はWindows build 26200.9457／25H2、Python 3.11.4で、COMODOの使用なしとの申告があり、
診断Pythonにguard64.dllのロードがなかった。

- 元の履歴保存テストを使った比較は、初回58試行（現行ソース38・初期化なしのコピー20）で成功した。
  追加の現行ソース20試行と、初回の単独公開probeも成功した。
- 200ラウンド・3 workerの取得競合では、5組すべてに初期化失敗があり、件数は7・9・7・11・12だった。
- 合計46件の全tracebackが`_ensure_lock_byte():165 / stream.flush()`のPermissionErrorを示した。
- 初期化を省いたコピーは、順序を交互にした同じ5組・計3,000回でエラー0件だった。
- openの長時間停止は、その別環境では観測していない。

したがって、初期化失敗をCOMODO固有の障害として説明する根拠はない。
単独の履歴テストが成功する環境にも、本体の初期化競合は存在する。
別環境のsandbox内にあった`asyncio`初期化のsocketpair／accept停止は、workerのlock処理前の別経路である。
今回の比較はsandbox外で行い、その実行制約との混同を避けた。

## この環境での同じ公開プローブ

08:34 JSTから、取得した`history_lock_contention.py`を変更せず、sandbox外で実行した。
各条件は3 worker、200ラウンド指定、取得期限5秒、保護区間の待機1ミリ秒である。
親が全workerの解放後に専用ファイルを削除・作成／書込みし、次のラウンドへ進む条件も同じである。
利用者の履歴やprofileは使っていない。

| 条件 | 完了したラウンド | 停止までの共有カウンター | 終了時のファイルサイズ | 実測 |
| --- | --- | --- | --- | --- |
| 現行・未作成 | 0 / 200 | 2 | 1バイト | open停止、27.594秒で診断終了 |
| 現行・空ファイル | 3 / 200 | 11 | 1バイト | open停止、27.188秒で診断終了 |
| 現行・1バイト事前作成 | 13 / 200 | 41 | 1バイト | open停止、29.407秒で診断終了 |
| 初期化なしの隔離コピー・未作成 | 23 / 200 | 71 | 0バイト | open停止、32.515秒で診断終了 |

4条件ともfaulthandlerのworker stackが`Path.open`と製品の`acquire()`内を示した。
初期化なしの条件では、読まれたmoduleは専用コピーであり、同期・非同期の初期化呼出しが除去されている。
この停止に初期化flushを通ることは必須ではない。

全体テスト終了後、初期化なしだけを200ラウンド指定でもう一度診断し、8ラウンド完了・
共有カウンター26・ファイル0バイトの状態で同じopen停止になった（31.000秒で診断終了）。
診断用worker wrapperはPID・親PIDの記録だけを加え、所有子プロセスを確認してnative stackを採取した。
採取結果は`_wopen → CreateFileW → guard64.dll → NtCreateFile`（export名`ZwCreateFile`）だった。
COMODO内部frameの近傍symbolから、個別機能や実際の内部関数名は判断できない。
停止中に別の診断プロセスが同じ0バイトファイルを`r+b`で開き、byte 0を取得・解放できた。
この事実も、停止を単に保持されたbyte lockの待機では説明できないことを支持する。

openが戻らずworkerが次のbarrierへ到達しなかった結果、親にBrokenBarrierErrorが発生した。
20秒のbarrier期限とfinallyのworker終了によって診断を終え、probe自体のexitcodeは1だった。
製品の5秒期限はopen後で設定するため、この停止を解消していない。

このprobeは親の例外時にworker結果を空配列とする。そのため保存された`errors=0`・
`successful_acquisitions=0`は、取得が600回成功したことも、取得失敗がなかったことも表さない。
共有カウンターには未完了ラウンドでの取得も含まれる。完了数・parent_error・workerの終了・stackを合わせて判断した。
workerのexitcode -15は、probeのfinallyで所有workerを終了した結果であり、別の自然終了エラーとは扱わない。

前回の20回成功と今回の未完了は試験条件が異なる。
前回は新しいprofileに3 workerが1回ずつ保存し、今回の専用試験では同じ名前のファイルを各ラウンドで
削除・再作成／書込みする。今回の完了ラウンド数を実利用での発生率に換算しない。
これまでの成功記録は保存し、open停止に対する保証の範囲を今回の結果で限定する。

## 検証環境

| 項目 | この環境 | 別環境 |
| --- | --- | --- |
| Windows | build 19045.7727／22H2 | build 26200.9457／25H2 |
| Python | 3.11.4、MSC v.1934、AMD64 | 同じ詳細バージョン |
| pytest／pytest-cov | 9.1.1／7.1.0 | 同じ |
| pytest追加plugin | anyio、asyncio、cov、mock | 同じentry point |
| Python環境 | system-site-packagesを使うquality-venv | 基底のPythonを直接使用 |
| COMODO | 再有効化後の登録値と同じ。新規Pythonにguard64.dllあり | 使用なしとの申告。診断Pythonにguard64.dllなし |

08:34のSecurity Centerのraw値はCOMODO Antivirus 397312（0x61000）、Windows Defender 393472（0x60100）だった。
個別機能の状態はdecodeしていない。調査側で保護設定を変更していない。
両環境の対象3ファイルはGit blobで同一と確認した。CRLF／LFの違いをコードの違いとは扱わない。
別環境には追加パッケージがあり、OS・仮想環境・保護環境も異なるため、完全に同一条件の比較ではない。

## 再テスト

Ruffは成功し、mypyは95 source filesで成功した。
最新HEADのv3全体をsandbox外で、通常のpytest pluginとカバレッジを使って再実行した。

| 検証 | 結果 |
| --- | --- |
| v3全体 | 1 failed / 1685 passed / 17 skipped、655.11秒 |
| coverage | 88.19%、75%基準を満たす |
| 元の履歴の複数プロセステスト | 成功、2.835秒（JUnitの実測） |
| 失敗した署名checkoutテストの条件調整後の再実行 | 1 passed、7.70秒 |

17 skipはMailpitのopt-in 12件とsymlink作成権限に依存する5件である。
全体テストの失敗は`test_signed_plugins_survive_windows_line_ending_conversion`の
ローカル`git clone --no-checkout --no-hardlinks`で発生し、署名検証には到達していない。
同じcloneを専用の一時先で再現したところ、Gitはexit 128と`detected dubious ownership`を返した。
`.git`の所有者が`CodexSandboxOffline`、sandbox外の実行ユーザーが`pc01`であるための安全性チェックだった。

再テストの子プロセスに限り、`GIT_CONFIG_COUNT`／`GIT_CONFIG_KEY_n`／`GIT_CONFIG_VALUE_n`で
このリポジトリの`.git`だけを`safe.directory`に追加すると、既存テストは成功した。
グローバル設定・所有者・製品コード・テストは変更していない。全体実行の1 failedはそのまま記録し、
個別再テストの成功と合算して「全体が全件成功」とはしない。
これは履歴lockのopen停止・初期化flush競合とは別の実行環境上の失敗である。

今回の履歴テスト1回の成功は、専用試験や以前の60秒テストで再現した不定期の停止を否定しない。

## 証拠と再実行

集計・対象ソースのSHA-256／Git blob・各条件のstack・native frame・全体と個別テストの結果を
[今回の証拠JSON](raw/workflow-history-cross-environment-recheck-evidence-2026-10-11.json)に保存した。
元ログ・coverage JSON・JUnit・隔離コピーはignore対象の
`.runtime/history-cross-environment-recheck-20261011/`に保存した。過去の記録は保存している。

公開probeは新しい出力先を指定して、次の条件で実行した。

```powershell
& .runtime/quality-venv/Scripts/python.exe `
  docs/investigations/quality-security-review-2026-10-06/probes/history_lock_contention.py `
  .runtime/history-cross-environment-recheck-20261011/contention-01 `
  --rounds 200 --record-all-errors
```

全体再テストは`python -m pytest tests/v3 -q --cov=image_downloader --cov-report=term`に
JSON coverage出力とJUnit出力を加えて実行した。どちらもsandbox外の通常plugin条件である。
公開probeは約20秒のbarrier待機とworkerの終了処理で止まるため、無期限停止は行っていない。

## 修正候補と残る調査

初期化競合には、byteデータの初期化そのものを除く方針が第一候補である。
EOFを越えるbyte rangeのlockは[Python仕様](https://docs.python.org/3.11/library/msvcrt.html#msvcrt.locking)と
[Microsoft仕様](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex)が認めており、
1バイトの実データは必須ではない。正式修正時には同期・非同期と各OSで回帰固定する。

open停止には別の検討が必要である。期限をopen前へ移すだけでは、進行中の同期I/Oを止められない。
取得全体の期限を保証するには、取消可能なI/O方式とhandleの所有権・終了処理を含めた設計が要る。
COMODOの有効化との対応は以前の比較で支持されるが、Windows Defenderの登録値も変化し、
別環境はOSも異なる。COMODO単独の欠陥やkernel内部の理由を確定したとはしない。

今回も製品コードへの正式な修正は行わず、診断用コピーは実運用に使っていない。
