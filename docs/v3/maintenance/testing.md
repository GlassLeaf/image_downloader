<a id="testing-and-documentation-contracts"></a>

# テストと文書の契約

<a id="testing-config-cli"></a>
<a id="testing-package-trust"></a>
<a id="testing-migration"></a>
<a id="testing-runtime-library"></a>
<a id="testing-release"></a>

v3のテストは、設定値の受理、CLIの振り分け・JSON・秘匿化、プラグインパッケージの信頼、ライフサイクル・認証・回復、通信・永続化の安全性、結果・例外の挙動、ソース形式の画像処理プラグインの例を確認します。

文書の契約テストでは、さらに次を確認します。

- ファサードの`__all__`と公開API一覧の順序。
- [公開シグネチャ一覧](../reference/api-signatures.md)の`api-contract`マーカー、引数の名前・種類・既定値、コルーチンかどうか、DTO・Pydanticのフィールド名、列挙値・リテラル値、`ERROR_CATALOG`。
- パーサーによるオプションの受理・効果・無効指定・拒否、およびJSONの構造。
- 旧資料と現行資料の記述ID、移管先マーカー、元資料のスナップショットにある根拠、`superseded`とした理由。
- 現行ドキュメント内のリンクを再帰的に検査。アーカイブは現行仕様の参照先に含めない。

Pydanticから継承したAPIと非公開名は、独自の安定メソッド一覧から除外します。公開挙動を変更する場合は、実装、正本の参照資料、契約マーカー、挙動テストを同時に更新してください。

## 多環境CIの確認と検証記録

品質検査はUbuntu・Windows・macOSとPython 3.11〜3.14の12組合せです。各ジョブでpip check、Ruff、mypy、全体pytestとcoverage下限75%を確認します。配布smokeは3 OSのPython 3.11でbuildと通常モードのsmokeを実行し、依存解決も検証します。

品質検査のJUnitとcoverage XMLは`.runtime/ci/`へ出力し、失敗時にもCI成果物へ保存します。成果物名はOS・Python版・run attemptを含み、保存期間は14日です。対象は2つのXMLだけで、作業ディレクトリや配布物全体を保存しません。インストール等でpytest開始前に失敗した場合は、レポート未生成の警告を出し、元のジョブ失敗を保持します。

各ジョブの「Record checkout identity」にはhead SHA、checkout SHA、run ID・attemptをJSONで記録します。PR実行ではcheckoutがmerge commitになることがあるため、対象headと区別してください。最新変更に対応する品質検査12ジョブと配布smoke3ジョブのすべてがsuccessであることを確認し、ジョブ単位の未実行・キャンセル・スキップを成功として数えません。

pytestの`-ra`とJUnitでスキップ理由を確認します。OS条件・任意Mailpitによる件数差を許容し、環境間で同じ件数を要求しません。結果は[リファクタリングの進捗](refactoring-progress.md)とDraft PRに記録し、後から新しい変更をpushした場合は最新headのCIを確認します。

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
