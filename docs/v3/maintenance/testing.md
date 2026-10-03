# Testing and documentation contracts

<a id="testing-config-cli"></a>
<a id="testing-package-trust"></a>
<a id="testing-migration"></a>
<a id="testing-runtime-library"></a>
<a id="testing-release"></a>

The v3 suite checks configuration acceptance, CLI dispatch/JSON/redaction, plugin package trust, lifecycle/auth/recovery, transport/persistence safety, result/exception behavior, and source processor examples.

Documentation contract tests additionally check:

- facade `__all__` and inventory order;
- [public signature index](../reference/api-signatures.md) の visible `api-contract` marker、parameter names/kinds/defaults、coroutine status、DTO/Pydantic field names、enum/literal values、and `ERROR_CATALOG`;
- parser option acceptance/effect/no-op/rejection and JSON payload shapes;
- legacy/current claim IDs, destination markers, source snapshot evidence, and `superseded` rationale;
- recursive current-documentation links. Archive is not a current-link target.

Pydantic inherited API and private names are excluded from the custom stable-method inventory. A change to public behavior must update the implementation, canonical reference, contract marker, and behavior test together.

## Mailpitによるメール通知の実機テスト

`tests/v3/observability/test_mailpit_integration.py` は実SMTP送信と別プロセスのCLIを検証する。
通常のpytest実行ではスキップする。MailpitをSMTP `localhost:1025`、管理画面/API
`http://localhost:8025` で起動し、認証・TLSなしの状態で実行する。
メール通知だけを使うため、デスクトップ用の `notify` extra は不要。

```powershell
$previousMailpitOptIn = $env:IMAGE_DOWNLOADER_TEST_MAILPIT
try {
    $env:IMAGE_DOWNLOADER_TEST_MAILPIT = "1"
    python -m pytest tests/v3/observability/test_mailpit_integration.py -m mailpit -v -s
} finally {
    if ($null -eq $previousMailpitOptIn) {
        Remove-Item Env:IMAGE_DOWNLOADER_TEST_MAILPIT -ErrorAction SilentlyContinue
    } else {
        $env:IMAGE_DOWNLOADER_TEST_MAILPIT = $previousMailpitOptIn
    }
}
```

このリポジトリの既存検証環境を使う場合、`python` を
`.runtime/quality-venv/Scripts/python.exe` に置換する。
明示的に有効化した状態でSMTPまたはAPIへ接続できなければ、テストは失敗する。

12ケースで、日本語の件名・本文、正常保存、404と部分成功、不正画像のdecode失敗、
既存ファイル競合、複数失敗の集約と秘匿、通知無効・対象外・経路指定、SMTP 550拒否時の
主処理継続、ワークフロー再試行後の成功・失敗通知を確認する。
CLI終了コードは成功0、部分成功5、全件失敗1。全件失敗を5とする当初計画の期待値は、
既存CLI仕様に合わせて訂正した。HTTPの内部リトライは1試行、ワークフローは追加1回、待機0秒。
画像失敗を結果として返すワークフローは、保存0件でもURL単位のJSON状態が `partial` となる。
その場合も全体終了コードは1であり、テストは保存0件・失敗1件を明示的に確認する。

各ケースは一意の `.test` 宛先と一時設定・保存先を使用する。
テスト用HTTP/SMTPサーバーはループバックの空きポートで起動し、終了時に停止する。
Mailpit受信待ちは最大10秒、件数確認後も2秒間監視して重複を検出する。
既存メールの削除、Mailpit設定変更、メールrelease/転送は行わない。

実行レポートは `.runtime/mailpit/<UTC日時>-<実行ID>/` にケース別JSONとして保存する。
合否、検証項目、宛先、メールID・本文、CLI終了コード・JSON・診断ログを含む。
失敗したケースも記録する。APIから取得したテストメールは既読になる。
テストメールは残すため、管理画面でレポートの宛先を検索し、正常・部分成功・再試行後成功の
代表メールを確認できる。API仕様は [Mailpit API documentation](https://mailpit.axllent.org/docs/api-v1/) を参照する。
