# QS-013 修正確認：組立て失敗時の所有資源解放

対象issue：[QS-013 / #6](https://github.com/GlassLeaf/image_downloader/issues/6)。
当時の[監査報告書](report.md#qs-013)、指摘データ、問題挙動をassertする
[プローブ](behavior_probe.py)は変更していない。修正後の安全な期待値は通常回帰テストで検証する。

## 修正保証と所有権

通常のcompose、CLI inspection、workflow plan、compose_registryに構築ガードを追加した。
組立て中はcomposerが作成済み資源を所有し、完成したserviceまたはregistryを正常に返す段階でだけ
所有権を移譲する。内部の依存組立て失敗でも、すでに作成した資源を閉じる。

- 作成済みlog sinkはlogger構築成功後にloggerへ移譲し、二重closeを避ける。
- 完成した依存DTOを受け取った後にservice構築が失敗した場合も、画像processor、gateway、logger、registryを解放する。
- registryのbuiltin登録・prepare失敗では当該runtimeを閉じ、そのmoduleとfinder登録を解放する。
- 元の例外の同一性・causeを保持し、KeyboardInterrupt・CancelledErrorでも後始末を試みる。
- cleanupの例外・中断を固定診断に留め、後続資源の解放を続ける。例外本文・秘密値・パスを追加診断へ出さない。
- failure cleanupはservice.closeを呼ばず、cookie保存・通知送信を行わない。

公開API・同期compose・依存DTOのコンストラクター／フィールド／比較・設定schema・正常終了時のcloseは維持する。
各構築呼出しは従来の形を維持し、独自の依存構築結果をserviceへ渡す経路も保持する。
gateway、画像processor、file sinkは、ローカルのlock等の初期化を済ませてから資源を取得する。

## 非同期終了処理と制限

呼出元でasyncio event loopが動作していなければasyncio.runで非同期cleanupを完了する。
動作中の場合は一時スレッドの独立loopで実行し、終了を待つ。
待機中に再中断された場合も元の構築エラーを保持して終了を待つ。
同期資源のcloseは呼出元スレッドで行い、完成したserviceの通常closeは従来の経路を使う。

対象は本体が構築し、通信・画像処理をまだ開始していない資源である。
利用済みclient・独自のthread/loopに依存する資源を移動するための一般機能ではない。
HTTPXの非公開状態を操作せず、gateway.closeからclient.acloseを実行する。
他runtimeの所有module・finder登録は保持する。

ディレクトリ、log、lockファイルの作成自体は取り消さない。cookie/stateの移行は不要である。
失敗時の例外返却は終了処理の完了まで遅れる。後始末自体の失敗、プロセス強制終了、
回復不能なメモリ不足では完全解放を保証できない。独自プラグインのimportが作った外部資源や
外部依存moduleのすべてを巻き戻す保証は含めない。

## 検証環境・基準

- 基準コミット：`92a3d0cdd3f81eba553bd2920b9c768922947edb`。
- 実装修正コミット：`d7e6a1bec2b58a66e702356f16c8dc3d7a107cd9`。
- 着手時の作業ツリー：clean。修正ブランチ：`codex/fix-qs-013-construction-cleanup`。
- 実行日：2026-10-11（Asia/Tokyo）。Windows 10 build 19045、Python 3.11.4。
- Ruff 0.16.5、mypy 2.3.1、pytest 9.1.1、pytest-cov 7.1.0、HTTPX 0.28.1、httpcore 1.0.9。
- 最小対応版検証：HTTPX 0.27.0とsniffio 1.3.1を検証専用ディレクトリに導入。既存環境・依存宣言は変更していない。

## 検証結果

通常回帰テストは[test_composition_cleanup.py](../../../tests/v3/application/test_composition_cleanup.py)に追加した。
通信はMockTransport、keyring/cookieは模擬、workerは模擬executorを使用する。

| 検証 | 結果 |
| --- | --- |
| QS-013の新規回帰テスト / HTTPX 0.28.1 | 64 passed |
| 同じ回帰テスト / HTTPX 0.27.0 | 64 passed。検証launcherの事前importによるpytest assert-rewrite warningが1件あり、製品の警告ではない |
| Ruff | 成功 |
| mypy | 95 source filesで成功 |
| 公開API・文書契約 | 21 passed |
| ローカルv3全体・カバレッジ | 1 failed / 1685 passed / 17 skipped、679.33秒、88.19%。失敗の詳細は下記 |
| ローカル配布package smoke | wheel・sdistビルド成功、非editable installによるoffline smoke成功 |
| CI / 3 OS × Python 3.11〜3.14・3 OS package smoke | [run 38069118436](https://github.com/GlassLeaf/image_downloader/actions/runs/38069118436)、attempt 2で15/15 success。初回の失敗と再実行は下記 |

回帰テストではcookie読込み・snapshot・processor構築・output locks構築・service構築・logger構築・
registry組立てに失敗を注入する。同期／動作中loop、例外／中断、cleanup失敗、cleanup待機中の再中断、
反復失敗、別runtimeの遅延import、正常時の所有権移譲とidempotent closeを確認する。
module／finder登録の前後比較、実際のfile streamとclientのclosed状態、worker shutdown、
終了loop・スレッドの解放、cookie保存・通知の非実行をassertする。

再実行例：

```powershell
python -m pytest tests/v3/application/test_composition_cleanup.py -q
python -m ruff check .
python -m mypy src/image_downloader
python -m pytest tests/v3 -q --cov=image_downloader
python -m build --sdist --wheel
python tools/release_smoke.py
```

historical probeは修正前の問題をassertするため、修正後には失敗することがある。
修正後の合否判定には上記の通常回帰テストを使う。

## ローカル全体テストで残った既存の失敗

`test_workflow_history.py::test_independent_processes_merge_under_shared_history_lock`で、
子プロセスの`join(60)`後にも`exitcode`が`None`となり失敗した。
同じテストの単独再実行でも再現した（sandbox内64.83秒、sandbox外70.27秒）。
基準コミット`92a3d0c`を隔離したコピーで実行しても同じassertionが失敗した（67.09秒）。
そのためQS-013修正前にも存在する失敗と確認したが、子プロセスが終了しない根本原因は未確定である。
sandboxが原因と断定せず、ローカル全体が全件成功したとも判定しない。
該当テスト・履歴保存・interprocess lockの実装は今回変更していない。

ローカル17 skipはMailpit opt-inの12件と、実行環境のsymlink作成権限に依存する5件である。
srcおよびtests/v3の175ファイルについて、全体テスト開始後のSHA-256比較で変更がないことを確認した。
元のログ・カバレッジJSON・基準コピーはignore対象の`.runtime/qs013-verification/`に保存した。

## CIの確認結果

修正コミット`d7e6a1b`のquality workflowで、3 OS × Python 3.11〜3.14のテスト12件と
3 OSのpackage smokeを確認した。テストジョブではpip check・Ruff・mypyも成功した。
機械可読な集計は[qs-013-verification.json](qs-013-verification.json)に保存した。

| OS | 各Python版の結果 | coverage |
| --- | --- | --- |
| Ubuntu | 1690 passed / 13 skipped | 87.97〜88.04% |
| macOS | 1690 passed / 13 skipped | 87.97〜88.04% |
| Windows | 1691 passed / 12 skipped | 88.20〜88.26% |

CIの12 skipはMailpit opt-inである。Ubuntu/macOSにはWindows専用テストの1 skipが加わる。
初回はWindows 3.14の既存`test_cancel_or_timeout_distinguishes_interrupted_and_not_started[True]`が
`started.wait()`の5秒制限でTimeoutErrorとなった。他の14ジョブは初回成功した。
全ジョブ終了後、該当Windows 3.14ジョブを同一コミットのまま再実行し、1691 passed / 12 skippedで成功した。
最終のworkflow conclusionはsuccessである。再実行の成功だけからタイムアウトの根本原因は断定しない。
この既存テストとworkflow本体は今回変更していない。

この確認記録と集計の追記では、本体コード・回帰テストを変更していない。
