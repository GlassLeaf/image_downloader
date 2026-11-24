<a id="release-preflight"></a>

# リリース前の確認

未リリースのCLI互換性の変更とJSONの移行例は、[CLI診断の移行](cli-diagnostics-migration.md)を参照してください。

次回リリースの互換性メモ（2026-11-25）：`cookie browser-import DOMAIN`および
`CookieStore.import_browser()`のdomainはCookieを利用するホストとして扱います。
明示Chrome・自動検出とも、従来の完全一致限定から、PSL境界内で適用可能な親Domain Cookieも
既定で取り込む動作へ変わります。親host-only Cookie、兄弟・子ドメイン、public suffixや
別テナントへの拡張は許可しません。新しい許可フラグはありません。
プラグインinstallは所有を確認した予約領域を種類別ディレクトリ内に作成します。
未解決の公開記録が残るrootへの変更は、[手動復旧](../reference/plugin-package.md#management-policy)まで拒否します。

今回のリファクタリングの完了状況は[進捗と検証記録](refactoring-progress.md)、CIの対象と結果の確認方法は[多環境CI](testing.md#多環境ciの確認と検証記録)を参照してください。Draft PRとCI確認の完了は、マージや公開配布の実施を意味しません。

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

<a id="release-psl"></a>

## 認証用Public Suffix Listの更新

endpointのワイルドカードと `operation.site_domain` は、`src/image_downloader/configuration/public_suffix_list.dat` の公式PSLスナップショットを使う。依存publicsuffix2の古い内蔵リストや、通信制限用の簡易フォールバックは使わない。アプリ実行時にデータを取得しない。

リリース準備時、ネットワークを利用できる環境で次を実行する。公式配布元に対する取得は一日一回以下にする。

```powershell
python tools/update_public_suffix_list.py
python -m pytest tests/v3/configuration/test_endpoint_origins.py tests/v3/transport/test_endpoint_patterns.py -q
```

更新ツールは公式URLから取得し、ライセンス・VERSION・COMMIT・ICANN/PRIVATE両領域と代表的な境界を確認してから置き換える。上流のMPL 2.0表記とバージョンヘッダーを維持する。PSL差分をレビューし、パッケージを再構築する。wheel・sdist両方への同梱はrelease smokeで確認する。新しいPRIVATE境界により既存ワイルドカードが拒否される場合は、具体originへの変更をリリースノートに記載する。
