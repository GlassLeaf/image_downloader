# image-downloader API v3

この directory が API v3 の唯一の正本である。履歴資料は [v3 pre-reorg snapshot](../../archive/docs/legacy/v3-pre-reorg/README.md) にあり、現行仕様としては使用しない。

| 読者 | 最初に読む文書 | 目的 |
| --- | --- | --- |
| 利用者・運用者 | [CLI と設定](configuration-and-cli.md) | 実行、設定 layer、JSON 出力、終了コード |
| plugin 作者 | [plugin 作者ガイド](plugin-author-guide.md) | plugin package、型、設定、実装範囲 |
| HTTP/API を扱う plugin 作者 | [実行ライフサイクル](execution-lifecycle.md) | hook の順序、動的 URL、認証、並行性 |
| 組込み利用者 | [ライブラリ API](library-api.md) | compose、run、結果、例外、resource lifetime |
| API 利用者・保守者 | [完全 API 参照](api-reference.md) | 全 stable export、DTO field、署名、例外 |
| 配布・管理者 | [配布・信頼・運用](distribution-and-operations.md) | manifest、署名、catalog、ログ、診断 |
| 保守・公開担当 | [検証・移行・リリース](testing-migration-and-release.md) | 契約テスト、移行、公開判定 |

## 読み方

実行時の hook の順序、回数、失敗処理は [実行ライフサイクル](execution-lifecycle.md) が唯一の正本である。各 hook の型定義、設定 schema、公開 class の引数と戻り値は [完全 API 参照](api-reference.md) が正本である。ほかの文書は同じ仕様を複写せず、用途と実例に集中する。

## 安定性

<code>image_downloader</code>、<code>image_downloader.config</code>、<code>image_downloader.runtime</code>、<code>image_downloader.security</code>、<code>image_downloader.cli</code>、<code>image_downloader.observability.logging</code> の <code>__all__</code> は API v3 の stable contract である。内部 module を直接 import する利用は互換性対象ではない。

## 最小の組込み例

~~~python
from pathlib import Path

from image_downloader import RuntimeComposer, load_application_config

config = load_application_config(Path("app.yaml"), require_config=True)
service = RuntimeComposer(
    config,
    config_root=Path(".").resolve(),
    plugin_root=Path("plugins").resolve(),
).compose()

try:
    result = await service.run("https://example.invalid/work/1")
finally:
    await service.close()
~~~

<code>DownloadService</code> は一 instance 内で operation を直列化する。必ず <code>close()</code> するか async context manager として使用する。
