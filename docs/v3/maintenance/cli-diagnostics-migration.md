<a id="cli-diagnostics-changes-unreleased"></a>

# CLI診断の変更（未リリース）

CLIの構文、コマンドへの適用可否、競合、不正な引数値による失敗は、`argument_error`、`exception="ArgumentError"`、問題を具体的かつ安全に説明する`message`を返します。設定ファイルによる失敗は引き続き`configuration_error`です。どちらも終了コードは2です。従来の引数エラーコードを返す互換モードはありません。

通常のエラーは標準エラー出力へ`error [code]: message`の形式で表示します。`--json`を指定すると、標準出力には`{"error": {...}}`オブジェクトを一つだけ出力します。必須フィールドは従来どおり`code`、`reason`、`exception`、`message`、`operation`です。`reason`は安定した分類文、`message`は具体的な問題の説明です。任意の`details`は必須の`kind`と、該当する`option`、`options`、`command`、`argument`、`choices`、`field`、`source`、`line`、`column`を持ちます。オプション値、入力全体、絶対パス、YAMLの抜粋、認証情報は含めません。構文解析でコマンドが確定していなければ`operation="cli"`、確定していれば正規の最上位コマンド名を使います。未知の外部例外には、安全な汎用メッセージを使います。

この出力を利用する側では、引数エラーを扱う分岐を次のように変更してください。

```python
if result["error"]["code"] == "argument_error":
    show_input_problem(result["error"]["message"])
elif result["error"]["code"] == "configuration_error":
    show_configuration_problem(result["error"]["message"])
```

オプションの省略表記は受理しません。`--j`は`--json`へ、従来の省略表記`--list`は`--list-updated-urls`へ置き換えてください。`help [COMMAND [SUBCOMMAND ...]]`は、JSONを有効にしていてもテキストのヘルプを表示します。未知の単語を暗黙的にダウンロードURLとして扱うこともありません。ホストを持つ絶対HTTP(S) URLを使用してください。成功時のJSON、既存の終了コード、効果がないと文書化されたオプション、従来のCookieオプションの契約は維持します。

詳細は[CLI参照](../reference/cli.md)と[公開エラー一覧](../reference/library-api.md#api-errors)を参照してください。

オプション・選択肢・設定フィールドの追加は[定義の拡張](cli-definition-extensions.md)を参照してください。
