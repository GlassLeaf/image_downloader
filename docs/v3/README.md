# image-downloader API v3 documentation

<a id="docs-entry"></a>

この directory は API v3 の現行正本である。文書は目的別に分かれる。設定値、型、入出力、例外は **Reference**、hook の順序・回数・並行性は **Execution lifecycle explanation** だけを正本とする。tutorial/how-to は規則を再定義せず、その anchor を参照する。

| 読者・目的 | 読み始める場所 |
| --- | --- |
| 初めて CLI を実行する | [CLI tutorial](tutorials/first-download.md) |
| 初めて plugin を作る | [Plugin tutorial](tutorials/first-plugin.md) |
| Python から組み込む | [Library tutorial](tutorials/embedded-download.md) |
| 設定・profile を運用する | [Configuration how-to](how-to/configure-profiles.md) |
| cookie、secret、plugin を管理する | [Operations how-to](how-to/operate-securely.md) |
| 認証、pagination、短命 URL を実装する | [Dynamic URL how-to](how-to/dynamic-urls-and-auth.md) |
| CLI と JSON を自動化する | [CLI reference](reference/cli.md) |
| YAML、HTTP、cookie、update state を確認する | [Configuration reference](reference/configuration.md) と [runtime behavior reference](reference/runtime-behavior.md) |
| plugin package と hook を実装する | [Plugin package reference](reference/plugin-package.md) と [hook reference](reference/plugin-hooks.md) |
| stable Python API を使う | [Library API reference](reference/library-api.md)、[signature index](reference/api-signatures.md)、[API inventory](reference/api-contract-inventory.md) |
| logging を組み込む | [Logging reference](reference/logging.md) |
| ライフサイクルと設計境界を理解する | [Execution lifecycle](explanation/execution-lifecycle.md) |
| 移行、release、文書保全、未決の運用方針を担当する | [Maintenance index](maintenance/README.md) |

## Stability and implementation truth

`image_downloader`、`image_downloader.config`、`image_downloader.runtime`、`image_downloader.security`、`image_downloader.cli`、`image_downloader.observability.logging` の `__all__` は API v3 の stable contract である。その他の module は内部実装であり、直接 import は互換性対象ではない。

Reference の signature、DTO field、default、async 性、例外は実装照合テストで検証する。挙動を reflection だけで判断できない claim は、対応する behavior test と [coverage ledger](maintenance/legacy-coverage.md) で追跡する。

## Historical material

[v3 pre-reorg snapshot](../../archive/docs/legacy/v3-pre-reorg/README.md) と [v3 pre-taxonomy snapshot](../../archive/docs/legacy/v3-pre-taxonomy/README.md) は移管監査用の履歴資料である。現在の仕様や実装方法には引用せず、この index からリンクした正本を使用する。
