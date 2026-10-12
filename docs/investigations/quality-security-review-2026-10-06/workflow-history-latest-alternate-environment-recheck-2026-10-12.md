# 最新の共通ファイルロック修正の別環境追試

## 判定

最新ブランチへ切り替えて追試した。この環境では、初期化修正のみ・最終版とも
未作成／既存空ファイルへの各1,800回、計3,600回の取得がすべて成功した。
初期化例外、返った取得例外、open滞留、worker欠落、共有counterの不整合は観測しなかった。
修正前には同じ比較で初期化flush例外が93件再現した。
**排他外の初期化競合への修正を支持する結果である。**

一方、修正前もこの環境ではopen滞留を起こさなかった。
[別環境の保護有効時の追試](workflow-history-lock-security-enabled-recheck-2026-10-12.md)では
最終版にも滞留が残っている。**今回の成功から、Windowsのopen滞留への暫定対策が
根本原因を解消したとは判定できない。** Windowsの版と保護環境が同時に異なるため、
差をCOMODOだけ、またはWindowsの版だけに帰属しない。

個別36ケースは[CSV](workflow-history-latest-alternate-environment-runs-2026-10-12.csv)、
環境・ソース照合・93件の例外trace・テスト出力・監督記録・集計用スクリプトは
[raw evidence](raw/workflow-history-latest-alternate-environment-evidence-2026-10-12.json)に保存した。
本体ソースは試験の開始前後で同一だった。

## 最新ブランチと検証対象

試験前に`git fetch origin --prune`を行い、リモートのコミット日時と内容を確認した。
作業ツリーがcleanな状態から次の最新ブランチへ切り替えた。

| 対象 | コミット | 内容 |
| --- | --- | --- |
| 最新ブランチ `codex/fix-qs-016-secret-env` | `2994570ac01becb674cf8b4c114db3411cb74df6` | PR #12の共通ロック修正をマージ。2026-10-12 15:17:40 JST |
| 修正元 `codex/fix-qs-013-construction-cleanup` | `469f0c695e8dcb219ca3869a79d5a15443e64db7` | 修正と暫定対策の限界を記録。最新ブランチとtreeが同一 |
| 修正前の比較用スナップショット | `c4ef652bed1d3fa45813b1e2e790b327b9e82b68` | 前回この環境で競合を調査した版 |
| 初期化修正のみの比較用スナップショット | `3d40a0f5af64a59dbe771c78ccf93f9434abb1fa` | 初期化をロック取得後に移し、非バッファ化・失敗時の解放を追加 |

別環境で最終実装として検証した`e7e45e0`と最新ロックソースの差はコメント追加のみ。
差分を保存し、ASTも一致することを確認した。別環境の記録にあるファイルbytesのSHA-256と
今回の値は、コメントと改行形式の違いがあるため同じ値とは扱わない。

比較用`src`全体を各Gitコミットから別の新規ディレクトリへ展開した。
最終スナップショットのロックSHA-256は
`16b6635bd9d861a1ab1ae696dcec820e06d4b8c911398774ff469d3f3df394cd`。
通常テストで使った作業ツリーの同ファイルも同じSHA-256だった。
比較36ケースの108 workerと、履歴20回の各試験プロセスで読込み位置・SHA-256を照合した。

試験後にもリモートを再取得し、最新ブランチが引き続き`2994570`であり、
ローカルとのahead／behindがともに0であることを確認した。

## この環境と比較の条件

- 64bit Python 3.11.4、`C:\Program Files\Python311\python.exe`。
- Windowsのレジストリ値はDisplayVersion 25H2、build **26200.9457**、EditionID Core。
  別環境の記録はWindows 10 Home、build **19045**。`platform.platform()`が返す
  `Windows-10`という文字列だけで両環境を同じWindows版とは扱わない。
- pytest 9.1.1、pytest-cov 7.1.0、Ruff 0.16.5、mypy 2.3.1。
- 元環境から取得した`Downloads\requirements.txt`の106 pinは、開始前・終了後ともすべて一致。
  全インストール済みパッケージの版も試験前後で変化していない。
- 同ファイルに含まれない追加パッケージが31件ある。後述の依存不整合もあるため、
  Pythonパッケージ環境全体が元環境と完全一致したとはしない。
- 保護環境は前回のユーザー申告「この環境ではセキュリティー製品を使用していない」を引き継いだ。
  診断runnerの開始・終了時に`guard64.dll`はロードされていなかった。
  全workerのDLL一覧や保護設定全体を取得したものではない。
- 試験はCodexサンドボックス外で順番に実行した。前回この環境で確認した
  サンドボックス内のasyncio socketpair起動の滞留を、今回のファイルロック比較に混在させていない。
  保護設定・依存パッケージ・テストの待機時間は変更していない。

測定は2026-10-12 **16:59:52〜17:16:56 JST**。
他環境との全体実行時間の差から、OSや保護製品の単独の影響を推定しない。

## 通常回帰とv3全体

| 試験 | 結果 | pytestの所要時間 |
| --- | --- | --- |
| `tests/v3/storage_output/test_interprocess_lock.py` | 94成功、失敗・スキップ0 | 4.89秒 |
| v3全体、カバレッジ付き | 1,774成功、17スキップ、失敗0 | 204.62秒 |
| 元の独立複数プロセス履歴テスト、最終版で20回 | 20／20成功、外部期限の発動0 | 監督・起動込み48.81秒 |

v3全体のbranch coverageは**88.34%**で、基準75%を満たした。
17スキップはシンボリックリンク作成不能5件と、Mailpitのopt-in未構成12件。
ロック回帰94件には実processでの排他、空ファイルの初期化順、失敗時の解放、
共有違反の判定・再試行、親／対象IDの保持、同期／非同期の中断が含まれる。

別環境の初回全体実行で一度失敗した
`test_cancellation_during_final_log_write_preserves_success_and_revises_history`も成功した。
今回のJUnit記録は0.730秒で、元の`started.wait(5)`は変更していない。
この成功で以前の5秒待機失敗の原因が解消・特定されたとはしない。

履歴20回は毎回新しいpytestプロセスで実行した。各試験に既存probeの120秒外部期限を設け、
plugin autoloadを無効化する指定は追加していない。ロック回帰・全体テスト・反復比較との並行実行は避けた。

## 同じprobeによる3版の反復比較

[別環境の保護停止時](workflow-history-lock-remediation-2026-10-12.md)と同じ
[history_lock_remediation.py](probes/history_lock_remediation.py)を使用し、3版×4条件×3反復の36ケースを実行した。
各ケースは新しい親プロセスと3つのspawn workerを持ち、版の実行順を回転・反転した。
定期faulthandler dumpは追加していない。

- 未作成／既存空：各worker 200 round、1ケース600操作。full acquireの保護区間に1ms待機を置き、
  共有counterが成功した取得数と一致することを確認する。
- 非空保持：各worker 2,000 round、1ケース6,000操作。同じファイルのopen／closeだけの対照。
  この条件を6,000回のロック取得試験とは扱わない。
- 未作成はroundごとに新しいファイル名。既存空はparentが作成・closeした後に開始。
  前roundの全workerのclose後に次roundへ進む。使用中のunlink／truncateは行わない。
- barrierの期限20秒、case全体の外部期限120秒。滞留時に止めるのはprobeが所有するprocessだけ。

| 条件、各3反復の合計 | 修正前の成功／計画 | 初期化修正のみ | 最終版 | 修正前の返った例外 |
| --- | --- | --- | --- | --- |
| 未作成、full acquire | 1,737／1,800 | 1,800／1,800 | 1,800／1,800 | initialization_flush 63件 |
| 既存空、full acquire | 1,770／1,800 | 1,800／1,800 | 1,800／1,800 | initialization_flush 30件 |
| 1バイト保持、open-only | 18,000／18,000 | 18,000／18,000 | 18,000／18,000 | 0件 |
| 14バイト保持、open-only | 18,000／18,000 | 18,000／18,000 | 18,000／18,000 | 0件 |

**全36ケースがプロトコルを完走**した。open滞留、worker欠落、worker異常終了、外部期限の発動は全版で0。
full acquireのcounterはすべて成功数と一致した。
非空保持18ケースでは、前後のサイズ・ファイルID・mtimeが一致し、終了後の内容も初期内容と一致した。

修正前の例外数は、未作成で**21・14・28件**、既存空で**5・12・13件**。
全93件が`InterProcessLockError`のcauseとしての`PermissionError`で、errno=13、winerror=None、
旧実装の`_ensure_lock_byte()`内の`stream.flush()`に到達していた。
これは返った例外であり、openが戻らなかった現象ではない。
修正後は新たなwrite／flush／open例外への移行も観測しなかった。

比較probeの終了値は1だった。修正前の既知の93失敗によるもので、
初期化修正のみ・最終版は全操作成功である。終了値だけで版ごとの成否を判定しない。

## 考察と残る確認事項

失敗件数と発生roundは変動したが、修正前では3反復の両cold条件すべてで同じ初期化flush競合が再現した。
前回の[この環境での追試](workflow-history-repeatability-2026-10-11.md)とも整合する。
発現にはタイミングが関係する一方、競合する処理が排他前に残っているという実装上の問題である。
今回、同じ環境・probeで初期化修正のみが全取得を成功させたことは、修正の効果を支持する。
各操作は同じworker・barrierを共有するため、独立した統計標本3,600件とは扱わず、実利用の失敗率も推定しない。

この環境では公開方式を追加する前からopen滞留が再現していないため、
初回公開方式や0.1秒の限定的再試行について、滞留を解消した効果は測定できない。
今回の成功から、自然に共有違反が発生して再試行が実行された回数も分からない。
返った共有違反に対する機能の確認と、戻らないnative openの解消は別の確認である。

別環境の保護有効時には、最終版で未作成3ケース中2ケース、既存空3ケース中3ケースが滞留した。
今回と同じ最終実装で通常履歴20回はそちらでも成功していた。
通常テストの成功だけでcold競合時の問題を否定できないという点は変わらない。
本体のロック待機deadlineはopen完了後に設定されるため、返らないnative openは
ロック待機timeoutや例外後の再試行で終了させられない。
残存問題を追う測定は、再現する環境でのnative thread stack、関連handle／driver待機、
対象・一時ファイルのIOイベントの観測が必要である。

環境整合性の追加確認として、`pip check`は終了値1で次の2件を報告した。

- torchaudio 2.5.1+cu118はtorch 2.5.1+cu118を要求するが、インストール済みtorchは2.10.0。
- torchvision 0.20.1+cu118もtorch 2.5.1+cu118を要求する。

この3パッケージはすべてrequirements.txtにない追加パッケージである。
`src`、`tests/v3`、`pyproject.toml`、`plugin-sources`の検索ではTorch系の参照は見つからなかった。
競合probeは標準ライブラリと監督用psutilを使用し、対象ロックモジュールを直接読み込む。
この依存不整合を今回のflush競合の原因とは結び付けないが、環境全体の完全一致を主張する根拠もない。
今回パッケージの変更は行っていない。

Ruffは全体で成功、mypyは95 source filesで成功。全体テストは失敗がなかったため再実行せず、
追加の全OS CIやパッケージbuildは今回の追試には含めていない。

## 再実行

同じPythonと既存依存環境を使用し、試験専用の新しい出力先を用意する。
比較用`src`は上記3コミットのGit archiveから別々に展開する。

```powershell
python -m pytest -q tests/v3/storage_output/test_interprocess_lock.py
python -m pytest -q tests/v3 --cov=image_downloader --cov-report=term-missing --cov-fail-under=75
python docs/investigations/quality-security-review-2026-10-06/probes/history_lock_remediation.py NEW_OUTPUT --source before=BEFORE_SRC --source initialization=INIT_SRC --source final=FINAL_SRC --protection alternate-no-security-product-user-reported --repeats 3 --cold-rounds 200 --stable-rounds 2000
python docs/investigations/quality-security-review-2026-10-06/probes/history_lock_history_recheck.py NEW_HISTORY_OUTPUT --source final=FINAL_SRC --protection alternate-no-security-product-user-reported --repeats 20
```

今回使用した外部監督・JUnit出力・ソース展開・ハッシュ記録の具体的なrunnerもraw evidenceに含めた。
実行時は通常テストと競合比較を順番に実行し、保護設定は変更しない。
