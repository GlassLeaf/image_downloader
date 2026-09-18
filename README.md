# image-downloader

署名済み local-directory plugin を使う、非同期画像ダウンローダーです。現行拡張仕様は
**local plugin API v3**。v1/v2 の entry point、class descriptor、wheel sidecar、旧設定
tree は発見・互換読込しません。

対応環境はPython 3.11～3.14、およびWindows、Linux、macOSです。
公開ベータの版番号は`0.0.0.1b0`です。旧プロトタイプ`0.3.0`をインストール済みの場合は
版番号が小さくなるため、`python -m pip install --force-reinstall .`で入れ替えてください。

## Quick start

```powershell
python -m pip install .

# Optional: persist chosen roots in a user configuration. All paths are absolute.
image-downloader config init "C:\Users\you\AppData\Local\image-downloader\conf\app.yaml" `
  --data-root "D:\Images\image-downloader" `
  --plugin-root "C:\Users\you\AppData\Local\image-downloader\plugins" --yes

image-downloader "https://example.test/gallery"

# 設定、plugin catalog、profile data path を診断
image-downloader doctor --json
```

通常実行は CWD の `app.yaml` を読みません。`--config` を省略すると platformdirs の固定
user config を読み、未作成なら package 同梱 `app.yaml` を使う。そこに root がない場合は、
対話端末で data root と plugin root を absolute path として入力する。保存確認で `Y` を選ぶと
user config に保存し、`N` なら保存せずその実行だけに適用する。`--data-root` と
`--plugin-root` を両方指定すれば、入力せずその実行だけに absolute root を与えられる。`--config`、
`storage.data_root`、`plugins.root`、`--data-root`、`--plugin-root` はすべて absolute path
である。console command と `python -m image_downloader` は同じ user config と plugin root を
使用する。

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
```

詳細は [v3 ドキュメント](docs/v3/README.md) を参照する。

## Documentation

- [設定と CLI](docs/v3/configuration-and-cli.md)
- [plugin 作者ガイド](docs/v3/plugin-author-guide.md)
- [信頼・配布・運用](docs/v3/trust-and-operations.md)
- [ライブラリ API](docs/v3/library-api.md)
- [テストと移行](docs/v3/testing-and-migration.md)
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
