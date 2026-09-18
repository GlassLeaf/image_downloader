# image-downloader 0.3.0 プラグイン開発ガイド（v2・履歴資料）

> v3 の local-directory layout、context、class contract は [v3 plugin 作者ガイド](../../../../docs/v3/plugin-author-guide.md) を参照してください。entry point/wheel 手順は現行実装では使いません。

このガイドでは、v2 site pluginを新規作成し、固定fixtureで検証し、署名済みwheelとして配布するまでを説明します。クラスと各メソッドの厳密な契約は[プラグインAPIリファレンス](plugin-api-reference.md)を併読してください。

## 1. pluginとcoreの責任分担

site pluginが担当するのは次の範囲です。

- 対応URLの同期判定
- HTML/APIからのmanifest構築
- 画像ごとのrequest構築
- site固有の認証失敗判定と認証情報適用・更新
- 短命な画像URLの再生成
- coreがdecodeする前のsite固有画像変換
- 更新確認用の完全snapshot作成

coreはHTTP client、Cookie jar、retry、同時実行数、認証refresh集約、画像正規化、processor chain、filesystem、ログ、通知、更新stateを担当します。pluginからこれらを直接操作しないでください。

## 2. packageを作る

推奨する最小構成です。

```text
example-site-plugin/
├── pyproject.toml
├── src/
│   └── example_site/
│       ├── __init__.py
│       └── plugin.py
└── tests/
    ├── fixtures/
    │   ├── catalog.json
    │   └── image.png
    └── test_plugin.py
```

`pyproject.toml`では対応するcore minor versionを制限し、site entry pointを登録します。

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "example-site-plugin"
version = "1.0.0"
requires-python = ">=3.11"
dependencies = ["image-downloader>=0.3,<0.4"]

[project.entry-points."image_downloader.plugins"]
example-site = "example_site.plugin:ExampleSitePlugin"

[tool.setuptools.packages.find]
where = ["src"]
```

entry point右辺はclassです。次は無効です。

```toml
# instanceやfactoryを返すfunctionは不可
example-site = "example_site.plugin:create_plugin"
```

classの`__init__`は引数なしで呼べる必要があります。URL選択では登録済みsite plugin classが照合のため生成されるので、constructorは軽量・副作用なしにします。設定と秘密値はコンストラクタへ渡さず、各メソッドのcontextから受け取ります。

## 3. 最小site plugin

次の例は、catalog JSONを取得して1章のmanifestへ変換する最小実装です。

```python
from __future__ import annotations

import json
from urllib.parse import urlparse

from image_downloader import (
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageResource,
    PluginDescriptor,
    PluginExecutionContext,
    RequestResponse,
    RequestSpec,
    SitePlugin,
    TransformContext,
)


class ExampleSitePlugin(SitePlugin):
    descriptor = PluginDescriptor("com.example.gallery", priority=100)

    def matches(self, url: str) -> bool:
        parsed = urlparse(url)
        return (
            parsed.scheme in {"http", "https"}
            and parsed.hostname == "gallery.example.test"
            and parsed.path.startswith("/works/")
        )

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        work_id = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
        response = await context.requests.execute(
            RequestSpec(
                f"https://gallery.example.test/api/works/{work_id}",
                headers={"Accept": "application/json"},
            )
        )
        payload = json.loads(response.body)
        images = tuple(
            ImageResource(
                url=str(item["url"]),
                index=position,
                referer=url,
                image_id=str(item["id"]),
            )
            for position, item in enumerate(payload["images"], start=1)
        )
        return DownloadManifest(
            title=str(payload["title"]),
            content_id=str(payload["id"]),
            author=str(payload.get("author") or "") or None,
            revision=str(payload.get("revision") or "") or None,
            chapters=(
                Chapter(
                    number=1,
                    title=str(payload["title"]),
                    images=images,
                    chapter_id=str(payload["id"]),
                ),
            ),
            metadata={"source": "example-api"},
        )

    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec:
        return RequestSpec(
            image.url,
            headers=image.headers,
            referer=image.referer,
        )

    async def recover_image_request(
        self,
        image: ImageResource,
        failed: RequestSpec,
        response: RequestResponse,
        context: PluginExecutionContext,
    ) -> RequestSpec | None:
        return None

    def auth_flow(self, context: PluginExecutionContext):
        return None

    async def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        return artifact
```

実装時の要点は次のとおりです。

- `matches()`はnetwork I/Oなしで、対象host/pathを狭く判定する。
- `inspect()`のすべての通信を`context.requests`へ通す。
- JSON fieldの欠落・型違反を、秘密値やresponse bodyを含めない`PluginError`へ変換する。
- `ImageResource.headers`/`referer`を使う場合は`create_image_request()`で明示的にコピーする。
- 認証・回復・変換が不要でも対応メソッドを実装する。

上の例では説明を短くするためJSONの詳細検証を省いています。本番pluginではschemaを明示的に検証してください。

## 4. plugin設定を読む

利用者はroot設定の`plugins.<descriptor.id>.config`へ公開設定を置きます。

```yaml
plugins:
  com.example.gallery:
    config:
      api_base: https://gallery.example.test/api
      locale: ja-JP
```

plugin側では読み取り専用mappingとして取得します。

```python
from image_downloader import ConfigurationError

api_base = context.config.get("api_base")
if not isinstance(api_base, str) or not api_base.startswith("https://"):
    raise ConfigurationError("com.example.gallery.api_base must be an HTTPS URL")
```

coreはplugin固有configのschemaを知りません。pluginが型、必須性、値域、組合せを検証し、エラーには設定keyを含めても値そのものは含めないでください。

## 5. 秘密値を読む

秘密値そのものをYAMLへ書かず、論理名からreferenceへのmappingだけを書きます。

```yaml
plugins:
  com.example.gallery:
    secrets:
      access_token: main-account-token
      refresh_token: main-account-refresh
```

```python
access_token = context.secrets.get("access_token")
```

環境変数を使う場合、上のreferenceは次になります。

```text
IMAGE_DOWNLOADER_PLUGIN_COM_EXAMPLE_GALLERY_MAIN_ACCOUNT_TOKEN
IMAGE_DOWNLOADER_PLUGIN_COM_EXAMPLE_GALLERY_MAIN_ACCOUNT_REFRESH
```

またはOS keyringのservice `image-downloader.plugin.com.example.gallery`、username `main-account-token` / `main-account-refresh`へ値を保存します。秘密値をinstance repr、例外、URL query、manifest metadataへ含めないでください。

## 6. 認証を実装する

Bearer tokenの例です。`apply()`は毎回最新tokenでAuthorizationを置換し、`refresh()`は認証対象外のtoken endpointを使います。

```python
from dataclasses import replace
import json

from image_downloader import (
    AuthenticationError,
    PluginExecutionContext,
    RequestResponse,
    RequestSpec,
)


class BearerFlow:
    def __init__(self, context: PluginExecutionContext) -> None:
        self.context = context
        self.access_token = context.secrets.get("access_token")

    def is_auth_failure(self, request: RequestSpec, response: RequestResponse) -> bool:
        content_type = response.headers.get("content-type", "").lower()
        return "application/json" in content_type and b'"code":"login_required"' in response.body

    async def apply(self, request: RequestSpec) -> RequestSpec:
        headers = {key: value for key, value in request.headers.items() if key.lower() != "authorization"}
        headers["Authorization"] = f"Bearer {self.access_token}"
        return replace(request, headers=headers)

    async def refresh(self, failed: RequestSpec, response: RequestResponse) -> RequestSpec | None:
        token_response = await self.context.requests.execute(
            RequestSpec(
                "https://gallery.example.test/oauth/token",
                method="POST",
                form={
                    "grant_type": "refresh_token",
                    "refresh_token": self.context.secrets.get("refresh_token"),
                },
                auth_required=False,
            )
        )
        try:
            payload = json.loads(token_response.body)
            token = payload["access_token"]
            if not isinstance(token, str) or not token:
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AuthenticationError("token refresh response is invalid") from exc
        self.access_token = token
        return failed
```

pluginからflowを返します。

```python
def auth_flow(self, context: PluginExecutionContext) -> BearerFlow:
    return BearerFlow(context)
```

重要な条件です。

- 401/403はcoreが常に認証失敗と判定する。
- HTTP 200のlogin HTML/JSONだけを`is_auth_failure()`で追加判定する。
- `apply()`は複数回呼ばれるため、headerを追記せず置換する。
- token endpoint requestは`auth_required=False`にして、同じflowを再適用しない。
- 同時認証失敗の`refresh()`はcoreが1回へ集約する。plugin独自のglobal lockは通常不要。
- refresh失敗時はtoken/bodyを含めず`AuthenticationError`を送出する。

## 7. 短命な画像URLを回復する

画像URLが403/404等で期限切れになった場合は`recover_image_request()`で1回だけ再生成できます。

```python
import json

from image_downloader import RequestSpec


async def recover_image_request(self, image, failed, response, context):
    if response.status not in {403, 404} or image.image_id is None:
        return None
    refreshed = await context.requests.execute(
        RequestSpec(
            f"https://gallery.example.test/api/images/{image.image_id}/url",
            headers={"Accept": "application/json"},
        )
    )
    payload = json.loads(refreshed.body)
    url = payload.get("url")
    if not isinstance(url, str):
        return None
    return RequestSpec(url, referer=image.referer)
```

このhookは認証refreshの後に評価されます。置換requestにもAuthFlowが適用され、transport retryは改めて行われます。2本目も失敗してもhookは再呼出しされません。

## 8. サイト固有画像変換を実装する

DTOはimmutableなので`dataclasses.replace()`を使います。

```python
from dataclasses import replace


async def transform_image(self, artifact, context):
    decoded = decode_site_scramble(artifact.data, image_id=context.image_id)
    return replace(
        artifact,
        data=decoded,
        history=(*artifact.history, "com.example.gallery.unscramble"),
    )
```

ここではsite固有処理だけを行います。JPEG/PNG/WEBPへの一般変換、quality、最終extensionは`ImageSaveOptions`とcoreへ任せます。`TransformContext`からmanifest、chapter、画像ID/index、plugin configを参照できますが、HTTPとsecretにはアクセスできません。

変換後dataはPillowでdecode可能な通常画像にしてください。例外を直接漏らすとoperation全体の`PluginError`になるため、fixtureでアルゴリズムを十分に検証してください。

## 9. 保存形式を画像ごとに指定する

```python
from image_downloader import ImageResource, ImageSaveOptions

image = ImageResource(
    url="https://cdn.example.test/cover",
    index=1,
    save_options=ImageSaveOptions(
        format="PNG",
        extension=".png",
        optimize=True,
        compress_level=6,
    ),
)
```

`format`を指定するとrootの`output.image_format`より優先されます。formatを省略するとroot設定が最終形式になります。extensionはformatと一致させてください。対応範囲は[公開APIリファレンス](api-reference.md)に記載しています。

## 10. 更新確認を実装する

同じsite plugin classへ`check_updates()`を追加します。

```python
from datetime import UTC, datetime
import json

from image_downloader import RequestSpec, UpdateCandidate, UpdateSnapshot


async def check_updates(self, url, context):
    response = await context.requests.execute(
        RequestSpec(
            "https://gallery.example.test/api/updates",
            headers={"Accept": "application/json"},
        )
    )
    payload = json.loads(response.body)
    candidates = tuple(
        UpdateCandidate(
            url=str(item["url"]),
            content_id=str(item["id"]),
            revision=str(item.get("revision") or "") or None,
        )
        for item in payload["items"]
    )
    return UpdateSnapshot(url, candidates, datetime.now(UTC))
```

返すのは今回追加されたURLだけではなく、現在存在する完全一覧です。`content_id`はURLが変わっても同一contentを追跡できる安定値にします。snapshotから消えたkeyはcoreがremovedと判定します。

## 11. 外部画像processorを作る

site固有ではなく複数siteへ適用する変換はprocessorとして登録します。

```python
from dataclasses import replace

from image_downloader import ImageArtifact, ImageProcessor, TransformContext


class WatermarkProcessor(ImageProcessor):
    name = "com.example.watermark"

    async def transform(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        data = add_watermark(artifact.data)
        return replace(
            artifact,
            data=data,
            history=(*artifact.history, self.name),
        )
```

```toml
[project.entry-points."image_downloader.image_processors"]
watermark = "example_site.processor:WatermarkProcessor"
```

sidecarの`capabilities`へ`image-processor`を含め、利用者はchainを設定します。

```yaml
image_processors:
  chain:
    - com.example.watermark
```

processor classも引数なしです。compose時に`name`と`transform`を検査するinstanceが一度作られ、実行時は各画像に新しいinstanceが作られてchain記載順に1回ずつ呼ばれます。constructorへ実行stateを置かないでください。戻り値は`ImageArtifact`でなければなりません。最終形式をprocessor内で固定してもcoreが`ImageSaveOptions`へ再正規化することに注意してください。

## 12. fixed fixtureで単体テストする

ネットワークを使わず、contextのportをfakeにします。

```python
import asyncio
import json

from image_downloader import (
    PluginExecutionContext,
    RequestResponse,
)


class FakeRequests:
    def __init__(self, routes: dict[str, RequestResponse]) -> None:
        self.routes = routes
        self.seen = []

    async def execute(self, spec):
        self.seen.append(spec)
        return self.routes[spec.url]


class NoSecrets:
    def get(self, name):
        raise AssertionError(f"unexpected secret access: {name}")


def test_inspect_builds_manifest():
    url = "https://gallery.example.test/works/42"
    api = "https://gallery.example.test/api/works/42"
    body = json.dumps(
        {
            "id": "42",
            "title": "Fixture Work",
            "images": [{"id": "p1", "url": "https://cdn.example.test/p1.png"}],
        }
    ).encode()
    requests = FakeRequests({api: RequestResponse(api, 200, {"content-type": "application/json"}, body)})
    context = PluginExecutionContext({}, NoSecrets(), requests)

    manifest = asyncio.run(ExampleSitePlugin().inspect(url, context))

    assert manifest.content_id == "42"
    assert manifest.chapters[0].images[0].image_id == "p1"
    assert requests.seen[0].headers["Accept"] == "application/json"
```

Fake portはcore retry/authのテストにはなりません。package受入テストではcore serviceと`httpx.MockTransport`を組み合わせ、次を追加してください。

- 401/403とHTTP 200認証失敗
- 複数画像同時失敗時のrefresh 1回集約
- `auth_required=False`のtoken endpoint
- 署名URL回復が最大1回
- manifest宣言順のoutcomeとindex順log
- `continue_on_error`のtrue/false
- cancel時に未完taskが残らないこと
- content-type/decode/mismatch各mode
- processorの順序、画像別instance、例外
- updateのadded/changed/removedとstable content ID
- error messageにtoken、Cookie、署名query、bodyが出ないこと

全受入項目は[プラグイン受入テストガイド](plugin-testing-guide.md)を参照してください。

## 13. localで読込確認する

sidecarなしpluginは`security.plugin_verification=off`でもimport前に拒否されます。開発用wheelにも構造的に有効なv2 sidecarを含めてください。署名・wheel更新手順は[plugin package・署名ガイド](plugin-package-template.md)にあります。

開発profileでstrictだけを一時的にoff相当にする場合は次を使えます。

```powershell
image-downloader doctor --profile development --debug-allow-unverified-plugins --json
image-downloader "https://gallery.example.test/works/42" `
  --profile development `
  --debug-allow-unverified-plugins
```

このflagはsidecar要件を無効化しません。warn/offでも効果はありません。production profile等で指定すると設定エラーです。

## 14. 配布前チェックリスト

- classが引数なしで生成できる。
- root importだけを使用し、`image_downloader.runtime`等へ依存していない。
- `descriptor.id`、sidecar `manifest.id`、設定keyが一致する。
- `matches()`が同期・副作用なしで対象URLを限定する。
- manifest ID/indexの意味的整合性をpluginテストで保証する。
- network I/Oは`RequestPort`だけを通る。
- configに秘密値を置かず、SecretProviderの論理名を使う。
- AuthFlow.applyが冪等で、HTTP 200認証失敗もfixture化している。
- 回復hookが失敗response bodyをログへ出さない。
- transformがdecode可能な`ImageArtifact`を返す。
- update providerが差分ではなく完全snapshotを返す。
- sidecar/file tree/RECORDを最終wheelへ正しく格納している。
- strict、warn、offで`image-downloader doctor --json`のdiagnosticを確認した。
- pytest branch coverage、Ruff、Mypyを通した。

## 15. 対応外機能に遭遇した場合

multipart、WebSocket、SSE、browser JavaScript、対話型MFAが必須なら、無理に直接clientを持ち込まず`UnsupportedSiteFeature`を返してください。公開HTTP/JSON endpointなど、v2のRequestSpecで表現可能な公式経路がある場合だけ対応します。CAPTCHAやアクセス制御の回避はpluginの対象外です。
