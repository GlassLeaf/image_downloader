<a id="how-to-operate-cookies-secrets-and-plugins-safely"></a>

# Cookie・秘密情報・プラグインを安全に運用する

- ブラウザーのセッションは`image-downloader cookie browser-import DOMAIN`で、プロファイルのCookie保存先へ取り込みます。ブラウザー対応は任意の依存パッケージです。
- エクスポート・インポートでは、パスフレーズを対話的に扱います。Cookieの本文、パスフレーズ、ブラウザーの記録、秘密情報の参照を、JSONやログへ出力しないでください。
- pluginのinstall・trust・revoke・uninstallの前に、プラグインルートをバックアップし、manifestのID、発行者、種別、バージョン、フィンガープリント、ツリーのダイジェストを確認します。
- 自動処理で変更操作を確認する場合だけ、`--yes`を使います。

暗号化・マージ・ロック・保証範囲は[実行時動作リファレンス](../reference/runtime-behavior.md#runtime-cookies)、カタログのポリシーは[プラグインパッケージリファレンス](../reference/plugin-package.md#plugin-catalog)、CLIの出力は[CLIリファレンス](../reference/cli.md#cli-json)を正本とします。

資格情報は`credential plugin set`・`credential smtp set`でプロファイルごとに登録します。
保管庫の初期化・暗号化バックアップ・空ルートへの復元・鍵更新と中断再開は
[資格情報保管庫](../reference/credential-vault.md)を参照してください。旧環境変数・旧keyring項目は使用しません。
