# マスターキー方式への切替

資格情報とCookieを、データルートごとのマスターキーで保護するファイル保管庫へ変更しました。OSストアの通常登録数は保管庫あたり1件です。

- サイト・SMTP資格情報はプロファイル別です。新しい`credential plugin`・`credential smtp` CLIで各プロファイルに登録してください。
- 資格情報の環境変数と旧keyring項目は使用しません。旧資格情報・Cookie・設定の自動移行・削除は行いません。
- 新Cookieは`cookies.v2.enc`です。旧`cookies.enc`を保持したまま空のセッションから始めます。必要ならブラウザーから再取込するか、旧版のパスフレーズ付きexportを明示importしてください。
- SMTPは`notification.email.credential_reference`で新しい登録を選択します。旧`username`・`credential_service`は互換項目として設定に残ります。
- `RuntimeSecrets`と`CookieStore`の直接構築には`store`引数が必須です。依存関係を直接組み立てる`_RuntimeDependencies`にも`credentials`が必要です。`RuntimeComposer`とプラグインの`context.secrets.get(name)`は同じ利用方法です。
- `--data-root`を変えると資格情報の取得先も変わります。独立した複製は新しい`vault backup/restore`を使ってください。
- `vault rotate-key`は本文の暗号文を維持してデータ鍵を保護し直します。中断時には`vault rotate-key --resume`で再開してください。
- 破損・鍵欠損のあるCookie保存先をimportで上書きする動作は廃止しました。

保存場所、入力上限、バックアップ・復元・鍵更新の操作と障害時の扱いは[資格情報保管庫](../reference/credential-vault.md)を参照してください。
