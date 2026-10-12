# システム再起動後の履歴ロック再調査

この記録の後、[COMODOアンインストール後の再調査](workflow-history-post-uninstall-2026-10-11.md)を行った。
従来のopen停止はその試験では未観測だが、初期化flushの競合は残った。以下は再起動直後の記録として保存する。

調査日：2026-10-11（JST）。対象HEAD：`c4ef652bed1d3fa45813b1e2e790b327b9e82b68`。
対象ブランチ：`codex/fix-qs-013-construction-cleanup`。
開始時には前回の調査資料4ファイルの未コミット変更があり、それらを保存したまま調査した。
製品コード・既存テスト・設定には変更がない。

## 再起動による影響の評価

**システム再起動後も、履歴ロックのopen停止は再現した。再起動だけで解消したとは判定できない。**
今回も、現行ソースの空・1バイト事前作成と、初期化呼出しを除いた隔離コピーで、
workerが`Path.open("a+b")`内で停止した。
期限はopen後に設定されるため、指定した5秒の取得期限ではこの停止を検出できていない。

新しい診断ディレクトリ・新しいworkerを使い、再起動前の診断ファイルやprofileは再利用していない。
再起動前のプロセスやhandleの残留だけでは、今回の再現を説明できない。
一方、再起動後も同じ保護製品の登録と新規Pythonへの`guard64.dll`のロードがあり、
その環境との相互作用は残る。登録値やDLLの存在から、個別保護機能の状態や内部の待機原因は断定しない。

初期化flush競合は、COMODOを使用しないとの申告がある別環境でも確認した問題である。
今回の公開競合試験は途中で終了してworker結果が得られていないため、
`errors=0`を初期化flush競合が消えた根拠には使えない。
初期化除去はflushへの対策候補であり、open停止には別の対処が必要という前回の評価を維持する。

## 起動と環境の確認

`Win32_OperatingSystem.LastBootUpTime`は09:46:31.500 JSTだった。
環境取得は10:03:42 JST、起動から約17分後であり、再起動前の08時台の試験より後の起動を確認した。

| 項目 | 再起動前 | 再起動後 |
| --- | --- | --- |
| HEAD | `c4ef652` | 同じ |
| Windows | build 19045.7727、22H2、Core | 同じ |
| Python | 3.11.4、MSC v.1934、AMD64、quality-venv | 同じ |
| pytest／pytest-cov | 9.1.1／7.1.0 | 同じ |
| pytest追加plugin | anyio、asyncio、cov、mock | 同じentry point |
| COMODO登録値 | 397312（0x61000） | 同じraw値 |
| Windows Defender登録値 | 393472（0x60100） | 同じraw値 |
| 新規Pythonのguard64.dll | ロードあり | ロードあり |

httpx 0.28.1、anyio 4.14.2、pytest-asyncio 1.4.0、pytest-mock 3.15.1、
Ruff 0.16.5、mypy 2.3.1も同じだった。
COMODO関連プロセスは再起動後のPIDで存在した。COMODOの保護設定やサービスの変更は行っていない。
`guard64.dll`のファイル情報はCompanyName COMODO、ProductName COMODO Internet Security、
FileVersion／ProductVersion 12, 2, 3, 8026だった。再起動前の同じファイル情報は採取していないため、
DLL版が同じだったという比較には使わない。

## 同じ公開競合プローブでの比較

[再起動前の調査](workflow-history-cross-environment-recheck-2026-10-11.md)と同じ
`history_lock_contention.py`を変更せず、sandbox外で実行した。
3 worker・200ラウンド指定・取得期限5秒・保護区間の待機1ミリ秒・barrier期限20秒を維持した。
各ラウンド後に親が専用lockを削除・再作成／書込みする条件も同じである。

| 条件 | 再起動前：完了ラウンド／counter | 再起動後：完了ラウンド／counter | 再起動後の結果 |
| --- | --- | --- | --- |
| 現行・未作成 | 0／2 | 0／0 | 最初のbarrierで中断。worker 1の停止位置は未確定 |
| 現行・空ファイル | 3／11 | 1／5 | worker 0がopenで停止 |
| 現行・1バイト事前作成 | 13／41 | 8／26 | worker 1がopenで停止 |
| 初期化なしの隔離コピー・未作成 | 23／71 | 0／2 | worker 0がopenで停止 |

4条件とも200ラウンドは完了せず、公開probeのexitcodeは1だった。
各条件の実測は25.235・26.438・28.187・27.453秒である。
親がBrokenBarrierErrorになった後、finallyで所有workerだけを終了した。
exitcode -15は診断による終了であり、自然終了時の別の製品エラーとは扱わない。

未作成条件では、到達したworker 0・2のstackは最初のbarrier待機だけを示し、
worker 1のstackファイルはなかった。lockファイルもなく、counterも0なので、
この試行を製品のlock内のopen停止やflush失敗とは分類しない。
残り3条件では、繰り返しのstackが`pathlib.py:1044 / open`と
`interprocess_lock.py:96 / acquire`を示している。

公開probeは親の例外後にworker結果を空配列とするため、保存されたerrors・successesの0は
結果の欠落を表す。counterには未完了ラウンドの取得も含まれる。
完了ラウンド数の変化は、実利用での発生率や再起動による改善率に換算しない。

## native stackによる追加確認

所有workerにPID記録だけを加える前回と同じ診断wrapperを使用し、
未作成・初期化なしの条件を新しい出力先で再度確認した。

| 条件 | 完了ラウンド／counter | ファイルサイズ | 診断実測 |
| --- | --- | --- | --- |
| 現行・未作成の再確認 | 0／2 | 1バイト | 26.812秒 |
| 初期化なしの再確認 | 13／41 | 0バイト | 30.219秒 |

両条件のPython stackはopen内を示し、native stackは再起動前と同じ
`_wopen → CreateFileW → guard64.dll → NtCreateFile`（export名`ZwCreateFile`）だった。
現行の停止workerはPID 12796／TID 7976、初期化なしではPID 4164／TID 12768である。
PIDの記録と採取時の親PIDを照合し、この診断の所有子プロセスだけを観測した。
native内部frameの近傍export名からCOMODOの実際の内部関数や機能は推測しない。

停止中に、どちらのファイルでも別の診断プロセスが`r+b`で開き、byte 0を取得・解放できた。
したがって、この停止を残ったbyte lockの占有だけでは説明できない。
最初の未作成試行の開始前停止の原因は未確定のまま保存し、この再確認結果で置き換えない。

この結果は、初期化flushを通らなくても停止し、同じI/O経路に入るという前回の根拠を補強する。
具体的な待機を解消する設計・製品側の初期化競合修正は、今回の調査では実装していない。

## 既存テストの再実行

元の履歴テストを通常のpytest plugin条件で3回逐次実行し、その後v3全体をカバレッジ付きで実行した。
競合プローブと全体テストは同時に実行しない。

| 検証 | 結果 |
| --- | --- |
| 元の履歴テスト・独立実行1 | 1 passed、pytest 2.91秒 |
| 元の履歴テスト・独立実行2 | 1 passed、pytest 2.71秒 |
| 元の履歴テスト・独立実行3 | 1 passed、pytest 2.74秒 |
| v3全体 | 1686 passed / 17 skipped、失敗0、pytest 554.58秒 |
| 全体実行内の元の履歴テスト | 成功、JUnit実測2.357秒 |
| 全体実行内の署名checkoutテスト | 成功、JUnit実測4.036秒 |
| coverage | 88.19%、75%基準を満たす |
| Ruff | 成功 |
| mypy | 95 source filesで成功 |

独立実行ではstack採取用sitecustomizeだけを加え、新しいpytestプロセス・一時ディレクトリを使った。
本体・元のテスト・join(60)は変更していない。通常の保存1回の成功と、ファイルを繰り返し
削除・再作成する専用試験の停止は両立する。3回の成功から、利用中の停止が消えたとは判断しない。

前回の全体実行で失敗したローカルcloneは、再起動後も同じ`detected dubious ownership`でexit 128となった。
`.git`の所有者が`CodexSandboxOffline`、sandbox外の実行ユーザーが`pc01`という条件が残っている。
全体再テストでは、前回の個別再テストで確認した、当該`.git`パスだけのプロセス限定
`safe.directory`を設定する。グローバル設定・所有者・テストは変更しない。
このGit条件の違いを明記し、Gitのテスト結果の改善を再起動の効果とは扱わない。

17 skipはMailpit opt-inの12件と、symlink作成権限に依存する5件だった。
全体テストは10:09〜10:18 JSTに実行し、外側の1800秒期限には達していない。
製品コード・v3テスト・設定の開始時と終了時のSHA-256は168ファイルすべて一致した。

全体の実測は再起動前655.11秒、再起動後554.58秒だった。ただし各1回の測定で、
Gitの条件も上記のとおり異なるため、この時間差を再起動による性能改善とは判断しない。
今回の全体実行は成功と記録するが、公開競合試験の未完了・open停止もそのまま記録する。

## 証拠と制限

生の環境・起動時刻・Security Center登録値、各条件のstackと完了数、native stack、
全体テストのJUnit・カバレッジ集計を
[再起動後の証拠JSON](raw/workflow-history-post-reboot-evidence-2026-10-11.json)に保存した。
元ログ・隔離コピーはignore対象の`.runtime/history-post-reboot-20261011-1003/`に保存した。

公開probeは次の条件で実行した。出力先は未作成のディレクトリである必要がある。

```powershell
& .runtime/quality-venv/Scripts/python.exe `
  docs/investigations/quality-security-review-2026-10-06/probes/history_lock_contention.py `
  .runtime/history-post-reboot-20261011-1003/contention-01 `
  --rounds 200 --record-all-errors
```

native観測用wrapperは前回の`.runtime/history-cross-environment-recheck-20261011/`から
同じコードをコピーし、`--variants missing no-initialization`を指定した。
観測には前回と同じローカルexport symbolの採取helperを使った。
全体テストは`python -m pytest tests/v3 -q --cov=image_downloader --cov-report=term`に
JSON coverage出力・JUnit出力を加え、通常のplugin自動読込みを維持している。

今回は同じ機械の再起動前後の観測であり、再起動を繰り返したランダム化比較ではない。
Windows内部の待機・filter driver・COMODOの個別機能を分離した試験やkernel stackの採取は行っていない。
従来の別環境・保護機能の停止／再有効化の根拠は保存し、今回の結果で上書きしない。
