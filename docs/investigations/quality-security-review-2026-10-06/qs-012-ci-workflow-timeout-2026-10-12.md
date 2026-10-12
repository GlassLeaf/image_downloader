# QS-012コミット後のCI失敗：workflowログ試験の時刻依存

## 対象とCI結果

- 対象コミット：`1cb31d98be6250a255148c07a7e79c860c6b0882`。
- [quality run 38241315328](https://github.com/GlassLeaf/image_downloader/actions/runs/38241315328)。15 jobのうち14件成功、`test (ubuntu-latest, 3.12)`だけ失敗。
- Python 3.12.15。pytestは `1 failed, 1825 passed, 17 skipped`。
- 失敗したのは `test_cancel_or_timeout_distinguishes_interrupted_and_not_started[True]` の `await asyncio.wait_for(started.wait(), 5)`。本体が返したSMTP例外ではなく、テスト側の処理開始待機が `TimeoutError` になっている。
- [CI jobの失敗部分](raw/qs-012-ci-failure.log)、[job結果一覧](raw/qs-012-ci-failure.json)。

## 原因と再現

このテストでは初回にAのmanifest検査を失敗させ、再試行のAの検査を無期限に待たせ、その間に期限切れとなった場合の `url_interrupted` を検証している。元のテストは再試行全体に0.2秒の実時間制限を設定した。一方、検査開始の合図を待つテスト側の制限は5秒だった。

本体の期限は、再試行待機のログ、更新確認、状態・plan保存を含む再試行全体に適用される。Aの検査に到達する前に0.2秒を消費すると、本体は正しく `WorkflowRetryTimeoutError` を返し、開始合図は永久に設定されない。そのため、テスト側だけが5秒後に失敗する。CIのログには工程ごとの所要時間がないため、CI上で期限を消費した個別工程は特定できない。

Windows / Python 3.11.4のローカル環境で、再試行の `WorkflowLogWriter.plan()` に0.3秒の遅延を注入すると同じ行で失敗した。本体の最後の記録は `timed_out=True`、`cancelled=False`、`exit_code=5` で、再試行のAの `url_started` がなかった。本体が停止すべき期限に停止したことと、テストの到達条件が成立しなかったことを分離できる。

該当テストと `application/workflow.py` はQS-012コミットでは変更されていない。テストの最終変更は `afd7581`（workflow plan・進捗ログの追加）。今回のSMTP変更によるworkflow期限の変更は確認されず、既存テストの時刻依存が表面化したと判断する。

## 修正

変更対象は `tests/v3/application/test_workflow_logging.py` の該当テストだけとする。

1. テストが指定した0.2秒の `asyncio.timeout()` だけを捕捉し、期限未設定の実際の `asyncio.Timeout` を使用する。他の期限は従来の関数へ委譲する。
2. Aの検査が開始して待機した合図を確認した後、`Timeout.reschedule(loop.time())` で期限を発火させる。期限切れは次のloop反復で起こり、本体のキャンセル処理・例外変換・ログ処理を通る。
3. `expired()`、`WorkflowRetryTimeoutError`、`url_interrupted`、`timed_out` / `cancelled`、ユーザーキャンセル時のBの未開始・attempt数という元の期待値を確認する。
4. 待機・完了確認には5秒の試験用制限を維持する。途中でテストが失敗しても `finally` で所有taskをキャンセル・回収し、未回収例外や実行中taskを残さない。

実装は [Python公式のTimeout仕様](https://docs.python.org/3.11/library/asyncio-task.html#asyncio.Timeout.reschedule) に基づく。本体の期限の開始位置・実時間設定・公開API・SMTP処理は変更しない。実時間の期限そのものは既存のworkflow retryテストでも検証する。

## 検証

プッシュ前の検証環境はWindows 10 build 19045 / Python 3.11.4。反復はpytest fixtureを各回作り直し、各回の一時ディレクトリとruntimeを分離した。通信は既存MockTransport、小さな画像を使用し、再現用の遅延は0.3秒、外部期限は各反復群300秒。外部期限で終了した試験はない。

| 条件 | 結果 | 記録 |
|---|---|---|
| 修正前、再試行plan保存に0.3秒の遅延 | 期待した失敗を再現、1 failed | [stdout](raw/qs-012-ci-before-slow.stdout) |
| 修正後、同じ遅延 | キャンセル・期限切れとも成功、2 passed | [stdout](raw/qs-012-ci-after-slow.stdout) |
| 修正後、通常条件で各20回 | 40 passed | [stdout](raw/qs-012-ci-repeat-normal-verified.stdout) |
| 修正後、遅延条件で各10回 | 20 passed | [stdout](raw/qs-012-ci-repeat-slow-verified.stdout) |
| workflow logging・retry全体 | 107 passed | [stdout](raw/qs-012-ci-workflow-focused.stdout) |
| 保存した公開probeで遅延条件を再確認、各10回 | 20 passed | [stdout](raw/qs-012-ci-published-probe.stdout) |
| Ruff / mypy / pip check | 全件成功 | [コマンド・環境・hash](raw/qs-012-ci-verification.json) |

最初の反復用スクリプトはWindowsの子プロセス起動に必要なmain保護を欠いていたため、その試験結果は採用していない。正式な反復試験はpytest pluginによるfixtureのparameter化に変更した。

3 OS × Python 3.11〜3.14およびpackage smokeの確認には、この修正をプッシュしたコミットのquality runを使用する。この記録のローカル結果はプッシュ前の確認時点を示す。

## 公開probeの実行

[workflow_logging_timeout.py](probes/workflow_logging_timeout.py)は該当テストだけを反復・遅延させる。通常のv3実行には登録されない。リポジトリのルートで、例えば次のように実行する（PowerShell）。

```powershell
$env:PYTHONPATH = "docs/investigations/quality-security-review-2026-10-06/probes"
python -m pytest -q tests/v3/application/test_workflow_logging.py::test_cancel_or_timeout_distinguishes_interrupted_and_not_started -p workflow_logging_timeout --repeat-workflow-interruption 10 --delay-retry-plan 0.3
```

遅延なしの比較は `--delay-retry-plan 0` を使用する。反復数は1〜100回、遅延は有限値の0〜3秒に制限する。上記記録の試験では別プロセスの監視から300秒の外部期限も設定した。
