<a id="image-downloader-api-v3-documentation"></a>

# image-downloader API v3 ドキュメント

<a id="docs-entry"></a>

このディレクトリはAPI v3の現行仕様の正本です。文書を目的別に分けています。設定値、型、入出力、例外は**リファレンス**、フックの順序・回数・並行性は**実行ライフサイクルの解説**を正本とします。チュートリアルや操作手順では規則を再定義せず、対応する箇所を参照します。

| 読者・目的 | 読み始める場所 |
| --- | --- |
| 初めてCLIを実行する | [CLIチュートリアル](tutorials/first-download.md) |
| 初めてプラグインを作る | [プラグインチュートリアル](tutorials/first-plugin.md) |
| プラグインを生成・単体実行・模擬ダウンロードする | [プラグイン開発支援CLI](how-to/plugin-devtools.md) |
| Pythonから組み込む | [ライブラリのチュートリアル](tutorials/embedded-download.md) |
| よくある運用・実装判断を確認する | [CLI・ライブラリ・プラグインのFAQ](how-to/faq.md) |
| 設定・プロファイルを運用する | [設定の操作手順](how-to/configure-profiles.md) |
| Cookie・秘密情報・プラグインを管理する | [安全な運用の手順](how-to/operate-securely.md) |
| 資格情報を登録し、バックアップ・鍵更新を行う | [資格情報保管庫](reference/credential-vault.md) |
| 認証・ページ分割・短命URLを実装する | [動的URLの実装手順](how-to/dynamic-urls-and-auth.md) |
| CLIとJSONを自動処理する | [CLIリファレンス](reference/cli.md) |
| YAML・HTTP・Cookie・更新状態を確認する | [設定リファレンス](reference/configuration.md)と[実行時動作リファレンス](reference/runtime-behavior.md) |
| プラグインのパッケージとフックを実装する | [プラグインパッケージリファレンス](reference/plugin-package.md)と[フックリファレンス](reference/plugin-hooks.md) |
| 安定したPython APIを使う | [ライブラリAPIリファレンス](reference/library-api.md)、[シグネチャ一覧](reference/api-signatures.md)、[API一覧](reference/api-contract-inventory.md) |
| ログを組み込む | [ログリファレンス](reference/logging.md) |
| ライフサイクルと設計上の境界を理解する | [実行ライフサイクル](explanation/execution-lifecycle.md) |
| 移行・リリース・文書保全・未決の運用方針を担当する | [保守資料の索引](maintenance/README.md) |

<a id="stability-and-implementation-truth"></a>

## 安定性と実装との整合性

`image_downloader`、`image_downloader.config`、`image_downloader.runtime`、`image_downloader.security`、`image_downloader.cli`、`image_downloader.observability.logging`の`__all__`は、API v3の安定した公開契約です。それ以外のモジュールは内部実装であり、直接importする使い方は互換性の対象外です。

リファレンスのシグネチャ、DTOのフィールド、既定値、非同期かどうか、例外を、実装と照合するテストで検証します。実行時の型情報だけでは判断できない仕様は、対応する動作テストと[旧文書との対応台帳](maintenance/legacy-coverage.md)で追跡します。

<a id="historical-material"></a>

## 履歴資料

[v3再編前のスナップショット](../../archive/docs/legacy/v3-pre-reorg/README.md)と[v3分類整理前のスナップショット](../../archive/docs/legacy/v3-pre-taxonomy/README.md)は、移管を監査するための履歴資料です。現行仕様や実装方法として引用せず、この索引からリンクした正本を使ってください。
