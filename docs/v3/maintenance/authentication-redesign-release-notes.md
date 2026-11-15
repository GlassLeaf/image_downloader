# 認証状態管理の更新（2026-11-16）

開発中のプラグインAPI番号3を維持した破壊的更新。旧認証フックとの互換層は提供しない。

- `initialize`、同期 `apply`、三値の `classify_response`、`verify`、`refresh(auth_context)` に責務を分離した。
- 認証更新の再入を検出し、並列要求で初期化・更新・検証を共有する。確認不能時は専用エラーで停止する。
- 追加ホストと確認頻度は作者YAML・プロファイルの共通設定に宣言する。`allowed_origins` は廃止した。
- `RequestSpec.auth_required` は `auth: AuthMode` に置き換えた。認証不要サイトの401/403は通常のHTTPエラーとして扱う。
- 非冪等要求の認証再送には `retry_non_idempotent=True` が必要。

プラグイン作者は[認証API移行手順](../how-to/authentication-migration.md)に沿ってコード・設定を更新し、再署名する。利用者は更新版を `image-downloader plugin install` で再インストールする。インストール済みファイルやカタログを直接変更しない。
