# COMODOアンインストール後の履歴ロック再調査

後続の[回数・時間・競合条件を変えた調査](workflow-history-open-variability-2026-10-11.md)では、
保存できた97,332回のopenの最大は51.305msで、従来の長時間停止は確認されなかった。
初期化flushの失敗と、定期stack出力を伴う別種のアクセス違反・probeのbarrier停止を区別して記録した。
以下はアンインストール直後の観測記録として保存する。

調査日：2026-10-11（JST）。HEAD：`c4ef652bed1d3fa45813b1e2e790b327b9e82b68`。
対象ブランチ：`codex/fix-qs-013-construction-cleanup`。
開始時には前回までの調査資料6ファイルの未コミット変更があった。本体・既存テスト・設定に変更はない。
それらを保存し、ユーザーがCOMODOをアンインストールした条件で再調査した。

## 原因の評価

**今回のアンインストール後の試験では、従来のopen停止を観測していない。
一方、本体の初期化flush競合は残っている。両者を別の失敗経路として扱う評価が補強された。**

同じ機械・Windows build・Python・本体ソース・公開probeで、COMODO有効時と再起動後には
途中で止まった4条件が、今回はすべて200ラウンドを完了した。
新規Pythonへの`guard64.dll`のロードもなくなった。
単なる再起動では止まったままだった比較と合わせ、COMODOを含む保護環境との相互作用が
open停止の主要な発生要因だったという根拠は強まる。

ただし、Windows Defenderの登録値と起動セッションも変化している。
アンインストールは複数のcomponent・設定を同時に変えるため、COMODOの特定機能・DLL・driverの
単独の欠陥やWindows内部の待機原因を特定したとはしない。
有限回の未観測から、すべての同期openが将来も停止しないことは保証しない。

現行ソースの未作成・空ファイルでは、排他取得前の`_ensure_lock_byte():165 / stream.flush()`の
PermissionErrorが残った。初期化なし・事前作成では同じ競合試験を成功した。
別環境での初期化失敗とも一致し、COMODOの存在が初期化競合の必要条件ではないことを、
同じ機械のアンインストール後の実測でも確認した。

## これまでの操作との対応

| 条件 | openの長時間停止 | 初期化flushの失敗 | 解釈 |
| --- | --- | --- | --- |
| COMODO有効時の初回 | 元の履歴テストで再現、guard64.dllを含むnative stack | 観測あり | 両経路がある |
| ユーザーが停止可能な機能を一時停止 | 未作成lockの38試行では未観測 | 3試行で再現 | DLLが残っていても停止しない条件があった |
| 再有効化 | 元の60秒テストでも再現 | 初期化の排他不足は未変更 | 有効化に伴う保護環境の変化との対応 |
| 有効のままシステム再起動 | 現行・初期化なしで再現 | 専用試験の結果欠落のため0件とは判定しない | 再起動だけでは解消しない |
| 今回のアンインストール後 | 4条件すべて完了、従来の停止は未観測 | 未作成50件・空11件 | openの停止と本体の初期化競合を切り分ける根拠 |

詳細は[初回・一時停止・再有効化](workflow-history-process-hang-2026-10-11.md)、
[別環境との比較](workflow-history-cross-environment-recheck-2026-10-11.md)、
[再起動後](workflow-history-post-reboot-2026-10-11.md)を参照する。
操作に伴ってDefenderの登録値も変化しているため、COMODOのDLLの有無だけを単一の原因とはしない。

## アンインストール後の観測状態

13:52:38 JSTのSecurity CenterにはWindows Defenderだけが登録されていた。
COMODOの登録、`cis`／`cmdagent`のプロセス、選択した7つのdriver名の登録は見つからなかった。
`C:/Windows/System32/guard64.dll`はなく、新規Pythonの`GetModuleHandleW`でもロードなしだった。
driverの調査対象名は`cmdguard`・`cmdhlp`・`cmderd`・`cmdvirth`・`inspect`・`cfrmd`・`cmdcss`であり、
あらゆる残存componentを完全に列挙したという保証には使わない。

起動時刻は13:39:35.500 JSTで、前回の09:46:31.500 JSTとは異なる。
COMODOの削除時刻は採取していないため、削除と起動の細かい順序は推測しない。
調査側で保護設定・サービス・driver・global Git設定を変更していない。

| 項目 | 前回の再起動後 | 今回 |
| --- | --- | --- |
| HEAD | `c4ef652` | 同じ |
| Windows | build 19045.7727、22H2、Core | 同じ |
| Python | 3.11.4、MSC v.1934、AMD64、quality-venv | 同じ |
| pytest／pytest-cov | 9.1.1／7.1.0 | 同じ |
| 主要パッケージ・pytest plugin | 前回の記録 | 同じ版・entry point |
| COMODOのSecurity Center登録 | あり、397312（0x61000） | なし |
| COMODOの確認対象プロセス | あり | なし |
| 新規Pythonのguard64.dll | ロードあり | ロードなし |
| Windows Defender登録値 | 393472（0x60100） | 397568（0x61100） |

Security Centerの値はraw値として記録し、個別保護機能の状態をdecodeしていない。
Windows Defenderが残っているため、「セキュリティ製品がすべてない環境」とは扱わない。
前後で版を照合したパッケージはpytest、pytest-cov、pytest-asyncio、pytest-mock、httpx、anyio、
Ruff、mypy、psutilである。前回の全インストールパッケージのsnapshotはないため、全依存の同一性は主張しない。

## 同じ公開競合プローブ

[前回の再起動後の調査](workflow-history-post-reboot-2026-10-11.md)と同じ
`history_lock_contention.py`を変更せず、sandbox外で実行した。
3 worker・200ラウンド・取得期限5秒・barrier期限20秒・保護区間の待機1ミリ秒を維持した。
利用者のprofileや既存履歴は使わず、各ラウンドで専用lockだけを削除・再作成／書込みする。

| 条件 | 前回の完了ラウンド | 今回の完了ラウンド | 取得成功 | 取得エラー | 実測 |
| --- | --- | --- | --- | --- | --- |
| 現行・未作成 | 0 / 200（開始barrier未到達、箇所未確定） | 200 / 200 | 550 / 600 | 50 | 38.594秒 |
| 現行・空ファイル | 1 / 200（open停止） | 200 / 200 | 589 / 600 | 11 | 41.078秒 |
| 現行・1バイト事前作成 | 8 / 200（open停止） | 200 / 200 | 600 / 600 | 0 | 42.078秒 |
| 初期化なしの隔離コピー | 0 / 200（open停止） | 200 / 200 | 600 / 600 | 0 | 43.406秒 |

前回の未作成条件は、native採取用の追加試行で製品のopen停止を確認した。
初回の開始前停止の箇所は未確定のまま保存し、今回の表でもopen停止と決め付けていない。

今回の4条件とも親のbarrier timeout・製品のlock timeoutはなく、workerはすべてexitcode 0だった。
全200ラウンドが完了し、共有counterと取得成功数は一致した。
probeのexitcode 0はプロトコル完了を表すのであり、取得エラーが0だったという意味ではない。
未作成50件・空11件の全tracebackを保存し、すべて次の経路だった。

```text
InterProcessFileLock.acquire():97
  → _ensure_lock_byte():165 / stream.flush()
  → PermissionError: [Errno 13] Permission denied
  → InterProcessLockError
```

初期化なしのコピーは、同期・非同期の初期化呼出し2か所だけを除いたものを使った。
製品本体には変更を適用していない。EOFを越えるbyte rangeの排他・解放がこの試験で動作した。
取得回数は同じround内で相関するため、取得エラー率を実利用での発生率には換算しない。
前回は途中で終了して結果が欠落したため、初期化エラー件数の増減も比較できない。

## 順序を交互にした再現性確認

現行・未作成と初期化なしを5組、各200ラウンド・600回ずつ、新しいworkerで逐次実行した。
順序は奇数組で現行→初期化なし、偶数組で逆とし、各組の前後に元の履歴テストを2回ずつ実行した。
5組・元の履歴テスト20回の測定を終え、次の結果になった。

| 組 | 順序 | 現行の取得エラー／600回 | 初期化なし |
| --- | --- | --- | --- |
| 1 | 現行→初期化なし | 58 | 600回成功、エラー0 |
| 2 | 初期化なし→現行 | 55 | 600回成功、エラー0 |
| 3 | 現行→初期化なし | 37 | 600回成功、エラー0 |
| 4 | 初期化なし→現行 | 44 | 最初の開始barrierで中断、停止箇所未確定 |
| 5 | 現行→初期化なし | 64 | 600回成功、エラー0 |

現行は計3,000回中258件の取得エラーで、全tracebackが初期化flushのPermissionErrorだった。
現行の5条件は全ラウンドを完了し、成功数・共有counterが一致した。
初期化なしは4条件・計2,400回を完了してエラー0であり、中断した4組目の600回を成功に数えない。

4組目の初期化なしはcounter 0・lockファイルなし、worker 0のstackなし、worker 1・2のstackは空だった。
parentは公開probeの最初の`barrier.wait(20):95`でBrokenBarrierErrorとなり、38.813秒で診断を終えた。
製品のopen・初期化flush・lock取得に入った証拠はないため、worker開始待ちの未確定事項として扱う。
出力のerrors=0はworker結果の欠落であり、600回成功を意味しない。

この中断に対し、プロセス限定sitecustomizeで製品import前からstackを採る補助試験を1回行った。
同じ初期化なし条件は200ラウンド・600回を完了し、エラー0だった。
補助試験の成功は元の中断を取り消さず、その原因が解決したとも判断しない。
通常の4条件試験の初期化なし600回、5組の有効な4条件2,400回、補助試験600回を合わせると、
完了が確認できた初期化なしは3,600回・エラー0だが、試験群ごとの内訳を保持する。

元の履歴テスト20回は19 passed / 1 failedであり、失敗は下記の初期化flushだった。
repeatability runnerのexitcodeは1だった。元の履歴テストの1失敗と4組目の測定中断を含む結果として保存し、
すべての組が完全に成功したとは記録しない。

最初の組の前後の4試行では、3件成功・1件失敗だった。
失敗した`pair-01-history-after-2`はpytest 3.05秒で終わり、workerのexitcodeは1だった。
captured stderrは`_ensure_lock_byte():165 / stream.flush()`のPermissionErrorを示した。
COMODO有効時の「open内で戻らずjoin(60)後にもexitcodeがNone」という失敗と区別する。
元のテストのassertionが同じでも、原因と終了状態は異なる。

この比較は別環境で使った公開`history_lock_repeatability.py`を変更せず使用した。
版検査用のrequirementsは今回の起動時のパッケージ名・版だけから生成し、URL・秘密値・local pathは含めない。
これは試験中のパッケージ版の変動検査用であり、アンインストール前の全依存一致の証拠ではない。
この比較の履歴テストは別環境と同じplugin自動読込み無効の条件である。

## 既存テスト・静的解析

再現性試験後、通常plugin条件の元の履歴テスト3回を逐次実行した。
独立実行1・2はそれぞれ1 passed（pytest 2.99秒・2.94秒）、独立実行3は1 failed（3.39秒）だった。
失敗したworkerのexitcodeは1で、初期化flushのPermissionErrorだった。
別環境と合わせたplugin自動読込み無効の20回と、通常条件の3回の両方で同じ経路が失敗した。
したがって、pytest pluginの自動読込みはこの初期化エラーの必要条件ではない。

続いてカバレッジ付きv3全体を14:06〜14:15 JSTに実行し、プローブとは同時に動かしていない。

| 検証 | 結果 |
| --- | --- |
| 元の履歴テスト・独立実行1 | 1 passed、pytest 2.99秒 |
| 元の履歴テスト・独立実行2 | 1 passed、pytest 2.94秒 |
| 元の履歴テスト・独立実行3 | 1 failed、pytest 3.39秒、初期化flushのPermissionError |
| v3全体 | 1686 passed / 17 skipped、失敗0、pytest 500.65秒 |
| 全体実行内の元の履歴テスト | 成功、JUnit実測2.184秒 |
| 全体実行内の署名checkoutテスト | 成功、JUnit実測3.780秒 |
| coverage | 88.19%、75%基準を満たす |
| Ruff | 成功 |
| mypy | 95 source filesで成功 |

17 skipはMailpit opt-inの12件と、symlink作成権限に依存する5件だった。
全体実行は外側の1800秒期限には達していない。
全体の履歴テストは成功したが、独立実行23回には同じ初期化失敗が2件ある。
実行ごとの結果を保存し、全体1回の成功から既存問題が解消したとは判断しない。
前回の554.58秒より今回は短いが、各1回の時間差からアンインストールによる性能改善は主張しない。

全体実行では前回と同じ当該`.git`パスだけのプロセス限定`safe.directory`を使う。
global設定・所有者・製品コード・既存テストは変更していない。
製品コード・v3テスト・設定168ファイルのSHA-256は前回の再起動後・今回の開始時・終了時で一致した。

本体の原因は、`acquire()`／`acquire_async()`の両方で、空ファイルの検査と初期化用write／flushを
OSの排他取得より前に行うことである。複数workerの初期化・lock処理が重なり得る。
今回のすべての取得エラーは排他待機中のtimeoutではなく、この初期化flushが拒否されたものだった。
ここまでの試験から初期化処理の排他不足は確認できるが、Windows内部の書込み拒否の細かい
実行順序までは採取していない。修正を実施する際は、既存の空／1バイトlockファイル、同期／非同期API、
Windows／POSIXの挙動を別途検証する必要がある。

## 証拠と限界

環境・ソースのhash、全条件の結果・例外、繰返し確認、開始待ち中断のstack有無、
JUnit・カバレッジ集計を
[アンインストール後の証拠JSON](raw/workflow-history-post-uninstall-evidence-2026-10-11.json)に保存した。
元ログ・隔離コピーはignore対象の`.runtime/history-post-uninstall-20261011-1350/`に保存した。
以前の有効／一時停止／再有効化／再起動／別環境の記録は保存した。

公開probeの実行条件は次のとおりで、出力先は未作成の専用ディレクトリを指定した。

```powershell
& .runtime/quality-venv/Scripts/python.exe `
  docs/investigations/quality-security-review-2026-10-06/probes/history_lock_contention.py `
  .runtime/history-post-uninstall-20261011-1350/contention-01 `
  --rounds 200 --record-all-errors

& .runtime/quality-venv/Scripts/python.exe `
  docs/investigations/quality-security-review-2026-10-06/probes/history_lock_repeatability.py `
  .runtime/history-post-uninstall-20261011-1350/repeatability-01 `
  --pairs 5 --rounds 200 `
  --requirements .runtime/history-post-uninstall-20261011-1350/requirements-current-environment.txt
```

初期化なしのコピーは初期化呼出し2か所だけを削除したものと再照合し、補助試験のコピーも含めてhashを保存した。
通常条件の独立試験は既存の`history_process_stacks.py`を使用し、元のテスト・join(60)を変更していない。
全体テストは`python -m pytest tests/v3 -q --cov=image_downloader --cov-report=term`に
JSON coverage出力・JUnit出力を加え、通常のplugin自動読込みを維持した。

個別の保護機能を分離した比較、COMODO内部のコード、kernel stackは調査していない。
今回のアンインストールをアプリの修正方法として要求するものではない。
初期化競合の修正と、openまで含めた取得期限の保証・I/O終了処理の検討は別の課題として残る。
