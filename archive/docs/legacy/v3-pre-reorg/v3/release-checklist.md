# 公開ベータ確認事項

対象版は `0.0.0.1b0`。GitHub での公開を想定し、PyPI への登録は行わない。
旧プロトタイプ `0.3.0` より版番号が小さいため、移行時は README の
`--force-reinstall` 手順を案内する。

## ローカルで確認すること

```powershell
python -m pytest -q --cov=image_downloader
python -m ruff check .
python -m mypy src/image_downloader
python -m pip check
python -m pip install build
python -m build --sdist --wheel
python tools/release_smoke.py
```

`release_smoke.py` は wheel と sdist の内容、非 editable wheel install、
`doctor --json`、署名済み同梱 plugin の strict 読込を確認する。
依存パッケージを既に導入したオフライン環境では
`python tools/release_smoke.py --offline` を使用できる。
このオフライン実行は依存解決の検証にはならない。

## 公開を止める条件

- プロジェクトのライセンスを所有者が選定し、ルートに正しい文面を置く。
  未選定のまま公開しない。
- 脆弱性の非公開報告先または GitHub の private vulnerability reporting
  の運用方法を決め、利用者に案内する。
- 同梱する第三者コードと `plugin-sources/generic-css-selector` の
  ライセンス・表示条件を確認する。
- GitHub 上で Python 3.11～3.14 × Windows／Linux／macOS の
  品質 matrix と、3 OS の package smoke が成功することを確認する。
- 公開する wheel・sdist がローカルで検証したものと一致し、
  `.local`、`.runtime`、`profiles`、IDE 管理ファイルを含まないことを確認する。
- 公開ページにベータ版であること、v1/v2 plugin 非互換、
  利用時の注意点と移行手順を記したリリースノートを添える。

この checkout には `.git` がないため、GitHub の remote、CI 結果、
公開対象ファイルはこの環境だけでは確認できない。
