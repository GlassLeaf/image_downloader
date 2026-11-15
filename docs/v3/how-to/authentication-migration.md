# 認証API 3の移行

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

`AuthLease(expires_in_seconds=...)` は有限の正数、または期限不明を表す `None` を受け付ける。コアは単調時計で期限を管理する。`refresh()` は元要求ではなく `AuthLease` を返す。新しいtokenの永続保存は行わない。

## 接続先と確認頻度

作者の `plugin.yaml` はプラグイン固有の `config` と、コアが管理する `authentication`・`endpoints` を分ける。共通設定を `validate_config()` に渡さない。利用者は `plugin_settings.<id>.authentication/endpoints` で上書きできる。

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

最初の認証対象要求前に初期化する。既定では各要求の確認通信は行わない。開始時に追加確認する場合は `verify_on_start`、毎送信試行前なら `verify_before_request`、定期確認なら正の `verify_interval_seconds` を指定する。毎要求確認を優先する。接続先の `verification.before_request/interval_seconds` は共通設定を部分的に上書きし、間隔を `null` にすればその接続先の定期確認を無効にできる。`refresh_skew_seconds` は期限より早く更新する秒数で、既定値は0。

検証を宣言するサイトでは `verify()` を実装し、`AuthVerification(AuthStatus.VALID/INVALID/UNKNOWN)` を返す。VALIDの場合だけ `lease=AuthLease(...)` で期限を更新でき、省略時は元の期限を維持する。初期化・更新に成功しても、そのことだけでは「追加検証済み」の時刻を更新しない。

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

`context.endpoints["api"].request("/items")` は宣言したoriginへの `RequestSpec` を構築する。絶対URLでも同じ接続先解決を通す。`operation` はoperationのoriginを指す補助接続先である。追加ホストは明示的に宣言し、同一originに矛盾する認証・確認方針を宣言しない。

`RequestSpec.auth` は `AuthMode.AUTO`（宣言に従う）、`NONE`（認証フローを通さない）、`REQUIRED`（フローと認証対象宣言を必須にする）から選ぶ。`NONE` でもCookie jarは使う。フローがある場合、未宣言の追加originへのAUTO/REQUIRED要求は `AuthScopeError` となる。サイト選択用の対応ホスト一覧から資格情報の送信許可を推測しない。

## 応答判定と明示確認

`is_auth_failure()` は `classify_response()` に置き換える。戻り値は `AuthDecision.AUTH_FAILURE`、`NOT_AUTH_FAILURE`、`UNKNOWN` である。既定では401が認証失敗、403がUNKNOWN、その他が認証更新対象外。署名URL失効の403はNOT_AUTH_FAILUREに分類し、画像URL回復へ渡せる。認証不要サイトの401/403は通常のHTTPエラーになる。

UNKNOWNは追加検証を求める。検証もUNKNOWNなら `AuthStateUnknownError` で停止し、成功扱いや無条件の再ログインはしない。応答だけで判断できないサイトでは、すべての応答をUNKNOWNにする代わりに、事前確認の宣言や `await context.auth.ensure_ready(check=True)` を使う。判断材料がない認証切れの即時検出は保証できない。

解析後に認証切れが分かった場合は `context.auth.invalidate(response)` で無効化し、次の認証対象要求の前に更新する。応答の `auth_receipt` はoperationと資格情報の世代を表す不透明な値として扱う。古い世代の無効化は無視し、別operationの応答は拒否する。

## 再入・並列実行・検査

認証フックの通信は `AuthContext.requests` に統一する。同じ認証処理へ再入せず、HTTPエラー応答も本文付きで返す。Cookie jar、プール、タイムアウト、通信上限は共有する。送信先は宣言済みoriginに限定し、cross-origin redirectでは非認証経路と同じヘッダー除去・本文転送制限を適用する。認証フックから通常の `context.requests` や `context.auth` を使うと `AuthReentryError` となる。

同時要求は実行中の初期化・更新・検証を共有する。初期化・更新の失敗もoperation内で共有する。待機要求のキャンセルは共有タスクを中断せず、operation終了時に残存タスクを回収する。自動の認証再試行は要求ごとに `network.auth_refresh_attempts` 回まで。非冪等要求の再送には `retry_non_idempotent=True` が必要。

`AuthScopeError`（設定）、`AuthReentryError`（実装契約）、`AuthStateUnknownError`（確認不能）はworkflowの自動再試行対象外。`inspect` も認証準備と同期applyを通すが画像本体は送信しない。Cookie変更は分離した検査セッション内に留まり、サーバー上のログイン・token発行などの副作用は取り消せない。
