# CLI と設定

この文書は日常操作と機械可読出力の正本である。プログラムからの config API は [完全 API 参照](api-reference.md) を参照する。

## 設定 layer

設定は package defaults、固定 user config、明示 config、profile layer、site layer、runtime override を順に解決する。<code>ResolvedApplicationConfig.layers</code> は適用 layer、<code>origins</code> は各有効値の由来を示す。

<code>resolve_application_config(..., rewrite_user_layers=True)</code> と <code>load_application_config(..., rewrite_user_layers=True)</code> は obsolete または unknown key を user YAML から除去し得る。これは読み取り専用 API ではない。library 利用で書換えを望まない場合は既定値 false を維持する。CLI の初期設定・migration path は必要に応じて true を使う。

## command と option の適用先

| command | URL | --json | config/profile/site/override | download 固有 | plugin 管理 |
| --- | --- | --- | --- | --- | --- |
| <code>download URL</code> | 必須 | 可 | 可 | fallback、cookie import/export、list-updated-urls | 不可 |
| <code>doctor</code> | 不可。--host は可 | 可 | 可 | 不可 | 不可 |
| <code>config path/explain/init</code> | 不可 | command ごと | 必要なものだけ | 不可 | 不可 |
| <code>plugin list/install/trust/revoke/uninstall</code> | 不可 | command ごと | plugin root と verification | 不可 | 可 |

parser は command に不適切な option を validation error として拒否する。正確な programmatic entry point は <code>image_downloader.cli</code> を参照する。

## 終了コード

| 値 | 定数 | 意味 |
| --- | --- | --- |
| 0 | EXIT_SUCCESS | operation は成功 |
| 1 | EXIT_FAILURE | 一般 failure、または全画像が失敗 |
| 2 | EXIT_CONFIGURATION | 設定・引数 validation failure |
| 3 | EXIT_AUTHENTICATION | 認証または secret failure |
| 4 | EXIT_PLUGIN | plugin discovery、verification、execution failure |
| 5 | EXIT_PARTIAL | 少なくとも一画像を保存または skip したが image failure がある |

## JSON output

<code>download --json URL</code> の成功 payload:

~~~json
{
  "saved": ["/absolute/or/configured/output/path.jpeg"],
  "skipped": ["/absolute/or/configured/output/path.jpeg"],
  "failures": [{
    "kind": "fetch|process|save",
    "exception": "ImageDownloaderError subclass name",
    "message": "notification-safe reason",
    "code": "stable_reason_code",
    "reason": "notification-safe reason",
    "output_path": "safe relative path or null",
    "response_url": "masked URL or null",
    "http_status": 403,
    "transport": "failed|response_received|response_limit_exceeded|redirect_rejected|completed"
  }]
}
~~~

<code>download --list-updated-urls --json URL</code> は <code>{"updated_urls": ["..."], "removed": 0}</code> を返す。<code>doctor --json</code> は healthy、application/library、plugin_root、verification、configuration、paths、loaded_plugins、plugins を含む診断 object を返す。config/plugin command の JSON は、その command が報告する path、resolved setting、または plugin mutation 結果を返す。

すべての command failure は JSON 時に次の形で標準出力へ一件だけ出力する。

~~~json
{
  "error": {
    "operation": "download|doctor|config|plugin",
    "exception": "safe exception name",
    "code": "stable_reason_code",
    "reason": "safe reason",
    "message": "safe message",
    "response_url": null,
    "http_status": null,
    "output_path": null
  }
}
~~~

URL、secret、path は privacy rules により redaction され得る。JSON schema の field は追加互換を前提とし、利用側は未知 field を許容する。
