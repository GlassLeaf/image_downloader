# 保持した0／1バイトファイルとopenモードの比較（2026-10-11）

## 結論

**同じ1バイトファイルを保持する`r+b`・`a+b`は各3回とも完了したが、同じ0バイトファイルでは3モードすべてで各3回ともopenが停止した。**
初期1バイトの`w+b`も各3回停止した。`w+b`は初回openで0バイトへ切り詰めるため、非空ファイルを保持した場合とは条件が異なる。
毎回削除・作成する`a+b`と、毎回1バイトへ書き直す`r+b`の対照も各3回停止した。

「事前作成して存在させるだけ」と「書込み済みの1バイトを保持して再利用すること」は区別する必要がある。
今回の条件では後者に改善が見られた。少数の連続試験から恒久的な解消や停止確率は保証しない。

## 環境と試験方法

HEADは`c4ef652bed1d3fa45813b1e2e790b327b9e82b68`、ブランチは`codex/fix-qs-013-construction-cleanup`。
監査資料の未コミット変更を保持し、本体・既存テスト・設定を変更していない。
CPython 3.11.4（MSC v.1934、AMD64）、Windows 10 22H2／19045.7727、
COMODO Internet Security 2027 Premium 12.4.0.8170で実行した。
起動セッション、DLL版・hash、Security Center登録、確認対象driverを前回の再導入後と照合した。
調査側で保護設定・除外を変更せず、機能ごとの実効設定が完全に一致するとの判定はしていない。

新しい[history_open_size_modes.py](probes/history_open_size_modes.py)から、
既存の[history_lock_variability.py](probes/history_lock_variability.py)の試験進行・barrier・phase採取を再利用した。
workerは標準ライブラリだけを使い、対象への`Path.open(mode)`と`close()`だけを行う。
本体のmodule、initializer、byte lock、write、明示flushは使わない。
close時の空のbufferの処理まで除いたという意味ではない。
fatal handlerを有効にし、定期`dump_traceback_later()`は使わなかった。

各条件は3 worker、2,000ラウンド、3回。各回は新しいparentと試験専用ファイルを使う。
同じラウンドのworker同士は並行openを許す。parentは初回だけ`write_bytes(b"")`または
`write_bytes(b"\0")`で準備し、準備handleのclose後に開始barrierを解放する。
保持条件の2ラウンド目以降はparentが対象を書き直したり削除したりしない。
全workerのclose後の終了barrierを通過してから次のラウンドへ進む。
open／closeの例外も記録するため、barrier通過だけでは成功とは判定せず、worker結果も照合した。

| 条件 | workerモード | 2ラウンド目以降のparent操作 | 想定するサイズ変化 |
| --- | --- | --- | --- |
| 保持・初期0バイト | `r+b`／`a+b` | なし | 0バイトを保持 |
| 保持・初期1バイト | `r+b`／`a+b` | なし | 1バイトを保持 |
| 保持・初期0／1バイト | `w+b` | なし | openがtruncateするため0バイトになる |
| 対照・毎回未作成 | `a+b` | unlink | workerが新規作成する |
| 対照・毎回1バイトへ書き直し | `r+b` | `write_bytes(b"\0")` | wbでtruncate・1バイト書込み・close |

`w+b`はファイルを保持していても内容を切り詰める。
1バイトで始めた`w+b`は、全ラウンドで1バイトを維持する試験ではない。
仕様は[Pythonのopen](https://docs.python.org/3.11/library/functions.html#open)と、
使用版の[FileIO実装](https://raw.githubusercontent.com/python/cpython/v3.11.4/Modules/_io/fileio.c)で照合した。
`r+b`は`O_CREAT`／`O_TRUNC`なし、`w+b`は両者あり、`a+b`は`O_CREAT`／`O_APPEND`あり。
全モードが`O_RDWR`／`O_BINARY`／`O_NOINHERIT`を使う。
既存ファイルで`O_CREAT`を指定することと、実際の新規作成を同一扱いにしない。
[Microsoftの_wopen仕様](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/open-wopen?view=msvc-170)も参照した。

実行順は1回目が通常順、2回目が逆順、3回目は順番を回転させた。
barrierの期限は60秒、各条件には別プロセスの120秒watchdogを設けた。
監視対象phaseへの2秒以上の滞留を0.25秒ごとに記録し、終了できない所有workerだけを終了する。
OS内のopenをcancelした試験ではなく、有限時間で打ち切る試験である。
保持の確認には初回前、最初の完了ラウンド後、試験終了後のsize・device・inode・mtimeを使う。
全ラウンドでstatを追加せず、検査によるタイミング変更を抑えた。
初回ラウンド自体が完了しない条件には中央のsnapshotがない。

## 比較結果

| 条件 | 完了ラウンド（1／2／3回目、各予定2,000） | 完了条件数 | 最長open滞留（秒、各回） |
| --- | --- | ---: | --- |
| 保持・初期0バイト・r+b | 14／214／16 | 0/3 | 64.828／65.063／64.954 |
| 保持・初期0バイト・w+b | 0／15／13 | 0/3 | 64.891／64.797／64.875 |
| 保持・初期0バイト・a+b | 2／0／21 | 0/3 | 64.859／65.031／65.047 |
| 保持・初期1バイト・r+b | 2000／2000／2000 | 3/3 | 2秒以上なし |
| 保持・初期1バイト・w+b | 7／0／40 | 0/3 | 65.047／64.906／64.891 |
| 保持・初期1バイト・a+b | 2000／2000／2000 | 3/3 | 2秒以上なし |
| 対照・毎回削除・a+b | 3／20／0 | 0/3 | 64.890／64.985／65.031 |
| 対照・毎回1バイトへ書き直し・r+b | 8／1／13 | 0/3 | 64.969／64.875／64.843 |

完了ラウンドはparentが終了barrierを通過した数で、最初のラウンドは番号0である。
停止ラウンドで他workerが完了したopenは、このラウンド数には含まれない。
停止したworkerの最終結果は欠落するため、保存されたerrors 0を全workerの無失敗とは扱わない。
全24条件中6条件が完了し、18条件でopen滞留を記録した。完了した6条件の計36,000回は全workerが成功し、エラー0だった。
予定は144,000回、欠落workerの最終結果は18件。保存された戻ったopenは36,810回、最大9.294ms。
外部120秒watchdogの打切りはなく、60秒barrierの期限後に所有workerを終了して全条件のsummaryを保存した。約65秒の観測値には終了前のjoin猶予が含まれ、永久停止と証明した値ではない。

保持条件では試験前後のdevice／inodeがすべて一致した。
`r+b`／`a+b`はサイズとmtimeも観測点で保持された。
`w+b`の初期1バイトは実測でも0バイトになり、ファイルIDは維持された。
単にパスが同じだけの削除・再作成と区別した。

## nativeの照合

| 条件 | open停止worker PID／TID | 採取したnative経路 |
| --- | --- | --- |
| repeat-01-stable-0byte-truncate | 13088／14080 | _wopen → CreateFileW → guard64.dll → ZwCreateFile |
| repeat-01-stable-0byte-append | 9144／13268 | _wopen → CreateFileW → guard64.dll → ZwCreateFile |
| repeat-02-stable-0byte-readwrite | 14724／7700 | _wopen → CreateFileW → guard64.dll → ZwCreateFile |

所有親子関係を確認したworkerのthreadを短時間停止し、finallyで再開する既存の採取器を使った。
一覧のnative採取はphase滞留の観測後に実施した。
採取した条件は観測による短時間の停止を含むことを証拠に記録した。
他workerのnative stackはmultiprocessingの待機系統で、対象workerのopen停止と整合する。
`ZwCreateFile`は採れたexport名であり、NtCreateFile系統として扱う。
COMODOの近傍export表示`Exported`は内部関数・機能名の特定ではない。
kernel stack、I/O要求、filterの待機対象は今回も取得していない。

## 原因の考察と修正への含意

今回の0バイト・`r+b`／`a+b`は、削除・truncate・書き直しを伴わないラウンドでも停止した。
したがって、以前の[再導入後の比較](workflow-history-post-reinstall-2026-10-11.md)から挙げた
「毎回のファイル準備と競合open」の範囲を、**保持された空ファイルの競合open**まで広げる。
今回の結果では「毎回の準備が停止の必要条件」とする説明は成立しない。

一方、同じ1バイトを保持する`r+b`／`a+b`は停止を観測しなかった。
先頭byteを排他利用するlockファイルについて、安定した非空ファイルの再利用は対策候補を支持する。
ただし、初期1バイトを`w+b`で切り詰める条件と、毎回1バイトへ書き直す`r+b`対照は停止した。
**「1バイトを事前に用意すれば、以後の操作にかかわらず直る」とは言えない。**
サイズの保持、実際の事前書込み、truncate／更新の有無、保護製品の扱いが関係する候補である。
0対1バイトの比較は内容（NUL）、事前に実書込みしたか、metadata・検査履歴も変わるため、
COMODO内部でサイズだけを見て分岐するという仕組みを確定したものではない。

`r+b`で空ファイルが停止するため、appendまたは`O_CREAT`だけを原因とする説明にも合わない。
3モードはPython・CRT・Win32の通常open経路を共有し、採取した停止はguard64.dllを経由する点も以前と一致する。
COMODOを含む保護環境とファイル状態・競合openとの相互作用を主要候補として維持する。
今回、COMODO不在条件や別Python版は比較していないため、特定componentのバグやPython単独のバグを確定しない。

workerに製品の初期化write／flushもbyte lockもない状態で停止した。
今回のopen停止も、先に確認した初期化flush競合から生じることを必要としない。
flush競合への修正とopen停止への対策は別々に検証する必要がある。
0バイトのopen-onlyが停止したことを、元のlock APIでも必ず同じ頻度で止まるという結論には広げない。

修正を試すなら、lockファイルを削除・毎回truncateせず、非空の同じ実体を保持する案を独立に比較する。
ただし複数プロセスの同時初回作成・先頭byte初期化には元の競合があり、
安全な作成・初期化・排他の手順を別途設計する必要がある。
既存のlock APIを`w+b`へ置き換える案は、この試験結果と内容破壊の点から支持しない。
この調査では本体修正を行っていない。

さらに切り分ける場合は、同じファイルIDのまま0→1バイトを一度だけ書いてから再開する対照、
1バイト以外の非空サイズ・内容、1 worker対3 worker、bufferなし`FileIO`／`os.open`を同じ手順で比較する。
条件ごとのkernel待機情報とCOMODO側の時刻・PID付き診断があれば、相関から停止機構の特定へ進める。
保護設定を変更する比較は今回実施していない。

## 証拠と再実行

[実行別CSV](workflow-history-open-size-modes-runs-2026-10-11.csv)、
[証拠JSON](raw/workflow-history-open-size-modes-evidence-2026-10-11.json)に全条件、
欠落worker、phase、初回・終了時のfile情報、startup module・guard64一覧、native結果、
実行したprobeと補助採取器のソース、前後の環境・hashを保存した。
元ログはignore対象の`.runtime/history-size-modes-20261011/`に保存する。

本体・既存テスト・設定168ファイルは試験前後と前回で一致した。
既存監査資料のhashも追記前に一致を確認した。
追加probeのRuffと構文検査は成功した。v3全体・CI・mypyは今回再実行していない。
最初のsandbox内の環境採取はCIMの読取り拒否となり、sandbox外で採り直してから試験を開始した。
試験本体も前回と同じsandbox外で実行した。この採取失敗をopen試験の失敗に数えていない。

```powershell
.runtime/quality-venv/Scripts/python.exe `
  docs/investigations/quality-security-review-2026-10-06/probes/history_open_size_modes.py `
  .runtime/history-size-modes-rerun --rounds 2000 --repeats 3 --controls --case-timeout 120
```

新しい出力先を指定する。終了コード1は今回のような未完了条件を含む場合にも返される。
過去の値・当時の判定は書き換えず、今回の追加観測を別記録とした。


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
