# plugin 作者ガイド

## 役割と信頼境界

v3 plugin は local directory 内の Python source である。core は HTTP retry、host/site
concurrency、Cookie persistence、output、image normalization、state、logging、notification
を所有する。plugin は site 固有の URL 判定、inspection、request 作成、認証判断、image
transform、optional update snapshot を実装する。

plugin code は sandbox ではない。同じ process で実行されるため、管理者は署名を確認して
信頼できる code だけを catalog へ登録する。一方、plugin contract 上は context にない
HTTP client、filesystem、logger、output path、scheduler を直接利用しない。追加 dependency
を core が install することもない。

実サイトの HTTP/API 統合、secret、Cookie、AuthFlow、URL recovery、並列制御、対象外機能は
[サイト plugin 統合・認証ガイド](site-plugin-integration-guide.md)にまとめている。

## directory unit

site plugin と image processor は一つの共通 plugin root の別の親 directory に置く。

```text
<plugin-root>/
  site_plugins/
    gallery-plugin/                 # 任意の directory 名、1 unit
      manifest.json
      plugin-metadata.json          # 付属 signer を使う場合だけの任意入力
      gallery.source                # 任意拡張子の UTF-8 Python entry source
      gallery.yaml                  # manifest が参照する author config、必須
      helpers/
        parser.py                   # relative import 用の通常 .py helper
  image_processor_plugins/
    scaler-plugin/
      manifest.json
      scaler.py
      scaler.yaml
```

`site_plugins` / `image_processor_plugins` と manifest の `kind` は一致しなければならない。
directory 名は ID ではない。manifest ID は全 kind で一意で、publisher を prefix とする
reverse-DNS ID である。例: publisher `com.example`、ID
`com.example.gallery`。

`plugin-metadata.json` は付属の `tools/sign_local_site_plugin.py` を使って manifest を生成する
場合だけの署名入力であり、runtime は読まない。手作業で完全な manifest を生成・署名する場合は
不要である。正確な schema と再署名時の規則は[信頼・配布・運用](trust-and-operations.md)を参照する。

entry source は隔離された synthetic namespace で load され、plugin root を `sys.path` に
追加しない。helper は `.py` の relative import（例: `from .helpers.parser import parse`）を
使う。entry file は `.py` 以外でも UTF-8 Python source として読まれるが、native binary
entry は使えない。

## author config

author config file は manifest の `config_file` が指す、relative path の lowercase `.yaml`
file である。root は必ず `config` 一つだけで、その値は mapping である。

```yaml
config:
  api_base: https://api.example.test
  pagination:
    per_page: 100
```

author default は利用者 YAML と CLI/library override より先に深く統合される。ここには
core settings、credential、secret、`enabled` を置かない。plugin 固有の型・必須性・値域は
`validate_config` で検証する。

## site plugin contract

class は引数なしで生成可能で、次を実装する。コンストラクタ、`matches`、
`validate_config` は軽量に保つ。class descriptor、class 上の ID/priority 属性は v3 にない。

```python
class GalleryPlugin:
    def validate_config(self, config, app_settings) -> None:
        # 同期的で副作用なし。成功時は必ず None。
        if "api_base" not in config:
            raise ValueError("api_base is required")

    def matches(self, url: str) -> bool:
        return url.startswith("https://gallery.example.test/")

    async def inspect(self, url, context): ...

    async def create_image_request(self, image, context): ...

    async def recover_image_request(self, image, failed, response, context):
        return None

    def auth_flow(self, context):
        return None

    async def transform_image(self, artifact, context):
        return artifact

    async def check_updates(self, url, context): ...  # optional
```

`matches()` は `bool` を返す。例外は「一致しない」として握りつぶされず、その operation を
`PluginError` にする。external site plugin が一致した場合は builtin generic fallback より
常に優先される。複数候補は manifest `match_priority`、次に catalog
`selection_priority` の高い方を選び、同順位は error である。

設定によって別hostを受け入れる plugin は、任意で次を実装できる。これを実装した場合は
`matches()` の代わりに選択で使われる。

```python
def matches_with_config(self, url, config, app_settings) -> bool:
    return url.startswith("https://mirror.example.test/") and config.get("allow_mirror") is True
```

`config` は author default、main/profile/siteの `plugin_settings`、operation override を
統合した読み取り専用mappingである。hookは `validate_config` より前に呼ばれるため、同期・
副作用なし・network/secret/filesystem不使用で、未検証の設定を安全に扱う必要がある。

`inspect` は `DownloadManifest`、`create_image_request` は `RequestSpec`、
`recover_image_request` は `RequestSpec | None`、`transform_image` は `ImageArtifact` を
返す。DTO は frozen value object なので、変更時は `dataclasses.replace()` を使う。
`check_updates` を実装するなら `UpdateSnapshot` を返し、呼び出されたfeed URLの完全snapshotを
返すこと。別feedの候補を混ぜる必要はない。対応しない場合、`--list-updated-urls` は
plugin error になる。

## image processor contract

processor manifest の `kind` は `image_processor_plugin`。`match_priority` は schema 上必須
だが現在は予約 metadata であり、実行順を決めない。順序は利用者の
`image_processors.chain` の記述順である。

```python
class Scaler:
    def validate_config(self, config, app_settings) -> None:
        return None

    async def transform(self, artifact, context):
        return artifact
```

chain にない processor は通常の compose 時に import されない。chain 内かつ
`plugin_settings.<id>.enabled` が true の processor だけ実行される。disabled processor は
正常 skip であり、secret を受け取れない。

processorはdownload operationの開始時、最初のHTTP requestより前にchain順で各1回だけ生成・
設定検証され、同一operation内の全画像で再利用される。同じinstanceの`transform()`は
並行実行されないため、operation単位の状態を保持できる。次のoperationでは新しいinstanceを
生成する。終了処理が必要なprocessorは任意の`close()`または`aclose()`を実装できる
（両方ある場合は`aclose()`を優先）。成功、失敗、キャンセル、chain初期化途中の失敗でも
生成済みinstanceを逆順で終了し、終了hookは`None`を返すこと。終了失敗だけが起きた場合は
plugin errorになり、本処理も失敗していた場合は元の失敗を優先する。

## context と capability

### site lifecycle context

`PluginExecutionContext` は読み取り専用で、次の property を持つ。

| property | 内容 |
|---|---|
| `config` | author default、永続設定、operation override を統合した plugin private config |
| `app_settings` | 安全な final app view。output/media/execution と安全な network control |
| `manifest` | この plugin の署名済み manifest |
| `catalog` | strict で pin 済みなら catalog entry、warn/off/builtin では `None` |
| `secrets` | site plugin の external secret resolver |
| `requests` | `RequestPort.execute(RequestSpec)` のみ |

`app_settings` からは `network.headers`、`network.proxy`、`security`、notification、
credential、他 plugin 設定を除外する。mapping/list は再帰的に immutable である。
secret value は exception、URL、log、manifest に含めない。

processor と site transform の `TransformContext` は HTTP port と secrets を持たない。自身の
`config`、`app_settings`、`plugin_manifest`、optional `catalog`、image ID/index、
`DownloadManifest`、chapter を読む。processor の場合だけ `site_manifest` と
`site_catalog` も読める。site の private config や secret は渡らない。

pluginは`logging.getLogger(__name__)`で標準Python logを出力できる。coreは選択されたsite
pluginと実行対象processorの動的module名前空間だけを`debug.log`へ取り込み、root loggerの
handlerは変更しない。他pluginやhost applicationのloggerは取り込まず、複数serviceを同時に
使用しても記録先を混在させない。log本文にはsecret、credential、未承認のURL parameterを
含めてはならない。

callbackは宣言されたDTOを返すだけでなく、そのnested要素も契約を満たす必要がある。
`DownloadManifest.chapters`は`Chapter`、`Chapter.images`は絶対HTTP(S) URLを持つ
`ImageResource`、`UpdateSnapshot.candidates`は絶対HTTP(S) URLを持つ`UpdateCandidate`で
構成する。number/indexは0以上の整数とし、header/query/form等のmappingは文字列同士にする。
契約違反と予期しないcallback例外は、plugin IDとhook名を含む`PluginError`としてoperationを
停止する。

## secret と request の使い方

```python
token = context.secrets.get("api_token")
response = await context.requests.execute(
    RequestSpec("https://api.example.test/v1/items", headers={"Authorization": f"Bearer {token}"})
)
```

logical name が YAML にない時と、environment/keyring から値を解決できない時は
`SecretNotFound` になる。core は request port を通じて retry、timeout、concurrency、Cookie、
auth flow を管理する。plugin は raw HTTP client を作らない。

`AuthFlow` の認証情報は、既定ではoperation URLと同じorigin（scheme、host、port）にしか
適用されない。既存の`AuthFlow`実装に追加属性は必要ない。認証済みCDNなど別originが必要な
flowだけ`OriginScopedAuthFlow`を実装し、`allowed_origins`に絶対originを列挙する。画像URLや
redirect先を無条件に許可してはならない。
リダイレクトは送信前に一段ずつ検査される。認証済みrequestの別origin転送は
`allowed_origins`に宣言した先だけ許可され、HTTPSからHTTPへの転送は拒否される。
匿名の別origin転送はGET/HEADだけ追跡し、元のCookie、Referer、plugin指定header、
app共通headerは転送しない。別originでも認証が必要な場合は、そのoriginを明示的に
信頼した`OriginScopedAuthFlow`を使う。`network.follow_redirects: false`なら転送を追跡しない。

`RequestSpec` のPOST等は既定ではtransport retryされない。冪等性キーなどにより安全性を
保証できるrequestだけ `retry_non_idempotent=True` を指定する。GET等のretryでは
`Retry-After` とjitterがcoreにより処理される。

## fallback との関係

`core.generic-html` は static manifest を持つ builtin fallback で、ユーザーの
`plugin_settings.core.generic-html` は許可されない。external plugin が一つも match せず、
`fallback.generic_html.enabled` または operation の fallback override が false なら
operation は error になる。
