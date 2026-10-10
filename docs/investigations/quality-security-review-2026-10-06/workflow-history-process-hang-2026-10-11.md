# 履歴保存の複数プロセステスト停止：追加調査

調査日：2026-10-11（JST）。対象コミット：`b5c1a453ab54563cb5af598ff3fa4a15762cf0a3`。
調査開始時の作業ツリーはclean。本体・既存テスト・設定の変更は行っていない。

## 結論

初回調査で観測した停止の直接の原因は、履歴ロックファイルを開くWindowsの同期I/Oが戻らないことである。
停止した子プロセスは`InterProcessFileLock.acquire()`の`Path.open("a+b")`内におり、
履歴の読込み・書込みやプロセス終了処理には到達していない。
30秒の期限はopenと初期化の後で作成されるため、この停止を検出できない。

プログラム側では、**ロック取得前に行う初期化用1バイトの書込みが競合する**。
空かどうかの検査とwrite／flushにプロセス間の排他がなく、同時に開始したworkerが
この初期化と他workerのopen・lockを重ねることができる。
初期化だけを省いた隔離コピーでは10回すべて成功し、同じコピーで初期化を戻すと6回中4回停止した。
初期化時の`flush()`が`PermissionError`になる別の失敗も観測した。
単に`join()`を長くする対策では、この競合と期限の適用範囲は変わらない。

Windows内部でopenが戻らない最終的な理由は、プログラム側の競合と分けて扱う。
初回の3件と再有効化後のnative stackにはCOMODO Internet Securityの`guard64.dll`が存在する。
同製品またはファイルシステム側の処理との相互作用が疑われるが、DLLの存在だけでは原因を断定できない。
初回調査ではセキュリティ製品の停止による比較を行っていなかった。
その後、ユーザーが停止可能なCOMODOの機能をすべて一時停止した条件で再調査した。
本体を変更せず行った未作成lockの38試行ではopen停止を観測しなかったが、
初期化の`flush()`での`PermissionError`が3試行に残った。
さらにユーザーがCOMODOを再有効化した条件では、元の60秒待機のテストで同じopen停止が再現した。
6回ずつの比較でも未作成・空ファイルはそれぞれ3回停止し、1バイトを事前作成すると6回すべて成功した。
**有効時に停止 → 一時停止中は未観測 → 再有効化で再現**という順序から、
COMODOの有効化操作に伴う保護環境の変化がopen停止の発生に影響する、という根拠は強まった。
一時停止中の初期化flush失敗も残るため、本体側の排他外初期化への対処が必要である。
Windows Defenderの登録値も同時に変化しており、個別機能を制御した比較やkernel stackの調査は行っていない。
「COMODOだけが原因」「COMODOの特定機能の不具合」までは確定していない。
詳細は下記の[一時停止後](#comodo一時停止後の再調査)・[再有効化後](#comodo再有効化後の再調査)の記録に示す。

## 確認した停止経路

```text
test_workflow_history._process_save
  → WorkflowHistoryStore.save
  → InterProcessFileLock.acquire
  → Path.open("a+b")
  → CRT _wopen / Windows CreateFileW
  → guard64.dll内のframe
  → NtCreateFile（取得symbol名はZwCreateFile）で待機
```

Python stackは`faulthandler.dump_traceback_later()`を起動時に有効にして取得した。
本体コードやworkerを置換せず、元の単独テストでも60秒のjoin後の失敗を再現した。
native stackは対象のテストが作成した子プロセス・threadだけを指定して取得した。
ローカルのexport symbolを使用したため、内部frameの近傍symbol名を実際の関数名とは断定しない。
特に`guard64.dll`の内部処理の意味は分かっていない。

| 箇所 | 確認した内容 |
| --- | --- |
| `tests/v3/application/test_workflow_history.py:115` | 子プロセスの履歴保存呼出し |
| 同ファイル`:134`・`:135` | join後にもexitcodeがNoneとなるassertion |
| `src/image_downloader/storage/interprocess_lock.py:96` | 子プロセスが停止するopen |
| 同ファイル`:97`・`:160`〜`:166` | 排他取得前の空判定・write・flush |
| 同ファイル`:98`・`:99` | open・初期化後に設定する期限とlockの再試行 |
| 同ファイル`:120`〜`:123` | 非同期取得にも同じopen・初期化・期限の順序がある（今回の停止の実測は同期API） |

native取得の3件では他のworkerによる2件の履歴が保存されていた。
うち1件では停止したworkerを残したまま、別の診断プロセスから同じファイルを`r+b`で開き、
byte 0のlock取得・解放に即座に成功した。
この時点で別workerがそのbyte lockを持ち続けていたために待っていた、という説明は成立しない。
queue feederの待機threadも見えるが、main threadは終了処理に入っていない。

## 初回調査：条件を変えた比較

全ケースで3つのspawn workerと小さな模擬履歴を使用し、保存後の3件のmergeも既存テストのassertionで確認した。
比較実験では一時的なpytest pluginで親の`join(60)`だけを12秒に短縮し、製品の30秒期限は変更していない。
12秒版の結果だけで停止を判定せず、元の60秒版でも同じopenでの停止を確認している。
各条件の6回は同一pytestプロセス内で繰り返した。少数の試行であり、一般的な発生率を示す数値ではない。

| 条件 | 結果 | 補足 |
| --- | --- | --- |
| 現行コード・lock未作成 | 3 failed / 3 passed、50.81秒 | 3件のopen停止stack |
| 現行コード・1バイトを事前作成 | 6 passed、18.92秒 | 初期化時のwrite／flushを通らない |
| 上記＋既存ファイルを`r+b`でopen | 6 passed、17.32秒 | 検証用のみにopen modeを変更 |
| pytest自動plugin無効・lock未作成 | 5 failed / 1 passed、62.80秒 | open停止4件、初期化flushのPermissionError 1件 |
| pytest自動plugin無効・空ファイルを事前作成 | 2 failed / 4 passed、42.94秒 | 存在するだけでは競合を除けない |
| pytest自動plugin無効・1バイトを事前作成 | 6 passed、18.20秒 | coverage等の自動pluginの有無で結果を説明できない |
| 隔離コピーで初期化呼出しのみを省略 | 10 passed、34.35秒 | 新規の空lockファイルのまま排他取得・merge成功 |
| 同じ隔離コピーで元の初期化を復元 | 4 failed / 2 passed、62.41秒 | 再び4件のopen停止stack |
| 初期化を省いたコピーの既存lockテスト | 6 passed、5.96秒 | 他プロセスの排他、期限、例外後の解放を確認 |

隔離コピーで変更したのは同期・非同期の`self._ensure_lock_byte(stream)`呼出し2か所だけである。
設定ファイルを`pytest -c`で明示し、読まれたmoduleの`__file__`と初期化呼出しの有無を記録した。
正式な修正・全体回帰テストの代用ではなく、原因を切り分ける試作である。
初期化をなくすことでOSのあらゆるopen停止を防げると保証するものでもない。

## QS-013との関係と環境

前の調査では、QS-013修正前の`92a3d0c`の隔離コピーでも同じテストが失敗していた。
今回のworkerはcomposeやservice構築を呼ばず、QS-013の構築ガード・一時cleanup threadも使用しない。
本体の履歴保存・interprocess lock・この既存テストはQS-013で変更していない。
これらの根拠から、今回の失敗はQS-013で導入されたものではない。

| 項目 | 実測値 |
| --- | --- |
| OS | Windows 10、build 19045 |
| Python | 3.11.4、64 bit |
| pytest / pytest-cov | 9.1.1 / 7.1.0 |
| 注入されていたDLL | `C:/Windows/System32/guard64.dll` |
| DLL metadata | COMODO Internet Security 12.2.3.8026、署名検証Valid |

この環境に固有の相互作用の可能性は残る。前回確認した同一HEADのWindows CI成功は、
本ローカル環境の現象を否定する根拠にはしない。sandbox外でも既に再現しているため、sandboxだけを原因としない。

## COMODO一時停止後の再調査

2026-10-11 05:31〜05:41 JSTに再実行した。ユーザーから次の条件を確認した。

> COMODO Internet Security Premium , ユーザーが停止可能な機能は全て一時停止済み

調査側ではセキュリティ製品の設定を操作していない。再有効化、アンインストール、driverのunloadも行っていない。
対象HEADは引き続き`b5c1a45`で、履歴保存・lock実装・元のテストのSHA-256は初回調査と一致する。
pytest自動pluginは無効とし、元の3 worker・mergeのassertionを使った。
最初の6回の比較と単独probe、および隔離試作と関連テストには並行実行の時間帯がある。
実行時間や失敗率の厳密な比較には使わない。

| 一時停止後の条件 | 結果 | 観測内容 |
| --- | --- | --- |
| 元の単独テスト＋公開stack probe、join 60秒 | 1 passed、7.70秒 | lock内の停止stackなし |
| 現行コード・lock未作成、join 12秒 | 6 passed、29.77秒 | lock内の停止stackなし |
| 現行コード・空ファイルを事前作成、join 12秒 | 6 passed、22.72秒 | lock内の停止stackなし |
| 現行コード・1バイトを事前作成、join 12秒 | 6 passed、26.42秒 | lock内の停止stackなし |
| 現行コード・lock未作成を追加20回、join 12秒 | 2 failed / 18 passed、64.54秒 | 試行15・17で初期化flushのPermissionError。open停止stackなし |
| 元の単独テストを別pytestプロセスで10回、join 60秒 | 10 / 10 passed、各3.66〜5.50秒 | 元の待機時間でも成功。lock内の停止stack・native取得対象なし |
| 初期化を省く隔離試作・lock未作成を20回、join 12秒 | 20 passed、72.42秒 | lock内の停止stackなし。本体への変更は未適用 |
| 現行コードのlock・履歴・QS-013組立てテスト、join 60秒 | 1 failed / 93 passed、39.94秒 | 同じ履歴workerの初期化flushでPermissionError。QS-013の64件とlockの6件は成功 |

本体を変更しない未作成lockの専用試行は計37回で、35成功・2失敗だった。
関連テスト内の同じ1回を含めると計38回で、35成功・3失敗となる。
この38回で初回の「60秒経ってもopenが戻らない」現象は観測しなかった。
一方、失敗した3回は子プロセスがexitcode 1で終了しており、次の同じ例外経路だった。

```text
InterProcessFileLock.acquire():97
  → _ensure_lock_byte():165 / stream.flush()
  → PermissionError: [Errno 13] Permission denied
  → InterProcessLockError: cannot acquire inter-process file lock
```

この例外はjoinの短縮が原因ではない。関連テストでも元のjoin 60秒のまま再現している。
排他取得前の初期化が失敗し得るという本体側の問題は、ユーザーが停止可能な機能を一時停止した条件でも残る。
Windows側でPermissionErrorに至る個々のI/O・lockの順序までは取得していない。

### 一時停止状態の観測と解釈

05:37 JSTの読み取り専用Security Center照会（`root/SecurityCenter2:AntiVirusProduct`）では、
COMODO Antivirusの`productState`は393216（`0x60000`）、Windows Defenderは397568（`0x61100`）だった。
raw値として保存し、これだけでHIPS・封じ込め・firewall等の個別機能の停止を認定しない。
`cis`・`cmdagent`のプロセスは残っており、05:34および05:40の新規Pythonプロセスにも
`guard64.dll`が読み込まれていた。
したがって、今回の条件はユーザーが停止可能な機能の一時停止であり、COMODOのhookやkernel driverの完全な除去ではない。
DLLが存在することと、問題を引き起こす保護処理が動作していることを同一視できない。

初回の自動plugin無効・lock未作成6回ではopen停止4回とflush失敗1回を観測した。
一時停止後には同じ本体・同じ基本条件でopen停止を観測しなくなったため、
**COMODOの有効時の処理との相互作用が、open停止の発生に影響した可能性は高まった**。
この一時停止後の調査時点では、試行数と実行負荷は同一でなく、再有効化した対照群もなかった。
停止した個別機能の特定、COMODOの単独原因・製品不具合の断定、再発しない保証はできない。
Windows Defenderを含め、他の保護・driverの個別状態も今回の比較では制御していない。

flushの失敗が残り、初期化を省く隔離試作が一時停止後も20回成功したことから、
本体の排他外初期化を取り除くという前回の修正候補は引き続き妥当である。
正式な修正には元の失敗条件と対応OSでの回帰検証が必要であり、セキュリティ製品の一時停止を代替策とはしない。
今回の再実行は対象テストの調査であり、ローカルv3全体の再実行や全件成功の確認は行っていない。

集計、状態のraw値、再実行で使用したmodule、代表例外、ログのSHA-256は
[workflow-history-security-paused-evidence.json](raw/workflow-history-security-paused-evidence.json)へ追加保存した。
初回の証拠JSONは保存したままとし、再調査の結果で置き換えていない。

## COMODO再有効化後の再調査

ユーザーから「セキュリティー製品を再度有効にした」との申告を受け、同日06:19 JSTから再実行した。
対象は同じCOMODO Internet Security Premiumである。調査側は製品設定を変更していない。
HEADと本体・元のテストのSHA-256は初回・一時停止中と一致し、pytest自動pluginも引き続き無効とした。
元の60秒待機の単独テスト、6回ずつの3条件比較、隔離試作をこの順で逐次実行した。

### 停止の再現とnative観測

元の60秒待機を変えない単独テストでは、最初の1回は成功（4.49秒）、次の1回は失敗（64.53秒）だった。
停止を観測したため、この連続観測は2回で終了した。子プロセスPID 55100のmain threadは
`interprocess_lock.py:96`の`Path.open("a+b")`に留まり、初回と同じ停止経路を示した。

```text
Path.open / InterProcessFileLock.acquire():96
  → CRT _wopen / CreateFileW
  → guard64.dll内のframe
  → NtCreateFile（export symbolはZwCreateFile）で待機
```

native観測はこのテストが作成した子プロセスだけに行い、threadはfinallyで再開した。
ローカルexport symbolによる近傍名であり、guard64.dll内部の関数名や保護機能名は特定していない。
そのworkerが停止したまま、別の診断プロセスから同じファイルを`r+b`で開き、
byte 0のlock取得・解放に即座に成功した。履歴には他の2 workerの`plugin.0`・`plugin.2`が保存されていた。
他workerがそのbyte lockを保持し続けたことや、coverageの終了処理による停止という説明は、この観測と合わない。
30秒の期限はまだ設定されておらず、元の60秒join後にもexitcodeはNoneだった。

### 三段階の比較

下表は本体を変更しない6回ずつの比較である。親のjoinは診断用に12秒、製品側の期限は30秒のままで、
6回のassertionと3 worker・新しい一時ディレクトリの条件を各段階で維持した。
試行数を揃えた部分を取り出したもので、発生率の統計的な推定は行わない。

| lockの初期状態 | 一時停止前・自動plugin無効 | 一時停止中・自動plugin無効 | 再有効化後・自動plugin無効 |
| --- | --- | --- | --- |
| 未作成 | 5 failed / 1 passed、62.80秒（open停止4・flush失敗1） | 6 passed、29.77秒（停止stackなし） | 3 failed / 3 passed、53.62秒（open停止3） |
| 空ファイルを事前作成 | 2 failed / 4 passed、42.94秒（open停止2） | 6 passed、22.72秒（停止stackなし） | 3 failed / 3 passed、53.14秒（open停止3） |
| 1バイトを事前作成 | 6 passed、18.20秒 | 6 passed、26.42秒 | 6 passed、17.55秒 |

再有効化後の6件の失敗は、いずれもexitcode Noneのassertionと対応するopen停止stackを伴った。
この条件比較では初期化flushのPermissionErrorは観測しなかった。
未作成lockの単独2回と比較6回を合わせると、再有効化後は8回中4回でopen停止を観測した。
空ファイル条件の3回を含めると、今回の本体による20試行ではopen停止7回・成功13回だった。
一時停止中の未作成lock 38回でopen停止を観測しなかった結果と、同じコードで異なる挙動になった。

同期・非同期の初期化呼出しだけを省いた隔離試作も、再有効化後に20回実行した。
**20 passed、55.45秒**で、open停止stackと初期化時の例外を観測しなかった。
このコピーのsource hashは一時停止中の20回成功時と同じで、元の本体への変更は適用していない。
初回の有効時10回成功、一時停止中20回成功、再有効化後20回成功という結果になる。
空ファイル上の排他取得と3件の履歴mergeを確認しているが、正式修正や全OSでの保証の代用にはしない。

### 状態の観測と原因評価

06:19 JSTのSecurity Centerのraw値は以下のとおりである。
いずれも個別機能の有効／無効状態をdecodeするためには使用していない。

| 登録された製品 | 一時停止中のproductState（05:37） | 再有効化後のproductState（06:19） |
| --- | --- | --- |
| COMODO Antivirus | 393216（`0x60000`） | 397312（`0x61000`） |
| Windows Defender | 397568（`0x61100`） | 393472（`0x60100`） |

`cis`・`cmdagent`は同じPIDで存続し、06:20の新規Pythonにも`guard64.dll`が読み込まれていた。
DLLはCOMODO Internet Security 12.2.3.8026で、停止中にもロードされていたことから、
DLLの有無だけを停止発生の条件とはできない。

今回の再有効化で、前回の「再有効化との比較がない」という制限を補った。
同じソースで停止が戻り、停止経路も一致したため、**COMODOの有効化操作に伴う保護環境との相互作用が
open停止を誘発・増幅する説明は有力になった**。
ただしCOMODOとWindows Defenderの登録値が両方変わり、COMODOの個別機能を一つずつ制御していない。
ランダム化した比較やkernel stackもないため、単独製品・特定機能の欠陥、oplock等の内部原因までは確定しない。

本体側には、排他取得前の初期化write／flushが別workerのopen・lockと重なり得る処理と、
そのopen・初期化を対象外とする期限設定がある。
現時点で有力な説明は、この本体側の条件とWindows／保護環境のファイル取得経路との相互作用である。
初期化を省く・事前に1バイトを置く実験はこの説明を支持するが、停止する具体的なI/O順序を直接証明してはいない。
一時停止中にもflush失敗があったため、製品の停止や待機時間の延長だけで本体側の問題を解消できるとは判定しない。

再有効化後の機械可読な集計、native／Python stack、同じファイルへのlock診断、状態のraw値、
sourceとログのSHA-256は[workflow-history-security-reenabled-evidence.json](raw/workflow-history-security-reenabled-evidence.json)へ保存した。
初回と一時停止中の証拠は変更せず、追加記録として管理する。
今回もローカルv3全体やCIの再実行は行っていない。

## 再観測と保存した証拠

元のテストを変更せずstackを取得するprobeを追加した。
出力先には未作成のディレクトリを指定する。失敗時も元のテストのfinallyが所有workerを終了する。

```powershell
.runtime/quality-venv/Scripts/python.exe docs/investigations/quality-security-review-2026-10-06/probes/history_process_stacks.py .runtime/history-hang-observation --no-autoload
```

初回調査ではprobe自身も実行し、元の60秒joinによる1 failedとopen停止stackを確認した。
一時停止後の同じprobeの結果は上記の再調査表に記載した。
probeは起動時のfaulthandlerだけを追加し、本体・既存テスト・machineの環境変数定義元は変更しない。
子プロセス用の環境変数はこの実行にだけ渡す。

機械可読な集計、代表stack、初期化時の例外と出典ログのSHA-256は
[workflow-history-hang-evidence.json](raw/workflow-history-hang-evidence.json)に保存した。
元の全ログ・比較plugin・隔離コピーはignore対象の`.runtime/qs013-verification/`にある。

## 修正を進める際の方針

ロックファイルを排他取得前に初期化する必要を取り除くことが第一候補である。
Windowsの`msvcrt.locking`はEOFを越える範囲もlockできるため、1バイトの実データが必須ではない。
[Python公式仕様](https://docs.python.org/3.11/library/msvcrt.html#msvcrt.locking)もこの動作を規定している。
今回の隔離試作では空ファイル上の排他・期限・解放が動作した。
データが必要であれば、初期化を排他取得後に限定する案を別途検証する。

事前作成をテストに足すだけでは、利用者の新規profileや既存の空lockファイルに対する問題が残る。
正式な修正では現行の新規作成テストを維持し、初期化競合と空ファイル上の排他を回帰テストで固定する。
共有lock primitiveの利用者、同期／非同期、3 OS・対応Python版への影響を確認する必要がある。

また、期限をopenより前に移すだけでは、実行中のWindows同期I/Oを中断できない。
現在の期限がlock再試行に対するものなのか、openまで含む取得全体に対するものなのかを明確にし、
後者を保証する場合はI/O取消と所有handleの後始末を含む別の設計が必要になる。
[CreateFileWの公式仕様](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)には取得期限の引数がない。
セキュリティ製品の停止や一括除外をアプリの修正方法にはしない。
