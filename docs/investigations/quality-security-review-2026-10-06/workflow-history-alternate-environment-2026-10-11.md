# 履歴ロックの別環境調査

調査日：2026-10-11（JST）。対象ブランチは `codex/fix-qs-013-construction-cleanup`、
HEAD は `0d63138`。元環境の調査記録を含む最新のブランチに切り替えて実行した。
元の調査対象 `b5c1a45` とこの HEAD の間に、製品ソース・既存テストの変更はない。

## 結論

元の履歴保存テストは、この環境では繰り返しても成功した。一方、同時にロックを
取得するタイミングを揃えると、現行コードの初期化 `stream.flush()` が
`PermissionError: [Errno 13] Permission denied` になる現象を再現した。
元環境の[追加調査](workflow-history-process-hang-2026-10-11.md)で観測した初期化失敗と同じ経路である。

したがって、初期化失敗には COMODO の存在が必須ではない。
製品コードがロック取得前に行う初期化書込みの競合を、別環境でも確認できた。
元環境の `Path.open("a+b")` が60秒以上戻らない現象は、今回の初期化失敗とは区別する。
今回、その open 停止は再現していない。Windows のバージョンも異なるため、
open 停止を COMODO 単独の原因と断定する比較にはならない。

## 環境と比較条件

| 項目 | 元環境の記録 | 今回 |
| --- | --- | --- |
| Windows | Windows 10、build 19045 | build 26200.9457、DisplayVersion 25H2、EditionID Core |
| Python | 3.11.4、64 bit、MSC v.1934 AMD64 | 同一の詳細バージョン |
| pytest / pytest-cov | 9.1.1 / 7.1.0 | 9.1.1 / 7.1.0 |
| Python 実行ファイル | リポジトリ内の quality-venv | `C:\Program Files\Python311\python.exe` |
| セキュリティー製品 | COMODO 有効・一時停止・再有効化の比較あり | 使用していないとのユーザー申告。診断 Python に `guard64.dll` のロードなし |
| パッケージ | ユーザーが取得した freeze 一覧 | `C:\Users\user\Downloads\requirements.txt` の106件がすべて一致。追加31件あり |
| 保存先 | 元記録では別のユーザーのローカルリポジトリ | このリポジトリ内の専用 `.runtime/history-alternate-environment/` |

requirements の欠落・指定バージョンとの差は0件だった。追加パッケージと仮想環境の有無が
異なるため、完全に同一の Python 環境とは扱わない。pytest の自動プラグイン無効でも比較した。
通常の自動プラグインは anyio、pytest-asyncio、pytest-cov、pytest-mock の4件だった。

履歴保存・対象テストは元記録の SHA-256 と一致する。ロック実装のファイルは
チェックアウト時の改行が CRLF になっているが、LF に揃えた SHA-256 が元記録と一致する。
Git の blob も元調査コミットと同じである。

## 元の履歴保存テスト

対象は `test_workflow_history.py::test_independent_processes_merge_under_shared_history_lock`。
3つの spawn worker、3件の履歴 merge の assertion、元の `join(60)` を維持した。
各試行は新しい pytest プロセスと一意の一時ディレクトリで、逐次実行した。
stack 取得用の sitecustomize と、比較条件を準備する親側 pytest プラグインだけを追加した。

| 条件 | 結果 |
| --- | --- |
| 現行コード・未作成 lock・自動プラグイン無効 | 20 / 20 成功 |
| 現行コード・未作成 lock・通常の自動プラグイン有効 | 6 / 6 成功 |
| 現行コード・空 lock を事前作成・自動プラグイン無効 | 6 / 6 成功 |
| 現行コード・1バイトを事前作成・自動プラグイン無効 | 6 / 6 成功 |
| 初期化を省いた隔離コピー・未作成 lock・自動プラグイン無効 | 20 / 20 成功 |

この58回のほか、既存の公開 stack probe による最初の単独実行も成功した（pytest 2.32秒）。
これらでは open 停止や PermissionError を観測しなかった。
自然な起動タイミングの少数試行が成功することは、初期化競合がない根拠にはならない。

## 同時取得を揃えたロック試験

3つの spawn worker を barrier で同時に開始し、1条件につき200ラウンド、計600回取得した。
各 worker は製品の `InterProcessFileLock` をそのまま呼び、保護区間で共有カウンターを
読んで1ミリ秒待機してから加算する。待機は競合を観測しやすくする試験上の条件である。
取得成功数と共有カウンターが一致することも確認した。

親は全 worker がロックを解放した後に、試験専用ファイルを次の初期状態へ戻す。
利用者の profile、履歴、既存 lock は使用・変更しない。
初期化を省く比較では、同期・非同期の `_ensure_lock_byte(stream)` 呼出し2か所だけを
削除した隔離ソースを読む。製品本体や既存テストには適用していない。

| サンドボックス内の条件 | 取得成功 | 取得エラー | 共有カウンター | 終了時の lock サイズ |
| --- | --- | --- | --- | --- |
| 現行コード・未作成 | 583 / 600 | 17 | 583 | 1バイト |
| 現行コード・空ファイル | 597 / 600 | 3 | 597 | 1バイト |
| 現行コード・1バイトを事前作成 | 600 / 600 | 0 | 600 | 1バイト |
| 初期化を省いた隔離コピー・未作成 | 600 / 600 | 0 | 600 | 0バイト |

代表例外は次の経路である。

```text
InterProcessFileLock.acquire():97
  → _ensure_lock_byte():165 / stream.flush()
  → PermissionError: [Errno 13] Permission denied
  → InterProcessLockError: cannot acquire inter-process file lock
```

この probe は例外を記録して次のラウンドへ進むため、worker の exitcode は0となる。
「取得エラーが0だった」という意味ではない。エラー数を別に集計している。
traceback は各 worker・各条件で最大3件を保存した。未作成条件の17件には9件、
空ファイル条件の3件には全3件の traceback があり、保存したものはすべて初期化 flush の PermissionError だった。
8秒ごとの faulthandler 出力には、通常の lock 再試行や barrier 待機も含まれる。
stack にロック実装が含まれるだけでは open 停止と判定しない。
全ラウンドが終了し、20秒の barrier timeout と5秒の取得 timeout も発生しなかった。
上記の件数は競合を強めた専用試験の観測値であり、実利用での発生率ではない。

### サンドボックス外の確認

サンドボックス外でも、新しい専用ディレクトリで同じ200ラウンドを逐次実行した。
公開した probe を実行し、製品ソースや元のテストには変更を加えていない。

| サンドボックス外の条件 | 取得成功 | 初期化エラー | 共有カウンター | 終了時の lock サイズ |
| --- | --- | --- | --- | --- |
| 現行コード・未作成 | 595 / 600 | 5 | 595 | 1バイト |
| 初期化を省いた隔離コピー・未作成 | 600 / 600 | 0 | 600 | 0バイト |

5件とも `_ensure_lock_byte()` の `stream.flush()` に起因する PermissionError だった。
全200ラウンドが終了し、worker の exitcode はすべて0だった。
今回の初期化失敗はサンドボックス内だけの現象ではない。
内外のエラー件数の差は、ランダム化した比較や発生率の推定には使わない。

## 関連テストと実行上の制約

ロック primitive、出力ディレクトリ、Cookie、更新状態、workflow、履歴、plan、ログの8ファイルから、
複数プロセス・ロック timeout 関連の27件を選択した。
サンドボックス外では **27 passed、116 deselected、pytest 15.94秒**だった。
pytest 自動プラグインは無効にし、本体と既存テストは同じものを実行した。

サンドボックス内の一括実行は進捗が得られず中断した。
ファイルへ直接ログを書き、子プロセスの stack を取得する形で再実行すると、
出力ディレクトリの overwrite テストが失敗し、子プロセスが
`asyncio.run()` のイベントループ初期化にある `socketpair()` の `accept()` で待っていた。
worker 内のロック処理には到達していない。
この実行も中断し、同じ27件をサンドボックス外で実行すると正常終了した。
サンドボックスのローカル通信制限が影響した可能性を示す実行条件上の問題として記録する。
元環境のファイル open 停止や、別途確認した初期化 flush 競合とは混同しない。

再現 probe は Ruff check と format check が成功した。
v3 全体のテスト、CI matrix、coverage の再検証は行っていない。

## 修正候補と確認できていない範囲

現行コードでは open、空判定、1バイトの write / flush の後に期限を作り、最後に byte lock を取得する。
このため、空判定から初期化書込みまでが排他で保護されていない。
元環境と今回の結果は、この書込みを省く修正候補を支持する。

空ファイル上の排他は今回の隔離コピーでも動作した。
[Python の msvcrt.locking 仕様](https://docs.python.org/3.11/library/msvcrt.html#msvcrt.locking)と
[Microsoft の byte-range lock 仕様](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex)も、
EOF を越える範囲のロックを認めている。実データの1バイトはこの排他の必須条件ではない。

正式な修正、同期・非同期の回帰固定、対応 OS・Python 全体の確認は今回の調査には含めていない。
今回 open 停止が発生しなかったため、その OS 内部の経路や COMODO の個別機能は特定できない。
製品の timeout は open と初期化を終えた後の再試行にしか適用されない点も残る。

再現用の [history_lock_contention.py](probes/history_lock_contention.py) は、新しい出力先を指定して使う。

```powershell
python docs/investigations/quality-security-review-2026-10-06/probes/history_lock_contention.py .runtime/history-lock-contention-new --rounds 200
```

この probe は取得失敗を観測値として保存する。全ラウンドの完了、worker の終了、
取得成功数とカウンターの一致が満たされれば、観測した初期化エラーがあっても exitcode は0となる。

環境情報、条件別の集計、代表 traceback、使用ソース、ログの SHA-256 は
[別環境の証拠 JSON](raw/workflow-history-alternate-environment-evidence.json)に保存した。
全ログと隔離コピーは ignore 対象の `.runtime/history-alternate-environment/` にある。

## 実行ごとの揺らぎを確認する再試験

同条件を5組、実行順を交互に変えて[再試験](workflow-history-repeatability-2026-10-11.md)した。
サンドボックス外の現行コードは、各600回の取得で7・9・7・11・12回のエラーを観測した。
計46件すべてが同じ初期化 flush の PermissionError だった。
初期化を省いたコピーは5組・計3,000回でエラー0件、元の履歴テストも20回すべて成功した。
件数と失敗ラウンドは変動するが、失敗を起こす初期化経路は各実行に共通している。
実行タイミングで発現する本体の競合という説明を支持する追加結果として保存した。
