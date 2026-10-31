# image-downloader

ローカルディレクトリのプラグインを使う非同期画像ダウンローダーです。現行の拡張仕様は**ローカルプラグインAPI v3**です。v1・v2のエントリーポイント、クラス記述子、wheelの補助ファイル、旧設定ツリーは検出・互換読込みしません。

対応環境はPython 3.11～3.14、およびWindows、Linux、macOSです。
公開ベータの版番号は`0.0.0.1b0`です。旧プロトタイプ`0.3.0`をインストール済みの場合は
版番号が小さくなるため、`python -m pip install --force-reinstall .`で入れ替えてください。

<a id="quick-start"></a>

## クイックスタート

```powershell
python -m pip install .

# 設定ファイルの固定位置、OS 標準 root、現在の有効値を確認
image-downloader config path
image-downloader config explain --json

# 任意：初めて状態を変更する操作の前に、固定のユーザー設定を作成
image-downloader config init `
  --data-root "D:\Images\image-downloader" `
  --plugin-root "C:\Users\you\AppData\Local\image-downloader\plugins"

image-downloader "https://example.test/gallery"

# 更新チェックから取得まで。失敗時は標準で10分後に1回再試行
image-downloader workflow "https://example.test/feed"

# 周回回数・間隔・初回処理後からの最大時間を指定（単位は秒）
image-downloader workflow "https://example.test/feed" `
  --workflow-retries 2 --workflow-retry-delay 30 --workflow-retry-timeout 300

# 保存済みログとworkflowの選択URLに対応する実ファイルを検査
image-downloader verify logs "D:\Images"
image-downloader verify workflow RUN_ID --output-dir "D:\Images" --json

# 設定・プラグインカタログ・プロファイルのデータパスを診断
image-downloader doctor --json
```

通常実行では、作業ディレクトリの`app.yaml`は読みません。`--config`を省略すると、platformdirsで決まる固定のユーザー設定だけを読みます。未作成でも、パッケージ同梱の基本設定とOS標準のデータ・プラグインのルートで続行します。
設定がない状態でdownload・update、Cookie操作、pluginのinstall・trust・revoke・uninstallを開始すると、主操作の前に、同梱の`app.yaml`に基づく全設定とOS標準の絶対パスのデータ・プラグインルートを、固定のユーザー設定へ自動保存します。CLIオプションは保存しません。完全なスナップショットを保存するため、以後の同梱既定値の更新は自動反映されません。利用者が管理する既存設定に旧キーや静的スキーマの未知キーがあれば、状態変更操作の前に無表示で削除します。`doctor`、`config path`、`config explain`、plugin listは読取り専用で、設定ファイルやディレクトリを作成・書換えしません。
固定の保存先は`config path`、最終値と各値の由来は`config explain`で確認できます。`storage.data_root`と`plugins.root`は`null`または絶対パス、CLIの`--data-root`と`--plugin-root`は絶対パスだけを指定できます。

```powershell
python -m image_downloader "https://example.test/gallery" `
  --config "C:\work\image-downloader\app.yaml" `
  --plugin-root "C:\work\image-downloader-plugins"
```

<a id="plugin-の信頼と操作"></a>

## プラグインの信頼と操作

```powershell
# 絶対パスのソースディレクトリを一時配置し、署名・ツリー・クラスを検証して信頼登録
image-downloader plugin install "C:\build\gallery-plugin" `
  --plugin-root "C:\ProgramData\image-downloader\plugins"

image-downloader plugin list --plugin-root "C:\ProgramData\image-downloader\plugins" --json
image-downloader plugin revoke com.example.gallery `
  --plugin-root "C:\ProgramData\image-downloader\plugins"

image-downloader plugin uninstall com.example.gallery `
  --plugin-root "C:\ProgramData\image-downloader\plugins"
```

詳細は[v3ドキュメント](docs/v3/README.md)を参照してください。

保存前の画像検査は既定で`media.input_validation: both`です。ORIGINAL保存でも全フレームを
読み込み、空本文・不正画像・Pillowで検査できない形式を拒否します。既存設定に明示された
`content_type`は自動変更しません。`image-downloader config explain --json`で有効値を確認し、
必要に応じて`both`へ変更してください。対応形式と互換設定は
[保存前の画像検査](docs/v3/reference/configuration.md#image-input-validation)を参照してください。

<a id="documentation"></a>

## ドキュメント

- [CLI・ライブラリ・プラグインのFAQ](docs/v3/how-to/faq.md)
- [CLIリファレンス](docs/v3/reference/cli.md)
- [設定リファレンス](docs/v3/reference/configuration.md)
- [資格情報・Cookieの保管庫と登録・バックアップ・鍵更新](docs/v3/reference/credential-vault.md)
- [実行ライフサイクル](docs/v3/explanation/execution-lifecycle.md)
- [プラグインパッケージ・フックリファレンス](docs/v3/reference/plugin-package.md)
- [配布・信頼・運用](docs/v3/reference/runtime-behavior.md)
- [ライブラリAPI](docs/v3/reference/library-api.md)
- [公開APIの全一覧](docs/v3/reference/api-contract-inventory.md)
- [検証・移行・リリース](docs/v3/maintenance/testing.md)
- [既存v3文書との対応台帳](docs/v3/maintenance/legacy-coverage.md)
- [プラグインのテンプレート](examples/plugin-v3-template)
- [画像メタデータをサイトとプロセッサー間で受け渡すプラグインの組合せ](examples/plugin-v3-metadata-bridge)
- [全ドキュメントの索引](docs/README.md)

<a id="development"></a>

## 開発

プラグインの生成・単体呼出し・模擬ダウンロードには
[`image-downloader-dev`](docs/v3/how-to/plugin-devtools.md)を利用できます。

```powershell
image-downloader-dev init "C:\work\my-gallery"
image-downloader-dev load "C:\work\my-gallery"
```

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
```

このリポジトリの現行テストの対象は`tests/v3/`です。v2資料は履歴参照用であり、v3の受入仕様ではありません。
