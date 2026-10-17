<a id="how-to-configure-profiles-and-inspect-effective-values"></a>

# プロファイルを設定し、有効な値を確認する

1. `image-downloader config path`で、固定のユーザー設定と既定のルートを確認します。
2. `config init`でメイン設定を作り、`config profile init NAME`でプロファイルの設定レイヤーを作ります。
3. `config explain --host HOST --json`で、有効値、設定レイヤー、値の由来を確認します。
4. 状態を変更する操作の前に、バックアップと`doctor --host HOST --json`を実行します。

プロファイル・サイトのレイヤーに書けない起動設定用フィールド、マージ順、`rewrite_user_layers`の書換えによる副作用、リンク・再解析ポイントの扱いは[設定リファレンス](../reference/configuration.md#config-layers)を参照してください。
