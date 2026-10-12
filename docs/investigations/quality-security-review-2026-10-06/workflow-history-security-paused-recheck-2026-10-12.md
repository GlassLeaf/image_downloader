# COMODO機能一時停止後の再比較（2026-10-12）

## 結論

**今回の一時停止条件ではopen停止を観測しなかった。一方、初期化flushのPermissionErrorは専用試験と既存の履歴テストで再現した。**

ユーザーから、COMODO Internet Securityの停止可能な機能をすべて一時停止したとの回答を得た。新版12.4.0.8170有効時の同じprobe・設定を再実行し、待機時間比較112試行は全完了（33,600回のopen/close成功）、2,000ラウンド比較24試行も全完了（144,000回成功）だった。有効時はそれぞれ78/112・18/24試行でopenが滞留した。

再起動・COMODOの版変更・本体修正を伴わない今回の比較でも差が出た。新規Pythonにguard64.dllが残り、対象4 driverもRunningのままなので、DLLやdriverの存在だけでは停止を説明できない。**COMODOの機能切替に伴う保護環境の変化がopen停止に関与するという根拠は強まった。** ただしDefenderの登録値も同時に変化しており、COMODOの特定機能・DLL・driverの欠陥までは確定していない。

既存の複数プロセステストは6/10成功・4/10失敗で、4件とも初期化flushのPermissionErrorだった。今回を「問題がすべて解消した」「全テスト成功」とは判定しない。

## 比較条件と環境

- HEAD：`c4ef652bed1d3fa45813b1e2e790b327b9e82b68`、branch：`codex/fix-qs-013-construction-cleanup`。本体・既存v3テスト・pyproject.tomlの168ファイルは有効時と一致し、試験前後も不変。旧調査資料115ファイルは追記前に完全一致を確認した。
- Windows 10 22H2、19045.7727、CPython 3.11.4（AMD64）。記録対象の依存9パッケージ・pytest entry pointは有効時と一致した。以前と同じquality-venv、sandbox外で逐次実行した。
- 前後・有効時ともbootは`2026-10-11T18:42:28.5000000+09:00`。guard64.dllは12.4.0.8170、SHA-256は`537ba3170e09efb65c5dd4ac708f805e6baa608adddc45eaf8ecec72513b7e8a`で不変。
- COMODOのproductState：有効時397312 → 今回393216。Defender：有効時393488 → 今回397568。今回の試験前・主比較後・全試験後で値は一致した。値はrawとして扱い、個別機能の実効状態にdecodeしない。
- `cmderd`・`cmdGuard`・`cmdhlp`・`inspect`は前後ともRunning。主比較336 worker・サイズ比較72 workerすべてでguard64.dllをロードし、本体moduleはロードしていなかった。個別保護機能の内部状態を独立に測定したものではない。
- 調査側は保護設定・除外・service・driver・依存版・本体を変更していない。出力は新規の使い捨てディレクトリに分け、以前の結果を保持した。今回の各機能停止はユーザー操作に基づく。

## 同じ112試行の比較

[前回の有効時](workflow-history-open-delay-errors-2026-10-11.md)と同じ[history_open_delays.py](probes/history_open_delays.py)を使用した。8条件 × 7待機設定 × 2回、3 worker × 100ラウンド、barrier 10秒、条件別watchdog 40秒、定期dumpなし。1回目は通常順、2回目は逆順で、112件すべてのprofileが前回と一致した。

workerは対象へwrite・明示flush・byte lockを行わない。戻った例外・latency・実測sleepを同じ方法で記録した。表は「有効時の全操作成功試行数 → 今回の全操作成功試行数」。

| 条件 | 待機なし | 前10ms | 前100ms | 保持10ms | 保持100ms | close後10ms | close後100ms |
|---|---|---|---|---|---|---|---|
| 保持0・r+b | 0/2 → 2/2 | 0/2 → 2/2 | 0/2 → 2/2 | 1/2 → 2/2 | 1/2 → 2/2 | 0/2 → 2/2 | 0/2 → 2/2 |
| 保持0・w+b | 0/2 → 2/2 | 0/2 → 2/2 | 0/2 → 2/2 | 0/2 → 2/2 | 1/2 → 2/2 | 0/2 → 2/2 | 0/2 → 2/2 |
| 保持0・a+b | 0/2 → 2/2 | 0/2 → 2/2 | 0/2 → 2/2 | 1/2 → 2/2 | 1/2 → 2/2 | 0/2 → 2/2 | 0/2 → 2/2 |
| 保持1・r+b | 2/2 → 2/2 | 2/2 → 2/2 | 2/2 → 2/2 | 2/2 → 2/2 | 2/2 → 2/2 | 2/2 → 2/2 | 2/2 → 2/2 |
| 初期1・w+b（→0） | 0/2 → 2/2 | 0/2 → 2/2 | 0/2 → 2/2 | 0/2 → 2/2 | 1/2 → 2/2 | 0/2 → 2/2 | 0/2 → 2/2 |
| 保持1・a+b | 2/2 → 2/2 | 2/2 → 2/2 | 2/2 → 2/2 | 2/2 → 2/2 | 2/2 → 2/2 | 2/2 → 2/2 | 2/2 → 2/2 |
| 毎回削除・a+b | 0/2 → 2/2 | 0/2 → 2/2 | 0/2 → 2/2 | 0/2 → 2/2 | 0/2 → 2/2 | 0/2 → 2/2 | 0/2 → 2/2 |
| 毎回wb書直し・r+b | 0/2 → 2/2 | 0/2 → 2/2 | 0/2 → 2/2 | 0/2 → 2/2 | 0/2 → 2/2 | 0/2 → 2/2 | 0/2 → 2/2 |

今回は112/112完了・例外0・open滞留0・worker結果欠落0・外部watchdog打切り0。保存された33,600回の戻ったopenの最大は34.307msだった。バッチの実行時間は有効時1535.906秒 → 今回673.328秒。ただし有効時は滞留打切りを含むため、通常openの速度改善率には換算しない。sleepは指定値だけでなく実測分布もJSON・CSVに保存した。

## 2,000ラウンドの追試

主比較の成功を受け、[前回の0/1バイト比較](workflow-history-open-size-modes-2026-10-11.md)と同じ[history_open_size_modes.py](probes/history_open_size_modes.py)を再実行した。8条件 × 3回、3 worker × 2,000ラウンド、barrier 60秒、条件別watchdog 120秒、定期dumpなし。24件すべてのprofile・probe SHA-256は前回と一致した。主比較・本体競合試験・追試は並行実行していない。

| 条件（2,000ラウンド） | 有効時の完了 | 今回の完了 | 有効時のopen滞留 | 今回のopen滞留 |
|---|---:|---:|---:|---:|
| 保持0・r+b | 0/3 | 3/3 | 3/3 | 0/3 |
| 保持0・w+b | 0/3 | 3/3 | 3/3 | 0/3 |
| 保持0・a+b | 0/3 | 3/3 | 3/3 | 0/3 |
| 保持1・r+b | 3/3 | 3/3 | 0/3 | 0/3 |
| 初期1・w+b（→0） | 0/3 | 3/3 | 3/3 | 0/3 |
| 保持1・a+b | 3/3 | 3/3 | 0/3 | 0/3 |
| 毎回削除・a+b | 0/3 | 3/3 | 3/3 | 0/3 |
| 毎回wb書直し・r+b | 0/3 | 3/3 | 3/3 | 0/3 |

24/24完了・144,000回成功・例外0・open滞留0・欠落worker 0。保存された戻ったopenの最大は51.003ms。空ファイル・truncate・毎回再作成／書直しも全完了し、今回の条件では1バイト保持だけが成功条件ではなかった。有限の連続試行であり、将来の無停止を保証する数値ではない。

## 初期化flushと既存テスト

[公開競合probe](probes/history_lock_contention.py)を変更せず、3 worker・200ラウンド・保持1ms・lock期限5秒・barrier 20秒・定期dump 8秒で再実行した。各条件は別parentと120秒の外部期限を持つ。初期化なしは隔離コピーの呼出し2か所だけを除き、本体は変更していない。

| 本体の条件 | 完了ラウンド | 取得成功／予定600 | 初期化flush例外 | 秒数 |
|---|---:|---:|---:|---:|
| missing | 200/200 | 566/600 | 34 | 38.859 |
| empty | 200/200 | 566/600 | 34 | 38.953 |
| seeded | 200/200 | 600/600 | 0 | 43.219 |
| no-initialization | 200/200 | 600/600 | 0 | 74.703 |

全4条件は200ラウンドを終了したが、missing・emptyには各34件の初期化flush例外がある。1バイト事前作成・初期化なしは各600回成功した。有効時の同じ4条件は各0・0・6・0ラウンドで中断し、3条件でopen停止、missingで起動中断が記録されていた。未完了側の例外数・欠落workerをゼロとして比較しない。

定期dumpを除いた[supervisor比較](probes/history_lock_supervisor.py)も前回と同じshort＋4 controls・3回・control 2,000ラウンド・条件別120秒で実行した。18/18のプロトコルが完了し、open滞留0だった。現行初期化の3試行には[4, 11, 10]件、計25件の`initialization_flush_PermissionError`があった。初期化なし3試行は各90回成功・例外0。open-only対照12試行は各6,000回、計72,000回成功・例外0だった。プロトコル完了を全取得成功と読み替えない。

元の`test_independent_processes_merge_under_shared_history_lock`は、[既存の診断wrapper](probes/history_process_stacks.py)で独立10回再実行した。join 60秒、通常のpytest plugin自動load、定期dump 8秒を維持した。結果は6成功・4失敗（history-01・02・05・10）。4失敗とも`interprocess_lock.py:165`の`stream.flush()`でPermissionError（errno 13）が発生し、workerの終了コード1によって失敗した。生存workerのopen滞留やjoin期限切れではない。有効時の独立10回成功は当時の観測として保持し、今回の失敗を隠さない。

## 原因の考察

1. **open停止と初期化flushは別の現象である。** 有効時のopen-onlyは初期化・byte lockなしで止まり、今回の機能一時停止条件ではそれらが全完了した。一方、本体のflush失敗は今回も再現した。flush失敗はopen停止の必要条件ではなく、保護機能停止でflush問題まで解消したとは言えない。
2. **本体側には排他外の空判定→1バイトwrite→flushが残る。** `acquire()`はopen・初期化後にbyte lockを取るため、この初期化は他workerと重なり得る。今回も例外位置を確認し、初期化なしの対照では再現しなかった。WindowsではEOFを超えるbyte lockも許されるため、1バイト初期化はbyte lockの一般的な必須条件ではない。[Microsoft _locking](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/locking?view=msvc-170)　ただし今回、失敗したWriteFileの直後のWin32コード・実書込みoffsetは採っておらず、errno 13を共有違反32等に置き換えない。
3. **open停止の主要候補は、機能の有効化に伴う保護処理と競合openの相互作用である。** 同じboot・版・ソースで大きな差があり、空ファイルや再作成の有効時失敗も一時停止後には全完了した。残留DLL／Running driverだけでは再現しない。既存の停止stackのguard64→NtCreateFile経路と整合するが、特定componentの内部待機原因を確定するものではない。
4. **単一要因の因果証明には制限がある。** Defenderの登録状態も変化し、実行時刻・ファイル検査履歴・出力先も異なる。今回は機能別の切替、ランダム化した有効→停止→有効の新しい交差試験、kernel stack・IRP・待機objectの同時採取はしていない。少数の反復・強制同期の結果から実サイトの発生率を推定しない。

今後は、初期化flushへの本体修正と、open停止への環境／I/O対策を分けて評価する。open原因をさらに絞るには、COMODOの各機能を個別に切り替えた反復比較、Defenderの実効状態の記録、停止時のkernel側待機・I/O・handle所有者の同時観測が必要となる。今回の結果を理由に、固定sleepや1バイト事前作成を常時有効環境の確実な修正としない。

## 成果物と確認範囲

- [証拠JSON](raw/workflow-history-security-paused-recheck-evidence-2026-10-12.json)：全試行、profileの一致、前後環境、worker結果と欠落、例外trace、再現に使ったsource、旧資料hashを保存。
- [実行別CSV](workflow-history-security-paused-recheck-runs-2026-10-12.csv)：主比較112＋サイズ比較24、計136試行。latency・実測sleepの集計範囲は保存されたworker。worker別p95最大を全呼出しのpool p95とは扱わない。
- scratchログ：`.runtime/history-security-paused-20261012/`。既存probe・本体・テスト・設定は不変。旧記録は保存し、総合調査記録への追記は元bytesのprefix一致で検証する。
- 今回は原因切り分けの再実行であり、v3全体・CI・Ruff/mypyの全体再実行、本体修正、issue操作、commit/pushは行っていない。
