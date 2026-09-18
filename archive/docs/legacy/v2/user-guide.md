# image-downloader 0.3.0 利用ガイド（v2・履歴資料）

> 現行の設定 tree、CLI、plugin 管理は [v3 設定と CLI](../../../../docs/v3/configuration-and-cli.md) が正本です。この文書の `plugins`、`plugin_catalog`、旧 profile path は使用できません。

## 1. installと起動

Python 3.11以上が必要です。

```powershell
python -m pip install .
image-downloader "https://example.test/gallery"
```

source checkoutからは次も使用できます。

```powershell
py app.py "https://example.test/gallery"
python -m image_downloader "https://example.test/gallery"
```

内蔵generic HTML pluginは、外部pluginが一致しない通常のHTTP/HTTPS URLを取得し、`<title>`と`<img src>`を1章のmanifestへ変換します。認証、JavaScript描画、site API、短命署名URLが必要なsiteは外部v2 pluginを使用してください。

## 2. CLI command

```text
image-downloader [URL] [options]
image-downloader doctor [options]
```

| option | 用途 |
|---|---|
| `--config PATH` | main YAML。明示したpathがなければ設定エラー。省略時の`app.yaml`がない場合は内蔵既定値を使う |
| `--profile NAME` | その実行で選択するprofile |
| `--no-console-log` | 章summaryのconsole出力を無効化 |
| `--existing-file MODE` | `overwrite`, `skip`, `rename`, `error`を一時上書き |
| `--image-format FORMAT` | `JPEG`, `PNG`, `WEBP`を一時上書き |
| `--list-updated-urls` | downloadせずupdate snapshotを比較する |
| `--json` | 通常結果またはdoctor結果をJSON出力する |
| `--debug-allow-unverified-plugins` | development profileのstrictだけを、その実行でoff相当にする |
| `--export-cookies PATH` | profile Cookieをpassphrase暗号化してexport |
| `--import-cookies PATH` | export containerをprofileへmerge |
| `--import-browser-cookies DOMAIN` | optional dependencyでbrowser Cookieをimport |

Cookie操作3種は相互排他で、URLと同時指定できません。`--json`で機械可読なdownload結果だけを標準出力へ出したい場合は、章console logとの混在を避けるため`--no-console-log`も指定してください。

### download

```powershell
image-downloader "https://example.test/gallery"
image-downloader "https://example.test/gallery" --existing-file rename --image-format WEBP
image-downloader "https://example.test/gallery" --json --no-console-log
```

通常downloadのJSONは次のshapeです。

```json
{
  "saved": ["C:\\...\\0001.jpeg"],
  "skipped": [],
  "failures": [
    {
      "kind": "fetch",
      "exception": "DownloaderError",
      "message": "HTTP request failed: 404"
    }
  ]
}
```

manifest等を含む完全結果が必要な場合はCLI JSONではなくPython APIの`DownloadResult`を使用してください。

### update確認

```powershell
image-downloader "https://example.test/list" --list-updated-urls
```

標準出力にはadded/changed URLを1行ずつ出します。removed URLは出さず、標準エラーへ件数を表示します。

```text
https://example.test/new-item
```

```text
updated: 1, removed: 2
```

pluginは現在の完全snapshotを返す必要があります。状態は選択profileの`state/updates.json`にatomic保存されます。

### doctor

```powershell
image-downloader doctor
image-downloader doctor --config .\app.yaml --json
```

doctorはprofile/download/log/state/cookie directoryを安全に作成し、catalog、site plugin、image processorの検出結果を表示します。通常表示と`--json`の両方で、application/libraryのversionとroot directory、実際のplugin root、最終app設定、doctor時のruntime override、読み込み済みpluginのpublisher/version/source/trust/config情報を表示します。設定中のheader、proxy、secretなど資格情報になり得る値は`<redacted>`になります。

```json
{
  "healthy": true,
  "application": {"version": "0.3.0", "root_directory": "C:\\...\\site-packages"},
  "library": {"version": "0.3.0", "root_directory": "C:\\...\\site-packages\\image_downloader"},
  "plugin_root": "C:\\...\\site-packages\\plugins",
  "verification": "strict",
  "paths": {
    "profile": "C:\\...\\profiles\\default",
    "downloads": "C:\\...\\profiles\\default\\downloads",
    "cookie": "C:\\...\\profiles\\default\\cookie",
    "logs": "C:\\...\\profiles\\default\\logs",
    "state": "C:\\...\\profiles\\default\\state"
  },
  "loaded_plugins": [
    {
      "id": "core.generic-html",
      "publisher": "core",
      "version": "3",
      "source": "builtin",
      "verification": "builtin"
    }
  ],
  "plugins": [
    {
      "name": "core.generic-html",
      "source": "builtin",
      "loaded": true,
      "detail": "",
      "warning": false
    }
  ]
}
```

`healthy`はcomposition全体が完了したことを表します。個別pluginが`loaded=false`でもdiagnosticとして列挙され、doctor自体は成功し得ます。監視では`healthy`だけでなく全`plugins[].loaded`と`warning`も確認してください。

## 3. 終了コード

| code | 意味 |
|---:|---|
| 0 | 成功。全件skip、空manifest成功、update/doctor成功も含む |
| 1 | 保存/skipが1件もない画像失敗、一般`DownloaderError`、OS error |
| 2 | config不正、明示config欠落、CLI引数組合せ不正 |
| 3 | 認証、秘密値、Cookie暗号鍵・復号の失敗 |
| 4 | 実行時plugin選択・契約違反等の`PluginError` |
| 5 | 1件以上の保存またはskipと、1件以上の画像failureが混在 |
| 130 | Ctrl+Cによる中断 |

strict catalogそのものの欠落・path不正・schema不正は2です。個別外部pluginの署名不一致等はregistryでfailed diagnosticとなり、そのpluginはskipされます。対象URLの実行が最終的にplugin errorになった場合は4です。

## 4. 設定の読込順

`--config`が指すmain YAMLを既定値へdeep mergeした後、後勝ちで次を読みます。

1. `<config-dir>/profiles/<profile>/conf/app.yaml`
2. `<config-dir>/sites/site_global.yaml`
3. `<config-dir>/profiles/<profile>/conf/site_global.yaml`
4. URL hostがある場合、`<config-dir>/sites/<safe-host>/<safe-host>.yaml`
5. URL hostがある場合、`<config-dir>/profiles/<profile>/conf/<safe-host>.yaml`

`safe-host`はhostnameの`.`と`:`を`_`へ置換した値です。mapping同士は再帰mergeし、それ以外の値とlist/tupleは後のlayerで置換します。

profile名は英数字、`_`、`-`だけです。`--profile`はmain設定の`profile.default`より優先されます。

## 5. 設定リファレンス

全modelは未知fieldを拒否し、作成後は変更できません。

### profile

| key | 既定値 | 制約・意味 |
|---|---|---|
| `profile.default` | `default` | 英数字、`_`、`-` |
| `profile.root` | `profiles` | platformdirs=falseならconfig基準の相対path。絶対pathと`..`は禁止 |
| `profile.use_platformdirs` | `false` | trueならOS user data directoryを使用 |

platformdirs=falseの保存profile rootは`<config-dir>/<profile.root>/<profile.default>`です。download rootは必ずこのprofile root内に制限されます。

### output

| key | 既定値 | 制約・意味 |
|---|---|---|
| `output.root` | `downloads` | profile内のdownload directory |
| `output.directory_format` | `%NUM%_%TITLE%_%SUBTITLE%` | 章directory名template |
| `output.filename_format` | `%NUM%.%EXT%` | 画像file名template |
| `output.existing_file` | `overwrite` | `overwrite`, `skip`, `rename`, `error` |
| `output.image_format` | `JPEG` | `JPEG`, `PNG`, `WEBP` |

placeholderは`%NUM%`、`%TITLE%`、`%SUBTITLE%`、filenameでは`%EXT%`も使えます。番号は4桁ゼロpaddingです。OS上で危険な文字、予約名、末尾space/dotは安全な1 path componentへ正規化されます。template内のslashで階層を増やすことはできません。

同じ章directoryは再利用し、`log.log`へ追記します。既存file policyは画像ごとに適用されます。

- `overwrite`: 同じ画像pathをatomic replaceする。
- `skip`: download/処理後のallocate時に既存なら保存せず正常skipにする。
- `error`: `FileExistsError`。continue=trueならSAVE failure、falseならoperation停止。
- `rename`: stemへ`_1`～`_9999`を付ける。章directory名は変更しない。

衝突予約は同じserviceの1 operation内だけです。別processとの同時書込みによる論理的なname競合は調停しません。ただし各保存自体はtemporary fileからatomic replaceします。

### media

| key | 既定値 | 値 |
|---|---|---|
| `media.input_validation` | `content_type` | `content_type`, `decode`, `both` |
| `media.content_type_mismatch` | `accept` | `accept`, `error` |

`content_type`は`text/*`、`application/json`、`application/xml`を明白な非画像として拒否します。HTMLが`text/html`ならこの規則に含まれます。`decode`はPillow decode/verify、`both`は両方です。

mismatch=`error`では、明示された`image/*` MIMEとPillow検出MIMEが違う場合に拒否します。`accept`でも最終変換でdecodeできないdataは失敗します。

### network

| key | 既定値 | 制約・意味 |
|---|---:|---|
| `max_concurrency` | 8 | operation内の全request/画像上限、1以上 |
| `max_chapter_concurrency` | 3 | 同時章job、1以上 |
| `host_max_concurrency` | null | host別上限。nullならmax_concurrency |
| `site_max_concurrency` | null | 現実装ではURL hostをkeyにしたsite上限。nullならmax_concurrency |
| `timeout_seconds` | 30 | 個別timeoutのfallback、0より大 |
| `connect_timeout_seconds` | null | nullならtimeout_seconds |
| `read_timeout_seconds` | null | nullならtimeout_seconds |
| `write_timeout_seconds` | null | nullならtimeout_seconds |
| `pool_timeout_seconds` | null | nullならtimeout_seconds |
| `max_retries` | 3 | 初回を含む総transport試行、1以上 |
| `max_retry_wait_seconds` | 30 | exponential backoff上限、0より大 |
| `max_auth_retries` | 1 | requestごとのrefresh上限、0以上 |
| `request_interval_seconds` | 0 | service全体のrequest開始最小間隔 |
| `max_connections` | 8 | HTTP connection pool上限 |
| `max_keepalive_connections` | 20 | keep-alive pool上限、0以上 |
| `http2` | true | httpx HTTP/2 |
| `follow_redirects` | true | redirect追従 |
| `proxy` | null | httpx proxy URL |
| `headers` | User-Agent | 全requestのclient既定header |

retry statusは429、500、502、503、504です。待機は0.25秒から指数的に増え、設定上限で止まります。401/403およびpluginが検出したHTTP 200認証失敗はtransport retry後のAuthFlowで扱います。

### execution

| key | 既定値 | 意味 |
|---|---:|---|
| `continue_on_error` | true | 通常の画像FETCH/PROCESS/SAVE失敗をoutcomeとして継続 |
| `allow_empty_manifest` | false | 章が0件のmanifestを成功にする |

`allow_empty_manifest`は「画像が0件」一般ではなく`DownloadManifest.chapters == ()`を判定します。画像0件の章は常に通常章として処理されます。

認証、設定、plugin contract、storage安全性違反はcontinue対象外です。

### logging

| key | 既定値 | 意味 |
|---|---|---|
| `logging.console.enabled` | true | 章`log.log`と同じsummaryをconsoleにも出す |
| `logging.safe_query_parameters` | `[]` | log表示を許可する公開query key |
| `logging.safe_fragment_parameters` | `[]` | log表示を許可する公開fragment key |

許可key名は英字で始まる最大64文字の英数字/`_.-`です。token、key、secret、signature、auth、cookie、password、session等を含むcredential-like名は設定時に拒否します。

章logは次の形式を追記します。download/saveは画像index順で、失敗詳細は章終了時にkind別集計します。

```text
---
url: https://example.test/gallery
title: Chapter title
---
download: https://cdn.example.test/1.png
save: 0001_Chapter/0001.jpeg
error: image fetch error (1)
download: https://cdn.example.test/2.png
exception: DownloaderError
detail: HTTP request failed: 404
done
```

profileの`logs/debug.log`にはmask済みdebug recordを出します。operation/plugin選択、plugin hook、HTTP request・response・retry・auth refresh、画像ごとの取得・変換・保存、通知deliveryを記録します。pluginは標準の`logging.getLogger(__name__)`で出したrecordも同じdebug logへ収集できます。完全なresponse body、認証header、form値、Cookieは記録しません。

### security

| key | 既定値 | 意味 |
|---|---|---|
| `security.plugin_catalog` | `plugins.catalog.json` | config directory基準のcatalog相対path |
| `security.plugin_verification` | `strict` | `strict`, `warn`, `off` |

strictはcatalog必須です。warnはcatalogが利用できれば検証し、不備はwarningとして構造的v2 pluginを読み込みます。offはcatalog/暗号検証を省略します。すべてのmodeでv2 sidecarの構造確認は必須で、v1またはsidecarなしpluginはimport前に拒否します。

詳細は[plugin package・署名ガイド](plugin-package-template.md)を参照してください。

### plugin設定

```yaml
plugins:
  com.example.gallery:
    config:
      locale: ja-JP
    secrets:
      api_token: production-token
```

`config`はpluginへ読み取り専用mappingとして渡します。`secrets`のvalueは秘密値本体ではなくreferenceで、英数字、`_`、`.`、`-`だけです。解決規則は[プラグインAPIリファレンス](plugin-api-reference.md)を参照してください。

### image processor

```yaml
image_processors:
  chain:
    - com.example.autocrop
    - com.example.watermark
```

IDは英数字で始まる最大128文字の英数字/`_.-`で、重複不可です。記載順に画像ごとに新しいinstanceを作って実行します。登録のないIDは実行時`ConfigurationError`です。

### notification

```yaml
notification:
  enabled: true
  methods: [desktop]
  notify_on: [fetch_error, save_error, auth_error]
  routes: {}
  desktop: {}
  email:
    smtp_host: ""
    smtp_port: 465
    use_tls: true
    from: ""
    to: []
    username: ""
    credential_service: image-downloader.smtp
```

desktop通知には`image-downloader[notify]`、browser Cookie importには`image-downloader[browser-cookies]`が必要です。emailのusernameを指定した場合、passwordは`IMAGE_DOWNLOADER_SMTP_PASSWORD`、次にOS keyringのconfigured service/usernameから読みます。

`methods`とrouteの値は不変tupleとして保持されますが、desktop/email deliveryはいずれも処理します。画像単位のfetch/save失敗はrun終了時にまとめて通知され、認証・plugin・設定・run全体の失敗も対応するeventとして通知対象になります。

## 6. Cookieの保存と移送

runtimeのCookie jarに未expired Cookieがある場合、close時にprofileの`cookie/cookies.enc`へAES-256-GCMで保存します。32-byte暗号鍵はOS keyring service `image-downloader.cookie-key`へ、Cookie file pathのSHA-256をaccount名として保存します。

別環境へはpassphrase付きexport containerを使います。

```powershell
image-downloader --export-cookies .\backup.cookies
image-downloader --import-cookies .\backup.cookies
```

passphraseはpromptで入力します。export keyはscrypt（N=16384, r=8, p=1）で導出されます。importは未expired Cookieを既存profile jarへmergeします。

browserから取り込む場合はoptional dependencyを導入します。

```powershell
python -m pip install ".[browser-cookies]"
image-downloader --import-browser-cookies example.test
```

Cookie操作でもservice compositionを行うため、strict設定では有効なcatalogが必要です。

## 7. Python APIから使う

```python
import asyncio
from pathlib import Path

from image_downloader import AppConfig, RuntimeComposer


async def main() -> None:
    config = AppConfig.model_validate(
        {
            "security": {
                "plugin_catalog": "plugins.catalog.json",
                "plugin_verification": "strict",
            },
            "output": {"existing_file": "rename", "image_format": "WEBP"},
        }
    )
    async with RuntimeComposer(config, Path.cwd()).compose() as service:
        result = await service.run("https://example.test/gallery")
        if result.failures:
            for failure in result.failures:
                print(failure.kind, failure.exception_type, failure.message)


asyncio.run(main())
```

結果DTO、例外、update APIの詳細は[公開APIリファレンス](api-reference.md)を参照してください。
