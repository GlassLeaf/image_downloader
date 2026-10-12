# 保護機能有効時の共通ロック追試と共有違反の再試行対照

## 判定

ユーザーがCOMODOの保護機能を再度有効にした後、同じPython・依存環境で追試した。
**初期化をロック下へ移す修正では新たなwrite／flush例外を観測しなかったが、
Windowsのopen停止への暫定対策は、保護有効時の完了条件を満たさなかった。**
最終版でも、初回作成の3反復中2回、既存空ファイルの3反復すべてでopenの滞留が再現した。
これを修正前だけの既知の失敗として扱わず、修正後に残った問題として記録する。

**限定的な再試行を除いた対照版では、追加の診断試験で共有違反が計3件返った。**
最終版には返った共有違反を観測していない。実際の共有禁止handleを用いた別の対照でも、
最終版は同期・非同期とも1回の再試行で取得でき、再試行なしは取得に失敗した。
返る共有違反への対策は有効だが、戻らないopenを再試行で解消できるという保証はない。

通常のロック回帰テスト94件と、元の履歴テストの独立20回は成功した。
同じ非空ファイルを保持するopen-only比較も成功した。この成功でcold条件の失敗を置き換えない。

個別64 caseは[CSV](workflow-history-lock-security-enabled-recheck-runs-2026-10-12.csv)、
例外trace・環境・固定ソース差分・検証記録は
[raw evidence](raw/workflow-history-lock-security-enabled-recheck-evidence-2026-10-12.json)へ保存した。
本体ソースは変更せず、比較用コピーと再現probe・調査記録だけを追加・更新した。

## 対象・環境・保護状態

- 対象HEADは`57f39d2bc86e901d0a4b79546164722d2c8c9e57`、branchは
  `codex/fix-qs-013-construction-cleanup`。本体の最終実装コミットは`e7e45e00cb2ec574b6d4bd71af9e80572c75a676`。
- Windows 10 Home、10.0.19045、64bit Python 3.11.4。pytest 9.1.1、pytest-cov 7.1.0、
  coverage 7.16.0、Ruff 0.16.5、mypy 2.3.1、HTTPX 0.28.1、Pillow 12.3.0、psutil 7.2.2。
- ユーザー申告は「セキュリティー機能を有効にした」。試験中のPython関連のブロック・隔離通知は
  「通知は出ていない」と回答された。保護設定・除外設定は試験側では変更していない。
- 2026-10-12 11:03:22 +09:00の読み取り専用照会で、登録状態のraw値はCOMODO Antivirus **397312**、
  Windows Defender **393488**だった。停止時はそれぞれ393216、397584だった。
- `guard64.dll`のファイル版は12.4.0.8170、SHA-256は
  `537BA3170E09EFB65C5DD4AC708F805E6BAA608ADDDC45EAF8ECEC72513B7E8A`。前回停止時と同じファイルである。
  今回はファイル版・ハッシュの照合であり、全workerのロード状態を個別に取得したものではない。
- 今回の最終起動時刻の照会値は2026-10-11 18:42:30.457748 +09:00。
  前回記録の18:42:28.5との約2秒差だけで、間に再起動したとは判断しない。

登録状態の数値から各保護機能の実状態を独自に解読しない。COMODOとDefenderの登録状態が両方変わっているため、
今回の比較からCOMODOの単独作用を確定できない。通知がないことも、無通知のhookや検査の不存在の証明にはならない。

試験開始時の作業ツリー状態・ツール版・監査資料132ファイルのハッシュを保存した。
この開始記録は、再試行なしコピーを作るprobeと例外メタデータの準備後に取得したものなので、
「今回のすべての変更より前」の状態ではない。以前の監査資料124ファイルは別の開始記録と照合し、
既存の未コミット文書・再現資料を含めて変更がないことを確認した。
本体`src`全体も、キャッシュ・egg-infoを除いて今回の固定最終版と一致した。

## 比較ソース

すべて別ディレクトリの`src`全体を使用した。作業ツリーをcheckoutして書き換えていない。
workerの最終記録が得られたものは読込み位置とSHA-256を照合した。
起動中・open中で強制終了したworkerの最終記録は欠落として数え、個別のsource pin完了を主張しない。

| ラベル | 内容 | lockファイルbytesのSHA-256 |
| --- | --- | --- |
| before | 前回調査で保存した修正前（開始HEAD `c4ef652bed1d3fa45813b1e2e790b327b9e82b68`） | `a42aa7fb0a2038d8fc68a07942d4b385ad0bea240afbce7a95e828fc02e0f21f` |
| initialization | 初期化修正のみ、`3d40a0f5af64a59dbe771c78ccf93f9434abb1fa`相当 | `fe9a975453652c9d1291ea57c8d5b1ed98573a0c4d8df925d5cb87063325347f` |
| no-retry | 最終版から同期・非同期の取得前待機／再試行だけを除いたコピー | `a414e97c18cd4667e4332c35e44abdc15b969a018a65ecdbab908a96d1f54183` |
| final | 現行最終版 | `f3b9253ed7dd7178265818ee7728ebd3a5f274e60b3c021ebe190018fac0b260` |

no-retryは[_open_for_acquisitionとその非同期版](../../../src/image_downloader/storage/interprocess_lock.py)を
`return self._open_stream()`に置き換えたもの。CRTの共有違反判定、callbackを作る処理、1バイト公開方式、
O_CREAT除去、親・ファイルID照合、ロック下の初期化、後始末は維持した。
この2 helper以外の差分がないことは生成probeの差分とファイル照合で確認した。
[生成probe](probes/history_lock_retry_control.py)と完全な差分を保存し、本体の再試行コードは外していない。

## 基準比較の方法と結果（48 case）

前回停止時と同じ[比較probe](probes/history_lock_remediation.py)を使用した。
例外が返った時だけ共有違反のprivate flagを記録する追加を行い、通常openの観測方法は維持した。
4 sourceの順序を回転・反転し、各条件を3回実行した。比較試験同士、通常回帰テストとの並行実行は避けた。

| 条件 | 1反復の計画 | 操作・ファイル準備 |
| --- | --- | --- |
| cold-missing | 3 worker × 200 round = 600操作 | roundごとに未作成の別名。full acquireと共有counterの排他を確認 |
| cold-empty | 同上 | parentが0バイトファイルを作りcloseした後に開始。full acquire |
| stable-one | 3 worker × 2,000 round = 6,000操作 | parentが1バイトを作りclose。同じ非空ファイルでopen／closeのみ |
| stable-many | 同上 | parentが14バイトを作りclose。同じ非空ファイルでopen／closeのみ |

parentは前roundの全workerのclose後に次のファイルを準備する。使用中のunlink／truncateは行わない。
full acquireでは保護区間に1ms待機がある。stableは`_open_stream()`を直接呼ぶため、
byte lock・初期化・取得前再試行の試験ではなく、通常のopen／closeの対照である。

各barrierの期限は20秒。その期限後のjoinで5秒待っても生存する所有workerを止め、
case全体には別に120秒の外部watchdogを設けた。定期dumpは行わなかった。
以下の「滞留」はこの観測期限を超えたことを意味し、無期限停止を証明したものではない。
今回は120秒watchdogより先にbarrierの不成立を検出したため、全caseで外部watchdog発動は0だった。

| source | 未作成：成功／計画1,800 | 完走／3 | open滞留case | 既存空：成功／計画1,800 | 完走／3 | open滞留case | 返った初期化例外 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| before | 197 / 1,800 | 0 | 2（別に起動中1） | 56 / 1,800 | 0 | 3 | flush 3件 |
| initialization | 66 / 1,800 | 0 | 3 | 141 / 1,800 | 0 | 3 | 0件 |
| no-retry | 1,800 / 1,800 | 3 | 0 | 72 / 1,800 | 0 | 3 | 0件 |
| final | 874 / 1,800 | 1 | 2 | 102 / 1,800 | 0 | 3 | 0件 |

分母は計画した操作数であり、停止後の未実行操作を例外・失敗に加算していない。
成功数には停止roundで先に完了したworkerの操作も含む。返った操作例外、未完了のopen、
起動中停止、barrierの監督エラーは別々に記録した。
未完走20 caseは、native_open中19 case、startup中1 case。最終版はcold条件6 case中5 caseが未完走だった。
startupの1件はロックのopenに到達した証拠がなく、open滞留件数には含めない。
今回のprobe終了値1には最終版自身の未完走が含まれる。

beforeの3例外は未作成2件、既存空1件の`initialization_flush`で、causeはPermissionError、errno=13、winerror=None。
修正した3 sourceにwrite／flush例外はなかったが、早い滞留で試行が減っているため、
この有効時の追試だけで計画した全1,800取得の初期化確認を完了したとはしない。

1バイト保持・14バイト保持は、**各source・各条件で18,000／18,000操作、3／3 caseが成功**した。
24 stable caseすべてで内容・サイズ・ファイルID・mtimeが前後で同一だった。
同じ非空ファイルを保持する条件は今回も安定した一方、未作成から新しく公開された1バイトファイルを
繰り返し使う条件では最終版も滞留した。「初回openまでに1バイトあれば十分」という一般保証にはできない。

## 追加のopen診断と、再試行なしでの共有違反

基準比較では返った共有違反は0件だった。そこで追加のdisk writeを行わず、shared memoryへ
対象／一時ファイル、openの層、os.openのflagsを記録する別probeでcold条件を比較した。
追加のPython処理はタイミングを変えるため、基準比較とは別の集計とした。

最初の[層記録probe](probes/history_lock_open_phase_control.py)は最後に入った層を保存する。
内側の呼出しが戻っても層を復元しないため、保存された`os.open`だけでその呼出しが処理中とは判定できない。
この限界を明記したうえで、[呼出しが戻ったら層を復元するprobe](probes/history_lock_open_active_layer_control.py)でも追試した。

| 診断 | source | 未作成：成功／計画 | 返った共有違反 | 未作成のopen滞留case | 既存空：成功／計画 | 既存空のopen滞留case |
| --- | --- | --- | --- | --- | --- | --- |
| 最後に入った層・3反復 | no-retry | 1,233 / 1,800 | 2件（反復1、0始まりround 171） | 1 / 3 | 99 / 1,800 | 3 / 3 |
| 同上 | final | 1,733 / 1,800 | 0件 | 1 / 3 | 99 / 1,800 | 3 / 3 |
| 処理中の層・1反復 | no-retry | 599 / 600 | 1件（round 50） | 0 / 1 | 62 / 600 | 1 / 1 |
| 同上 | final | 600 / 600 | 0件 | 0 / 1 | 29 / 600 | 1 / 1 |

再試行なしで返った計3件は、いずれも初回公開後の対象ファイルを開く処理のInterProcessLockErrorで、
causeはPermissionError、errno=13、winerror=None、`verified_sharing_violation=true`だった。
このflagは、本体がos.open直前にCRT拡張エラーをclearし、失敗後に`_get_doserrno()==32`を確認した時だけ立つ。
rawに保存したのはこの判定結果であり、後から読んだGetLastErrorや独立した生の32値を保存したとはしない。
無理に競合handleを入れた試験とは異なり、これら3件は通常の複数worker比較中に返った例外である。

処理中の層を復元する診断の既存空条件では、両sourceとも**対象ファイルの`os.open`が戻る前**に滞留していた。
その呼出しのflagsは32906（0x808A）、O_CREAT=False、O_EXCL=Falseだった。
対象はparentが作成・closeしたファイルであり、存在しないファイルを作成禁止で開くという前提不一致の証拠はない。
他workerの初期化により、滞留時点では既に1バイトになっている可能性がある。parentが0バイトを準備したことと、
各workerのnative呼出し瞬間の長さが0であることは同一視しない。

未作成条件の基準比較におけるfinalの滞留は、対象／一時ファイルやnative内部の詳細を保存していない。
層を復元する診断ではその未作成の滞留は再現しなかったので、既存空の対象os.openという特定を
基準比較の未作成の失敗すべてに拡張しない。

no-retryが基準比較の未作成を完走しても、診断では共有違反や滞留を起こした。
またfinalにも診断で未作成の滞留が1件あった。今回の回数・計測処理の違いから、
「再試行を消すと停止が直る」「再試行が停止を起こす」という因果関係は確定できない。
戻らなかったopenにはerrno／winerror／CRTの最終値がなく、監督側のBrokenBarrierErrorはそのopenのOSエラーコードではない。

## 実際の共有禁止handleによる再試行の機能対照

[対照probe](probes/history_lock_sharing_release_control.py)で、事前作成1バイトファイルに
Win32の共有を許可しないhandleを意図的に置いた。最初のos.openが共有違反を返した直後に
probeが自分のhandleを閉じ、次の取得ができる状態にした。**COMODOの自然発生例ではなく、意図的な機能試験である。**

| source | 同期／非同期 | 最初の結果 | os.open回数 | 取得結果 |
| --- | --- | --- | --- | --- |
| no-retry | 両方 | errno=13、共有違反32のflag=True | 各1回 | 各InterProcessLockError |
| final | 両方 | 同上 | 各2回 | 0.1秒待機後に成功、releaseも完了 |

全4 caseが対照の固定期待値を満たした。すべてのnative openでO_CREATは除かれていた。
最終版が共有違反を返さなかった理由について、基準比較だけでは再試行の実行回数が分からないが、
この対照では最初の実OSエラーと次の成功を記録した。限定的な再試行の機能自体は確認できた。

## 前回の停止時試験との比較・原因の考察

前回の[停止時記録](workflow-history-lock-remediation-2026-10-12.md)では、同じ最終ソースと
初期化修正のみの各cold条件で1,800／1,800取得に成功し、open滞留・worker欠落は0だった。
今回は保護有効で両sourceともcold条件に滞留が出た。非空保持のstable条件はどちらの保護状態でも安定している。
保護状態と停止の関連を示す観察は今回も得られたが、製品内部の作用箇所は特定できていない。

修正前のflush例外は停止時320件、今回は3件だった。今回の修正前cold条件は全case早期停止しており、
試行の母数・曝露が違うため、この減少を保護有効による改善と解釈しない。
初期化修正後に新たなwrite例外へ失敗が移った証拠はなく、停止時の完走と通常回帰テストも初期化修正を支持する。
一方、有効時のcold全取得成功という確認は完了していない。

返る共有違反と、native openが戻らない現象は分けて扱う。
前者は確認済みのCRT 32に対する短い待機・再試行で回復できる例がある。
後者は最初のopenが戻らないため、その後の例外処理へ到達しない。今回の追加診断でも
初期化処理へ進む前の対象os.openで止まっており、アプリの初期化flush例外から回復できず止まった、という経路ではない。
他workerの初期化や保護製品の検査がOS内部の待機に影響する可能性までは、このデータで否定できない。

現時点では、確認済みの共有違反を再発させるため、限定的な再試行を本体から削除する根拠はない。
初期化修正と既存非空ファイルの保持も維持する。一方、初回公開方式をopen停止の解決として完了扱いにはしない。
根本原因をさらに追うには、自然に止まったworkerのnative thread stack、関連handle／driver待機、
対象ファイル・一時ファイルのイベントを外部から観測し、停止の当事者を特定する必要がある。
現在のprobe内観測だけではWindows CRT、CPython、保護製品hook、filter driverのどこが待機しているか分離できない。

## 回帰・検証範囲

- ロック回帰テスト94件成功、12.01秒。外部180秒期限は発動せず、単一テストが長期停止した時の45秒dumpも発動していない。
  この通常テストと、定期dumpなしの基準比較は別条件である。
- 元の`test_independent_processes_merge_under_shared_history_lock`は、固定最終ソースで独立20／20回成功。
  各呼出しの外部120秒期限も、全体300秒期限も発動せず、全体128.578秒だった。
  source pinを各試験プロセスで照合した。単独成功をcold競合の失敗否定には使わない。
- Ruff、mypy（95 source files）、`git diff --check`を確認した。終了値と出力はraw evidenceに記録した。
- 本体の変更はないため、今回v3全体・全OS CIの再実行は行っていない。前回のv3全体は
  1,774成功・17スキップ、88.29%だったが、保護有効時の全体成功とは記載しない。
  前回の3 OS × Python 3.11〜3.14のCI成功も、この端末の保護有効時の停止を否定しない。

期待条件は、初期化修正の根拠・返る共有違反への限定的な回復を確認できた一方、
**保護有効でopen滞留・欠落・予期しない取得失敗がないことは確認できず、open対策は未完了**と判定する。

## 再実行

同じHEADの`src`を新しい作業用ディレクトリへコピーし、キャッシュを除く。
再試行なしのコピーは、既存ディレクトリを上書きせず次のprobeで作る。

```powershell
python docs/investigations/quality-security-review-2026-10-06/probes/history_lock_retry_control.py FINAL_SRC NO_RETRY_SRC
python docs/investigations/quality-security-review-2026-10-06/probes/history_lock_remediation.py NEW_OUTPUT --source before=BEFORE_SRC --source initialization=INIT_SRC --source no-retry=NO_RETRY_SRC --source final=FINAL_SRC --protection user-enabled --repeats 3 --cold-rounds 200 --stable-rounds 2000
python docs/investigations/quality-security-review-2026-10-06/probes/history_lock_open_phase_control.py NEW_PHASE_OUTPUT --source no-retry=NO_RETRY_SRC --source final=FINAL_SRC --protection user-enabled --repeats 3 --conditions cold-missing cold-empty
python docs/investigations/quality-security-review-2026-10-06/probes/history_lock_open_active_layer_control.py NEW_ACTIVE_LAYER_OUTPUT --source no-retry=NO_RETRY_SRC --source final=FINAL_SRC --protection user-enabled --repeats 1 --conditions cold-missing cold-empty
python docs/investigations/quality-security-review-2026-10-06/probes/history_lock_sharing_release_control.py NEW_RELEASE_OUTPUT --source no-retry=NO_RETRY_SRC --source final=FINAL_SRC
python docs/investigations/quality-security-review-2026-10-06/probes/history_lock_history_recheck.py NEW_HISTORY_OUTPUT --source final=FINAL_SRC --protection user-enabled --repeats 20
```

再実行時も保護設定はprobeで変更しない。試験専用の新しい出力先を使用し、共有禁止handleの機能対照と
履歴20回には外部期限の監督を置く。今回の条件・source順・監督結果はraw evidenceを参照する。
