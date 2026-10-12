# workflow history：openモードとファイル存在条件の照合（2026-10-12）

## 結論

本体の履歴ロック、open-only比較probe、0/1バイト・待機時間比較の通常生成ケースに、意図しないモードと存在条件の不一致は確認されなかった。既存の147試行の開始前snapshotも、設定されたファイル準備と一致している。現在のPython 3.11.4で単独プロセスの12条件を確認し、open成功9条件・想定した例外3条件のすべてが期待どおりだった。

`a+b`と`w+b`は「ファイルが存在しないこと」を要求しない。両方とも未作成なら作成し、既存なら開く。既存であることを拒否するのは`x`系である。0バイトファイルも存在するファイルであり、`r+b`で開ける。従って、現在のopen停止をモードの取り違えで説明する根拠は得られていない。

ただし、従来の各workerは毎回open前に`exists()`を呼んでおらず、保存済みsnapshotは最初の開始前・最初のラウンド後・試行終了後の観測である。全openの直前に対象が存在したことや、外部プロセスによる削除・置換がなかったことまで実測で保証するものではない。

## モードの要件

以下は通常ファイルについての存在条件である。親ディレクトリの存在、アクセス権、Windowsの共有制限は別途必要になる。

| モード | ファイルがない | ファイルがある（0バイトを含む） | 既存内容 |
|---|---|---|---|
| `r+b` | 作成せず`FileNotFoundError` | 開く | openだけでは保持 |
| `w+b` | 作成する | 開く | open時に0バイトへtruncate |
| `a+b` | 作成する | 開く | openだけでは保持。書込みは末尾への追加 |
| `x+b` | 排他的に新規作成する | `FileExistsError` | 既存ファイルを上書きしない |

`b`はバイナリ、`+`は読み書き指定であり、存在条件を変える指定ではない。[Python公式open仕様](https://docs.python.org/3.11/library/functions.html#open)と照合した。`Path.write_bytes()`は開く・書く・閉じる処理であり、既存ファイルを上書きする。[Python公式Path.write_bytes仕様](https://docs.python.org/3.11/library/pathlib.html#pathlib.Path.write_bytes)

## コード上の確認

### 本体

- [WorkflowHistoryStore.lock](../../../src/image_downloader/storage/workflow_history.py)の108–112行は`ensure_directory()`で親を準備し、`filesystem.exists(relative)`を呼ぶ。戻り値は使用しないため、ファイルがない場合もある場合も`InterProcessFileLock`を返す。この確認は、存在する対象の通常ファイル・パス安全性の検査を伴う。未作成をエラーにする処理ではない。
- [InterProcessFileLock](../../../src/image_downloader/storage/interprocess_lock.py)の96・120行は同期／非同期とも`a+b`である。直接API自体には直前の存在確認や親ディレクトリ作成はない。ファイルの有無の両方を許容するモードなので、この点に不一致はない。直接利用時に親がない等の`OSError`は`InterProcessLockError`のcauseとして扱われる。
- open後の空ファイルへの1バイト書込み・flushは、ファイル存在条件とは別の初期化処理である。今回の確認は、その競合やopen停止を解決したという判定ではない。
- 本体には設定新規作成用の`x`もある。[setup.py](../../../src/image_downloader/commands/setup.py)の65–73行、[config.py](../../../src/image_downloader/commands/config.py)の209–217行は、事前に既存設定を拒否し、競合して先に作成された場合も`FileExistsError`を処理する。これは既存設定を上書きしないための意図された使用であり、履歴ロックのopenには使われない。
- 補助確認として、[filesystem.py](../../../src/image_downloader/storage/filesystem.py)の既存ファイル読込みは通常ファイル確認後の`rb`、appendは`O_CREAT | O_APPEND`、一時ファイル書込みは`mkstemp()`取得済みdescriptorへの`fdopen()`である。[source_loader.py](../../../src/image_downloader/plugins/source_loader.py)の`rb`も通常ファイル確認後に使用される。ここでも存在条件の取り違えは確認されなかった。この確認は全I/O競合の監査をやり直したものではない。

### 調査probe

| 条件 | 親の準備と存在状態 | workerのモード | 判定 |
|---|---|---|---|
| 未作成／毎回作成 | 全workerのclose後、親がunlinkしてから開始barrierを解放 | `a+b` | 適合。最初のworkerが作成した後、後続workerは既存ファイルを同じモードで開ける |
| 0バイトを保持 | 最初に`write_bytes(b"")`で作成・close。以降は親が変更しない | `r+b` / `w+b` / `a+b` | すべて適合。空であることは未作成を意味しない |
| 1バイトを保持 | 最初に`write_bytes(b"\0")`で作成・close。以降は親が変更しない | `r+b` / `w+b` / `a+b` | すべて適合。`w+b`で0バイトになるのは意図した比較条件 |
| 1バイトを書き直す | 各ラウンドで`write_bytes(b"\0")`を完了してから開始barrierを解放。unlinkしない | `r+b` | 適合。worker開始時には親が作成・close済み |

[history_lock_variability.py](probes/history_lock_variability.py)の212–218行が準備処理であり、244–246行で「準備→開始barrier→終了barrier」を繰り返す。workerはclose後に終了barrierへ進む。親が保持した準備用ハンドルのままworkerを開始する実装ではない。[history_open_size_modes.py](probes/history_open_size_modes.py)の189–201行が通常の組合せを生成し、[history_open_delays.py](probes/history_open_delays.py)も同じ準備を利用する。

従来の`readwrite-created`という条件名は、親が`wb`で事前作成／書き直しするケースを指す。workerが未作成ファイルを`r+b`で作成する意味ではない。

非表示の診断用`--child-profile`は、モード・lifecycleそれぞれの許容値だけを検査し、両者の組合せまでは拒否しない。そのため手入力で`r+b`＋`missing`を指定することは可能である。今回照合した通常の生成ケースと保存済み147試行には、その組合せはなかった。

[history_open_error_controls.py](probes/history_open_error_controls.py)の`missing-file`＋`r+b`は、エラーコードを採取するための意図した不正条件である。open停止の通常比較とは分離されている。同probeのネイティブflagsでも`r+b`には`O_CREAT`がなく、`w+b`は`O_CREAT | O_TRUNC`、`a+b`は`O_CREAT | O_APPEND`であり、`O_EXCL`は付かない。直接CreateFileWの対照試験でもそれぞれOPEN_EXISTING／CREATE_ALWAYS／OPEN_ALWAYSを選んでいる。

## 証拠の照合・今回の実測

### 保存済みの開始前snapshot

| 記録群 | 試行数 | 0バイト事前作成・同一ファイル | 1バイト事前作成・同一ファイル | 毎回未作成・`a+b` | 毎回1バイト書直し・`r+b` |
|---|---:|---:|---:|---:|---:|
| 0/1バイト・モード比較 | 24 | 9 | 9 | 3 | 3 |
| 待機時間比較・有効なmatrix | 112 | 42 | 42 | 14 | 14 |
| held延長比較 | 3 | 3 | 0 | 0 | 0 |
| native opener比較 | 8 | 3 | 3 | 1 | 1 |
| 合計 | 147 | 57 | 54 | 18 | 18 |

`r+b`の55試行はすべて開始前に存在していた。`w+b`37試行・`a+b`55試行も、そのモードが許容する初期状態だった。各snapshotの存在・サイズはlifecycleの要求と一致した。準備関数・worker等の保存済み15ソース記録は現行ファイルのSHA-256とすべて一致し、入力とした2つの旧JSONも読み取り前後で不変だった。以前の無効pilot（`Path.open()`に不対応のopener引数を渡したケース）は、この147試行に含めていない。

worker終了記録に収録されたopen/close例外の合計は0である。ただしopenが戻らず終了記録を残せなかったworkerもあるため、これは全操作に例外がなかったという証明ではない。

### 小規模実測

既存の大量並列比較は繰り返さず、今回の実行環境で「未作成・既存0バイト・既存1バイト」×「`r+b`・`w+b`・`a+b`・`x+b`」を独立した使い捨てファイルで確認した。ケースごとに子プロセス10秒の期限を設け、open直前の存在・通常ファイル・サイズとclose後の状態を記録した。全12条件で期限超過はなかった。

- 未作成＋`r+b`：`FileNotFoundError`、errno=2。作成されなかった。
- 既存0/1バイト＋`x+b`：ともに`FileExistsError`、errno=17。元のファイル状態を保持した。
- 残り9条件：成功。`w+b`は既存1バイトを0バイトにし、`r+b`・`a+b`のopen/closeは既存サイズを保持した。
- 今回採取したPython例外の`winerror`は3件とも`None`。errnoとGetLastErrorを同一視せず、以前のネイティブコード採取試験と区別する。

実行したのは現在のコマンド環境内の逐次確認であり、セキュリティー製品の状態差や、並列時の停止発生率の比較結果には使用しない。プロセス終了・closeまで含めたモード契約の確認である。

再実行例（出力先は未作成のディレクトリを指定する）：

```powershell
python docs/investigations/quality-security-review-2026-10-06/probes/history_open_mode_preconditions.py --output .runtime/history-open-preconditions-new --evidence .runtime/history-open-preconditions-new.json
```

## 記録・制限

- 確認コミット：`c4ef652bed1d3fa45813b1e2e790b327b9e82b68`、branch：`codex/fix-qs-013-construction-cleanup`。
- 作業ツリー：以前の調査資料・probeの変更／未追跡ファイルを保持。本体・既存テスト・設定・CIファイルの差分なし。今回追加したものは本記録、[再現／照合probe](probes/history_open_mode_preconditions.py)、[JSON証拠](raw/workflow-history-open-mode-preconditions-evidence-2026-10-12.json)。旧資料は変更しない。
- Python：3.11.4、Windows、`C:\Program Files\Python311\python.exe`。追加probeのRuffは成功。検証対象は存在条件の確認であり、v3全体の再実行やopen停止の修正は行っていない。
- `exists()`は確認時点の観測であり、次のopenまでの削除・置換を原子的に防ぐ操作ではない。`exists()`の有無でモードを切り替えるだけでは、競合対策にならない。`a+b`は、先に他workerが作成しても同じモードで開ける。
- より強い実測が必要なら、問題を起こす通常probeとは分けて、各open直前・親の準備直後の存在／file ID／サイズを採取する。ただしstat追加に伴うタイミングの変化を記録し、非計測条件と比較する必要がある。必要に応じOS側のCreateFileイベントや停止時の対象パス状態とも照合する。

今回の結果からは、モード変更をopen停止の修正として採用する根拠はない。従来からの未返却openと、空ファイル初期化flushの競合は、それぞれの観測根拠に基づいて引き続き区別する。
