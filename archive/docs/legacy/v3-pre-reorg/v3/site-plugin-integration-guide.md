# サイト plugin 統合・認証ガイド

この文書は、HTTP site plugin を実装する作者が認証、request、画像 URL、Cookie、回復、
並列制御、例外規約を一続きに確認するための入口である。manifest、設定 tree、公開 DTO の
詳細な正本はそれぞれの v3 文書にあり、矛盾時は [v3 ドキュメント索引](README.md)の仕様優先順位に従う。

## 役割分担と実装の順序

core は HTTP transport、retry、redirect policy、Cookie jar、rate/concurrency limiter、画像の
取得・保存を所有する。site plugin は URL の選択、ページまたは API の inspection、画像 request、
認証判断、site 固有の image transform を実装する。plugin は raw HTTP client、filesystem、
browser automation を直接使わず、`PluginExecutionContext` の `requests` と `secrets` だけを
使う。

実装は次の順で設計する。

1. `matches()` で受け入れる HTTP(S) URL を安全に絞る。設定で別 host を許可する場合だけ
   `matches_with_config()` を使う。
2. `inspect()` でページまたは API を読み、絶対 HTTP(S) URL を持つ `ImageResource` を含む
   `DownloadManifest` を返す。
3. `create_image_request()` で各画像の `RequestSpec` を作る。画像 URL が短命なら
   `recover_image_request()` で replacement request を返す。
4. 認証が必要なら `auth_flow()` を実装し、secret、Cookie、token の取得と refresh をそこへ閉じ込める。

## ID/password、API token、Cookie

site plugin の credential は `plugin_settings.<id>.secrets` に logical name と**外部参照名**を
対応付ける。値そのものを author YAML、manifest、plugin metadata、source、log、例外、URL に
置いてはならない。

```yaml
plugin_settings:
  com.example.gallery:
    secrets:
      username: GALLERY_USERNAME
      password: GALLERY_PASSWORD
      api_token: GALLERY_API_TOKEN
```

`context.secrets.get("username")` は、まず
`IMAGE_DOWNLOADER_PLUGIN_<NORMALIZED_ID>_<REFERENCE>` 環境変数、次に OS keyring の
`image-downloader.plugin.<plugin-id>` service / `<REFERENCE>` username を解決する。見つからない
場合は `SecretNotFound` になる。processor は secret を受け取れない。

ログイン済み browser の session を使う場合は、plugin ではなく実行前の CLI で import する。

```powershell
image-downloader cookie browser-import example.test
```

browser import には `.[browser-cookies]` が必要である。Cookie jar は profile 単位で暗号化保存され、
通常 request に core が適用する。plugin が request-local cookie を指定する必要がある場合だけ
`RequestSpec.cookies` を使う。

## HTTP request とレスポンス

すべての HTTP request は `await context.requests.execute(RequestSpec(...))` を通す。core が timeout、
retry、Cookie、redirect、認証、origin/domain concurrency を管理するため、plugin は raw HTTP client を
作らない。

| `RequestSpec` field | 用途 |
|---|---|
| `url` | 絶対 HTTP(S) URL。 |
| `method` | HTTP method。既定は `GET`。 |
| `headers` | request 固有の string-to-string header。`Authorization`、`Accept`、`Origin` 等をここで付与する。 |
| `cookies` | request 固有の string-to-string Cookie。通常は profile Cookie jar を使う。 |
| `referer` | HTTP(S) Referer URL。画像 request では `ImageResource.referer` を引き継ぐ。 |
| `query` | string-to-string query parameter。更新時は DTO を置換する。 |
| `form` | string-to-string form body。`json` と同時には指定できない。 |
| `json` | JSON body。`form` と同時には指定できない。 |
| `auth_required` | 既定は `true`。login、token refresh など AuthFlow を適用してはならない request は `false`。 |
| `retry_non_idempotent` | POST 等でも plugin が安全性を保証できる場合だけ `true`。 |

`RequestSpec`、`RequestResponse`、`ImageResource` は immutable DTO である。header や query を
変更する時は `dataclasses.replace(request, headers={...})` のように新しい値を返す。
`RequestResponse` は response URL、status、headers、body bytes を持つ。JSON/HTML の decode、
site 固有の response schema 検証、相対 URL の絶対化は plugin の責務である。

GET/HEAD/OPTIONS/TRACE は transport retry の対象で、429/500/502/503/504 の `Retry-After` と
jitter は core が扱う。POST 等は既定で再試行されない。redirect は hop ごとに検査され、匿名の
cross-origin redirect では Cookie、Referer、plugin header、app header を引き継がない。

## URL、画像 URL、site 固有 header

`matches()` は network や secret を使わず `bool` を返す。例外を `False` として握りつぶしてはならない。
`inspect()` は指定 URL から必要な API URL を組み立て、`DownloadManifest` を返す。各
`ImageResource.url` は絶対 HTTP(S) URL、`number` と `index` は 0 以上、header/query/form の
mapping はすべて string-to-string でなければならない。

共通の URL 組み立て規則や API response schema はない。対象サイトの公開 API、HTML、ページング方式を
検証して plugin 内で実装する。次の fixture source は pattern の参考であり、一般契約ではない。

| pattern | 参照 source |
|---|---|
| HTML の相対画像 URL と `Referer` | [generic CSS selector](../../plugin-sources/generic-css-selector/plugin.py) |
| HTML の `Origin`／`Referer` header | [public gallery](../../plugin-sources/public-gallery/public_gallery.py) |
| cursor pagination と API token header | [cursor API gallery](../../plugin-sources/cursor-api-gallery/cursor_api_gallery.py) |
| CSRF form login | [CSRF login gallery](../../plugin-sources/csrf-login-gallery/csrf_login_gallery.py) |
| OAuth bearer token refresh | [OAuth media API](../../plugin-sources/oauth-media-api/oauth_media_api.py) |
| short-lived signed image URL の再発行 | [signed CDN gallery](../../plugin-sources/signed-cdn-gallery/signed_cdn_gallery.py) |

`network.headers` と `network.proxy` は安全な `app_settings` から除外される。plugin 固有 header や
query の値は `context.config`、`context.secrets`、または直前の response から組み立て、
`RequestSpec` にだけ設定する。

## 認証と失効回復

`auth_flow()` は `AuthFlow | None` を返す。認証が不要なら `None` を返す。認証 flow は次の
contract を実装する。

```python
class AuthFlow:
    def is_auth_failure(self, request, response) -> bool: ...
    async def apply(self, request) -> RequestSpec: ...
    async def refresh(self, failed, response) -> RequestSpec | None: ...
```

- core は 401/403 を認証失敗として扱い、`is_auth_failure()` では 200 + JSON error のような
  site 固有の失効応答を追加で検出できる。
- `apply()` は bearer token や request header を付与した新しい `RequestSpec` を返す。
- `refresh()` は login form、refresh token、Cookie 更新等を行い、再実行する request を返す。
  回復を拒否する場合は `None` を返す。回復回数は `network.auth_refresh_attempts` に従う。
- 認証は既定で operation URL と同じ origin だけに適用される。認証済み CDN 等へ送る必要がある場合は
  `OriginScopedAuthFlow.allowed_origins` に絶対 origin を明示する。画像 URL や redirect 先を無条件に
  許可してはならない。

access token の refresh 後の値は operation の AuthFlow instance 内で更新できる。一方、plugin から
environment variable、OS keyring、設定 YAML へ token を永続書込みする API はない。次回 operation でも
使う refresh token は外部 secret store で管理する。

認証とは別に、`recover_image_request(image, failed, response, context)` は HTTP error を受けて
短命署名 URL 等の replacement `RequestSpec` を返せる。`None` は通常の HTTP error 処理へ進む。
core は replacement を最大一度だけ試すため、無限 refresh/recovery loop を plugin に実装してはならない。

## 並列数、rate limit、応答サイズ

対象 server への HTTP access を直列化する場合は、対象 host を解決する site YAML で
`network.origin_request_concurrency: 1` を設定する。subdomain をまたいで一つに制限する場合は
`network.registrable_domain_request_concurrency: 1` も設定する。

```yaml
# sites/gallery.example.test.yaml
network:
  origin_request_concurrency: 1
  registrable_domain_request_concurrency: 1
  global_request_interval_seconds: 0.5
download:
  chapter_concurrency: 1
  image_concurrency_per_chapter: 1
```

origin/domain concurrency は HTTP request の同時数を制限する。画像の fetch、transform、save を含めて
operation 全体を直列にしたい場合だけ chapter/image concurrency も `1` にする。response は streaming で
読み、`network.max_response_bytes` を超えると core が停止する。

## hook と例外

| hook / 場面 | 契約 |
|---|---|
| `validate_config(config, app_settings)` | 同期・副作用なしで `None` を返す。plugin 設定が不正なら `ValueError` を送出する。 |
| `matches(url)` / `matches_with_config(...)` | `bool` を返す。例外は不一致ではなく `PluginError` になる。 |
| `inspect(url, context)` | `DownloadManifest` を返す。response schema が site 固有で不正なら `PluginError` にする。 |
| `create_image_request(image, context)` | 有効な `RequestSpec` を返す。 |
| `recover_image_request(...)` | `RequestSpec | None` を返す。画像 URL 再発行など、HTTP error に限定する。 |
| `auth_flow(context)` | `AuthFlow | None` を返す。認証の失敗は `AuthenticationError`、secret 欠落は `SecretNotFound` を使う。 |
| `transform_image(artifact, context)` | `ImageArtifact` を返す。site に不要ならそのまま返す。 |
| `check_updates(url, context)` | 任意。実装する場合は完全な `UpdateSnapshot` を返す。 |

callback の戻り値契約違反と予期しない callback 例外は、plugin ID と hook 名を持つ
`PluginError` に変換される。`RequestPort` が送出する transport/status/redirect error を独自に
握りつぶしたり、secret を含む例外文を作ったりしてはならない。HTTP contract で表現できない要件には
`UnsupportedSiteFeature` を使う。

## 契約外・未対応

v3 site plugin contract は HTTP(S) request と宣言済み DTO だけを対象にする。次は契約外であり、
plugin 内の raw client や独自 browser 実装で回避してはならない。

- HTTP(S) 以外の protocol、SSE、WebSocket
- JavaScript 実行、DOM automation、headless browser 操作
- CAPTCHA、対話型 MFA、WebAuthn など人または browser UI を必要とする認証
- multipart/streaming request body

純粋な HTTP request、external secret、Cookie、CSRF form、OAuth refresh だけで完結する認証は
この contract の対象内である。対象外の機能が不可欠なサイトは `UnsupportedSiteFeature` として明示的に
失敗させる。

## 詳細な正本

- [plugin 作者ガイド](plugin-author-guide.md): directory、class contract、context、DTO。
- [設定と CLI](configuration-and-cli.md): secret 解決、Cookie import、network 設定。
- [ライブラリ API](library-api.md): DTO、service、例外 catalog。
- [テストと移行](testing-and-migration.md): contract の受入範囲。
