# open前後の待機時間とWindowsエラーの比較（2026-10-11）

## 結論

前回のopen-only比較には、workerのopen前・open後・close後の明示sleepはなかった。
今回、各位置に0／10／100msの待機を入れて比較した。
open前とclose後の待機では、空ファイル・truncate・毎回準備の条件で停止を解消しなかった。
open後にhandleを保持する待機では一部の試行が完了したが、同じ設定の再実行で停止した。
**待機時間の追加を安定した修正とする根拠は得られなかった。**

112試行中34試行が完了し、78試行でopenが滞留した。保持した1バイトのr+b・a+bは、待機なしを含む全7設定で各2回とも完了した。

停止したopenは例外・失敗戻り値を返していない。その呼出しの最終GetLastErrorは取得できず、
0、32（共有違反）、5（アクセス拒否）などの値を割り当てていない。
返る失敗の対照試験では、ネイティブ境界で2／3／5／32を区別できた。
これらの対照試験のコードを、実際に停止したopenの原因コードと読み替えてはいけない。

## 以前の待機と今回の変更

[前回の保持した0／1バイトの比較](workflow-history-open-size-modes-2026-10-11.md)では、
[history_open_size_modes.py](probes/history_open_size_modes.py)のworkerは
開始barrier → Path.open → close → 終了barrierの順で動いた。
open前後にtime.sleepはなく、profileのhold=0もこのworkerからは使っていない。
barrierによる同期と、observerのEvent.wait(0.25)は存在したが、workerのopen前後のsleepではない。
parentは準備handleを閉じてから開始barrierへ進み、全workerの終了barrier後に次の準備を行う。

新しい[history_open_delays.py](probes/history_open_delays.py)で、次の単一要因を変更した。
複数位置の待機を同時に組み合わせた比較や、workerごとに異なる時間でずらす比較ではない。

| 設定 | open前 | open成功後、handleを保持中 | close後 |
| --- | ---: | ---: | ---: |
| zero | 0ms | 0ms | 0ms |
| pre-10 / pre-100 | 10 / 100ms | 0ms | 0ms |
| held-10 / held-100 | 0ms | 10 / 100ms | 0ms |
| closed-10 / closed-100 | 0ms | 0ms | 10 / 100ms |

preの待機は開始barrier後、closedの待機は終了barrier前に置く。
3 workerに同じ設定を適用し、並行openを許す。handle保持はbyte lockの取得ではない。
戻ったopen/closeの例外も記録して進めるため、closedのpacingは例外後にも行う。
通常比較ではPath.openを維持し、ネイティブhelper DLLをloadしていない。
全設定でエラー診断用にucrtbaseを参照し、open前にdoserrnoを0へクリアした。
前回のprobeとはこの計測追加と期限も異なるため、発生率を直接比較しない。

workerは対象ファイルへwrite・明示flush・byte lockを実行せず、本体moduleもimportしない。
診断ファイルへの書込みと、close時の空buffer処理まで存在しないという意味ではない。
定期faulthandler dumpは使わず、fatal handlerのみ有効にした。

## 環境・試験条件

HEAD: c4ef652bed1d3fa45813b1e2e790b327b9e82b68。
branch: codex/fix-qs-013-construction-cleanup。既存の未コミット調査資料を保持した。
CPython 3.11.4（MSC v.1934、AMD64）、Windows 10 22H2 / 19045.7727、
COMODO Internet Security 2027 Premium 12.4.0.8170で実行した。
同じboot/sessionとguard64の版・hashを前後で記録し、調査側で設定・除外を変更していない。
COMODO不在環境、別Python版、機能別無効化との新しい対照は今回実施していない。
Security Centerの登録状態だけから各保護機能の実効状態を断定しない。
試験は従来の比較と同じsandbox外で実施した。

対象は保持した初期0／1バイト × r+b／w+b／a+bの6条件と、毎回削除のa+b、
毎回wbで1バイトへ書き直すr+bの2対照。
保持条件ではparentの準備は初回だけ、対照では毎ラウンド行う。
w+bは初回も以降もtruncateするため、初期1バイトを保持し続ける条件ではない。
モードの仕様は[Python open](https://docs.python.org/3.11/library/functions.html#open)と
[CPython 3.11.4 FileIO](https://raw.githubusercontent.com/python/cpython/v3.11.4/Modules/_io/fileio.c)に従う。

主比較は8条件 × 7待機設定 × 2回 = 112試行、各3 worker × 100ラウンド。
1回目は通常順、2回目は待機設定・条件の両方を逆順にした。
barrier期限10秒、所有workerのjoin猶予5秒、条件別外部watchdog40秒。
2秒以上のopen滞留をobserverが0.25秒間隔で記録した。
約15秒の滞留は有限観測後の打切りであり、永久停止の証明ではない。
前回の約65秒と短長を比べて回復が早まったと判断しない。

## 主比較の結果

値は「全workerの全open/close成功で完了した試行数 / 2」。
barrier通過だけでは成功と判定せず、各workerの成功数・例外・終了状態を照合した。

| 条件 | 待機なし | 前10ms | 前100ms | 保持10ms | 保持100ms | close後10ms | close後100ms |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 保持0・r+b | 0/2 | 0/2 | 0/2 | 1/2 | 1/2 | 0/2 | 0/2 |
| 保持0・w+b | 0/2 | 0/2 | 0/2 | 0/2 | 1/2 | 0/2 | 0/2 |
| 保持0・a+b | 0/2 | 0/2 | 0/2 | 1/2 | 1/2 | 0/2 | 0/2 |
| 保持1・r+b | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| 初期1・w+b（→0） | 0/2 | 0/2 | 0/2 | 0/2 | 1/2 | 0/2 | 0/2 |
| 保持1・a+b | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| 毎回削除・a+b | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 |
| 毎回wb書き直し・r+b | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 |

主比較の予定は33,600回。完全に完了した試行の10,200回は全workerが成功した。
保存された戻ったopenは12,596回、最大333.410ms。durableに記録したopen/close例外は0件、workerの最終結果欠落は78件。
open滞留は14.703〜15.219秒で打ち切った。条件別外部watchdogの打切りは0件。
欠落workerの過去の成功数は集計に加えず、例外0を全workerの無障害と解釈していない。

待機時間は指定値であり、sleepの実測値は各workerで別に採った。
10ms設定の保存された試行別worker加重平均は10.138〜13.283ms、最大実測57.369ms。
100ms設定の保存された試行別worker加重平均は100.222〜126.872ms、最大実測230.396ms。
worker結果が欠落した部分はlatencyやsleep分布へ補完しない。
実行別の平均・最大とworker別p95の最大値をCSVに載せた。
集約したworker別p95の最大値は、全呼出しをpoolしたp95ではない。
openのlatencyには明示sleepを含めない。
保持条件の試験前後はfile IDが一致し、r+b/a+bはsize・mtimeも一致した。
w+bの初期1バイトは実測でも0バイトとなった。この照合結果もJSONに保存した。

## ラウンド数を増やした確認

1回目のheld-100で空ファイル3モードが各100ラウンド完了したため、
同じ3条件を追加で1,000ラウンド、各1回とした。
主比較の2回目で停止した条件も除外しない探索的な追試である。
barrier期限60秒、条件別watchdog240秒とし、短い期限による打切りとの違いも確認した。
主比較・追試・対照を並行実行していない。
主比較は22:28〜22:54、native補助比較は22:56〜22:58、追試は23:28〜23:32（JST）。
native補助比較と追試の間には約30分の間隔があり、短期の連続再実行と同一視しない。

| 空ファイル・held-100 | 完了ラウンド / 1,000 | 全open成功 | open滞留観測（秒） |
| --- | --- | --- | --- |
| r+b | 87 | 未完了 | 65.015 |
| w+b | 60 | 未完了 | 64.953 |
| a+b | 122 | 未完了 | 64.937 |

0/3試行が完了、3/3試行でopen滞留。返った例外0件、条件別外部watchdog打切り0件。
保存された戻ったopenは544回。欠落worker結果は3件であり、その履歴はゼロとして補完していない。

## 最終エラーの捕捉

次の三種類を区別した。

1. 通常Path.openが返した例外のerrno・winerror。
2. 同じthreadで例外捕捉後に読んだGetLastError・doserrno。これは遅い診断値である。
3. C helperが_wopenの復帰直後に保存したGetLastError・CRT errno・doserrnoと、
   別呼出しのCreateFileW復帰直後のGetLastError。

GetLastErrorはthreadごとの状態で、関連APIの復帰直後に読む必要がある。
別threadの値、または処理中threadの以前の値を、未復帰openの最終結果として扱えない。
[Microsoft GetLastError](https://learn.microsoft.com/en-us/windows/win32/api/errhandlingapi/nf-errhandlingapi-getlasterror)
を根拠とする。[ctypesのuse_last_error](https://docs.python.org/3.11/library/ctypes.html)には
private copyの交換があるため、ctypes.get_last_error()をPath.openの結果と同一視していない。

CPython 3.11.4の通常FileIOは、_wopen失敗をerrnoに基づく例外へ変換する。
winerrorがNoneでもWin32で失敗しなかったという意味ではない。
Pythonが例外を構築するまでの処理により、後から読んだGetLastError等は変わり得る。
_wopenの後にもCRT内部の後始末があり、そのGetLastErrorが下位CreateFileWの値を
必ず保存する保証はしない。CRTによるOSエラーからerrnoへの変換を照合するには
[doserrnoの仕様](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/get-doserrno?view=msvc-170)
も参照する。各native呼出し前にerrno・doserrno・LastErrorをクリアした。

[C helper](probes/history_open_error_capture.c)は /LD /MD /O2 /W4でビルドした。
compiler file versionは19.44.35227.0（VS 2022 x64）。_wopenのC4996は意図した診断APIについての警告で、
本体コードへ追加した警告ではない。ソース・flags・compiler/DLL hashを証拠へ保存した。
ソース、build例、helperをloadする補助試験を追加し、DLLはignore対象の.runtimeへ置く。

openerで_wopenをC helperへ渡す補助比較は、8条件・待機なし・100ラウンド各1回。2試行完了、6試行open滞留、復帰後のopen/close例外0件だった。
この経路はbuiltins.open(opener=...)と追加DLLを使うため、通常Path.open比較と合算していない。

取得手段の対照は[history_open_error_controls.py](probes/history_open_error_controls.py)で実施した。
試験専用の存在しないパス、directoryをfileとして開く要求、明示的な共有不可handleを使った。
既存ファイルのACL、保護設定、他プロセスのhandleは変更していない。

| 意図した条件 | モード | Python errno / winerror | _wopen errno / doserrno / LastError | CreateFileW LastError |
| --- | --- | --- | --- | --- |
| missing-file | r+b | 2 / None | 失敗 2 / 2 / 2 | 失敗 2 |
| missing-file | w+b | 成功 | 成功 0 / 0 / 183 | 成功 183 |
| missing-file | a+b | 成功 | 成功 0 / 0 / 183 | 成功 183 |
| missing-parent | r+b | 2 / None | 失敗 2 / 3 / 3 | 失敗 3 |
| missing-parent | w+b | 2 / None | 失敗 2 / 3 / 3 | 失敗 3 |
| missing-parent | a+b | 2 / None | 失敗 2 / 3 / 3 | 失敗 3 |
| directory | r+b | 13 / None | 失敗 13 / 5 / 5 | 失敗 5 |
| directory | w+b | 13 / None | 失敗 13 / 5 / 5 | 失敗 5 |
| directory | a+b | 13 / None | 失敗 13 / 5 / 5 | 失敗 5 |
| sharing-conflict | r+b | 13 / None | 失敗 13 / 32 / 32 | 失敗 32 |
| sharing-conflict | w+b | 13 / None | 失敗 13 / 32 / 32 | 失敗 32 |
| sharing-conflict | a+b | 13 / None | 失敗 13 / 32 / 32 | 失敗 32 |

12対照のうち10件が予定した失敗を返し、2件が成功した。対照の外部watchdog打切りは0件。
これらは再現するエラーを意図して作った対照であり、主比較の自然発生エラー件数に含めない。例外捕捉後の遅い診断値もJSONに保存したが、原因確定には境界で保存した値を使用する。

共有違反32とアクセス拒否5がどちらもerrno 13になったことから、errno 13だけで
共有違反・保護製品の拒否・権限不足を区別してはいけない。
コードの意味は[Microsoftのエラー一覧](https://learn.microsoft.com/en-us/windows/win32/debug/system-error-codes--0-499-)
に従う。成功を戻り値で確認し、成功時の非ゼロLastErrorを失敗と数えない。
CREATE_ALWAYS/OPEN_ALWAYSで既存fileを正常に開いた場合の183もその一例である。
[CreateFileW仕様](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)
に従って解釈する。missing-fileのw+b/a+b対照は通常Pythonのopenが先に作成するため、
後続のnative呼出しでは既存fileになっている。

実際の滞留openには失敗戻り値がなく、native helperもその復帰後の記録へ到達しない。
barrierのBrokenBarrierError、条件の終了コード1、所有workerの強制終了コードは
そのopenのWin32エラーではない。外部打切りをERROR_OPERATION_ABORTED等とも読み替えない。

## native stackと原因の考察

repeat-01-held-10-stable-0byte-truncateのworker 1、PID 15288 / TID 10440は、
iteration 0のopenに留まり、native stackは_wopen → CreateFileW → guard64.dll → ZwCreateFileだった。
同じcaseのPID 6716、12648はmultiprocessingのbarrier待ちであり、phase記録と整合した。
所有親子関係を確認してthreadを短時間停止し、finallyで再開した。
このcaseは採取による短時間の停止を含み、他のcaseの無介入比較とは区別する。
guard64の近傍export名Exportedから内部機能名を特定したものではない。
kernel stack、I/O要求、minifilterやoplockの待機先は取得していない。

待機位置による差は、open成功後の早いcloseと他workerのopenが重なる順序、
ファイルの状態変更、保護製品の処理などの時間依存を調べる手掛かりになる。
しかしheldでも再現しており、少数試行から発生率や単一の競合手順を確定しない。
同じ時間のsleepは並行openを直列化しない。結果にはOSのスケジューリングや過去の検査状態も影響し得る。
closeが復帰した後に待つことと、handleを保持して待つことを同じ操作として扱わない。

保持した1バイトのr+b/a+bの結果は、前回の2,000ラウンド各3回の完了と整合する。
一方、初期1バイトのw+bは空へtruncateし、毎回1バイトを書き直す対照も別の状態になる。
非空の同じ実体を更新せず保持する案は対策候補として残るが、同時初回作成・初期化の安全性は別途必要である。

今回も初期化write/flush・byte lockなしでopenが滞留した。
初期化flush競合をopen停止の必要な原因とする説明は支持しない。
以前の不在環境との差とguard64を含む経路から、COMODOを含む環境との相互作用を主要候補とする。
guard64のframeがあるだけでは、COMODO固有の実装不良やPython単独の不良までは証明できない。
共有違反の対照が速やかに32を返したことも、滞留openが共有違反32だと断定する根拠にはならない。

固定sleepを本体へ追加する修正や、戻らないopenに対する同一threadのretryだけでは、
解消と期限保証を説明できない。今回の調査では本体の挙動を変更していない。
さらに絞るには、同じ手順で1 worker対3 worker、open/closeを段階同期した条件、
FileIO/os.open/direct CreateFileW、同じfile IDの0→1バイト変更を比較する。
再現時のkernel/ETWまたはProcmonのI/O時系列と、COMODO側のPID・時刻付き診断で、
アクセス・共有mode、別processのhandle、oplock・filter待機の有無を照合できれば、原因箇所へ近づける。
未復帰I/Oの原因追跡には、この種の待機情報が必要であり、最終GetLastErrorだけでは足りない。

## 証拠、検証、再実行

[実行別CSV](workflow-history-open-delay-errors-runs-2026-10-11.csv)、
[証拠JSON](raw/workflow-history-open-delay-errors-evidence-2026-10-11.json)に、全有効試行、
worker結果の欠落、durableな例外記録、実測sleep、初回・終了file snapshot、native stack、
境界エラー、環境前後、実行したソース・hashを保存する。
全ログと補助DLLは.runtime/history-open-delays-20261011/へ保存した。

最初の追加probeにPath.openへopenerという未対応keywordを渡す不備があり、TypeErrorを検出した。
その試行を停止・保存して比較から除外し、通常経路をPath.open、補助経路をbuiltins.open(opener=...)へ分けた。
旧/修正後hash、除外理由、停止記録と、修正後smokeの3ラウンド・9成功・例外0を保存した。
このTypeErrorはWindowsのopen障害に数えていない。修正後に主比較を最初からやり直した。
build開始時のcmdでのパス区切り指定も修正してからビルド成功を確認した。

本体・既存テスト・設定168ファイルは試験前後と前回で一致し、既存監査資料は追記前に全hashの一致を確認した。
追加Python probeのRuff・構文検査が成功した。証拠とCSVの件数・source hashを照合した。
v3全体・mypy・CIは今回再実行していない。通常の製品テストの全面成功を示す調査ではない。

```powershell
.runtime/quality-venv/Scripts/python.exe `
  docs/investigations/quality-security-review-2026-10-06/probes/history_open_delays.py `
  .runtime/history-open-delays-rerun --rounds 100 --repeats 2 `
  --barrier 10 --case-timeout 40 --controls

.runtime/quality-venv/Scripts/python.exe `
  docs/investigations/quality-security-review-2026-10-06/probes/history_open_delays.py `
  .runtime/history-open-held-rerun --rounds 1000 --repeats 1 `
  --timings held-100 --conditions stable-0byte-readwrite stable-0byte-truncate stable-0byte-append `
  --barrier 60 --case-timeout 240
```

出力先は毎回新しいdirectoryを指定する。未完了・戻ったエラーがあれば終了コード1となる。
ネイティブ対照を再実行する場合はx64のMSVC環境でC helperを/LD /MDでビルドし、
history_open_error_controls.pyへ新しい出力先とDLL絶対pathを渡す。
opener補助比較はhistory_open_delays.pyへ--native-helper DLL絶対path --timings zeroを追加する。
対照全体にも外部watchdogを設ける。過去資料の値・当時の判定は保存し、今回の結果を追記した。
