# QS-001 修正確認記録

修正日：2026-10-06（Asia/Tokyo）。修正前の基準：`aa20dc5b6b90ce7dd5250368114f75d53f3fb173`。

入口・相対import先のソースを一度読み、検証済みSHA-256に一致する同じbytesをコンパイルする。
`strict`・`warn`・`bypass-catalog`は受理した宣言tree、`bypass-signature`はcatalogと照合した同じ実測treeを使用する。
`off`・`bypass-all`は照合を迂回する現在の方針を維持し、各import時点のソースを読む。
いずれのmodeもプラグインのキャッシュを実行・生成せず、既存キャッシュを削除しない。

相対importは、activeなプラグイン名前空間専用のfinderで処理する。
入れ子・namespace package・遅延import、任意拡張子の入口、文字コード指定、元のmodule metadataとデータ参照を維持する。
未宣言・変更済みソース、プラグイン内のソースなしbytecode・native extensionは実行前に拒否する。
通常の外部依存ライブラリのimportと、公開API・DTOのfield・署名形式・catalog schemaは変更しない。
信頼済みコードによる明示的な任意コード実行まで制限するsandboxは対象外である。

## 回帰テスト

[test_verified_source_loading.py](../../../tests/v3/plugins/test_verified_source_loading.py)は修正後の安全な挙動を期待する。
元の[behavior_probe.py](behavior_probe.py)と[raw記録](raw/probes.stdout)は当時の問題挙動をassertする監査資料として保存する。
修正後に元のQS-001プローブが失敗することは、未署名コードの実行が止まった結果である。

回帰テストは次を確認する。

- 6つの検証modeと、timestamp・unchecked hash・checked hashの3種類の改変キャッシュ。
- 入口・helper・package初期化ファイルのキャッシュを無視し、正当なソースを実行すること。
- 検証後の変更、未宣言helper、遅延import前の変更、reload時の変更を実行前に拒否すること。
- content pinが受理したtreeを再収集せず保持し、読込み後の差替えでも検査済みbytesだけを実行すること。
- 同梱plugin、module metadata、リソース参照、公開DTO、複数runtime、installのclass検査と失敗時の登録解放。

## 実行結果

ローカル環境はWindows 10・Python 3.11.4。

| 検証 | 結果 | 記録 |
| --- | --- | --- |
| v3全体・カバレッジ付き | 1,534成功、17スキップ、606.31秒 | [pytest stdout](raw/qs-001-pytest-v3.stdout) |
| statement + branch coverage | 87.89%、既存の75%基準を達成 | [coverage JSON](raw/qs-001-coverage.json) |
| QS-001回帰テスト | 81成功、symlinkの1件スキップ | [focused stdout](raw/qs-001-focused.stdout) |
| Ruff・mypy | 合格、本体94ファイルを型検査 | [Ruff](raw/qs-001-ruff.stdout)、[mypy](raw/qs-001-mypy.stdout) |

全体の17スキップは、opt-in Mailpitの12件と、環境でsymlinkを作成できない既存4件・追加1件。
これはsymlink経路が成功したという意味ではない。hard linkの拒否は実行・確認した。
[実行環境・コマンド・本体SHA-256](raw/qs-001-checks-metadata.json)も保存する。

Ubuntu・Windows・macOSとPython 3.11〜3.14の12構成、および3 OSのpackage smokeは既存CIで検証する。
CIの実行結果は[専用ブランチのActions](https://github.com/GlassLeaf/image_downloader/actions?query=branch%3Acodex%2Ffix-qs-001-source-loading)と、このブランチのPRを参照する。
