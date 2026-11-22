# 認証結果のスナップショット

架空のモックAPIで、ユーザーIDと有効期間をコアへ返す独立サンプルです。
`AuthFlow`に状態取得メソッドや別の共有ストアを追加せず、`AuthLease.public_data`と
`context.auth.snapshot()`を使います。`auth_snapshot`対応本体が必要です。

`POST /api/login`は`access_token`、`user_id`、`expires_in`を返します。
トークンは認証フロー内に保持し、公開するのは`user_id`だけです。
`inspect()`は`ensure_ready()`後にユーザーIDを読み、`GET /api/gallery?user_id=...`を送ります。
fixtureは最初の画像要求に401を返します。コアの再認証で世代は1から2、ユーザーIDは
`user-1`から`user-2`、有効期間は3600秒から7200秒へ更新されます。
これはモックの仕様で、実サイトの認証項目を表すものではありません。

```powershell
# リポジトリのルートで実行。実通信なしで取得・再認証・画像保存を試験する
image-downloader-dev simulate .\examples\plugin-v3-auth-snapshot `
  https://auth-snapshot.example.test/gallery --mode replay `
  --fixture .\examples\plugin-v3-auth-snapshot\fixture.yaml

# inspectと画像要求構築を同じセッションで確認する
image-downloader-dev check .\examples\plugin-v3-auth-snapshot `
  --fixture .\examples\plugin-v3-auth-snapshot\fixture.yaml
```

fixture内の資格情報はダミーです。詳細ログと保存方針は既存仕様のままです。
`snapshot()`は同期・通信なしで、その瞬間の世代を不変DTOとして返します。
返された古いスナップショットは再認証後も変化しません。最新値は取り直してください。
サーバーでの追加確認が必要なら、実際の確認APIを使う`verify()`を実装してから
`await context.auth.ensure_ready(check=True)`を使います。このサンプルの`verify()`は既定のUNKNOWNです。

配布用の署名済みmanifestは含めていません。配布する場合は`plugin-metadata.json`を使って
通常どおり署名・インストールしてください。手順は[署名サンプル](../plugin-v3-metadata-bridge/README.md#sign-and-install)、
APIと移行方針は[移行ガイド](../../docs/v3/how-to/structured-data-and-auth-snapshot-migration.md)を参照してください。
