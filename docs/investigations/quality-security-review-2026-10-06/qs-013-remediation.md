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
| v3全体・カバレッジ | 実行中。完了結果を追記する |
| 配布package smoke | 実行中。完了結果を追記する |
| CI / 3 OS × Python 3.11〜3.14・3 OS package smoke | 修正コミットで確認後、run・結果を追記する |

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
