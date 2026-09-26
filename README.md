# image-downloader

local-directory plugin を使う、非同期画像ダウンローダーです。現行拡張仕様は
**local plugin API v3**。v1/v2 の entry point、class descriptor、wheel sidecar、旧設定
tree は発見・互換読込しません。

対応環境はPython 3.11～3.14、およびWindows、Linux、macOSです。
公開ベータの版番号は`0.0.0.1b0`です。旧プロトタイプ`0.3.0`をインストール済みの場合は
版番号が小さくなるため、`python -m pip install --force-reinstall .`で入れ替えてください。

## Quick start

```powershell
python -m pip install .

# 設定ファイルの固定位置、OS 標準 root、現在の有効値を確認
image-downloader config path
image-downloader config explain --json

# Optional: create the fixed user configuration before the first state-changing operation.
image-downloader config init `
  --data-root "D:\Images\image-downloader" `
  --plugin-root "C:\Users\you\AppData\Local\image-downloader\plugins"

image-downloader "https://example.test/gallery"

# 設定、plugin catalog、profile data path を診断
image-downloader doctor --json
```

通常実行は CWD の `app.yaml` を読みません。`--config` を省略すると platformdirs の固定
user config だけを読み、未作成でも package 同梱 baseline と OS 標準の data/plugin root で継続します。
設定がない状態で download/update、cookie 操作、plugin install/trust/revoke/uninstall を開始すると、主操作の前に
同梱 `app.yaml` 基準の全設定と OS 標準の absolute data/plugin root を fixed user config へ自動保存します。
CLI option は保存しませんが、完全 snapshot のため以後の bundled default 更新は自動反映されません。既存の
user-managed config に旧キーまたは static schema の未知キーがあれば、状態変更操作の開始前に無表示で削除
します。`doctor`、`config path`、`config explain`、plugin list は read-only で設定 file や directory を
作成・書換えしません。固定 location は
`config path`、最終値と各値の由来は `config explain` で確認できます。`storage.data_root` と
`plugins.root` は `null` または absolute path、CLI の `--data-root` と `--plugin-root` は absolute path
のみです。

```powershell
python -m image_downloader "https://example.test/gallery" `
  --config "C:\work\image-downloader\app.yaml" `
  --plugin-root "C:\work\image-downloader-plugins"
```

## plugin の信頼と操作

```powershell
# absolute source directory を staged install し、署名/tree/class を検証して trust
image-downloader plugin install "C:\build\gallery-plugin" `
  --plugin-root "C:\ProgramData\image-downloader\plugins"

image-downloader plugin list --plugin-root "C:\ProgramData\image-downloader\plugins" --json
image-downloader plugin revoke com.example.gallery `
  --plugin-root "C:\ProgramData\image-downloader\plugins"

image-downloader plugin uninstall com.example.gallery `
  --plugin-root "C:\ProgramData\image-downloader\plugins"
```

詳細は [v3 ドキュメント](docs/v3/README.md) を参照する。

## Documentation

- [CLI と設定](docs/v3/configuration-and-cli.md)
- [実行ライフサイクル](docs/v3/execution-lifecycle.md)
- [plugin 作者ガイド](docs/v3/plugin-author-guide.md)
- [配布・信頼・運用](docs/v3/distribution-and-operations.md)
- [ライブラリ API](docs/v3/library-api.md)
- [完全 API 参照](docs/v3/api-reference.md)
- [検証・移行・リリース](docs/v3/testing-migration-and-release.md)
- [plugin template](examples/plugin-v3-template)
- [全ドキュメント索引](docs/README.md)

## Development

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
```

repository の現行 test target は `tests/v3/` である。v2 資料は履歴参照用で、v3 の
受入仕様ではない。
