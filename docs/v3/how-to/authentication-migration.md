# 認証API 3の移行

認証結果の公開情報を参照する追加APIについては[構造化データと認証スナップショットへの移行](structured-data-and-auth-snapshot-migration.md)を参照する。追加APIに対しては現行AuthFlowの無変更互換性を維持し、以下の過去の認証契約変更とは区別する。

開発中API 3の認証契約を破壊的に更新した。API番号は3を維持するが、旧AuthFlowとの互換層は提供しない。プラグインのソースと作者設定を修正し、再署名後に `image-downloader plugin install` で再インストールする。インストール済みファイルやカタログを直接書き換えない。

## 責務と最小実装

`initialize()` は初回の認証準備、同期 `apply()` は保持済み資格情報の付与、`classify_response()` は応答の分類、`verify()` は追加確認、`refresh()` は更新を担当する。`apply()` と分類フックではネットワーク通信しない。

```python
from dataclasses import replace

from image_downloader import AuthContext, AuthLease, BaseAuthFlow, RequestSpec


class TokenFlow(BaseAuthFlow):
    async def initialize(self, auth_context: AuthContext) -> AuthLease:
        self.token = auth_context.secrets.get("access_token")
        return AuthLease()

    def apply(self, request: RequestSpec) -> RequestSpec:
        return replace(request, headers={**request.headers, "Authorization": f"Bearer {self.token}"})
```

Cookieログインの実例は[CSRFフォーム認証](../../../plugin-sources/csrf-login-gallery/csrf_login_gallery.py)、token更新は[OAuth](../../../plugin-sources/oauth-media-api/oauth_media_api.py)、追加ホストは[機能ラボ](../../../examples/plugin-v3-feature-lab/origin-auth-gallery/plugin.yaml)を参照する。認証不要のサイトは `auth_flow()` で `None` を返す。

既存Cookieの読込み、ブラウザインポート、通常通信、認証用通信、終了時保存の関係は、[Cookieと認証の流れ](../explanation/authentication-and-cookie-flow.md)（[HTML版](../explanation/authentication-and-cookie-flow.html)）の①〜⑨の図とAPI対応表で確認できる。

`AuthLease(expires_in_seconds=...)` は有限の正数、または期限不明を表す `None` を受け付ける。コアは単調時計で期限を管理する。`refresh()` は元要求ではなく `AuthLease` を返す。新しいtokenの永続保存は行わない。

## 接続先と確認頻度

manifestの `config_file` が指す作者YAML（`plugin.yaml` など）はプラグイン固有の `config` と、コアが管理する `authentication`・`endpoints` を分ける。共通設定を `validate_config()` に渡さない。利用者は `plugin_settings.<id>.authentication/endpoints` で部分的に上書きでき、作者設定へマージしてから検証する。`--plugin-config` は固有の `config` 用であり、共通設定の上書きには使わない。

### 共通設定を省略して移行する

`authentication` と `endpoints` はそれぞれ省略できる。同一origin内でログイン・通常要求が完結するなら、CSRFサンプルと同じ次の作者設定でよい。

```yaml
config: {}
```

確認方針を省略すると、開始時・毎要求・間隔による追加確認はしない。フローがあれば初回認証対象要求の前に `initialize()` を実行し、各認証対象要求へ `apply()` を適用する。期限・無効化・失敗応答による更新も有効である。認証不要なら `auth_flow()` でNoneを返し、通常のHTTP処理を使う。

operation開始URLのoriginは、フローありでは `session`、なしでは `none` の既定接続先となり、`context.endpoints["operation"]` から参照できる。フローがあるサイトの追加originには宣言が必要で、公開の通常要求なら明示的な `AuthMode.NONE` も使える。認証フック専用の `AuthContext.requests` は常に宣言済みoriginに限り、`NONE` でも未宣言originへは送信できない。

### 追加接続先と確認方法を宣言する

次の設定は、追加APIの認証状態を確認する `verify()` を実装したプラグイン向けである。

```yaml
config: {}
authentication:
  verify_on_start: false
  verify_before_request: false
  verify_interval_seconds: null
  refresh_skew_seconds: 0
endpoints:
  api:
    origin: https://api.example.test
    auth: session
    verification:
      before_request: true
  images:
    origin: https://cdn.example.test
    auth: none
```

`verify_on_start` はアプリ起動時ではなく、operationで最初に認証を準備する際の追加確認である。`verify_before_request` は認証対象要求の認証準備時、正の `verify_interval_seconds` は最後の有効な確認から指定時間が経過した後の要求前に確認する。間隔確認はバックグラウンドの定期通信ではない。毎要求確認を優先し、現行実装では低層transport内部の再試行・リダイレクト各hopに別の確認を挟まない。

`authentication.verify_before_request` は共通の既定値、`endpoints.api.verification.before_request` はapiのoriginだけの上書きで、呼び出す `verify()` は同じである。上例ではapiの要求前に毎回確認し、imagesへの通常要求は確認を含む認証フローを通さない。`verification.before_request: false` だけでは継承した間隔は無効にならない。毎要求・間隔確認を両方止めるなら、`before_request: false` と `interval_seconds: null` を明示する。

`refresh_skew_seconds` は期限より早く更新する秒数である。残存600秒・skew 30秒なら約570秒後以降の認証準備時に更新し、期限不明なら作用しない。設定可能な値・省略とnullの違い・利用者上書きの完全な例は[設定リファレンス](../reference/configuration.md#config-plugin-connections)を参照する。

検証を宣言するサイトでは `verify()` を実装し、`AuthVerification(AuthStatus.VALID/INVALID/UNKNOWN)` を返す。設定だけから確認用URL・確認方法を自動生成することはない。`BaseAuthFlow.verify()` はUNKNOWNを返すため、未実装のまま追加確認を有効にすると `AuthStateUnknownError` になる。VALIDの場合だけ `lease=AuthLease(...)` で期限を更新でき、省略時は元の期限を維持する。初期化・更新に成功しても、そのことだけでは「追加検証済み」の時刻を更新しない。認証状態・確認時刻はoperation内で一つであり、接続先ごとに独立したセッション・時計を作らない。

例えば、認証状態確認APIが `{"authenticated": true/false}` を返すサイトでは、前述の `TokenFlow` を次のように拡張できる。実際の画像応答から認証状態を推測する必要はない。

```python
import json

from image_downloader import AuthContext, AuthStatus, AuthVerification


class VerifiedTokenFlow(TokenFlow):
    async def verify(self, auth_context: AuthContext) -> AuthVerification:
        response = await auth_context.requests.execute(
            auth_context.endpoints["api"].request(
                "/session", headers={"Authorization": f"Bearer {self.token}"}
            )
        )
        if response.status == 401:
            return AuthVerification(AuthStatus.INVALID)
        if response.status != 200:
            return AuthVerification(AuthStatus.UNKNOWN)
        try:
            data = json.loads(response.body)
        except ValueError:
            return AuthVerification(AuthStatus.UNKNOWN)
        value = data.get("authenticated") if isinstance(data, dict) else None
        status = AuthStatus.VALID if value is True else AuthStatus.INVALID if value is False else AuthStatus.UNKNOWN
        return AuthVerification(status)
```

`context.endpoints["api"].request("/items")` は宣言したoriginへの `RequestSpec` を構築し、それ自体は通信しない。`create_image_request()` でも使えるが、返す要求は画像本体の取得先にする。JSONの一覧・署名APIなら、フック内で `context.requests.execute()` に渡し、応答から画像要求を別途構築する。[画像要求の例](dynamic-urls-and-auth.md#endpoint-image-requests)を参照する。未宣言の名前はKeyErrorになる。`operation` は自動的に用意される補助接続先である。

絶対URLの要求でも、同じorigin解決と認証方針を通す。接続先名はURL構築の補助であり、URLの自動書換えは行わない。同一origin内のパスごとには方針を分けず、同じoriginを別名で宣言する場合も実効的な認証・確認方針を一致させる。

`RequestSpec.auth` は `AuthMode.AUTO`（宣言に従う）、`NONE`（認証フローを通さない）、`REQUIRED`（フローと認証対象宣言を必須にする）から選ぶ。`NONE` でもCookie jarは使う。フローがある場合、未宣言の追加originへのAUTO/REQUIRED要求は `AuthScopeError` となる。サイト選択用の対応ホスト一覧から資格情報の送信許可を推測しない。

これらの方針はログイン用URLだけでなく、通常のページ・API・画像要求にも適用する。`auth: none` の通常要求と `AuthMode.NONE` は認証フローを通さないが、Cookieや要求に明示した資格情報を全面的に除去する指定ではない。認証用の専用通信は後述のとおり、確認・apply・認証更新を自動実行しない。

<a id="cookie-session-refresh"></a>

## Cookieログインを初期化・更新へ移す

[CSRFサンプル](../../../plugin-sources/csrf-login-gallery/csrf_login_gallery.py)の `initialize()` は `refresh()` を呼び、初回要求前にログインする。`refresh()` 内の `session = await auth_context.requests.execute(...)` が受け取るのは `RequestResponse` であり、HTTPクライアントやセッション管理オブジェクトではない。`session.status` でPOSTのHTTPエラーを確認している。説明上は `login_response` と読むと役割が分かりやすい。

Cookie方式では、応答オブジェクトをフロー内に保持しなくても次の順序で認証状態を引き継ぐ。

1. `GET /login` でフォームとCSRF tokenを取得する。事前セッションCookieが発行されれば共有HTTPクライアントが保存する。
2. `POST /session` で資格情報・CSRF tokenと、適用可能な保存済みCookieを送信する。
3. サーバーが `Set-Cookie` で認証済みCookieを発行し、HTTPXが共有Cookie jarへ反映する。既存Cookieが指すサーバー側セッションだけを認証済みにする方式もある。
4. 後続の通常要求は同じクライアントを使い、domain・path・secure等の条件に合うCookieを送信する。

`AuthContext.requests` は認証フローへの再入を避ける専用の入口であり、別のCookie jarを作るものではない。Cookie反映は応答を受信する際にHTTPクライアントが行うため、`RequestResponse.set_cookies()` や `context.get_cookies()` を呼んでコピーし直す必要はない。これらは読取り用APIである。サンプルが `apply()` を上書きせず既定の要求を返しても、Cookieは通常のHTTP処理で付与される。

| 状態 | 管理する場所 |
| --- | --- |
| ログイン済みのサーバー側セッション | サーバー |
| 後続要求へ送信するセッションCookie | 共有HTTPクライアントのCookie jar |
| 準備済みか・資格情報の世代・期限 | コアの `OperationAuthSession` |

成功して `AuthLease()` を返すと、コアは世代を進め、無効状態を解除し、期限不明の利用可能な状態として扱う。`AuthLease` 自体がCookieを作成・保存したり、サーバーのログイン成功を検証したりするわけではない。クライアントのCookie共有とコアの認証状態管理は役割が異なる。

サンプルのPOST成功判定はHTTPステータスだけで、200のログイン失敗フォームやセッションCookie欠落までは検証しない。実サイトでは本文・遷移先・確認APIなどに基づき成功を判定し、失敗なら `AuthenticationError` とする。JSON本文にBearer tokenを返す方式では、本文を解析してフロー内に保持し、同期 `apply()` で付与する必要がある。Cookie共有だけではBearer認証に対応できない。

## 応答判定と明示確認

`is_auth_failure()` は `classify_response()` に置き換える。戻り値は `AuthDecision.AUTH_FAILURE`、`NOT_AUTH_FAILURE`、`UNKNOWN` である。既定では401が認証失敗、403がUNKNOWN、その他が認証更新対象外。署名URL失効の403はNOT_AUTH_FAILUREに分類し、画像URL回復へ渡せる。認証不要サイトの401/403は通常のHTTPエラーになる。

UNKNOWNは追加検証を求める。検証もUNKNOWNなら `AuthStateUnknownError` で停止し、成功扱いや無条件の再ログインはしない。応答だけで判断できないサイトでは、すべての応答をUNKNOWNにする代わりに、事前確認の宣言や `await context.auth.ensure_ready(check=True)` を使う。判断材料がない認証切れの即時検出は保証できない。

解析後に認証切れが分かった場合は `context.auth.invalidate(response)` で無効化し、次の認証対象要求の前に更新する。応答の `auth_receipt` はoperationと資格情報の世代を表す不透明な値として扱う。古い世代の無効化は無視し、別operationの応答は拒否する。

## 再入・並列実行・検査

認証フックの通信は `AuthContext.requests` に統一する。同じ認証処理へ再入せず、HTTPエラー応答も本文付きで返す。Cookie jar、プール、タイムアウト、通信上限は共有する。送信先は宣言済みoriginに限定し、cross-origin redirectでは非認証経路と同じヘッダー除去・本文転送制限を適用する。認証フックから通常の `context.requests` や `context.auth` を使うと `AuthReentryError` となる。

同時要求は実行中の初期化・更新・検証を共有する。初期化・更新の失敗もoperation内で共有する。待機要求のキャンセルは共有タスクを中断せず、operation終了時に残存タスクを回収する。自動の認証再試行は要求ごとに `network.auth_refresh_attempts` 回まで。非冪等要求の再送には `retry_non_idempotent=True` が必要。

通信の一時障害へのHTTP再試行（`network.max_attempts`）は個々の通信を扱い、
`auth_refresh_attempts`は認証応答を受けた要求の更新回数を制限する。認証hook自体の失敗を
同じoperation内で単純にリセットして再実行する設定ではない。workflowの新しい試行は新しい
認証セッションを準備する。操作内の失敗共有と試行間の回復を区別する。

`AuthScopeError`（設定）、`AuthReentryError`（実装契約）、`AuthStateUnknownError`（確認不能）はworkflowの自動再試行対象外。`inspect` も認証準備と同期applyを通すが画像本体は送信しない。Cookie変更は分離した検査セッション内に留まり、サーバー上のログイン・token発行などの副作用は取り消せない。
