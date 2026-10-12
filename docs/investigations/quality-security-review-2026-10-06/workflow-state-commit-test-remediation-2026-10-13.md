# State commit期限テストのタイミング依存修正

## 発見と原因

QS-011のpush CI [38265444425](https://github.com/GlassLeaf/image_downloader/actions/runs/38265444425)
初回のWindows / Python 3.11 jobで、既存の
test_deadline_during_state_commit_finishes_transactionがstarted.wait()のFalseにより失敗した。
同じhead 49e950aef70afc44be5bcb8226be56230e745885で失敗jobを再実行すると成功し、
PR CIと統合後main CIも12 test job・3 package smokeが全成功した。

旧テストは、追加周回の0.5秒の期限までにstate commitが開始することを前提にしていた。
retry planの準備へ0.7秒の遅延を入れると、この前提が崩れて同じassertion失敗を再現した。
本体はstate commitに入る前に正しくWorkflowRetryTimeoutErrorを返している。
CIにおける個別の遅延源までは特定していない。Cookie検証、COMODO、OSのopen停止を
今回のassertion失敗の原因として確定する証拠はない。

## 修正

QS-011とは別のcodex/fix-workflow-state-commit-test-timingでテストだけを変更する。
基点はmain c4138e080030ea8ab324faa062bac4354264eefe。本体の期限・commit処理は変更しない。

実際のasyncio.Timeoutを使い、state commitの開始barrierに到達してから期限をrescheduleする。
期限が発火し、workflowにキャンセルが届いても、commitを止めている間はtaskが完了しないこと、
解放後は成功結果を含むWorkflowRetryTimeoutErrorが返ること、次のworkflowで完了項目が
再選択されないことを確認する。sleepによる準備速度の仮定を除き、失敗時にもbarrierを解放し、
taskをcancel/gatherして残さない。

## 再現用プローブ

probes/workflow_state_commit_deadline.pyをpytest pluginとして使用する。
各反復は別のtmp_pathを使い、外部通信や実credential storeは使用しない。
準備遅延0は通常条件、0.7は旧0.5秒期限を超える条件である。

```powershell
$env:PYTHONPATH = (Resolve-Path 'docs/investigations/quality-security-review-2026-10-06/probes').Path
python -m pytest -q -p workflow_state_commit_deadline --repeat-state-commit=20 --state-commit-preparation-delay=0.7 tests/v3/application/test_workflow_retry.py -k deadline_during_state_commit
```

修正前の単独試験は6.78秒でassertion失敗、修正後の同条件単独試験は6.15秒で成功。
修正後の遅延付き20反復と既存workflow retry/loggingは126 passed（147.49秒）。
初期のプローブ実行にはplugin検索失敗と、出力なしで外部60秒期限に達した1回があった。
これらは原因の再現結果に含めず、出力が得られた試験と区別する。停止の内訳は未確定である。

全体テスト終了後の通常20反復は20 passed（40.18秒）、v3全体は2116 passed / 17 skipped
（704.79秒）、coverage 88.66%。Ruff・mypy（96 source files）・pip checkも成功した。
raw/state-commit-fix-*へ結果を保存した。環境・source/test SHA-256はverification.jsonに記録する。
17 skipはローカルで作成できないsymlink 5件とopt-in Mailpit 12件である。
この資料を含むコミットに対するPR・mainの全15 jobと対象SHAはPRへ追記する。

通常20反復を全体テストと並行して実行した試験は19 passed / 1 failed。
失敗回は10秒の開始監視に達し、初回周回（round_number=0）が未完了だった。
保存済み履歴はtimed_out=false、cancelled=trueで、retryの0.5秒期限の競合は起きていない。
初回周回の具体的な遅延源はこの記録では特定できないため、本体の不具合や保護製品へ帰属させない。
並行実行時の失敗ログと、全体終了後の単独反復を別々に保存する。
