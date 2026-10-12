# 履歴ロックの全ブランチ・コミット履歴調査

調査日：2026-10-11（JST）。元のHEAD：`c4ef652bed1d3fa45813b1e2e790b327b9e82b68`。
元のブランチ：`codex/fix-qs-013-construction-cleanup`。

**初期化flushの問題は、追跡可能な最初の公開コミット`18488a5`に既に存在し、その実コードで再現した。
全52コミットを隔離cloneへ一つずつチェックアウトした結果でも、保存された結果に同じflush失敗が含まれた。
最近のQS-001／002／013／016修正によって初めて導入された問題ではない。**

一方、従来のopen停止は現在のCOMODO不在の環境では確認できず、最初に発生するコミットを判定できない。
「停止しない版→停止する版」の境界を確定したとは扱わない。
初期化flushとopen停止の関係は[回数・時間・競合条件の比較](workflow-history-open-variability-2026-10-11.md)の評価を維持する。

## 履歴の範囲と保全

`git fetch --all --tags --no-prune`で取得した後、`git rev-list --all --reverse --topo-order`の全コミットを調べた。
originのfetch設定は`+refs/heads/*:refs/remotes/origin/*`である。

| 対象 | 確認結果 |
| --- | --- |
| ローカルbranch | main、QS-001、QS-002、QS-013、QS-016の5本 |
| originのbranch | 同じ5本、origin/HEADはmainを指す |
| tag | なし |
| 到達可能なcommit | 52。すべて共通の一本の履歴に属し、別系統・merge commitはない |
| shallow repository | false |
| root commit | `18488a56860a5a2a6aedccc85f92f86b9ae14168`、2026-09-18のInitial public beta release |
| reflogを含めたcommit数 | 52。追加のcommitはない |
| fsckで見つかった到達不能commit | 0。到達不能tree／blobはあるが、追加のcommit履歴ではない |

主作業ツリーをcheckoutせず、`.runtime/history-lock-git-20261011-1805/repository/`へ
`git clone --no-hardlinks --no-checkout`で作ったcloneだけをdetached HEADで移動した。
各移動前にcloneがcleanであること、移動後のHEADと対象SHAが一致することを確認した。
元のHEAD・本体・既存テスト・設定・既にあった未コミット資料を保持した。

全52コミットの親・日時・件名・変更ファイル・lock blob・実行結果を
[証拠JSON](raw/workflow-history-git-history-evidence-2026-10-11.json)へ保存した。
[コミット別CSV](workflow-history-git-revisions-2026-10-11.csv)と
[候補変更CSV](workflow-history-git-candidates-2026-10-11.csv)も保存した。
remote側の削除済みbranch・このrepositoryに保存されていない公開前の履歴までは取得できない。

## コード履歴で確認したこと

`src/image_downloader/storage/interprocess_lock.py`のGit blobは2種類だけだった。

| 範囲 | lock blob | commit数 | 差分 |
| --- | --- | --- | --- |
| 初回〜`f1a321b` | `fb5ee91b84c7c557557bb8c4bbdafe818ddb14d1` | 20 | 初期実装 |
| `dc8e4af`〜現在 | `5ca9df58c3d0c38aab0972ea0933a3a8abd62d03` | 32 | msvcrtの読込みを直接importからimportlib経由と型Protocolへ変更 |

`InterProcessFileLock`クラス本体のASTを行番号・空白を除いて比較すると、**全52コミットで同一**だった。
これはクラス本体の同一性であり、周囲のapplication・依存環境全体が同一という意味ではない。
Windows backendのimport方法は実際に変更されており、その前後も実行して確認した。

初回から次の順序になっていた。

```text
open("a+b")
  → 排他前に空判定・write・flush
  → deadlineを作成
  → byte lock取得をpoll
  → 保護区間
  → unlock・close
```

同期・非同期の両方で、排他前の初期化とopen後のdeadlineが存在する。
初回のflushは行158、現在は行165で、行番号の違いは主に追加Protocolによる。
open方式、lock対象byte、初期化順序は変わっていない。

## 疑わしい変更と確認結果

下表は直接関係する変更と、利用箇所・周辺環境を変える主要候補である。
設定・状態・依存変更を含む15候補の全変更ファイルは候補CSVに列挙した。
残るコミットも省略せずcheckout・probe実行し、証拠JSONの全変更一覧と照合した。

| commit | 変更箇所・疑った理由 | 確認結果 |
| --- | --- | --- |
| `18488a5` | 共通ロック、cookie・state・出力の利用を初回追加 | 初回そのものに問題経路があり、実行で同じflush失敗を再現 |
| `f1a321b` | `storage/workflow.py`などでworkflow・feedロックを追加 | 既存ロックの利用拡大。共通クラスは同一で、それ以前のrootでも再現 |
| `dc8e4af` | Windows backendのmsvcrt読込みとテストを変更 | 変更前の`f1a321b`・変更後の双方で再現。初期化問題の導入境界ではない |
| `2d01543` | workflow plan・非同期ロックの利用を追加 | 共通ロック本体に差分なし。今回の同期flushの導入ではない。全版の非同期stressは実行していない |
| `21decb6` | `storage/workflow_history.py`と問題の複数processテストを追加 | 履歴機能で既存ロックを使用し始めた時点。共通ロックの不具合導入とは分ける |
| `40a93d1` | 履歴testのjoinを15秒から60秒へ延長、例外診断を変更 | 当時すでにhistory-process timeoutの報告がある。本体のlock処理は変更していない |
| `afd7581` | workflow plan・log処理と実行順序を変更 | 共通ロック本体に差分なし。この変更以前からflush失敗する |
| `1e5b47e`／`7835102`／`aee66c9` | QS-001のimport、QS-002の要求、QS-016の秘密参照 | この3変更の前後で再現。flushを初めて導入した変更ではない |
| `d7e6a1b` | QS-013の組立て失敗時の解放を追加 | 直前`92a3d0c`でも再現。履歴workerは直接storeを作り保存し、composeを呼ばない |

`40a93d1`のcommit本文には「one history-process timeout」があり、同commit内の
[当時の検証記録](../cli-arguments-2026-10-03/verification.md)にも、15秒の子process待機失敗と60秒への延長が記録されている。
これは今回調べた履歴中の早い失敗報告だが、native stackや初期化のtracebackがないため、
現在確認したopen停止／flush失敗／正当な処理の待機不足のどれかは分類できない。
記録にある821件とcommit本文の1116件の全体結果も、別の当時の実行としてそのまま保持し、今回の数へ合算しない。

## 各checkoutでの試験

[history_lock_revision_probe.py](probes/history_lock_revision_probe.py)を、各commitへ移動した後の
実ファイルに対して、新しいspawn worker 3件・30ラウンド・1ms保持で一度ずつ実行した。
全worker解放後に専用lockファイルを削除し、毎回未作成から同時取得を行った。
本体のopen・初期化・backend・5秒のlock期限・100ms pollは変更していない。

共通ロックだけを比較するため、probeはpackage初期化を省いた名前空間で、checkoutのlock moduleと
例外定義をそのままimportした。application全体のimport時間や資源構築は、このprobeの検証範囲に含めない。
各workerの実module pathとSHA-256を記録し、checkoutの実ファイルと一致することを確認した。
各条件に90秒の外部watchdogを設け、定期faulthandler出力は使わず、fatal handlerだけを有効にした。

| 項目 | 結果 |
| --- | --- |
| checkout・試験したcommit | 52／52 |
| 予定した取得数 | 4,680 |
| 観測を完了したcommit条件 | 51 |
| 保存できた取得結果 | 4,650。51条件の4,590＋中断条件の2 workerの60 |
| 初期化flush PermissionError | 保存結果で406件。完了条件403＋中断条件の生存worker3 |
| 保存結果にflush失敗があるcommit | 52／52 |
| その他の取得エラー | 保存結果で0 |
| counter | 完了条件では成功取得数と一致 |

初回`18488a5`の本調査では90取得中11件、現在`c4ef652`では90取得中6件が同じflushで失敗した。
本調査前の初回checkoutの予備30ラウンドでも12件を確認し、その結果は別に保存した。
完了条件の失敗数は各2〜16件だった。回数の差を、そのcommitの悪化・改善率とは扱わない。
実行は古い順でランダム化しておらず、取得の相関、スケジューリング、実行時刻、probeのimport範囲の影響がある。

## 結果収集が不完了だった条件

`1aebba5`の条件では、worker-0の最終JSONが0バイトで、親がJSONDecodeErrorになった。
worker-1・2はそれぞれ30ラウンドを完了し、flush失敗1件・2件を記録した。
最終JSON保存の完了を確認できず、worker-0の30取得分は欠落している。
この条件の3件は下限であり、全workerの合計ではない。

他の2 workerは最終barrierを終えているため、製品openの途中で全条件が止まったという結果ではない。
ただし欠落workerの保存途中のnative stack・終了コードは親のsummaryに残らず、
結果保存のどのI/Oで遅れたかや、join後の終了処理との関係までは確定できない。
従来のguard64.dllを通る製品open停止とは分類しない。

`1aebba5`はworkflow選択の説明を追加したcommitで、共通ロックのblobは直前・直後と同じである。
52条件終了後に同じcommitを新しい専用出力先で3回再確認し、3回とも30ラウンドを完了した。
flush失敗は4／10／9件、計270取得で23件だった。
この再確認の成功で元の欠落を取り消さず、元の収集失敗を「最初の不良commit」としてbisectに渡さない。

## 元の履歴テストでの照合

共通moduleだけのprobeに加え、次の6 checkoutで元の
`tests/v3/application/test_workflow_history.py::test_independent_processes_merge_under_shared_history_lock`
を、通常のpytest plugin自動読込みで各3回実行した。

| checkout | 意味 | 結果 |
| --- | --- | --- |
| `21decb6` | 履歴機能・元testを追加した版 | 3 passed |
| `40a93d1` | test待機を60秒へ延ばした版 | 3 passed |
| `afd7581` | mandatory plan・logを追加した版 | 3 passed |
| `92a3d0c` | QS-013修正直前 | 3 passed |
| `d7e6a1b` | QS-013修正直後 | 3 passed |
| `c4ef652` | 現在の記録済みHEAD | 3 passed |

計18回は成功し、open停止もflush失敗もこの小標本では出なかった。
初回rootには履歴機能・このtestがまだないため、rootをこのtestで合否判定していない。
初期化の実行機会を繰り返す共通module probeでは同じ版に失敗があるため、
元testが3回成功した版を「初期化問題のないgood commit」とは扱わない。

現在の同じquality-venvの依存を使い、各checkoutのconftest・tests・srcを利用した。
PYTHONPATHは隔離checkoutのsrcを指し、coverageとpytest cacheだけを無効にした。
過去の依存packageやOS／COMODO状態まで復元した実行ではない。
全52版のv3全体・3 OSのCI・全版の非同期stressは実行していない。

## 環境と調査上の補足

Windows 10.0.19045、Python 3.11.4、pytest 9.1.1、HTTPX 0.28.1などの既存quality-venvを使った。
18:12 JSTの開始時と終了・再確認後で、確認対象のCOMODO process／driver／guard64.dllはなく、
新規Pythonへのguard64.dllロードもなかった。Security CenterはDefenderだけで、raw登録値397584だった。
起動時刻は13:39:35.500 JSTのままで、依存package版も前後で一致した。
保護設定の変更・製品の再インストールは行っていない。

最初の環境照会はPowerShellのFile実行policyで拒否されたため、同じ読み取り専用の照会を通常のCommandで実行した。
execution policyは変更していない。sandboxユーザーが作ったcloneを通常ユーザーで試験するGit照会は
所有者チェックで拒否されたため、当該cloneの絶対パスだけへコマンド単位のsafe.directoryを指定した。
global設定は変更していない。いずれも試験開始前の準備上の問題で、本体のlock失敗と合算しない。

Ruffは全体で`All checks passed!`だった。本体・既存テスト・設定に差分はなく、
元からあった未コミット資料のSHA-256も保持した。今回のcommit・pushは行っていない。
元ログ、隔離clone、実行driverはignore対象の`.runtime/history-lock-git-20261011-1805/`へ保存した。
driverの内容・hash、環境・各実行コマンド・例外例は公開した証拠JSONにも収録した。

## 導入時点として判定できること

初期化flushについては、**このGit履歴の最初の公開版から不具合経路が存在する**と判定できる。
goodな先行commitがなく、この履歴内で「どの後続変更が初めて壊したか」というbisectの境界は存在しない。
公開前にいつ実装されたかまで特定するには、初回より前のrepository・バックアップ・作業記録が必要である。

履歴保存という利用箇所は`21decb6`で追加され、待機不足の報告は遅くとも`40a93d1`時点にある。
利用開始・失敗の報告時点・共通ロックの潜在的問題の存在時点を分けて扱う。

open停止については、全版で同じa+b openと期限の適用範囲を確認できるが、
それだけで最初の版から停止したとは証明できない。
COMODO有効時の再現環境と停止中のnative／kernel待機情報を使い、同じSHA・同じ試験・同じ依存で比較する必要がある。
現在の不再現をgood判定にして機械的にbisectすると、揺らぎや環境差を誤って導入commitへ帰属させる恐れがある。
