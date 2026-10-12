# CPython公式変更履歴と履歴保存テスト障害の照合（2026-10-11）

## 結論

Python側のバグは候補として残る。特に、定期stack出力中のアクセス違反には、
同じ`faulthandler.dump_traceback_later()`のクラッシュを扱った公式の修正がある。
ただし、今回の異常終了にはfaulting module・例外命令位置がなく、同一原因とは確定できない。

一方、今回の生存workerのopen停止に直接一致する修正は、調査した公式変更履歴では見つからなかった。
初期化flushの`PermissionError`に一致する修正も見つからなかった。
「一致する修正を見つけていない」は「Pythonの欠陥が存在しない」という判定ではない。
今回は履歴・issue・ソースの照合であり、別版Pythonでの再現試験は実施していない。

## 環境と調査範囲

- 実測した使用版：CPython 3.11.4、`tags/v3.11.4:d2340ef`、MSC v.1934、AMD64。
- 実行環境：`.runtime/quality-venv/Scripts/python.exe`、Windows 10 build 19045。
- 対象コミット：`c4ef652bed1d3fa45813b1e2e790b327b9e82b68`。
- 作業ツリーには以前の調査資料の未コミット変更がある。本体ソースの変更は今回追加していない。
- 2026-10-11時点の3.11～3.15公式変更履歴を全文取得し、Windows、open、close、flush、seek、tell、
  FileIO、Buffered I/O、msvcrt、faulthandler、multiprocessing、lock、race、deadlockを軸に候補を抽出した。
  候補の実行条件を既存probe・本体コード・公式issue・PRと照合した。全変更の詳細監査ではない。
- 過去系列の重複収録を除き、各ページの当該系列・3.11.4より後の版・2026-10-11までの確定日付を対象にした。
  未リリースの`Python next`は除外した。新系列のalpha／beta／RC記録も確認したが、表の適用版は原則として正式版で示す。
- 取得時の最新正式版：3.11.17、3.12.15、3.13.16、3.14.8、3.15.0。
  3.15.0の公式公開日は2026-10-09で、現行CIの3.11～3.14の範囲外として参考調査した。
  [3.15.0公式release](https://www.python.org/downloads/release/python-3150/)

公式変更履歴：
[3.11](https://docs.python.org/3.11/whatsnew/changelog.html)、
[3.12](https://docs.python.org/3.12/whatsnew/changelog.html)、
[3.13](https://docs.python.org/3.13/whatsnew/changelog.html)、
[3.14](https://docs.python.org/3.14/whatsnew/changelog.html)、
[3.15](https://docs.python.org/3.15/whatsnew/changelog.html)。

公式ページは各4～7MBで閲覧toolの容量制限を超えたため、承認されたネットワーク取得で全文を保存した。
取得元・サイズ・SHA-256・抽出件数・確認したissueと適用版を
[確認記録JSON](raw/workflow-history-python-changelog-review-evidence-2026-10-11.json)に記録した。
抽出件数は精読済みのバグ件数でも独立したバグ件数でもなく、同じ修正の系列間重複を含む。

## 関連候補の一覧

以下は今回のアプリの確定指摘や新しいQS番号ではなく、CPythonの修正候補の照合である。

| 公式issue | 修正内容・正式版 | 今回との関係 |
| --- | --- | --- |
| [GH-140815](https://github.com/python/cpython/issues/140815) | 定期stack出力で無効・解放済みframe／codeを参照するクラッシュを緩和。3.13.10／3.14.1、3.15.0にも収録 | **アクセス違反の重要な候補**。probeも同じ`dump_traceback_later()`を使用し、公式issueにはWindows Debug x64での再現もある。ただし公式再現は極短timeout・終了処理等を含み、今回の8秒周期・3.11.4との同一性は未確定 |
| [GH-116008](https://github.com/python/cpython/issues/116008)／[PR-141988](https://github.com/python/cpython/pull/141988) | stack出力時に解放済みthread stateを検出。mainと3.13／3.14へbackport | 3.11.4での公式報告がある。報告のLinux／signal方式は今回と異なる。PRは`skip news`なので変更履歴検索だけでは拾えない。3.13.10のtagソースに追加検査を確認。3.11／3.12への同修正の適用は今回確認できなかった |
| [GH-145792](https://github.com/python/cpython/issues/145792) | VLA代替のalloca確保量不足によるstack範囲外参照。3.14.4／3.15.0 | faulthandlerのクラッシュ修正だが、3.11.4の`traceback.c`には該当VLAマクロがなく、今回の版の直接原因としては不一致 |
| [GH-123967](https://github.com/python/cpython/issues/123967) | faulthandlerでtrampoline frameを除外。3.12.8／3.13.1、3.14.0以降 | frame出力の周辺修正。今回そのframeを通った証拠はなく、適用記録も3.11ではない |
| [GH-128400](https://github.com/python/cpython/issues/128400) | 他threadが動作中のdumpによるクラッシュ。3.13.2／3.14.0 | free-threaded build向け。使用中の3.11.4には該当せず、原因候補から除外 |
| [GH-149590](https://github.com/python/cpython/issues/149590) | faulthandler再import時のクラッシュ。3.13.14／3.14.6／3.15.0 | probeにmoduleの削除・再import操作はなく、現在の呼出し条件と一致しない |
| [GH-95782](https://github.com/python/cpython/issues/95782) | BufferedReader／BufferedRandomのseek・tellが負の位置を返し得る問題。3.11.9／3.12.3、3.13.0以降 | 同じbinary I/O層だが、負の位置やseek失敗を今回確認していない。停止はseekに進む前のnative open。flushの権限エラーを直接説明しない |
| [GH-115059](https://github.com/python/cpython/issues/115059) | BufferedRandom.read1で書込みbufferをflush。3.11.9／3.12.3、3.13.0以降 | 対象のopen-only／初期化に`read1()`呼出しはなく、不一致 |
| [GH-119506](https://github.com/python/cpython/issues/119506) | TextIOWrapperのflush中の再入writeでbufferが壊れる問題。3.11.10／3.12.5、3.13.0以降 | 対象ファイルはbinary I/Oで、TextIOWrapper・再入writeを使わないため不一致 |
| [GH-138720](https://github.com/python/cpython/issues/138720) | BufferedWriter／BufferedRandomのcloseとflushのclosed判定を一致。3.15.0 | 公式再現はBufferedRWPair・GC時の`ValueError: flush of closed file`。今回は明示close、通常FileIO、初期化中のPermissionErrorであり不一致 |
| [GH-140650](https://github.com/python/cpython/issues/140650) | raw I/Oのclosed属性で例外等が起きた場合のBufferedWriter.closeの異常。3.15.0 | 独自MockRawIOのclosed属性を使う再現。今回の通常FileIOのopen停止には一致しない |
| [GH-110036](https://github.com/python/cpython/issues/110036) | Windows Process.terminateで終了済みprocessのPermissionErrorを処理。3.11.6／3.12.1、3.13.0以降 | 中断後の後始末には関係し得るが、workerのopen内の停止を発生させる修正ではない |
| [GH-113009](https://github.com/python/cpython/issues/113009) | Windows terminate後のjoinが実終了前に戻る競合を修正。3.11.8／3.12.2、3.13.0以降 | **版選定上の注意**。3.11.6で導入された変更に関係する回帰で、使用中の3.11.4のterminateはreturncodeを先に固定していない。同修正が現在のopen停止を直すとは判断しない |
| [GH-107219](https://github.com/python/cpython/issues/107219)／[GH-114440](https://github.com/python/cpython/issues/114440) | worker異常終了後のQueue write側を閉じて待機を解消。前者3.11.6／3.12.1、後者3.12.2 | ProcessPoolExecutor／Queueの障害。今回の標準ライブラリ対照は直接Process＋Barrierで対象Queueを使わず、native open停止とは不一致 |
| [GH-109593](https://github.com/python/cpython/issues/109593) | GC finalizerからresource trackerに再入するdeadlock。3.11.6／3.12.1、3.13.0以降 | 今回の停止stackはresource trackerではなくCreateFile経路。Windows spawnで同じ再入を確認しておらず、直接一致しない |
| [GH-108520](https://github.com/python/cpython/issues/108520)／[GH-77377](https://github.com/python/cpython/issues/77377) | nested processへのSemLock再受渡し、fork／spawn context混在の処理。3.11.6／3.11.5等 | probeは単一spawn contextからworkerを作り、workerからSemLockを再受渡ししていない。fork混在もなく、当該条件に不一致 |
| [GH-77046](https://github.com/python/cpython/issues/77046) | WindowsでhandleをFDへ変換する際の継承属性を修正。3.13.0 | pipe／console等の`_open_osfhandle()`経路。対象ファイルは3.11.4でも`_wopen()`と`O_NOINHERIT`を使い、同じ問題として扱わない |
| [GH-141473](https://github.com/python/cpython/issues/141473) | input付きcommunicateのtimeout後、再呼出しで残りを送らず停止する問題。3.13.10／3.14.1／3.15.0 | 主な問題経路はPOSIX。今回のworkerのファイルopen内の停止とは別で、同じinput送信条件もない |

## 症状ごとの評価

### 生存workerのopen停止

[再導入後の調査](workflow-history-post-reinstall-2026-10-11.md)では、本体moduleをimportしない
標準ライブラリだけの対照でも停止した。これは本体依存を狭めるが、Python自身のバグと確定する証拠ではない。
Python、CRT、COMODO、Windows／filesystemの組合せはその対照にも残る。

採取したnative stackは`_wopen → CreateFileW → guard64.dll → NtCreateFile`系統だった。
現在のframe buffer lockやBarrier内で止まっている採取結果とは区別する。
3.11.4と3.11.17のFileIOソースを照合すると、通常のWindows openはどちらも
`O_NOINHERIT`を設定し、GILを解放して`_wopen()`を呼ぶ。
該当の呼出し・モード変換にopen停止を直接解消する変更は確認できなかった。
[3.11.4のFileIO](https://github.com/python/cpython/blob/v3.11.4/Modules/_io/fileio.c#L269-L364)、
[3.11.17のFileIO](https://github.com/python/cpython/blob/v3.11.17/Modules/_io/fileio.c#L269-L371)。

定期dumpを無効にしてもopen停止は再現している。したがって、faulthandlerのクラッシュ修正だけで
この停止が解消すると予測する根拠はない。CPythonと保護製品の相互作用や、履歴未記載の欠陥は残る。

### 初期化flushのPermissionError

本体は排他取得前に空判定→1バイトwrite→flushを行う。
別workerのbyte lockと書込みが重なり得るため、既存調査で示した本体の競合説明は維持する。
上表のread1／TextIOWrapper／closed判定の修正は、その競合を排他内へ移すものではない。
別Pythonでの試験は必要だが、更新だけでこの本体の競合がなくなるとは判断しない。

### 定期stack出力中のアクセス違反と、その後のBarrier停止

GH-140815は、今回のprobeと同じ定期dump APIを使うクラッシュで、GH-116008より直接的な調査候補となる。
3.13.10／3.14.1へのframe検査の適用は公式変更履歴・backport PRで確認した。
GH-116008のthread state検査も、3.13.10のtagソースで確認した。
[3.13.10のtraceback実装](https://github.com/python/cpython/blob/v3.13.10/Python/traceback.c#L844-L955)。
3.11.17には`_PyFrame_SafeGetCode`がなく、3.11のpatch更新だけで同じ対策を得られるとは判断しない。

公式開発者も、これらの検査後に完全な安全性を保証しておらず、出力が不完全になる場合や
残る再現例を記載している。修正版を使えば今回のクラッシュが必ずなくなる、とは扱わない。
[GH-140815の修正後評価](https://github.com/python/cpython/issues/140815)、
[GH-116008の修正後評価](https://github.com/python/cpython/issues/116008)。

クラッシュしたworkerが共有同期の状態更新を終えられず、残るworkerがBarrier内部で待つことは別の経路である。
使用中の`multiprocessing.synchronize.Condition.notify():290`は通知先の応答を期限なしで待っている。
今回のBarrier timeout値だけで、この内部待機まで必ず戻るとはいえない。
公式にも、共有lock／semaphoreを使用中のprocessが終了すると、ほかのprocessがdeadlockし得ると記載されている。
[multiprocessing公式仕様](https://docs.python.org/3.11/library/multiprocessing.html#multiprocessing.Process.terminate)
この二次停止を、CreateFile内のopen停止や、特定の未確認CPythonバグと一括しない。

## 次の比較試験で固定・記録する条件

1. Windows・COMODO版・設定を固定し、既存の標準ライブラリだけの4条件を
   3.11.4と修正を含む通常GIL版3.13.16／3.14.8で同じround数・繰返し数・barrier期限・順序で比較する。
   各Pythonの実パス・bit数・compiler、読み込まれたCRT DLLの版、guard64.dllのロードと適用ルールも記録する。
2. open停止の比較は定期dumpなしで行い、別実験としてburst条件の定期dumpあり／なしを比較する。
   アクセス違反、未完了open、flush失敗、worker死亡後の同期待機を別々に数える。
3. 新版でクラッシュした場合はfaulting module・例外アドレス・native dumpを採り、
   CPythonのframe／thread state参照位置か、COMODO／CRT／kernel経路かを確かめる。
4. open停止が新版でも残れば、同一Pythonで本体を使わない`os.open`対照、さらにC／Win32対照へ進む。
   後者にも同じ停止があればPythonのframe処理を必要条件から外せるが、共有フラグ等を揃える必要がある。

3.11.9は同一minorでの比較点にはなるが、今回見つけた新しいfaulthandler対策を含む版ではない。
3.11.10以降はsource-onlyで、最後の公式Windows installerは3.11.9である。
[3.11.17公式releaseの提供方針](https://www.python.org/downloads/release/python-31117/)
3.15.0は標準ライブラリ対照の追加比較には使えるが、現行本体・依存の対応確認とは分ける。

新版で問題が観測されなくても、CPython修正、compiler／CRT差、COMODOの対象別適用差、
タイミング差のどれが効いたかは追加照合が必要となる。有限試験での未観測を原因確定や完全解消としない。

## 今回の変更と確認

追加したのはこの調査記録と機械可読な照合記録で、本体・公開API・既存テスト・設定は変更していない。
既存の観測値は変更していない。今回はPythonの導入・更新、COMODO設定変更、v3全体の再試験は行っていない。
JSONの読込み、主要issueの正式版対応、参照ファイルの存在を確認した。
本体・関連テスト・設定の168ファイルのSHA-256が再導入後の調査記録と一致することも確認した。


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
