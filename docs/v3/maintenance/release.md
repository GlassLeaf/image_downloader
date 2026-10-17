<a id="release-preflight"></a>

# リリース前の確認

未リリースのCLI互換性の変更とJSONの移行例は、[CLI診断の移行](cli-diagnostics-migration.md)を参照してください。

リリース候補では次を実行し、失敗したら公開を止めます。

```powershell
python -m pytest -q --cov=image_downloader
python -m ruff check .
python -m mypy src/image_downloader
python -m pip check
python -m build --sdist --wheel
python tools/release_smoke.py
```

依存がインストール済みのオフライン環境では`python tools/release_smoke.py --offline`を使用できますが、依存の解決は検証しません。smokeでは、wheel・sdistの内容、編集可能モードを使わないwheelのインストール、`doctor --json`、同梱の署名済みプラグインのstrictロードを確認します。

- 公開成果物を検証済みのwheel・sdistと一致させ、`.local`、`.runtime`、プロファイルデータ、IDEファイル、履歴アーカイブを含めない。
- `pyproject.toml`の対応Python版と、Windows・Linux・macOSの組合せで品質検査・パッケージsmokeを確認する。
- プロジェクトのライセンス、第三者・同梱ソースの帰属表示、脆弱性の非公開報告の運用を公開前に決定する。
- リリースノートに、ベータ版と安定性、v1/v2プラグインとの非互換性、移行、設定の書き換えとバックアップ、必要なら旧版より小さな版番号へのインストール手順を記載する。
- strictでのinstall・trust・revoke・uninstall・doctorと、文書の契約・リンク検査をリリース候補で再実行する。
