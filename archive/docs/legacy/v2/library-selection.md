# ライブラリ選定 v2（履歴資料）

| 領域 | ライブラリ | v2での用途 |
|---|---|---|
| HTTP | httpx | `RequestGateway`、HTTP/2、timeout、接続pool |
| 画像 | Pillow | decode検証、正規化、最終保存形式 |
| 設定 | PyYAML + Pydantic | レイヤー読込、strict/frozen `AppConfig` |
| パス | platformdirs | 任意のprofile data root |
| 暗号 | cryptography | AES-GCM、Scrypt、Ed25519 plugin署名 |
| 秘密値 | keyring | Cookie暗号鍵とplugin／SMTP秘密値 |
| HTML | 標準`html.parser` | 内蔵generic HTML fallback |
| 通知 | desktop-notifier、標準`email`/`smtplib` | 任意のdesktop／SMTP通知 |

pluginへは`PluginExecutionContext`、`RequestPort`、`SecretProvider`、`TransformContext`だけを公開します。httpx client、Cookie jar、filesystem、logger、通知サービス、schedulerはcore内部です。

`RequestGateway`が輸送retry、認証回復、署名request回復を順に適用します。`max_retries`は初回を含む試行総数です。`OutputAllocator`は公開operation内のメモリ予約だけを保証し、プロセス間ロックは行いません。
