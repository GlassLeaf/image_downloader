# ライブラリ API

## 安定した import

API v3 で互換性を約束する import surface は <code>image_downloader</code>、<code>image_downloader.config</code>、<code>image_downloader.runtime</code>、<code>image_downloader.security</code>、<code>image_downloader.cli</code>、<code>image_downloader.observability.logging</code> の <code>__all__</code> だけである。内部 submodule、private name、<code>__all__</code> 外の名前は implementation detail である。

[完全 API 参照](api-reference.md) は全 export、constructor、public method、DTO field、例外を定義する。この文書は使用方法と operation boundary を補足する。

## compose と resource lifetime

~~~python
from pathlib import Path
from image_downloader import RuntimeComposer, load_application_config

config = load_application_config(Path("app.yaml"), require_config=True)
async with RuntimeComposer(
    config,
    config_root=Path(".").resolve(),
    plugin_root=Path("plugins").resolve(),
).compose() as service:
    result = await service.run("https://example.invalid/work/1")
~~~

<code>RuntimeComposer</code> は config root と plugin root を明示的に受け、<code>compose()</code> は resource を所有する <code>DownloadService</code> を返す。<code>close()</code> は実行中 operation の完了を待ち、gateway、processor、logger を閉じ、cookie delta を永続化する。close 後の <code>run</code> と <code>check_updates</code> は <code>RuntimeError</code> を送出する。

<code>run</code> と <code>check_updates</code> は service 内で直列化される。複数 URL を並列化する呼出側は service instance を URL ごとに分け、同じ output/config/plugin root を共有する場合の lock と rate limit を理解したうえで行う。

## 結果と失敗

<code>DownloadResult</code> は source URL、manifest、chapter results を持つ。<code>saved_files</code> と <code>skipped_files</code> の path は absolute string である。画像との相関が必要なら <code>chapters -&gt; outcomes -&gt; image/failure</code> をたどる。

<code>ImageFailure</code> 自体には image URL、章、画像番号はない。<code>DownloadResult.failures</code> は failure を平坦化した convenience property であり、その相関を失う。失敗を処理・再試行・報告する利用者は <code>ImageOutcome</code> を利用する。

<code>continue_on_image_error=true</code> の通常の fetch/process/save failure は結果に入る。認証、設定、plugin、storage safety、inter-process lock、fail-fast の失敗は <code>DownloadResult</code> を返さず例外で終了する。<code>check_updates</code> も完全な <code>UpdateResult</code> か例外のいずれかであり、部分 result は返さない。<code>asyncio.CancelledError</code> は捕捉して結果に変換しない。

## configuration

<code>resolve_application_config()</code> は解決済み config と layer/origin metadata を返す。<code>load_application_config()</code> はその config だけを返す。<code>apply_overrides()</code> は immutable <code>AppConfig</code> から新しい config を作る。<code>rewrite_user_layers=True</code> は user YAML を書き換え得るため、library 使用時には明示的に選択する。
